"""Shared fail-closed eligibility: no cached decisions and no scheduler."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Select, or_, select, true
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import utc_now
from app.models.source import ApprovedSource, SourceRevision

Context = Mapping[str, str | None]
SCOPE_FIELDS = ("campus", "program", "course", "academicTerm")


def is_eligible(
    *,
    approval: str,
    lifecycle: str,
    revision_status: str,
    readable: bool,
    conflicting: bool,
    effective_from: datetime | None,
    effective_until: datetime | None,
    now: datetime,
    scope: Context,
    context: Context,
) -> bool:
    if (
        approval != "approved"
        or lifecycle != "active"
        or revision_status != "active"
        or not readable
        or conflicting
    ):
        return False
    if any(
        value is not None and value.utcoffset() is None
        for value in (now, effective_from, effective_until)
    ):
        return False
    if effective_from and effective_until and effective_until <= effective_from:
        return False
    if effective_from and now < effective_from or effective_until and now >= effective_until:
        return False
    for field in SCOPE_FIELDS:
        value = scope.get(field)
        if value is None or field == "campus" and value == "all":
            continue
        if not value or context.get(field) != value:
            return False
    return scope.get("campus") in {"all", "hammond", "westville"}


def eligible_revisions(
    *, context: Context, now: datetime | None = None, include_conflicts: bool = False
) -> Select[tuple[SourceRevision]]:
    """Filter before ranking. Conflict diagnostics are NEVER answer evidence.

    include_conflicts also selects unresolved/grouped rows for retrieval diagnostics.
    The default and all publication rechecks continue to exclude them.
    """
    instant = now or utc_now()
    if instant.utcoffset() is None:
        raise ValueError("Eligibility clock must be timezone-aware")
    statement = (
        select(SourceRevision)
        .join(ApprovedSource)
        .where(
            ApprovedSource.approval_status == "approved",
            ApprovedSource.lifecycle_status == "active",
            SourceRevision.status.in_(("active", "unresolved"))
            if include_conflicts
            else SourceRevision.status == "active",
            SourceRevision.readable.is_(True),
            true() if include_conflicts else SourceRevision.conflict_group.is_(None),
            or_(SourceRevision.effective_from.is_(None), SourceRevision.effective_from <= instant),
            or_(SourceRevision.effective_until.is_(None), SourceRevision.effective_until > instant),
            or_(SourceRevision.campus == "all", SourceRevision.campus == context.get("campus")),
        )
    )
    for field, column in [
        ("program", SourceRevision.program),
        ("course", SourceRevision.course),
        ("academicTerm", SourceRevision.academic_term),
    ]:
        statement = statement.where(or_(column.is_(None), column == context.get(field)))
    return statement


def recheck_revisions(
    factory: sessionmaker[Session],
    revision_ids: Sequence[UUID],
    *,
    context: Context,
    now: datetime | None = None,
) -> bool:
    """Fresh transaction for the final publication/answer gate; never trust identity-map state.

    Call immediately before publication/return; a subsequent concurrent commit is
    outside this read's boundary. No helper can make an HTTP send atomic with a DB commit.
    """
    expected = set(revision_ids)
    if not expected:
        return False
    with factory() as session:
        found = session.scalars(
            eligible_revisions(context=context, now=now).where(SourceRevision.id.in_(expected))
        ).all()
        return {revision.id for revision in found} == expected


def mark_conflicting(session: Session, revision_ids: Sequence[UUID]) -> UUID:
    """Persist a conflict identified by ingestion/retrieval; does not infer semantic conflicts.

    This is an internal trusted-service operation, not a public endpoint. All
    participants are excluded until a Dean resolves the whole group.
    """
    identifiers = sorted(set(revision_ids), key=str)
    if len(identifiers) < 2:
        raise ValueError("A conflict requires at least two revisions")
    with session.begin_nested():
        source_ids = session.scalars(
            select(SourceRevision.source_id).where(SourceRevision.id.in_(identifiers))
        ).all()
        session.scalars(
            select(ApprovedSource)
            .where(ApprovedSource.id.in_(source_ids))
            .order_by(ApprovedSource.id)
            .with_for_update()
        ).all()
        revisions = session.scalars(
            select(SourceRevision)
            .where(SourceRevision.id.in_(identifiers))
            .order_by(SourceRevision.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        if len(revisions) != len(identifiers) or any(
            row.status != "active" or row.conflict_group is not None for row in revisions
        ):
            raise ValueError("Only active, ungrouped revisions can enter a conflict")
        group = uuid4()
        for revision in revisions:
            revision.status = "unresolved"
            revision.conflict_group = group
        session.flush()
    return group
