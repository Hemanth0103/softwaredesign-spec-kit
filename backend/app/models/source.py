"""Governed sources and revision history; authorization belongs to the service layer.

ORM validators protect ordinary model writes. Bulk SQL bypasses these validators;
use ORM instance writes for hash and lifecycle changes. T009 owns schema deployment.
"""

import re
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db.base import Base, TimestampMixin, UUIDMixin


class ApprovedSource(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "approved_source"
    __table_args__ = (
        CheckConstraint(
            "approval_status IN ('draft', 'approved', 'rejected')", name="approval_status"
        ),
        CheckConstraint(
            "lifecycle_status IN ('active', 'superseded', 'retired')", name="lifecycle_status"
        ),
        CheckConstraint("canonical_url LIKE 'https://%'", name="https_url"),
        CheckConstraint("length(trim(title)) > 0", name="title_nonempty"),
        CheckConstraint("length(trim(owner_office)) > 0", name="owner_nonempty"),
        CheckConstraint("length(trim(subject_area)) > 0", name="subject_nonempty"),
        Index("ix_source_eligibility", "approval_status", "lifecycle_status"),
    )

    canonical_url: Mapped[str] = mapped_column(String, unique=True)
    title: Mapped[str] = mapped_column(String)
    owner_office: Mapped[str] = mapped_column(String)
    subject_area: Mapped[str] = mapped_column(String)
    approval_status: Mapped[str] = mapped_column(String, default="draft", server_default="draft")
    lifecycle_status: Mapped[str] = mapped_column(String, default="active", server_default="active")
    revisions: Mapped[list["SourceRevision"]] = relationship(back_populates="source")

    @validates(
        "canonical_url",
        "title",
        "owner_office",
        "subject_area",
        "approval_status",
        "lifecycle_status",
    )
    def validate_field(self, key: str, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be non-empty")
        value = value.strip()
        if key == "canonical_url":
            url = urlsplit(value)
            if (
                url.scheme != "https"
                or not url.hostname
                or url.username
                or url.password
                or any(c.isspace() for c in value)
            ):
                raise ValueError("canonical_url must be an HTTPS URL without credentials")
            _ = url.port
        allowed = {
            "approval_status": {"draft", "approved", "rejected"},
            "lifecycle_status": {"active", "superseded", "retired"},
        }
        if key in allowed and value not in allowed[key]:
            raise ValueError(f"Invalid {key}")
        return value


class SourceRevision(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "source_revision"
    __table_args__ = (
        CheckConstraint("campus IN ('hammond', 'westville', 'all')", name="campus"),
        CheckConstraint(
            "status IN ('pending_review', 'approved', 'active', "
            "'unresolved', 'superseded', 'retired')",
            name="status",
        ),
        CheckConstraint("length(content_hash) = 64", name="hash_length"),
        CheckConstraint(
            "effective_until IS NULL OR effective_from IS NULL OR effective_until > effective_from",
            name="effective_window",
        ),
        UniqueConstraint("source_id", "content_hash", name="uq_revision_source_hash"),
        Index("ix_revision_source_status", "source_id", "status"),
        Index("ix_revision_scope", "campus", "program", "course", "academic_term"),
        Index("ix_revision_effective", "effective_from", "effective_until"),
    )

    readable: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    conflict_group: Mapped[UUID | None] = mapped_column(index=True)

    source_id: Mapped[UUID] = mapped_column(ForeignKey("approved_source.id", ondelete="RESTRICT"))
    source: Mapped[ApprovedSource] = relationship(back_populates="revisions")
    content_hash: Mapped[str] = mapped_column(String(64), active_history=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    campus: Mapped[str] = mapped_column(String, default="all", server_default="all")
    program: Mapped[str | None] = mapped_column(String(160))
    course: Mapped[str | None] = mapped_column(String(32))
    academic_term: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(
        String, default="pending_review", server_default="pending_review", active_history=True
    )

    @validates("content_hash")
    def validate_hash(self, key: str, value: str) -> str:
        if not re.fullmatch("[0-9a-f]{64}", value):
            raise ValueError("content_hash must be a lowercase SHA-256 hex digest")
        current = self.content_hash
        if current is not None and current != value:
            raise ValueError("Revision content_hash is immutable; create a new revision")
        return value

    @validates("status")
    def validate_status(self, key: str, value: str) -> str:
        current = self.status
        allowed: dict[str | None, set[str]] = {
            None: {"pending_review"},
            "pending_review": {"approved"},
            "approved": {"active"},
            "active": {"superseded", "retired", "unresolved"},
            "unresolved": {"active", "retired"},
            "superseded": set(),
            "retired": set(),
        }
        if value != current and value not in allowed.get(current, set()):
            raise ValueError("Invalid revision status transition")
        return value

    @validates("campus")
    def validate_campus(self, key: str, value: str) -> str:
        if value not in {"hammond", "westville", "all"}:
            raise ValueError("Invalid campus")
        return value

    @validates("retrieved_at", "effective_from", "effective_until")
    def validate_timestamp(self, key: str, value: datetime | None) -> datetime | None:
        if value is None and key != "retrieved_at":
            return None
        if value is None or value.utcoffset() is None:
            raise ValueError(f"{key} must be timezone-aware")
        return value.astimezone(UTC)

    @validates("program", "course", "academic_term")
    def validate_scope(self, key: str, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or len(value) > {"program": 160, "course": 32, "academic_term": 80}[key]:
            raise ValueError(f"Invalid {key}")
        return value
