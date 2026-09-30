"""T018 PostgreSQL contracts for T021/T025/T026; no SQLite or mocked persistence.

Future store.ingest_revision(factory, revision_id=..., content=..., media_type=...,
embed=..., model=..., version=..., dimension=..., chunk_size=..., chunk_overlap=...)
owns atomic publication; raises IngestionError on failed/ineligible imports.
refresh.register_revision(session, source_id=..., content=..., retrieved_at=...)
returns the existing or new pending revision, never granting approval.

Set T006_TEST_DATABASE_URL to a dedicated PostgreSQL database with pgvector.
Missing application modules deliberately fail, rather than skip/xfail.
"""

import hashlib
import os
from importlib import import_module
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.orm import sessionmaker

from app.auth import Reviewer
from app.db.base import Base, utc_now
from app.models.review_event import SourceReviewEvent
from app.models.source import ApprovedSource, SourceRevision
from app.models.source_chunk import SourceChunk
from app.services.source_eligibility import eligible_revisions
from app.services.source_governance import review_source

CONTENT = (
    "<main><h2 id='parking'>Parking</h2>"
    + "".join(
        f"<p>Parking section {i}: keep your permit visible in Hammond.</p>" for i in range(12)
    )
    + "</main>"
).encode()
OWNER = Reviewer("test-owner", frozenset({"Registrar"}))


@pytest.fixture
def database():
    url = os.environ.get("T006_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated PostgreSQL database with pgvector required")
    engine = create_engine(url)
    schema = "t018_" + uuid4().hex
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = engine.execution_options(schema_translate_map={None: schema})
    try:
        Base.metadata.create_all(
            scoped,
            tables=[
                ApprovedSource.__table__,
                SourceRevision.__table__,
                SourceChunk.__table__,
                SourceReviewEvent.__table__,
            ],
        )
        yield sessionmaker(scoped, expire_on_commit=False)
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


@pytest.fixture
def revision(database, corpus_clock):
    with database.begin() as session:
        source = ApprovedSource(
            canonical_url="https://www.pnw.edu/registrar/",
            title="Test parking policy",
            owner_office="Registrar",
            subject_area="Parking",
        )
        row = SourceRevision(
            source=source,
            content_hash=hashlib.sha256(CONTENT).hexdigest(),
            retrieved_at=corpus_clock,
            campus="hammond",
            program="MS CS",
            course="CS 50000",
            academic_term="Fall 2026",
            effective_from=corpus_clock,
            readable=True,
        )
        session.add(row)
        session.flush()
        review_source(
            session,
            reviewer=OWNER,
            source_id=source.id,
            action="approve",
            reason="Test-only source review",
        )
        review_source(
            session,
            reviewer=OWNER,
            source_id=source.id,
            action="approve",
            reason="Test-only fixture review",
            revision_id=row.id,
        )
        return row.id


def ingest(database, revision, **kwargs):
    module = import_module("app.ingestion.store")
    options = dict(
        content=CONTENT,
        media_type="text/html",
        embed=lambda texts: [[1.0, 0.5, 0.25] for _ in texts],
        model="test",
        version="v1",
        dimension=3,
        chunk_size=30,
        chunk_overlap=0,
    )
    options.update(kwargs)
    return module.ingest_revision(database, revision_id=revision, **options)


def chunks(database):
    with database() as session:
        return list(session.scalars(select(SourceChunk).order_by(SourceChunk.ordinal)))


def test_complete_pipeline_preserves_metadata_vectors_fulltext_and_indexes(database, revision):
    calls = []

    def embed(texts):
        calls.extend(texts)
        # Other transactions must see no partial publication while embedding runs.
        assert chunks(database) == []
        return [[1.0, 0.5, 0.25] for _ in texts]

    ingest(database, revision, embed=embed)
    rows = chunks(database)
    assert len(rows) > 1
    assert calls == [row.text for row in rows]
    assert [row.ordinal for row in rows] == list(range(len(rows)))
    assert all(row.revision_id == revision and row.citation_anchor == "parking" for row in rows)
    assert all("Parking" in row.heading for row in rows)
    assert all(list(row.embedding) == [1.0, 0.5, 0.25] for row in rows)
    assert all(
        (row.embedding_model, row.embedding_version, row.embedding_dimension) == ("test", "v1", 3)
        for row in rows
    )
    with database() as session:
        row = session.get(SourceRevision, revision)
        assert (row.campus, row.program, row.course, row.academic_term) == (
            "hammond",
            "MS CS",
            "CS 50000",
            "Fall 2026",
        )
        assert row.content_hash == hashlib.sha256(CONTENT).hexdigest()
        assert row.effective_from is not None and row.retrieved_at is not None
        assert row.source.canonical_url == "https://www.pnw.edu/registrar/"
        assert row.source.title == "Test parking policy" and row.source.owner_office == "Registrar"
        matches = list(
            session.scalars(
                select(SourceChunk.id).where(
                    SourceChunk.search_vector.op("@@")(
                        text("plainto_tsquery('english', 'parking')")
                    )
                )
            )
        )
        assert set(matches) == {c.id for c in rows}
        schema = session.get_bind().get_execution_options()["schema_translate_map"][None]
        indexes = {
            i["name"]: i
            for i in inspect(session.connection()).get_indexes(
                "source_chunk",
                schema=schema,
            )
        }
        assert indexes["ix_chunk_search_vector"]["dialect_options"]["postgresql_using"] == "gin"
        assert "ix_chunk_embedding_identity" in indexes


