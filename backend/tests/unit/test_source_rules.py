"""T011 foundation requirements; service interfaces are specified in foundation-tests.md."""

from importlib import import_module

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base, utc_now
from app.models.source import ApprovedSource, SourceRevision


@pytest.fixture
def source_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[ApprovedSource.__table__, SourceRevision.__table__])
    with Session(engine) as session:
        yield session
    engine.dispose()


def source():
    return ApprovedSource(
        canonical_url="https://www.pnw.edu/registrar/",
        title="Registrar",
        owner_office="Registrar",
        subject_area="Registration",
    )


def test_duplicate_revision_hash_rejected(source_session):
    parent = source()
    source_session.add(parent)
    source_session.flush()
    for _ in range(2):
        source_session.add(
            SourceRevision(source_id=parent.id, content_hash="a" * 64, retrieved_at=utc_now())
        )
    with pytest.raises(IntegrityError):
        source_session.flush()


def test_effective_window_constraint(source_session):
    parent = source()
    now = utc_now()
    source_session.add(
        SourceRevision(
            source=parent,
            content_hash="a" * 64,
            retrieved_at=now,
            effective_from=now,
            effective_until=now,
        )
    )
    with pytest.raises(IntegrityError):
        source_session.flush()


@pytest.mark.parametrize("terminal", ["superseded", "retired"])
def test_terminal_revision_cannot_reactivate(source_session, terminal):
    revision = SourceRevision(source=source(), content_hash="a" * 64, retrieved_at=utc_now())
    source_session.add(revision)
    source_session.flush()
    revision.status = "approved"
    revision.status = "active"
    revision.status = terminal
    source_session.commit()
    with pytest.raises(ValueError):
        revision.status = "active"


@pytest.mark.parametrize(
    "office,action,allowed",
    [
        ("Registrar", "approve", True),
        ("Registrar", "retire", True),
        ("Housing", "approve", False),
        ("Housing", "retire", False),
        ("Registrar", "resolve_conflict", False),
        ("Dean of Students", "resolve_conflict", True),
        ("Housing", "resolve_conflict", False),
        ("Dean of Students", "approve", False),
        ("", "approve", False),
    ],
)
def test_owning_office_and_dean_permissions(office, action, allowed):
    governance = import_module("app.services.source_governance")
    assert (
        governance.can_review(owner_office="Registrar", reviewer_office=office, action=action)
        is allowed
    )


@pytest.mark.parametrize(
    "case_id",
    [
        "active-hammond",
        "active-westville",
        "active-calendar",
        "draft",
        "rejected",
        "superseded",
        "retired",
        "pending-revision",
        "approved-not-active",
        "expired",
        "future",
        "program-course-term",
        "conflict-a",
        "conflict-b",
        "unreadable",
    ],
)
def test_corpus_eligibility(corpus, corpus_clock, case_id):
    eligibility = import_module("app.services.source_eligibility")
    case = next(case for case in corpus.cases if case.id == case_id)
    assert (
        eligibility.is_eligible(
            approval=case.approval,
            lifecycle=case.lifecycle,
            revision_status=case.revision_status,
            readable=case.readable,
            conflicting=bool(case.conflict_group),
            effective_from=case.effective_from,
            effective_until=case.effective_until,
            now=corpus_clock,
            scope={
                "campus": case.campus,
                "program": case.program,
                "course": case.course,
                "academicTerm": case.academic_term,
            },
            context={
                "campus": case.campus,
                "program": case.program,
                "course": case.course,
                "academicTerm": case.academic_term,
            },
        )
        is case.expected_eligible
    )


@pytest.mark.parametrize(
    "field,wrong",
    [
        ("campus", "hammond"),
        ("program", "MBA"),
        ("course", "CS 10000"),
        ("academicTerm", "Fall 2025"),
    ],
)
def test_material_scope_mismatch_excludes_evidence(corpus_clock, field, wrong):
    eligibility = import_module("app.services.source_eligibility")
    scope = dict(
        campus="westville",
        program="MS Computer Science",
        course="CS 50000",
        academicTerm="Fall 2026",
    )
    context = dict(scope, **{field: wrong})
    assert not eligibility.is_eligible(
        approval="approved",
        lifecycle="active",
        revision_status="active",
        readable=True,
        conflicting=False,
        effective_from=None,
        effective_until=None,
        now=corpus_clock,
        scope=scope,
        context=context,
    )
