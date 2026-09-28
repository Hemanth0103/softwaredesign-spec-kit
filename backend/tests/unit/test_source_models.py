"""Source model invariants and revision lifecycle."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base, utc_now
from app.models.source import ApprovedSource, SourceRevision


@pytest.fixture(params=["sqlite", "postgresql"])
def session(request):
    schema = None
    if request.param == "postgresql":
        url = os.environ.get("T006_TEST_DATABASE_URL")
        if not url:
            pytest.skip("PostgreSQL validation database not configured")
        engine = create_engine(url)
        schema = "t007_" + uuid4().hex
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = engine.execution_options(schema_translate_map={None: schema})
    else:
        engine = create_engine("sqlite://")
    tables = [ApprovedSource.__table__, SourceRevision.__table__]
    try:
        Base.metadata.create_all(engine, tables=tables)
        with Session(engine) as session:
            yield session
    finally:
        if schema:
            with engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def source():
    return ApprovedSource(
        canonical_url="https://www.pnw.edu/policy",
        title="Policy",
        owner_office="Registrar",
        subject_area="Registration",
    )


def revision(parent):
    return SourceRevision(
        source=parent, content_hash="a" * 64, retrieved_at=utc_now(), campus="all"
    )


def test_defaults_relationship_and_unique_url(session):
    parent = source()
    child = revision(parent)
    session.add(parent)
    session.commit()
    assert parent.approval_status == "draft"
    assert parent.lifecycle_status == "active"
    assert child.status == "pending_review"
    assert child.source_id == parent.id
    assert parent.revisions == [child]
    session.add(source())
    with pytest.raises(IntegrityError):
        session.flush()


def test_revision_transitions_and_immutable_hash(session):
    child = revision(source())
    session.add(child)
    session.flush()
    with pytest.raises(ValueError):
        child.status = "active"
    child.status = "approved"
    session.flush()
    child.status = "active"
    session.flush()
    child.status = "superseded"
    session.flush()
    with pytest.raises(ValueError):
        child.status = "active"
    with pytest.raises(ValueError):
        child.content_hash = "b" * 64


@pytest.mark.parametrize(
    "field,value",
    [
        ("canonical_url", "http://www.pnw.edu/policy"),
        ("title", " "),
        ("approval_status", "unknown"),
        ("lifecycle_status", "deleted"),
    ],
)
def test_invalid_source(field, value):
    with pytest.raises(ValueError):
        setattr(source(), field, value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("content_hash", "bad"),
        ("campus", "other"),
        ("status", "active"),
        ("retrieved_at", utc_now().replace(tzinfo=None)),
    ],
)
def test_invalid_revision(field, value):
    with pytest.raises(ValueError):
        setattr(revision(source()), field, value)


def test_retirement_and_loaded_hash(session):
    child = revision(source())
    session.add(child)
    session.commit()
    child.status = "approved"
    child.status = "active"
    child.status = "retired"
    session.commit()
    with pytest.raises(ValueError):
        child.status = "approved"
    with pytest.raises(ValueError):
        child.content_hash = "b" * 64


@pytest.mark.parametrize(
    "values",
    [{"approval_status": "invalid"}, {"title": " "}, {"canonical_url": "http://example.edu"}],
)
def test_database_constraints(session, values):
    parent = source()
    session.add(parent)
    session.commit()
    with pytest.raises(IntegrityError):
        session.execute(ApprovedSource.__table__.update().values(**values))
        session.flush()
