"""Atomic preparation of reviewed revisions; activation remains a governance action."""

import hashlib
from collections.abc import Callable, Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.ai import embed_chunks
from app.db.base import utc_now
from app.ingestion.chunk import DocumentChunk, chunk_document
from app.ingestion.extract import extract_document
from app.ingestion.sources import ManifestEntry, _governed
from app.models.source import SourceRevision
from app.models.source_chunk import SourceChunk


class IngestionError(ValueError):
    """Preparation failed without publishing any partial retrieval units."""


def _check(session: Session, entry: ManifestEntry, *, lock: bool = False) -> None:
    _, revision = _governed(session, entry, lock=lock)
    now = utc_now()
    if (
        not revision.readable
        or revision.conflict_group is not None
        or revision.effective_from is not None
        and revision.effective_from > now
        or revision.effective_until is not None
        and revision.effective_until <= now
    ):
        raise IngestionError("Revision is not eligible for preparation.")


def _existing(
    session: Session,
    revision_id: UUID,
    expected: Sequence[DocumentChunk],
    model: str,
    version: str,
    dimension: int,
) -> tuple[UUID, ...]:
    rows = session.scalars(
        select(SourceChunk)
        .where(
            SourceChunk.revision_id == revision_id,
            SourceChunk.embedding_model == model,
            SourceChunk.embedding_version == version,
        )
        .order_by(SourceChunk.ordinal)
    ).all()
    if not rows:
        return ()
    # An identity cannot silently acquire a new dimension or a different chunk layout.
    if len(rows) != len(expected) or any(
        (row.ordinal, row.text, row.heading, row.table_context, row.citation_anchor)
        != (chunk.ordinal, chunk.text, chunk.heading, chunk.table_context, chunk.citation_anchor)
        or row.embedding_dimension != dimension
        for row, chunk in zip(rows, expected, strict=True)
    ):
        raise IngestionError("Existing embedding identity is incompatible; use a new version.")
    return tuple(row.id for row in rows)


def ingest_revision(
    factory: sessionmaker[Session],
    *,
    revision_id: UUID,
    content: bytes,
    media_type: str,
    embed: Callable[[list[str]], object],
    model: str,
    version: str,
    dimension: int,
    chunk_size: int = 800,
    chunk_overlap: int = 100,
    timeout_seconds: float = 6.0,
) -> tuple[UUID, ...]:
    """Own transactions; return immutable chunk IDs only after a complete commit.

    Call with collected bytes and a previously registered/reviewed revision.
    Source/hash/scope metadata stays on its existing immutable relationships.
    No network/provider calls occur while governance rows are locked. Retries
    reuse a complete matching identity; concurrent imports serialize at commit.
    This does not approve, activate, supersede or change revision history.
    """
    try:
        if not model.strip() or not version.strip() or type(dimension) is not int or dimension < 1:
            raise IngestionError("Invalid embedding identity.")
        with factory() as session:
            revision = session.get(SourceRevision, revision_id)
            if revision is None:
                raise IngestionError("Revision not found.")
            entry = ManifestEntry(
                source_id=revision.source_id,
                revision_id=revision_id,
                canonical_url=revision.source.canonical_url,
                owner_office=revision.source.owner_office,
                sha256=hashlib.sha256(content).hexdigest(),
                media_type=media_type,
            )
            _check(session, entry)
        prepared = chunk_document(
            extract_document(content, media_type=media_type),
            size=chunk_size,
            overlap=chunk_overlap,
            revision_id=revision_id,
        )
        if not prepared:
            raise IngestionError("Document contains no retrieval units.")
        with factory.begin() as session:
            _check(session, entry, lock=True)
            existing = _existing(session, revision_id, prepared, model, version, dimension)
            if existing:
                return existing
        embedded = embed_chunks(
            prepared,
            embed=embed,
            model=model,
            version=version,
            dimension=dimension,
            timeout_seconds=timeout_seconds,
        )
        with factory.begin() as session:
            _check(session, entry, lock=True)
            existing = _existing(session, revision_id, prepared, model, version, dimension)
            if existing:
                return existing
            rows = [
                SourceChunk(
                    revision_id=revision_id,
                    ordinal=chunk.ordinal,
                    text=chunk.text,
                    heading=chunk.heading,
                    table_context=chunk.table_context,
                    citation_anchor=chunk.citation_anchor,
                    search_vector=func.to_tsvector("english", chunk.text),
                    embedding=list(chunk.embedding),
                    embedding_model=chunk.embedding_model,
                    embedding_version=chunk.embedding_version,
                    embedding_dimension=chunk.embedding_dimension,
                )
                for chunk in embedded
            ]
            session.add_all(rows)
            session.flush()
            identifiers = tuple(row.id for row in rows)
        return identifiers
    except IngestionError:
        raise
    except Exception:
        raise IngestionError("Revision preparation unavailable.") from None