def test_repeat_import_keeps_chunk_ids_and_does_not_reembed(database, revision):
    ingest(database, revision)
    before = {row.id for row in chunks(database)}

    def unexpected(texts):
        pytest.fail("An unchanged import must reuse prepared embeddings")

    ingest(database, revision, embed=unexpected)
    assert before and {row.id for row in chunks(database)} == before


@pytest.mark.parametrize("failure", ["timeout", "dimension", "nonfinite", "missing", "hash"])
def test_failed_import_publishes_no_partial_chunks(database, revision, failure):
    module = import_module("app.ingestion.store")

    def embed(texts):
        if failure == "timeout":
            raise TimeoutError("Provider unavailable")
        if failure == "dimension":
            return [[1.0] for _ in texts]
        if failure == "nonfinite":
            return [[float("nan"), 0, 1] for _ in texts]
        return []

    with pytest.raises(module.IngestionError):
        ingest(
            database,
            revision,
            embed=embed,
            content=CONTENT + b"changed" if failure == "hash" else CONTENT,
        )
    assert chunks(database) == []
    with database() as session:
        assert session.get(SourceRevision, revision).status == "approved"


def test_database_write_failure_rolls_back_all_chunks(database, revision):
    module = import_module("app.ingestion.store")
    inserted = []

    def fail_second(mapper, connection, target):
        inserted.append(target.ordinal)
        if len(inserted) == 2:
            raise RuntimeError("Injected storage failure")

    event.listen(SourceChunk, "before_insert", fail_second)
    try:
        with pytest.raises(module.IngestionError):
            ingest(database, revision)
    finally:
        event.remove(SourceChunk, "before_insert", fail_second)
    assert len(inserted) == 2
    assert chunks(database) == []


@pytest.mark.parametrize("change", ["retired", "superseded", "rejected", "unresolved"])
def test_status_change_during_embedding_blocks_publication_and_retry(database, revision, change):
    module = import_module("app.ingestion.store")

    def embed(texts):
        # Separate connection commits after the import has read its initial state.
        with database.begin() as writer:
            writer.execute(text("SET LOCAL lock_timeout = '2s'"))
            row = writer.get(SourceRevision, revision)
            if change == "rejected":
                row.source.approval_status = "rejected"
            elif change == "unresolved":
                row.status = "active"
                row.status = "unresolved"
                row.conflict_group = uuid4()
            else:
                row.source.lifecycle_status = change
        return [[1.0, 0.5, 0.25] for _ in texts]

    with pytest.raises(module.IngestionError):
        ingest(database, revision, embed=embed)
    with database() as reader:
        row = reader.get(SourceRevision, revision)
        if change == "rejected":
            assert row.source.approval_status == "rejected"
        elif change == "unresolved":
            assert row.status == "unresolved" and row.conflict_group is not None
        else:
            assert row.source.lifecycle_status == change
    with pytest.raises(module.IngestionError):
        ingest(database, revision)
    assert chunks(database) == []


def test_changed_content_requires_review_and_preserves_history(database, revision):
    refresh = import_module("app.ingestion.refresh")
    store = import_module("app.ingestion.store")
    ingest(database, revision)
    old_ids = {c.id for c in chunks(database)}
    changed = CONTENT.replace(b"visible", b"clearly displayed")
    with database.begin() as session:
        old = session.get(SourceRevision, revision)
        if old.status == "approved":
            review_source(
                session,
                reviewer=OWNER,
                source_id=old.source_id,
                action="activate",
                reason="Publish prepared fixture",
                revision_id=old.id,
            )
        new = refresh.register_revision(
            session, source_id=old.source_id, content=changed, retrieved_at=utc_now()
        )
        session.flush()
        new_id, source_id = new.id, old.source_id
        assert new.id != old.id and new.status == "pending_review"
        assert new.content_hash == hashlib.sha256(changed).hexdigest()
        assert (
            refresh.register_revision(
                session, source_id=source_id, content=changed, retrieved_at=utc_now()
            ).id
            == new_id
        )
    with pytest.raises(store.IngestionError):
        ingest(database, new_id, content=changed)
    assert {c.id for c in chunks(database)} == old_ids
    with database.begin() as session:
        review_source(
            session,
            reviewer=OWNER,
            source_id=source_id,
            action="approve",
            reason="Reviewed changed content",
            revision_id=new_id,
        )
    ingest(database, new_id, content=changed)
    with database.begin() as session:
        new = session.get(SourceRevision, new_id)
        if new.status == "approved":
            review_source(
                session,
                reviewer=OWNER,
                source_id=source_id,
                action="activate",
                reason="Publish updated fixture",
                revision_id=new_id,
            )
    with database() as session:
        assert session.get(SourceRevision, revision).status == "superseded"
        assert (
            session.get(SourceRevision, revision).content_hash
            == hashlib.sha256(CONTENT).hexdigest()
        )
        assert session.get(SourceRevision, new_id).status == "active"
        eligible = list(
            session.scalars(
                eligible_revisions(
                    context={
                        "campus": "hammond",
                        "program": "MS CS",
                        "course": "CS 50000",
                        "academicTerm": "Fall 2026",
                    }
                )
            )
        )
        assert revision not in {r.id for r in eligible}
    assert old_ids < {c.id for c in chunks(database)}


