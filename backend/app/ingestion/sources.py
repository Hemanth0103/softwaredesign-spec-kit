"""Collect explicit governed inputs, never crawl links or grant approval.

The caller owns the Session transaction: commit pending-review registrations on
success, rollback on error. Returned bytes still require T025's publication gate.
A changed hash returns no ingestible content and never activates a revision.
"""

import hashlib
import math
import re
import stat
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urljoin, urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utc_now
from app.models.review_event import SourceReviewEvent
from app.models.source import ApprovedSource, SourceRevision

MEDIA_TYPES = {
    "text/html",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


class SourceCollectionError(ValueError):
    """Actionable collection failure without document bodies or transport details."""


def validate_official_url(value: str) -> str:
    """Only HTTPS pnw.edu and its subdomains, using the standard TLS port."""
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        if (
            url.scheme != "https"
            or not re.fullmatch(r"[a-z0-9.-]+", host)
            or not (host == "pnw.edu" or host.endswith(".pnw.edu"))
            or any(
                not label or label.startswith("-") or label.endswith("-")
                for label in host.split(".")
            )
            or url.username is not None
            or url.password is not None
            or url.port not in {None, 443}
            or url.fragment
            or "\\" in value
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
        ):
            raise ValueError
    except ValueError:
        raise SourceCollectionError("Source requires an official PNW HTTPS URL") from None
    return value


class ManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    source_id: UUID
    revision_id: UUID
    canonical_url: str
    owner_office: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: str
    path: str | None = None
    retrieved_at: datetime | None = None

    @field_validator("canonical_url")
    @classmethod
    def official_url(cls, value: str) -> str:
        return validate_official_url(value)

    @field_validator("media_type")
    @classmethod
    def supported_type(cls, value: str) -> str:
        if value not in MEDIA_TYPES:
            raise ValueError("Unsupported document media type")
        return value

    @model_validator(mode="after")
    def snapshot_provenance(self) -> Self:
        if self.path is not None:
            if not self.path or self.retrieved_at is None:
                raise ValueError("Snapshots require a path and retrieval timestamp")
        elif self.retrieved_at is not None:
            raise ValueError("Remote retrieval time is recorded by the collector")
        if self.retrieved_at is not None and self.retrieved_at.utcoffset() is None:
            raise ValueError("Retrieval timestamp must be timezone-aware")
        return self


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    schema_version: Literal[1]
    documents: list[ManifestEntry] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_revisions(self) -> Self:
        ids = [item.revision_id for item in self.documents]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate revision in manifest")
        return self


@dataclass(frozen=True)
class CollectedSource:
    source_id: UUID
    revision_id: UUID
    canonical_url: str
    title: str
    owner_office: str
    subject_area: str
    campus: str
    program: str | None
    course: str | None
    academic_term: str | None
    effective_from: datetime | None
    effective_until: datetime | None
    sha256: str
    media_type: str
    retrieved_at: datetime
    fetched_url: str | None
    snapshot_path: str | None
    content: bytes | None
    review_required: bool


def _read_file(path: Path, max_bytes: int) -> bytes:
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            raise SourceCollectionError("Snapshot must be a regular file")
        with path.open("rb") as stream:
            content = stream.read(max_bytes + 1)
    except OSError:
        raise SourceCollectionError("Cannot read manifest or snapshot file") from None
    if not content or len(content) > max_bytes:
        raise SourceCollectionError("File is empty or exceeds the byte limit")
    return content


def load_manifest(path: Path) -> Manifest:
    try:
        return Manifest.model_validate_json(_read_file(path, 1024 * 1024))
    except ValidationError:
        raise SourceCollectionError(
            "Invalid source manifest; check schema and provenance"
        ) from None


def _governed(
    session: Session,
    entry: ManifestEntry,
    *,
    lock: bool = False,
) -> tuple[ApprovedSource, SourceRevision]:
    source_query = select(ApprovedSource).where(ApprovedSource.id == entry.source_id)
    revision_query = select(SourceRevision).where(SourceRevision.id == entry.revision_id)
    if lock:
        # Same source-before-revision ordering as the governance service.
        source_query = source_query.with_for_update()
        revision_query = revision_query.with_for_update()
    source = session.scalar(source_query.execution_options(populate_existing=True))
    revision = session.scalar(revision_query.execution_options(populate_existing=True))
    if (
        source is None
        or revision is None
        or revision.source_id != source.id
        or source.canonical_url != entry.canonical_url
        or source.owner_office != entry.owner_office
        or source.approval_status != "approved"
        or source.lifecycle_status != "active"
        or revision.status not in {"approved", "active"}
        or revision.content_hash != entry.sha256
    ):
        raise SourceCollectionError("Manifest requires a matching approved source and revision")
    if (
        session.scalar(
            select(SourceRevision.id)
            .where(
                SourceRevision.source_id == source.id,
                SourceRevision.conflict_group.is_not(None),
            )
            .limit(1)
        )
        is not None
    ):
        raise SourceCollectionError("Source has an unresolved conflict")
    # Office authorization is enforced when the governance service writes these
    # events. Manifest-supplied office names or approval flags cannot substitute.
    for revision_id in (None, revision.id):
        if (
            session.scalar(
                select(SourceReviewEvent.id)
                .where(
                    SourceReviewEvent.source_id == source.id,
                    SourceReviewEvent.revision_id == revision_id,
                    SourceReviewEvent.action == "approve",
                )
                .limit(1)
            )
            is None
        ):
            raise SourceCollectionError("Owning-office approval audit is required")
    return source, revision


def _fetch(
    client: httpx.Client,
    entry: ManifestEntry,
    *,
    max_bytes: int,
    timeout_seconds: float,
    max_redirects: int,
) -> tuple[bytes, str]:
    deadline = time.monotonic() + timeout_seconds
    url = entry.canonical_url
    visited: set[str] = set()
    try:
        for _ in range(max_redirects + 1):
            validate_official_url(url)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SourceCollectionError("Source download exceeded the time limit")
            if url in visited:
                raise SourceCollectionError("Source redirect loop")
            visited.add(url)
            # No implicit redirects, proxies, authentication or compressed bodies.
            with client.stream(
                "GET", url, timeout=remaining, headers={"Accept-Encoding": "identity"}
            ) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise SourceCollectionError("Redirect has no location")
                    # Validate raw absolute URLs too; urljoin can strip controls.
                    if any(c.isspace() or ord(c) < 32 for c in location) or "\\" in location:
                        raise SourceCollectionError("Invalid redirect location")
                    url = validate_official_url(urljoin(url, location))
                    continue
                if response.status_code != 200:
                    raise SourceCollectionError("Source download requires HTTP 200")
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise SourceCollectionError("Compressed transport is not accepted")
                media_type = (
                    response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                )
                if media_type != entry.media_type:
                    raise SourceCollectionError("Source media type does not match the manifest")
                length = response.headers.get("content-length")
                if length is not None and (not length.isdecimal() or int(length) > max_bytes):
                    raise SourceCollectionError("Source exceeds the byte limit")
                content = bytearray()
                for block in response.iter_bytes():
                    if time.monotonic() >= deadline:
                        raise SourceCollectionError("Source download exceeded the time limit")
                    if len(content) + len(block) > max_bytes:
                        raise SourceCollectionError("Source exceeds the byte limit")
                    content.extend(block)
                if time.monotonic() >= deadline:
                    raise SourceCollectionError("Source download exceeded the time limit")
                if not content:
                    raise SourceCollectionError("Source document is empty")
                return bytes(content), str(response.url)
    except httpx.HTTPError:
        raise SourceCollectionError("Source download failed or timed out") from None
    raise SourceCollectionError("Source exceeded the redirect limit")


def collect_manifest(
    session: Session,
    manifest_path: Path,
    *,
    max_bytes: int = 10 * 1024 * 1024,
    timeout_seconds: float = 10.0,
    max_redirects: int = 3,
    transport: httpx.BaseTransport | None = None,
) -> list[CollectedSource]:
    """Return verified bytes or pending-review identities, without committing.

    All entries must be governed before any I/O. Downloads have a byte cap,
    per-operation timeout and an elapsed-time deadline checked at every chunk
    and redirect. A blocked read can last at most one additional operation timeout.
    Local snapshots must reside inside the manifest directory. Future extraction
    establishes readability; collection alone cannot make evidence retrievable.
    """
    if (
        isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
        or max_bytes <= 0
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
        or isinstance(max_redirects, bool)
        or not isinstance(max_redirects, int)
        or max_redirects < 0
    ):
        raise SourceCollectionError("Collection limits must be finite and positive")
    manifest_path = Path(manifest_path).resolve()
    manifest = load_manifest(manifest_path)
    for entry in manifest.documents:
        _governed(session, entry)
    results = []
    with httpx.Client(transport=transport, follow_redirects=False, trust_env=False) as client:
        for entry in manifest.documents:
            fetched_url = None
            snapshot_path = None
            if entry.path is not None:
                relative = Path(entry.path)
                path = (manifest_path.parent / relative).resolve()
                if relative.is_absolute() or not path.is_relative_to(manifest_path.parent):
                    raise SourceCollectionError("Snapshot path must stay inside manifest directory")
                content = _read_file(path, max_bytes)
                assert entry.retrieved_at is not None  # validated by ManifestEntry
                retrieved_at = entry.retrieved_at.astimezone(UTC)
                snapshot_path = entry.path
            else:
                content, fetched_url = _fetch(
                    client,
                    entry,
                    max_bytes=max_bytes,
                    timeout_seconds=timeout_seconds,
                    max_redirects=max_redirects,
                )
                retrieved_at = utc_now()
            source, revision = _governed(session, entry, lock=True)
            digest = hashlib.sha256(content).hexdigest()
            changed = digest != revision.content_hash
            if changed:
                existing = session.scalar(
                    select(SourceRevision).where(
                        SourceRevision.source_id == source.id,
                        SourceRevision.content_hash == digest,
                    )
                )
                if existing is None:
                    existing = SourceRevision(
                        source_id=source.id,
                        content_hash=digest,
                        retrieved_at=retrieved_at,
                        campus=revision.campus,
                        program=revision.program,
                        course=revision.course,
                        academic_term=revision.academic_term,
                        effective_from=revision.effective_from,
                        effective_until=revision.effective_until,
                    )
                    session.add(existing)
                    session.flush()
                revision = existing
            results.append(
                CollectedSource(
                    source_id=source.id,
                    revision_id=revision.id,
                    canonical_url=source.canonical_url,
                    title=source.title,
                    owner_office=source.owner_office,
                    subject_area=source.subject_area,
                    campus=revision.campus,
                    program=revision.program,
                    course=revision.course,
                    academic_term=revision.academic_term,
                    effective_from=revision.effective_from,
                    effective_until=revision.effective_until,
                    sha256=digest,
                    media_type=entry.media_type,
                    retrieved_at=retrieved_at,
                    fetched_url=fetched_url,
                    snapshot_path=snapshot_path,
                    content=None if changed else content,
                    review_required=changed,
                )
            )
    return results
