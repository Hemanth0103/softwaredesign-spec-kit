"""T027 CLI with real PostgreSQL collection, governance and storage."""

import hashlib
import json
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.config import load_settings
from app.db.base import utc_now
from app.ingestion import __main__ as cli
from app.models.referral import ReferralDirectoryEntry
from app.models.review_event import SourceReviewEvent
from app.models.source import ApprovedSource, SourceRevision
from app.models.source_chunk import SourceChunk
from tests.integration.test_ingestion import CONTENT, database  # noqa: F401


@pytest.fixture
def invocation(database, monkeypatch, tmp_path, test_environment):  # noqa: F811
    for key, value in test_environment.items():
        monkeypatch.setenv(key, value)
    settings = load_settings()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "create_db_engine", Mock())
    monkeypatch.setattr(cli, "create_session_factory", lambda engine: database)
    with database.begin() as session:
        ReferralDirectoryEntry.__table__.create(session.connection())
    source_id, revision_id = uuid4(), uuid4()
    (tmp_path / "source.html").write_bytes(CONTENT)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "documents": [
                    {
                        "source_id": str(source_id),
                        "revision_id": str(revision_id),
                        "canonical_url": "https://www.pnw.edu/registrar/",
                        "owner_office": "Registrar",
                        "sha256": hashlib.sha256(CONTENT).hexdigest(),
                        "media_type": "text/html",
                        "path": "source.html",
                        "retrieved_at": "2026-09-27T12:00:00Z",
                    }
                ],
            }
        )
    )
    args = ["--manifest", str(manifest), "--environment", "test", "--deterministic"]
    return args, database, tmp_path, source_id, revision_id


def test_normal_import_cannot_grant_approval(invocation):
    args, factory, _, _, _ = invocation
    assert cli.main(args) == 1
    with factory() as session:
        assert session.scalars(select(ApprovedSource)).all() == []
        assert session.scalars(select(SourceReviewEvent)).all() == []


def test_seed_import_repeat_and_rebuild_preserve_audits(invocation, monkeypatch):
    args, factory, path, _, revision_id = invocation
    referrals = path / "referrals.json"
    referrals.write_text(
        json.dumps(
            [
                {
                    "id": str(uuid4()),
                    "topic": "registration",
                    "office": "PNW Registrar",
                    "contact_url": "https://www.pnw.edu/registrar/",
                }
            ]
        )
    )
    seeded = args + ["--seed-fixtures", "--seed-referrals", str(referrals)]
    assert cli.main(seeded) == 0
    with factory() as session:
        before = {row.id for row in session.scalars(select(SourceChunk))}
        audits = {row.id for row in session.scalars(select(SourceReviewEvent))}
        assert before and len(audits) == 2
        assert session.get(SourceRevision, revision_id).status == "approved"
    assert cli.main(seeded) == 0
    assert cli.main(args + ["--rebuild"]) == 0
    with factory() as session:
        assert {row.id for row in session.scalars(select(SourceChunk))} == before
        assert {row.id for row in session.scalars(select(SourceReviewEvent))} == audits
        assert len(session.scalars(select(ReferralDirectoryEntry)).all()) == 1
    monkeypatch.setenv("EMBEDDING_VERSION", "fixture-v2")
    monkeypatch.setattr(cli, "load_settings", load_settings)
    assert cli.main(args + ["--rebuild"]) == 0
    with factory() as session:
        assert before < {row.id for row in session.scalars(select(SourceChunk))}


def test_changed_content_and_retired_retry_do_not_publish(invocation):
    args, factory, path, source_id, _ = invocation
    assert cli.main(args + ["--seed-fixtures"]) == 0
    with factory() as session:
        before = {row.id for row in session.scalars(select(SourceChunk))}
    (path / "source.html").write_bytes(CONTENT.replace(b"visible", b"displayed"))
    assert cli.main(args) == 1
    with factory.begin() as session:
        assert {row.id for row in session.scalars(select(SourceChunk))} == before
        assert len(session.scalars(select(SourceRevision)).all()) == 2
        assert "pending_review" in {row.status for row in session.scalars(select(SourceRevision))}
        session.get(ApprovedSource, source_id).lifecycle_status = "retired"
    (path / "source.html").write_bytes(CONTENT)
    assert cli.main(args + ["--seed-fixtures"]) == 1
    with factory() as session:
        assert session.get(ApprovedSource, source_id).lifecycle_status == "retired"
        assert {row.id for row in session.scalars(select(SourceChunk))} == before


def test_bad_referral_rolls_back_all_fixture_seeding(invocation):
    args, factory, path, _, _ = invocation
    referrals = path / "bad.json"
    referrals.write_text(
        json.dumps(
            [
                {
                    "id": str(uuid4()),
                    "topic": "registration",
                    "office": "Registrar",
                    "contact_url": "https://evil.example/",
                }
            ]
        )
    )
    assert cli.main(args + ["--seed-fixtures", "--seed-referrals", str(referrals)]) == 1
    with factory() as session:
        assert session.scalars(select(ApprovedSource)).all() == []
        assert session.scalars(select(SourceReviewEvent)).all() == []


def test_seed_hash_mismatch_does_not_create_rows(invocation):
    args, factory, path, _, _ = invocation
    (path / "source.html").write_bytes(b"wrong content")
    assert cli.main(args + ["--seed-fixtures"]) == 1
    with factory() as session:
        assert session.scalars(select(ApprovedSource)).all() == []


def test_fixture_retry_does_not_approve_existing_pending_revision(invocation):
    args, factory, _, source_id, revision_id = invocation
    with factory.begin() as session:
        source = ApprovedSource(
            id=source_id,
            canonical_url="https://www.pnw.edu/registrar/",
            title="Existing",
            owner_office="Registrar",
            subject_area="Registration",
            approval_status="approved",
        )
        session.add(
            SourceRevision(
                id=revision_id,
                source=source,
                content_hash=hashlib.sha256(CONTENT).hexdigest(),
                retrieved_at=utc_now(),
                campus="all",
                readable=True,
            )
        )
    assert cli.main(args + ["--seed-fixtures"]) == 1
    with factory() as session:
        assert session.get(SourceRevision, revision_id).status == "pending_review"
        assert session.scalars(select(SourceReviewEvent)).all() == []
