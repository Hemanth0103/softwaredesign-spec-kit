import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session

from app.auth import Reviewer
from app.db.base import Base, utc_now
from app.models.review_event import SourceReviewEvent
from app.models.source import ApprovedSource, SourceRevision
from app.services.source_governance import (
    GovernanceError,
    ReviewForbidden,
    create_draft,
    review_source,
)

OWNER = Reviewer("owner-reviewer", frozenset({"Registrar"}))
DEAN = Reviewer("dean-reviewer", frozenset({"Dean of Students"}))


@pytest.fixture(params=["sqlite", "postgresql"])
def session(request):
    schema = None
    if request.param == "postgresql":
        url = os.environ.get("T006_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Dedicated PostgreSQL database required")
        engine = create_engine(url)
        schema = "t013_" + uuid4().hex
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = engine.execution_options(schema_translate_map={None: schema})
    else:
        engine = create_engine("sqlite://")
    try:
        Base.metadata.create_all(
            engine,
            tables=[
                ApprovedSource.__table__,
                SourceRevision.__table__,
                SourceReviewEvent.__table__,
            ],
        )
        with Session(engine) as session:
            yield session
    finally:
        if schema:
            with engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def draft(session):
    return create_draft(
        session,
        reviewer=OWNER,
        canonical_url="https://www.pnw.edu/registrar/",
        title="Registrar",
        owner_office="Registrar",
        subject_area="Registration",
    )


def apply(session, source, action, **kwargs):
    return review_source(
        session,
        reviewer=kwargs.pop("reviewer", OWNER),
        source_id=source.id,
        action=action,
        reason=kwargs.pop("reason", "Reviewed evidence"),
        **kwargs,
    )


def test_draft_approval_activation_supersession_and_retirement(session):
    source = draft(session)
    assert source.approval_status == "draft"
    assert list(session.scalars(select(SourceReviewEvent))) == []
    apply(session, source, "approve")
    revisions = []
    for digest in ["a", "b"]:
        revision = SourceRevision(
            source_id=source.id, content_hash=digest * 64, retrieved_at=utc_now()
        )
        session.add(revision)
        session.flush()
        apply(session, source, "approve", revision_id=revision.id)
        apply(session, source, "activate", revision_id=revision.id)
        revisions.append(revision)
    assert revisions[0].status == "superseded"
    assert revisions[1].status == "active"
    apply(session, source, "retire")
    assert source.lifecycle_status == "retired"
    events = list(session.scalars(select(SourceReviewEvent)))
    assert len(events) == 7
    assert all(row.reviewer_subject == OWNER.subject and row.reason for row in events)
    with pytest.raises(GovernanceError):
        apply(session, source, "approve")


def test_unauthorized_and_empty_reason_leave_no_audit(session):
    source = draft(session)
    for reviewer, reason, error in [
        (Reviewer("other", frozenset({"Housing"})), "Review", ReviewForbidden),
        (OWNER, " ", GovernanceError),
    ]:
        with pytest.raises(error):
            apply(session, source, "approve", reviewer=reviewer, reason=reason)
    assert source.approval_status == "draft"
    assert list(session.scalars(select(SourceReviewEvent))) == []


def test_dean_resolution_retires_selected_evidence(session):
    source = draft(session)
    apply(session, source, "approve")
    revision = SourceRevision(source_id=source.id, content_hash="c" * 64, retrieved_at=utc_now())
    session.add(revision)
    session.flush()
    apply(session, source, "approve", revision_id=revision.id)
    apply(session, source, "activate", revision_id=revision.id)
    with pytest.raises(ReviewForbidden):
        apply(session, source, "resolve_conflict", revision_id=revision.id)
    audit = apply(session, source, "resolve_conflict", revision_id=revision.id, reviewer=DEAN)
    assert revision.status == "retired"
    assert audit.action == "resolve_conflict"
    assert audit.reviewer_subject == DEAN.subject


def test_audit_failure_rolls_back_transition(session):
    source = draft(session)
    session.commit()

    def fail(*args):
        raise RuntimeError("test audit failure")

    event.listen(SourceReviewEvent, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError):
            apply(session, source, "approve")
    finally:
        event.remove(SourceReviewEvent, "before_insert", fail)
    session.refresh(source)
    assert source.approval_status == "draft"
    assert list(session.scalars(select(SourceReviewEvent))) == []
