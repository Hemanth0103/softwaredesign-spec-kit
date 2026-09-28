"""Append-only review history.

The governance service (T013) must authorize the owning office for subject-source
changes and the Dean of Students for conflict resolution before inserting events.
An event records an action; inserting it does not grant approval or permissions.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Connection,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    event,
    func,
)
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from app.db.base import Base, UUIDMixin, utc_now


class SourceReviewEvent(UUIDMixin, Base):
    __tablename__ = "source_review_event"
    __table_args__ = (
        Index("ix_review_source_timestamp", "source_id", "timestamp"),
        Index("ix_review_revision", "revision_id"),
        CheckConstraint(
            "action IN ('approve', 'activate', 'supersede', 'retire', 'resolve_conflict')",
            name="action",
        ),
        CheckConstraint("length(trim(reason)) > 0", name="reason_nonempty"),
        CheckConstraint("length(trim(reviewer_subject)) > 0", name="reviewer_nonempty"),
    )

    source_id: Mapped[UUID] = mapped_column(ForeignKey("approved_source.id", ondelete="RESTRICT"))
    # Source-level actions need no revision; revision-level actions retain both identifiers.
    revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("source_revision.id", ondelete="RESTRICT")
    )
    reviewer_subject: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String)
    reason: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


@event.listens_for(SourceReviewEvent, "before_insert")
def validate_event(
    mapper: Mapper[SourceReviewEvent], connection: Connection, target: SourceReviewEvent
) -> None:
    if target.action not in {"approve", "activate", "supersede", "retire", "resolve_conflict"}:
        raise ValueError("Invalid review action")
    if not target.reason.strip() or not target.reviewer_subject.strip():
        raise ValueError("Review requires an OIDC subject and a non-empty reason")
    if target.timestamp is not None:
        if target.timestamp.utcoffset() is None:
            raise ValueError("Review timestamp must be timezone-aware")
        target.timestamp = target.timestamp.astimezone(UTC)
    if target.revision_id is not None:
        from sqlalchemy import select

        from app.models.source import SourceRevision

        source_id = connection.scalar(
            select(SourceRevision.source_id).where(SourceRevision.id == target.revision_id)
        )
        if source_id != target.source_id:
            raise ValueError("Review revision must belong to the specified source")


@event.listens_for(SourceReviewEvent, "before_update")
@event.listens_for(SourceReviewEvent, "before_delete")
def reject_event_change(
    mapper: Mapper[SourceReviewEvent], connection: Connection, target: SourceReviewEvent
) -> None:
    raise ValueError("Review events are append-only")
