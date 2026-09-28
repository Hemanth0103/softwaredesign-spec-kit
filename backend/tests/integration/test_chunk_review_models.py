"""PostgreSQL-native chunk and audit persistence rules."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from app.db.base import Base, utc_now
from app.models.review_event import SourceReviewEvent
from app.models.source import ApprovedSource, SourceRevision
from app.models.source_chunk import SourceChunk


@pytest.fixture
def session():
    url = os.environ.get("T006_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated PostgreSQL database required")
    engine = create_engine(url)
    schema = "t008_" + uuid4().hex
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = engine.execution_options(schema_translate_map={None: schema})
    try:
        Base.metadata.create_all(
            engine,
            tables=[
                ApprovedSource.__table__,
                SourceRevision.__table__,
                SourceChunk.__table__,
                SourceReviewEvent.__table__,
            ],
        )
        with Session(engine) as session:
            yield session
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def records(session):
    source = ApprovedSource(
        canonical_url="https://www.pnw.edu/policy",
        title="Policy",
        owner_office="Registrar",
        subject_area="Registration",
    )
    revision = SourceRevision(source=source, content_hash="a" * 64, retrieved_at=utc_now())
    session.add(revision)
    session.flush()
    chunk = SourceChunk(
        revision_id=revision.id,
        ordinal=0,
        text="Registration policy",
        citation_anchor="registration",
        heading="Registration",
        table_context="Deadline table",
        search_vector=func.to_tsvector("english", "Registration policy"),
        embedding=[1, 0, 0],
        embedding_model="test",
        embedding_version="v1",
        embedding_dimension=3,
    )
    audit = SourceReviewEvent(
        source_id=source.id,
        revision_id=revision.id,
        reviewer_subject="oidc-reviewer",
        action="approve",
        reason="Reviewed",
    )
    session.add_all([chunk, audit])
    session.commit()
    return chunk, audit


def test_roundtrip_and_vector_search(session):
    chunk, audit = records(session)
    assert list(chunk.embedding) == [1, 0, 0]
    assert session.scalar(select(SourceChunk.embedding.cosine_distance([1, 0, 0]))) == 0
    assert audit.timestamp.utcoffset().total_seconds() == 0
    assert audit.reason == "Reviewed"
    assert chunk.heading == "Registration"
    assert "registr" in chunk.search_vector
    assert chunk.citation_anchor == "registration"


@pytest.mark.parametrize("kind", ["chunk", "audit"])
@pytest.mark.parametrize("operation", ["update", "delete"])
def test_immutable_records(session, kind, operation):
    chunk, audit = records(session)
    record = chunk if kind == "chunk" else audit
    if operation == "delete":
        session.delete(record)
    elif kind == "chunk":
        chunk.text = "Changed"
    else:
        audit.reason = "Changed"
    with pytest.raises(ValueError):
        session.flush()


def test_vector_dimension_mismatch(session):
    chunk, _ = records(session)
    session.add(
        SourceChunk(
            revision_id=chunk.revision_id,
            ordinal=1,
            text="Policy",
            embedding=[1, 2],
            embedding_model="test",
            embedding_version="v1",
            embedding_dimension=3,
        )
    )
    with pytest.raises(ValueError):
        session.flush()


@pytest.mark.parametrize(
    "field,value", [("reason", " "), ("reviewer_subject", ""), ("action", "delete")]
)
def test_invalid_review(session, field, value):
    _, audit = records(session)
    values = dict(
        source_id=audit.source_id,
        revision_id=audit.revision_id,
        reviewer_subject="reviewer",
        action="approve",
        reason="Reviewed",
    )
    values[field] = value
    session.add(SourceReviewEvent(**values))
    with pytest.raises(ValueError):
        session.flush()


def test_review_revision_must_match_source(session):
    _, audit = records(session)
    other = ApprovedSource(
        canonical_url="https://www.pnw.edu/other",
        title="Other",
        owner_office="Registrar",
        subject_area="Registration",
    )
    session.add(other)
    session.flush()
    session.add(
        SourceReviewEvent(
            source_id=other.id,
            revision_id=audit.revision_id,
            reviewer_subject="reviewer",
            action="approve",
            reason="Review",
        )
    )
    with pytest.raises(ValueError):
        session.flush()
