"""Review-first content refresh and immutable embedding rebuilds.

Registration uses the caller's transaction. Preparation owns its transactions;
activation remains an explicit owning-office source_governance.review_source
operation, atomically superseding the previous revision and appending audits.
"""

import hashlib
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.ingestion.extract import ExtractionError, extract_document
from app.ingestion.sources import MEDIA_TYPES, ManifestEntry, _governed, validate_official_url
from app.ingestion.store import ingest_revision
from app.models.review_event import SourceReviewEvent
from app.models.source import ApprovedSource, SourceRevision


class RefreshError(ValueError):
    """Refresh registration failed without granting approval or activation."""


def register_revision(
    session: Session,
    *,
    source_id: UUID,
    content: bytes,
    retrieved_at: datetime,
    media_type: str = "text/html",
    base_revision_id: UUID | None = None,
) -> SourceRevision:
    """Register changed bytes as pending review; never change an existing hash.

    Inherit scope/effective dates from the explicit reviewed base, or the single
    active revision (falling back to a single approved revision). Ambiguous bases
    require an explicit ID. Matching hashes reuse their identity and state,
    including terminal revisions; retries cannot resurrect retired evidence.
    Extraction establishes readability, not approval. Unreadable bytes remain
    pending and cannot be prepared. Caller must commit or roll back registration.
    """
    if not isinstance(content, bytes) or not content:
        raise RefreshError("Refresh requires non-empty document bytes.")
    if not isinstance(retrieved_at, datetime) or retrieved_at.utcoffset() is None:
        raise RefreshError("Retrieval timestamp must be timezone-aware.")
    if media_type not in MEDIA_TYPES:
        raise RefreshError("Unsupported document media type.")
    # Validate/extract outside row locks. Unsupported media fails safely; unreadable
    # supported content is still registered for manual review, without publication.
    try:
        readable = bool(extract_document(content, media_type=media_type))
    except ExtractionError:
        readable = False
    digest = hashlib.sha256(content).hexdigest()
    try:
        with session.begin_nested():
            source = session.scalar(
                select(ApprovedSource)
                .where(ApprovedSource.id == source_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if (
                source is None
                or source.approval_status != "approved"
                or source.lifecycle_status != "active"
            ):
                raise RefreshError("Refresh requires an approved active source.")
            validate_official_url(source.canonical_url)
            if (
                session.scalar(
                    select(SourceReviewEvent.id)
                    .where(
                        SourceReviewEvent.source_id == source_id,
                        SourceReviewEvent.revision_id.is_(None),
                        SourceReviewEvent.action == "approve",
                    )
                    .limit(1)
                )
                is None
            ):
                raise RefreshError("Owning-office source approval audit is required.")
            if (
                session.scalar(
                    select(SourceRevision.id)
                    .where(
                        SourceRevision.source_id == source_id,
                        SourceRevision.conflict_group.is_not(None),
                    )
                    .limit(1)
                )
                is not None
            ):
                raise RefreshError("Resolve source conflicts before refreshing.")
            existing = session.scalar(
                select(SourceRevision)
                .where(
                    SourceRevision.source_id == source_id,
                    SourceRevision.content_hash == digest,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if existing is not None:
                if existing.status == "pending_review":
                    # Collection registers bytes without extraction. Establish the
                    # derived readability flag before office review, without approval.
                    existing.readable = readable
                    session.flush()
                return existing
            bases = list(
                session.scalars(
                    select(SourceRevision)
                    .where(
                        SourceRevision.source_id == source_id,
                        SourceRevision.status.in_(("approved", "active")),
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            if base_revision_id is not None:
                bases = [row for row in bases if row.id == base_revision_id]
            else:
                active = [row for row in bases if row.status == "active"]
                bases = active or bases
            if len(bases) != 1:
                raise RefreshError("Select a single reviewed base revision for refresh.")
            base = bases[0]
            _governed(
                session,
                ManifestEntry(
                    source_id=source_id,
                    revision_id=base.id,
                    canonical_url=source.canonical_url,
                    owner_office=source.owner_office,
                    sha256=base.content_hash,
                    media_type=media_type,
                ),
            )
            revision = SourceRevision(
                source_id=source_id,
                content_hash=digest,
                retrieved_at=retrieved_at,
                campus=base.campus,
                program=base.program,
                course=base.course,
                academic_term=base.academic_term,
                effective_from=base.effective_from,
                effective_until=base.effective_until,
                readable=readable,
            )
            session.add(revision)
            session.flush()
            return revision
    except RefreshError:
        raise
    except Exception:
        raise RefreshError("Revision registration unavailable.") from None


def rebuild_revision(
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
    """Prepare an approved refresh or a new model identity without activation.

    Reuse storage's hash/approval/eligibility gates, bounded provider calls,
    immutable chunks, atomic writes and concurrency-safe retries. An existing
    model/version cannot change dimension or layout; use a new version instead.
    Old embeddings and all revision/review history are retained.
    """
    return ingest_revision(
        factory,
        revision_id=revision_id,
        content=content,
        media_type=media_type,
        embed=embed,
        model=model,
        version=version,
        dimension=dimension,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        timeout_seconds=timeout_seconds,
    )
