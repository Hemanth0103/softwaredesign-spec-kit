"""Exact hybrid search over governed content; no cache or question persistence.

Conflicts are diagnostics, never generation excerpts. Callers must return an
unresolved decision when conflict_revision_ids is nonempty. Final answer
verification must recheck eligibility again after generation (T029).
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.ai import ApprovedExcerpt
from app.db.base import utc_now
from app.ingestion.sources import validate_official_url
from app.models.source import ApprovedSource, SourceRevision
from app.models.source_chunk import SourceChunk
from app.services.source_eligibility import Context, eligible_revisions, recheck_revisions


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: UUID
    revision_id: UUID
    source_id: UUID
    text: str
    title: str
    canonical_url: str
    heading: str | None
    table_context: str | None
    citation_anchor: str | None
    campus: str
    program: str | None
    course: str | None
    academic_term: str | None
    score: float

    @property
    def url(self) -> str:
        return self.canonical_url

    def as_excerpt(self) -> ApprovedExcerpt:
        return ApprovedExcerpt(self.chunk_id, self.revision_id, self.text, self.title, self.url)


@dataclass(frozen=True)
class RetrievalResult:
    chunks: tuple[RetrievedChunk, ...] = ()
    conflict_revision_ids: tuple[UUID, ...] = ()


def retrieve(
    session_factory: sessionmaker[Session],
    *,
    question: str,
    query_embedding: Sequence[float],
    context: Context,
    model: str,
    version: str,
    dimension: int,
    now: datetime | None = None,
    limit: int = 8,
    min_similarity: float = 0.5,
) -> RetrievalResult:
    """Fuse lexical and semantic ranks with reciprocal rank fusion (k=60).

    Materialize eligibility AND embedding identity before cosine evaluation:
    pgvector rows may have different dimensions. Lexical evidence remains usable
    across embedding rebuilds; only compatible vectors enter semantic ranking.
    Similarity is a candidate gate, not proof of support; T029 verifies claims.
    """
    vector = list(query_embedding)
    if (
        not question.strip()
        or len(question) > 4000
        or type(dimension) is not int
        or dimension < 1
        or len(vector) != dimension
        or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector)
        or not any(vector)
        or not model.strip()
        or not version.strip()
        or type(limit) is not int
        or not 1 <= limit <= 100
        or not math.isfinite(min_similarity)
        or not -1 <= min_similarity <= 1
    ):
        raise ValueError("Invalid retrieval input")
    instant = now or utc_now()
    revision_query = eligible_revisions(context=context, now=instant, include_conflicts=True)
    eligible = (
        select(SourceChunk.__table__)
        .where(SourceChunk.revision_id.in_(revision_query.with_only_columns(SourceRevision.id)))
        .cte("governed_chunks")
        .prefix_with("MATERIALIZED", dialect="postgresql")
    )
    compatible = (
        select(eligible)
        .where(
            eligible.c.embedding_model == model,
            eligible.c.embedding_version == version,
            eligible.c.embedding_dimension == dimension,
            func.vector_norm(eligible.c.embedding) > 0,
        )
        .cte("compatible_chunks")
        .prefix_with("MATERIALIZED", dialect="postgresql")
    )
    query = func.websearch_to_tsquery("english", question)
    lexical = (
        select(eligible.c.id, eligible.c.revision_id)
        .where(eligible.c.search_vector.op("@@")(query))
        .order_by(func.ts_rank_cd(eligible.c.search_vector, query).desc(), eligible.c.id)
    )
    distance = compatible.c.embedding.cosine_distance(vector)
    semantic = (
        select(compatible.c.id, compatible.c.revision_id)
        .where(distance <= 1 - min_similarity)
        .order_by(distance, compatible.c.id)
    )
    scores: dict[UUID, float] = {}
    # Search both lists without a cutoff for conflict diagnostics. A relevant
    # conflict must not disappear merely because many active chunks rank ahead.
    with session_factory() as session:
        for statement in (lexical, semantic):
            for rank, row in enumerate(session.execute(statement), 1):
                scores[row.id] = scores.get(row.id, 0.0) + 1.0 / (60 + rank)
    if not scores:
        return RetrievalResult()
    # Fresh transaction: retirement/conflict updates after ranking take effect.
    chunks: list[RetrievedChunk] = []
    conflicts: set[UUID] = set()
    with session_factory() as session:
        rows = session.execute(
            select(SourceChunk, SourceRevision, ApprovedSource)
            .join(SourceRevision, SourceChunk.revision_id == SourceRevision.id)
            .join(ApprovedSource, SourceRevision.source_id == ApprovedSource.id)
            .where(
                SourceChunk.id.in_(scores),
                SourceRevision.id.in_(revision_query.with_only_columns(SourceRevision.id)),
            )
        )
        for chunk, revision, source in rows:
            if revision.status == "unresolved" or revision.conflict_group is not None:
                conflicts.add(revision.id)
                continue
            try:
                validate_official_url(source.canonical_url)
            except ValueError:
                continue
            chunks.append(
                RetrievedChunk(
                    chunk.id,
                    revision.id,
                    source.id,
                    chunk.text,
                    source.title,
                    source.canonical_url,
                    chunk.heading,
                    chunk.table_context,
                    chunk.citation_anchor,
                    revision.campus,
                    revision.program,
                    revision.course,
                    revision.academic_term,
                    scores[chunk.id],
                )
            )
    if conflicts:
        return RetrievalResult(conflict_revision_ids=tuple(sorted(conflicts, key=str)))
    chunks.sort(key=lambda item: (-item.score, str(item.chunk_id)))
    selected = tuple(chunks[:limit])
    if selected and not recheck_revisions(
        session_factory, [item.revision_id for item in selected], context=context, now=now
    ):
        return RetrievalResult()
    return RetrievalResult(chunks=selected)
