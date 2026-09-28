"""Authorized, atomic source changes. Call within session_scope; no internal commits."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import Reviewer
from app.models.review_event import SourceReviewEvent
from app.models.source import ApprovedSource, SourceRevision


class GovernanceError(ValueError):
    pass


class ReviewForbidden(PermissionError):
    pass


def can_review(*, owner_office: str, reviewer_office: str, action: str) -> bool:
    if action == "resolve_conflict":
        return reviewer_office == "Dean of Students"
    return (
        action in {"approve", "activate", "supersede", "retire"}
        and bool(owner_office)
        and reviewer_office == owner_office
    )


def create_draft(
    session: Session,
    *,
    reviewer: Reviewer,
    canonical_url: str,
    title: str,
    owner_office: str,
    subject_area: str,
) -> ApprovedSource:
    if not reviewer.subject or owner_office not in reviewer.offices:
        raise ReviewForbidden("Owning-office role required")
    source = ApprovedSource(
        canonical_url=canonical_url,
        title=title,
        owner_office=owner_office,
        subject_area=subject_area,
        approval_status="draft",
        lifecycle_status="active",
    )
    session.add(source)
    session.flush()
    return source


def review_source(
    session: Session,
    *,
    reviewer: Reviewer,
    source_id: UUID,
    action: str,
    reason: str,
    revision_id: UUID | None = None,
) -> SourceReviewEvent:
    """Resolve a conflict by retiring the explicitly selected losing revision.

    Dean resolution never grants owner approval or reactivates terminal evidence.
    Caller owns transaction commit/rollback; a savepoint keeps each action atomic.
    """
    if not reason.strip():
        raise GovernanceError("A non-empty review reason is required")
    with session.begin_nested():
        source = session.scalar(
            select(ApprovedSource)
            .where(ApprovedSource.id == source_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if source is None:
            raise GovernanceError("Source not found")
        if not reviewer.subject or not any(
            can_review(owner_office=source.owner_office, reviewer_office=office, action=action)
            for office in reviewer.offices
        ):
            raise ReviewForbidden("Review action not permitted")
        if source.lifecycle_status != "active":
            raise GovernanceError("Terminal source cannot change state")
        revision = None
        if revision_id is not None:
            revision = session.scalar(
                select(SourceRevision)
                .where(SourceRevision.id == revision_id, SourceRevision.source_id == source_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if revision is None:
                raise GovernanceError("Revision does not belong to source")
        if revision is not None and revision.conflict_group is not None:
            raise GovernanceError("Resolve the entire conflict group through Dean review")
        if action == "approve":
            if source.approval_status == "rejected":
                raise GovernanceError("Rejected sources cannot be approved")
            if revision is None:
                if source.approval_status != "draft":
                    raise GovernanceError("Source is already approved")
                source.approval_status = "approved"
            else:
                if revision.status != "pending_review" or source.approval_status != "approved":
                    raise GovernanceError("Approve source before its pending revision")
                revision.status = "approved"
        elif action == "activate":
            if (
                revision is None
                or revision.status != "approved"
                or source.approval_status != "approved"
            ):
                raise GovernanceError("Activation requires approved source and revision")
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
                raise GovernanceError("Resolve outstanding source conflicts before activation")
            previous = session.scalars(
                select(SourceRevision)
                .where(SourceRevision.source_id == source_id, SourceRevision.status == "active")
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
            for old in previous:
                old.status = "superseded"
                session.add(
                    SourceReviewEvent(
                        source_id=source_id,
                        revision_id=old.id,
                        reviewer_subject=reviewer.subject,
                        action="supersede",
                        reason=reason.strip(),
                    )
                )
            revision.status = "active"
        elif action in {"retire", "supersede", "resolve_conflict"}:
            target_status = "superseded" if action == "supersede" else "retired"
            if revision is not None:
                if revision.status != "active":
                    raise GovernanceError("Only active revisions can be retired or superseded")
                revision.status = target_status
            elif action == "resolve_conflict":
                raise GovernanceError("Select the conflicting revision to retire")
            else:
                source.lifecycle_status = target_status
        else:
            raise GovernanceError("Unsupported review action")
        audit = SourceReviewEvent(
            source_id=source_id,
            revision_id=revision_id,
            reviewer_subject=reviewer.subject,
            action=action,
            reason=reason.strip(),
        )
        session.add(audit)
        session.flush()
    return audit


def resolve_conflict_group(
    session: Session,
    *,
    reviewer: Reviewer,
    group_id: UUID,
    retained_revision_id: UUID | None,
    reason: str,
) -> None:
    """Dean selects one retained revision, or retires all; all changes are audited."""
    if "Dean of Students" not in reviewer.offices or not reviewer.subject:
        raise ReviewForbidden("Dean of Students role required")
    if not reason.strip():
        raise GovernanceError("A non-empty resolution reason is required")
    with session.begin_nested():
        source_ids = session.scalars(
            select(SourceRevision.source_id).where(SourceRevision.conflict_group == group_id)
        ).all()
        sources = session.scalars(
            select(ApprovedSource)
            .where(ApprovedSource.id.in_(source_ids))
            .order_by(ApprovedSource.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        revisions = session.scalars(
            select(SourceRevision)
            .where(SourceRevision.conflict_group == group_id)
            .order_by(SourceRevision.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        if (
            not revisions
            or retained_revision_id is not None
            and retained_revision_id not in {row.id for row in revisions}
        ):
            raise GovernanceError("Unknown conflict group or retained revision")
        owners = {source.id: source for source in sources}
        for revision in revisions:
            if revision.id == retained_revision_id:
                source = owners[revision.source_id]
                if (
                    revision.status != "unresolved"
                    or not revision.readable
                    or source.approval_status != "approved"
                    or source.lifecycle_status != "active"
                ):
                    raise GovernanceError(
                        "Retained evidence must remain approved, readable and active"
                    )
                revision.status = "active"
            elif revision.status == "unresolved":
                revision.status = "retired"
            revision.conflict_group = None
            session.add(
                SourceReviewEvent(
                    source_id=revision.source_id,
                    revision_id=revision.id,
                    reviewer_subject=reviewer.subject,
                    action="resolve_conflict",
                    reason=reason.strip(),
                )
            )
        session.flush()
