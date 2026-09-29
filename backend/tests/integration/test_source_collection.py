"""T021 PostgreSQL collection/registration and concurrent governance checks."""

import hashlib
import json
import os
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.auth import Reviewer
from app.db.base import Base, utc_now
from app.ingestion.sources import SourceCollectionError, collect_manifest
from app.models.review_event import SourceReviewEvent
from app.models.source import ApprovedSource, SourceRevision
from app.services.source_governance import review_source

CONTENT = b"<main>Reviewed fixture</main>"


@pytest.fixture
def collection_db(tmp_path):
    url = os.environ.get("T006_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated PostgreSQL database required")
    engine = create_engine(url)
    schema = "t021_" + uuid4().hex
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = engine.execution_options(schema_translate_map={None: schema})
    try:
        Base.metadata.create_all(
            scoped,
            tables=[
                ApprovedSource.__table__,
                SourceRevision.__table__,
                SourceReviewEvent.__table__,
            ],
        )
        factory = sessionmaker(scoped, expire_on_commit=False)
        with factory.begin() as session:
            source = ApprovedSource(
                canonical_url="https://www.pnw.edu/registrar/",
                title="Registrar",
                owner_office="Registrar",
                subject_area="Registration",
            )
            row = SourceRevision(
                source=source,
                content_hash=hashlib.sha256(CONTENT).hexdigest(),
                retrieved_at=utc_now(),
                effective_from=utc_now(),
                campus="hammond",
            )
            session.add(row)
            session.flush()
            for revision_id in [None, row.id]:
                review_source(
                    session,
                    reviewer=Reviewer("fixture-owner", frozenset({"Registrar"})),
                    source_id=source.id,
                    revision_id=revision_id,
                    action="approve",
                    reason="Fixture",
                )
            path = tmp_path / "manifest.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "documents": [
                            {
                                "source_id": str(source.id),
                                "revision_id": str(row.id),
                                "canonical_url": source.canonical_url,
                                "owner_office": source.owner_office,
                                "sha256": row.content_hash,
                                "media_type": "text/html",
                            }
                        ],
                    }
                )
            )
        yield factory, path, source.id, row.id
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def test_changed_revision_is_pending_idempotent_and_transactional(collection_db):
    factory, path, _, revision_id = collection_db
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200, content=CONTENT + b"changed", headers={"content-type": "text/html"}
        )
    )
    with factory.begin() as session:
        (result,) = collect_manifest(session, path, transport=transport)
        pending = session.get(SourceRevision, result.revision_id)
        old = session.get(SourceRevision, revision_id)
        assert pending.status == "pending_review" and not pending.readable
        assert pending.effective_from == old.effective_from and pending.campus == old.campus
        assert result.content is None and result.review_required
        with factory() as reader:
            assert reader.get(SourceRevision, result.revision_id) is None
    with factory.begin() as session:
        (again,) = collect_manifest(session, path, transport=transport)
        assert again.revision_id == result.revision_id
        assert len(list(session.scalars(select(SourceRevision)))) == 2
        assert len(list(session.scalars(select(SourceReviewEvent)))) == 2


def test_retirement_committed_during_fetch_is_observed(collection_db):
    factory, path, source_id, _ = collection_db

    def fetch(req):
        with factory.begin() as writer:
            writer.execute(text("SET LOCAL lock_timeout = '2s'"))
            review_source(
                writer,
                reviewer=Reviewer("fixture-owner", frozenset({"Registrar"})),
                source_id=source_id,
                action="retire",
                reason="Retired during download",
            )
        return httpx.Response(200, content=CONTENT, headers={"content-type": "text/html"})

    with factory.begin() as session:
        with pytest.raises(SourceCollectionError):
            collect_manifest(session, path, transport=httpx.MockTransport(fetch))
    with factory() as session:
        assert session.get(ApprovedSource, source_id).lifecycle_status == "retired"
        assert len(list(session.scalars(select(SourceRevision)))) == 1


def test_caller_rollback_discards_pending_registration(collection_db):
    factory, path, _, _ = collection_db
    with factory() as session:
        (result,) = collect_manifest(
            session,
            path,
            transport=httpx.MockTransport(
                lambda req: httpx.Response(
                    200, content=CONTENT + b"changed", headers={"content-type": "text/html"}
                )
            ),
        )
        session.rollback()
    with factory() as reader:
        assert reader.get(SourceRevision, result.revision_id) is None
