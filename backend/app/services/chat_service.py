"""Request-scoped orchestration; no prompts, drafts, or questions are retained."""

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.ai import AIAdapter, ApprovedExcerpt, Provider, _bounded
from app.api.schemas.chat import StudentQuestion
from app.config import Settings
from app.db.base import utc_now
from app.models.referral import ReferralDirectoryEntry
from app.models.source import ApprovedSource, SourceRevision
from app.models.source_chunk import SourceChunk
from app.services.citation_verifier import verify_answer
from app.services.decisions import evaluate_gates
from app.services.retrieval import retrieve
from app.services.source_eligibility import Context


class ChatService:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        settings: Settings,
        embed: Callable[[list[str]], Sequence[Sequence[float]]] | None = None,
        generate_grounded_answer: Callable[..., object] | None = None,
        now: Callable[[], datetime] = utc_now,
        providers: Mapping[str, Provider] | None = None,
    ) -> None:
        self.factory = session_factory
        self.settings = settings
        self.now = now

        # Resolve the provider lazily, so safety/context checks need no AI setup.
        def adapter() -> AIAdapter:
            return AIAdapter(settings, providers=providers or {})

        self.embed = embed or (lambda texts: adapter().embed(texts))
        self.generate = generate_grounded_answer or (
            lambda **kwargs: adapter().generate_grounded_answer(**kwargs)
        )

    def _contacts(self) -> list[dict[str, Any]]:
        with self.factory() as session:
            return [
                {
                    key: getattr(row, key)
                    for key in (
                        "topic",
                        "office",
                        "contact_url",
                        "phone",
                        "email",
                        "campus",
                        "active",
                    )
                }
                for row in session.scalars(select(ReferralDirectoryEntry))
            ]

    def _required_context(self, question: str, context: Context) -> list[str]:
        """Probe governed lexical scope metadata before embedding, never generation evidence.

        This conservative probe asks about restricted applicability when a relevant
        approved section has missing scope. Supplied scopes still filter candidates.
        It does not infer a campus/program/term from the student's free text.
        """
        fields = {
            "campus": SourceRevision.campus,
            "program": SourceRevision.program,
            "course": SourceRevision.course,
            "academicTerm": SourceRevision.academic_term,
        }
        missing = [field for field in fields if not context.get(field)]
        if not missing:
            return []
        words = re.findall(r"[a-zA-Z0-9]+", question)
        if not words:
            return []
        instant = self.now()
        query = func.to_tsquery("english", " | ".join(words))
        statement = (
            select(SourceRevision)
            .join(ApprovedSource)
            .join(SourceChunk)
            .where(
                ApprovedSource.approval_status == "approved",
                ApprovedSource.lifecycle_status == "active",
                SourceRevision.status.in_(("active", "unresolved")),
                SourceRevision.readable.is_(True),
                or_(
                    SourceRevision.effective_from.is_(None),
                    SourceRevision.effective_from <= instant,
                ),
                or_(
                    SourceRevision.effective_until.is_(None),
                    SourceRevision.effective_until > instant,
                ),
                SourceChunk.search_vector.op("@@")(query),
            )
            .distinct()
        )
        for field, column in fields.items():
            if context.get(field):
                statement = statement.where(
                    or_(
                        column == context[field],
                        column == "all" if field == "campus" else column.is_(None),
                    )
                )
        with self.factory() as session:
            rows = session.scalars(statement).all()
            return [
                field
                for field in missing
                if any(
                    getattr(row, "academic_term" if field == "academicTerm" else field)
                    not in (None, "all" if field == "campus" else None)
                    for row in rows
                )
            ]

    def answer(self, request: StudentQuestion) -> dict[str, Any]:
        context = request.model_dump(by_alias=True, exclude={"question"}, exclude_none=True)
        # Emergency handling has no database/provider dependency. Normal account
        # referrals are subsequently resolved against the governed directory.
        early = evaluate_gates(
            question=request.question,
            context=context,
            required_context=(),
            evidence_status="available",
            contacts=(),
        )
        if early and early["outcome"] == "emergency":
            return early
        contacts = self._contacts()

        def gate(status: str, required: Sequence[str] = ()) -> dict[str, Any] | None:
            return evaluate_gates(
                question=request.question,
                context=context,
                required_context=required,
                evidence_status=status,
                contacts=contacts,
            )

        decision = gate("available")
        if decision:
            return decision
        decision = gate("available", self._required_context(request.question, context))
        if decision:
            return decision
        try:
            vectors = _bounded(
                lambda: self.embed([request.question]), self.settings.ai_timeout_seconds
            )
            if len(vectors) != 1:
                raise ValueError("Invalid query embedding count")
            retrieval = retrieve(
                self.factory,
                question=request.question,
                query_embedding=vectors[0],
                context=context,
                model=self.settings.ai_embedding_model,
                version=self.settings.embedding_version,
                dimension=self.settings.embedding_dimension,
                now=self.now(),
            )
        except Exception:
            decision = gate("provider_failure")
        else:
            if retrieval.conflict_revision_ids:
                decision = gate("conflicting")
            elif not retrieval.chunks:
                decision = gate("unsupported")
            else:
                excerpts: tuple[ApprovedExcerpt, ...] = tuple(
                    c.as_excerpt() for c in retrieval.chunks
                )
                try:
                    draft = _bounded(
                        lambda: self.generate(
                            question=request.question, excerpts=excerpts, context=dict(context)
                        ),
                        self.settings.ai_timeout_seconds,
                    )
                except Exception:
                    decision = gate("provider_failure")
                else:
                    try:
                        return verify_answer(
                            self.factory,
                            draft=draft,
                            retrieval=retrieval,
                            context=context,
                            now=self.now(),
                        ).model_dump(exclude_none=True)
                    except Exception:
                        decision = gate("ambiguous")
        assert decision is not None
        return decision
