"""Fail-closed verification of untrusted drafts; no semantic model is trusted.

Support requires complete excerpt text (whitespace may vary). This deliberately
rejects paraphrases and partial excerpts: lexical overlap cannot prove entailment
or preserve exceptions, negation, dates, and table headers. T030 chooses a safe
referral/unresolved outcome on CitationVerificationError.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.api.schemas.chat import Citation, QuestionContext, SupportedAnswer
from app.ingestion.sources import validate_official_url
from app.models.source import ApprovedSource, SourceRevision
from app.models.source_chunk import SourceChunk
from app.services.retrieval import RetrievalResult, RetrievedChunk
from app.services.source_eligibility import Context, eligible_revisions


class CitationVerificationError(ValueError):
    """No verified answer exists; caller must select a safe decision."""


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _fresh_evidence_matches(
    factory: sessionmaker[Session],
    chunks: tuple[RetrievedChunk, ...],
    context: Context,
    now: datetime | None,
) -> bool:
    """Fresh transaction validates eligibility AND snapshot identity immediately before return."""
    with factory() as session:
        rows = session.execute(
            select(SourceChunk, SourceRevision, ApprovedSource)
            .join(SourceRevision, SourceChunk.revision_id == SourceRevision.id)
            .join(ApprovedSource, SourceRevision.source_id == ApprovedSource.id)
            .where(
                SourceChunk.id.in_([c.chunk_id for c in chunks]),
                SourceRevision.id.in_(
                    eligible_revisions(context=context, now=now).with_only_columns(
                        SourceRevision.id
                    )
                ),
            )
        )
        found = {chunk.id: (chunk, revision, source) for chunk, revision, source in rows}
        for evidence in chunks:
            if evidence.chunk_id not in found:
                return False
            chunk, revision, source = found[evidence.chunk_id]
            if (
                revision.id != evidence.revision_id
                or source.id != evidence.source_id
                or chunk.text != evidence.text
                or source.title != evidence.title
                or source.canonical_url != evidence.url
                or chunk.heading != evidence.heading
                or chunk.table_context != evidence.table_context
                or chunk.citation_anchor != evidence.citation_anchor
                or (revision.campus, revision.program, revision.course, revision.academic_term)
                != (evidence.campus, evidence.program, evidence.course, evidence.academic_term)
            ):
                return False
        return bool(chunks)


def verify_answer(
    session_factory: sessionmaker[Session],
    *,
    draft: object,
    retrieval: RetrievalResult,
    context: Context,
    now: datetime | None = None,
) -> SupportedAnswer:
    """Accept adapter {answer,citations} or the strict public supported-answer shape.

    Internal citations contain only chunkId; public citations must match canonical
    URL/title exactly. Applied context is constructed from trusted request context;
    provider-supplied context must equal it. No provider context label is accepted.
    Call immediately before answer return; concurrent commits after the final read
    remain outside the database/HTTP boundary, as with recheck_revisions.
    """
    try:
        if not retrieval.chunks or retrieval.conflict_revision_ids or not isinstance(draft, dict):
            raise ValueError
        expected = QuestionContext.model_validate(dict(context))
        if set(draft) == {"answer", "citations"}:
            answer, raw_citations = draft["answer"], draft["citations"]
        elif set(draft) == {"outcome", "answer", "citations", "appliedContext"}:
            public = SupportedAnswer.model_validate(draft)
            if public.applied_context != expected:
                raise ValueError
            answer, raw_citations = draft["answer"], draft["citations"]
        else:
            raise ValueError
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError
        if not isinstance(raw_citations, list) or not raw_citations:
            raise ValueError
        selected: dict[UUID, RetrievedChunk] = {}
        citations: list[Citation] = []
        for raw in raw_citations:
            if not isinstance(raw, dict):
                raise ValueError
            if set(raw) == {"chunkId"} and isinstance(raw["chunkId"], str):
                matches = [c for c in retrieval.chunks if c.chunk_id == UUID(raw["chunkId"])]
            elif set(raw) == {"title", "url"}:
                matches = [
                    c for c in retrieval.chunks if c.title == raw["title"] and c.url == raw["url"]
                ]
            else:
                raise ValueError
            if not matches:
                raise ValueError
            # Each cited source must supply complete text used in the answer.
            supporting = [c for c in matches if _normalize(c.text) in _normalize(answer)]
            if not supporting:
                raise ValueError
            for chunk in supporting:
                validate_official_url(chunk.url)
                selected[chunk.chunk_id] = chunk
            citation = Citation(title=supporting[0].title, url=supporting[0].url)
            if citation not in citations:
                citations.append(citation)
        # Cover the entire answer with complete cited excerpts, without deleting
        # arbitrary matching substrings that could leave an unsupported claim.
        remaining = _normalize(answer)
        texts = sorted({_normalize(c.text) for c in selected.values()}, key=len, reverse=True)
        while remaining:
            match = next(
                (
                    text
                    for text in texts
                    if text and (remaining == text or remaining.startswith(text + " "))
                ),
                None,
            )
            if match is None:
                raise ValueError
            remaining = remaining[len(match) :].lstrip()
        result = SupportedAnswer(
            outcome="answer", answer=answer, citations=citations, appliedContext=expected
        )
        if not _fresh_evidence_matches(session_factory, tuple(selected.values()), context, now):
            raise ValueError
        return result
    except Exception:
        raise CitationVerificationError("Answer verification unavailable.") from None
