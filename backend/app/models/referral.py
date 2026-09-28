"""Directory contacts for safe referrals; no student information is stored."""

from urllib.parse import urlsplit

from sqlalchemy import Boolean, CheckConstraint, Connection, Index, String, event
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class ReferralDirectoryEntry(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "referral_directory_entry"
    __table_args__ = (
        CheckConstraint("length(trim(topic)) > 0", name="topic_nonempty"),
        CheckConstraint("length(trim(office)) > 0", name="office_nonempty"),
        CheckConstraint("campus IN ('hammond', 'westville', 'all')", name="campus"),
        CheckConstraint(
            "coalesce(length(trim(contact_url)), 0) > 0 OR "
            "coalesce(length(trim(phone)), 0) > 0 OR "
            "coalesce(length(trim(email)), 0) > 0",
            name="contact_required",
        ),
        CheckConstraint(
            "contact_url IS NULL OR contact_url LIKE 'https://%'", name="https_contact"
        ),
        Index("ix_referral_topic_active_campus", "topic", "active", "campus"),
    )
    topic: Mapped[str] = mapped_column(String)
    office: Mapped[str] = mapped_column(String)
    contact_url: Mapped[str | None] = mapped_column(String)
    phone: Mapped[str | None] = mapped_column(String)
    email: Mapped[str | None] = mapped_column(String)
    campus: Mapped[str] = mapped_column(String, default="all", server_default="all")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


@event.listens_for(ReferralDirectoryEntry, "before_insert")
@event.listens_for(ReferralDirectoryEntry, "before_update")
def validate_referral(
    mapper: Mapper[ReferralDirectoryEntry], connection: Connection, target: ReferralDirectoryEntry
) -> None:
    if not target.topic.strip() or not target.office.strip():
        raise ValueError("Referral requires topic and office")
    if target.campus is not None and target.campus not in {"hammond", "westville", "all"}:
        raise ValueError("Invalid campus")
    contacts = [target.contact_url, target.phone, target.email]
    if not any(value and value.strip() for value in contacts):
        raise ValueError("Referral requires at least one contact")
    if any(value is not None and not value.strip() for value in contacts):
        raise ValueError("Contact values must be non-empty when supplied")
    if target.contact_url:
        url = urlsplit(target.contact_url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or any(c.isspace() for c in target.contact_url)
        ):
            raise ValueError("Contact URL must use HTTPS without credentials")
        _ = url.port
    if target.email and (
        "@" not in target.email
        or any(c.isspace() for c in target.email)
        or not all(target.email.split("@"))
        or target.email.count("@") != 1
    ):
        raise ValueError("Invalid contact email")
