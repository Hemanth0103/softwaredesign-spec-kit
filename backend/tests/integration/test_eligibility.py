import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.auth import Reviewer
from app.db.base import Base, utc_now
from app.models.review_event import SourceReviewEvent
from app.models.source import ApprovedSource, SourceRevision
from app.services.source_eligibility import eligible_revisions, mark_conflicting, recheck_revisions
from app.services.source_governance import (
    GovernanceError,
    ReviewForbidden,
    resolve_conflict_group,
    review_source,
)


@pytest.fixture
def database():
    url = os.environ.get("T006_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated PostgreSQL database required")
    engine = create_engine(url)
    schema = "t014_" + uuid4().hex
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = engine.execution_options(schema_translate_map={None: schema})
    try:
        Base.metadata.create_all(
            engine,
            tables=[
                ApprovedSource.__table__,
                SourceRevision.__table__,
                SourceReviewEvent.__table__,
            ],
        )
        yield sessionmaker(engine, expire_on_commit=False)
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def seed(factory):
    with factory.begin() as session:
        ids = []
        for index in range(2):
            source = ApprovedSource(
                canonical_url=f"https://www.pnw.edu/fixture-{index}",
                title="Test",
                owner_office="Registrar",
                subject_area="Test",
                approval_status="approved",
            )
            revision = SourceRevision(
                source=source, content_hash=str(index) * 64, retrieved_at=utc_now(), readable=True
            )
            session.add(revision)
            session.flush()
            revision.status = "approved"
            revision.status = "active"
            ids.append(revision.id)
        return ids


def test_retirement_recheck_ignores_stale_identity_map(database):
    ids = seed(database)
    with database() as reader:
        stale = reader.get(SourceRevision, ids[0])
        source = reader.get(ApprovedSource, stale.source_id)
        assert recheck_revisions(database, ids, context={})
        with database.begin() as writer:
            review_source(
                writer,
                reviewer=Reviewer("owner", frozenset({"Registrar"})),
                source_id=source.id,
                action="retire",
                reason="Retired",
            )
        assert source.lifecycle_status == "active"
        assert not recheck_revisions(database, ids, context={})
        assert not recheck_revisions(database, [], context={})


def test_conflict_is_persistent_until_dean_resolution(database):
    ids = seed(database)
    with database.begin() as session:
        group = mark_conflicting(session, ids)
    assert not recheck_revisions(database, ids, context={})
    with database() as session:
        assert list(session.scalars(eligible_revisions(context={}))) == []
    with database.begin() as session:
        with pytest.raises(ReviewForbidden):
            resolve_conflict_group(
                session,
                reviewer=Reviewer("owner", frozenset({"Registrar"})),
                group_id=group,
                retained_revision_id=ids[0],
                reason="Keep one",
            )
        resolve_conflict_group(
            session,
            reviewer=Reviewer("dean", frozenset({"Dean of Students"})),
            group_id=group,
            retained_revision_id=ids[0],
            reason="Verified evidence",
        )
    assert recheck_revisions(database, [ids[0]], context={})
    assert not recheck_revisions(database, [ids[1]], context={})
    with database() as session:
        events = list(session.scalars(select(SourceReviewEvent)))
        assert len(events) == 2 and all(event.action == "resolve_conflict" for event in events)


def test_cannot_restore_retired_source_during_conflict_resolution(database):
    ids = seed(database)
    with database.begin() as session:
        group = mark_conflicting(session, ids)
        revision = session.get(SourceRevision, ids[0])
        session.get(ApprovedSource, revision.source_id).lifecycle_status = "retired"
    with database.begin() as session:
        with pytest.raises(GovernanceError):
            resolve_conflict_group(
                session,
                reviewer=Reviewer("dean", frozenset({"Dean of Students"})),
                group_id=group,
                retained_revision_id=ids[0],
                reason="Keep",
            )
    assert not recheck_revisions(database, ids, context={})


def test_sql_scopes_dates_and_readability(database):
    ids = seed(database)
    now = datetime(2026, 9, 27, tzinfo=UTC)
    with database.begin() as session:
        revision = session.get(SourceRevision, ids[0])
        revision.campus = "westville"
        revision.program = "MS CS"
        revision.course = "CS 50000"
        revision.academic_term = "Fall 2026"
        revision.effective_from = now
        revision.effective_until = now + timedelta(days=1)
    context = dict(campus="westville", program="MS CS", course="CS 50000", academicTerm="Fall 2026")
    assert recheck_revisions(database, [ids[0]], context=context, now=now)
    assert not recheck_revisions(database, [ids[0]], context={}, now=now)
    assert not recheck_revisions(database, [ids[0]], context=context, now=now + timedelta(days=1))
    with database.begin() as session:
        session.get(SourceRevision, ids[0]).readable = False
    assert not recheck_revisions(database, [ids[0]], context=context, now=now)