def test_model_rebuild_retains_immutable_compatible_identities(database, revision):
    ingest(database, revision)
    old_ids = {c.id for c in chunks(database)}
    ingest(
        database,
        revision,
        model="new-model",
        version="v2",
        dimension=2,
        embed=lambda texts: [[0.5, 1.0] for _ in texts],
    )
    rows = chunks(database)
    assert old_ids < {c.id for c in rows}
    new = [c for c in rows if c.embedding_model == "new-model"]
    assert len(new) == len(old_ids)
    assert all(c.embedding_version == "v2" and c.embedding_dimension == 2 for c in new)
    assert all(len(c.embedding) == c.embedding_dimension for c in rows)
    before = {c.id for c in rows}
    ingest(
        database,
        revision,
        model="new-model",
        version="v2",
        dimension=2,
        embed=lambda texts: pytest.fail("Rebuild retry must be idempotent"),
    )
    assert {c.id for c in chunks(database)} == before


@pytest.mark.parametrize("state", ["draft", "rejected", "retired", "superseded", "unreadable"])
def test_ineligible_source_never_reaches_embedding(database, revision, state):
    module = import_module("app.ingestion.store")
    with database.begin() as session:
        row = session.get(SourceRevision, revision)
        if state in {"draft", "rejected"}:
            row.source.approval_status = state
        elif state == "unreadable":
            row.readable = False
        else:
            row.source.lifecycle_status = state

    def forbidden(texts):
        pytest.fail("Ineligible content must not be sent to the embedding provider")

    with pytest.raises(module.IngestionError):
        ingest(database, revision, embed=forbidden)
    assert chunks(database) == []


@pytest.mark.parametrize("change", ["dimension", "layout"])
def test_existing_identity_rejects_incompatible_retry(database, revision, change):
    module = import_module("app.ingestion.store")
    ingest(database, revision)
    before = {row.id for row in chunks(database)}
    options = {"dimension": 2} if change == "dimension" else {"chunk_size": 5}
    with pytest.raises(module.IngestionError):
        ingest(database, revision, **options)
    assert {row.id for row in chunks(database)} == before


def test_missing_approval_audit_blocks_provider(database, revision):
    module = import_module("app.ingestion.store")
    from sqlalchemy import delete

    # Simulate legacy/external status writes that bypassed governance.
    with database.begin() as session:
        session.execute(delete(SourceReviewEvent).where(SourceReviewEvent.revision_id.is_(None)))
    with pytest.raises(module.IngestionError):
        ingest(database, revision, embed=lambda texts: pytest.fail("Unreviewed source sent"))
    assert chunks(database) == []


def test_concurrent_imports_reuse_one_complete_identity(database, revision):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)

    def embed(texts):
        barrier.wait(timeout=3)
        return [[1.0, 0.5, 0.25] for _ in texts]

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(ingest, database, revision, embed=embed) for _ in range(2)]
        first, second = [future.result(timeout=10) for future in futures]
    assert first == second
    assert set(first) == {row.id for row in chunks(database)}


@pytest.mark.parametrize("change", ["audit", "unreadable", "expired"])
def test_final_gate_rechecks_review_readability_and_dates(database, revision, change):
    from datetime import timedelta

    from sqlalchemy import delete

    module = import_module("app.ingestion.store")

    def embed(texts):
        with database.begin() as session:
            row = session.get(SourceRevision, revision)
            if change == "audit":
                session.execute(
                    delete(SourceReviewEvent).where(SourceReviewEvent.revision_id == revision)
                )
            elif change == "unreadable":
                row.readable = False
            else:
                row.effective_until = utc_now() - timedelta(seconds=1)
        return [[1.0, 0.5, 0.25] for _ in texts]

    with pytest.raises(module.IngestionError):
        ingest(database, revision, embed=embed)
    assert chunks(database) == []
