"""T021 collection tests: real governance records, offline HTTP, no extraction/provider.

SQLite exercises portable source/review queries here; PostgreSQL publication and
concurrency remain covered by the T018/T025/T026 integration contracts.
"""

import hashlib
import json
from importlib import import_module
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.auth import Reviewer
from app.db.base import Base, utc_now
from app.models.review_event import SourceReviewEvent
from app.models.source import ApprovedSource, SourceRevision
from app.services.source_governance import review_source

CONTENT = b'<main><p>Fixture policy.</p><a href="/linked.pdf">Document</a></main>'
URL = "https://www.pnw.edu/registrar/"
OWNER = Reviewer("fixture-owner", frozenset({"Registrar"}))


@pytest.fixture
def governed():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine,
        tables=[
            ApprovedSource.__table__,
            SourceRevision.__table__,
            SourceReviewEvent.__table__,
        ],
    )
    with Session(engine, expire_on_commit=False) as session:
        source = ApprovedSource(
            canonical_url=URL,
            title="Registrar",
            owner_office="Registrar",
            subject_area="Registration",
        )
        revision = SourceRevision(
            source=source,
            content_hash=hashlib.sha256(CONTENT).hexdigest(),
            retrieved_at=utc_now(),
            campus="hammond",
            program="MS CS",
            course="CS 50000",
            academic_term="Fall 2026",
        )
        session.add(revision)
        session.flush()
        for revision_id in [None, revision.id]:
            review_source(
                session,
                reviewer=OWNER,
                source_id=source.id,
                action="approve",
                reason="Fixture owning-office approval",
                revision_id=revision_id,
            )
        session.commit()
        yield session, source, revision
    engine.dispose()


def entry(governed, **changes):
    _, source, revision = governed
    return dict(
        source_id=str(source.id),
        revision_id=str(revision.id),
        canonical_url=URL,
        owner_office="Registrar",
        sha256=revision.content_hash,
        media_type="text/html",
        **changes,
    )


def manifest(tmp_path, documents):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"schema_version": 1, "documents": documents}))
    return path


def collect(governed, tmp_path, *, documents=None, handler=None, **options):
    module = import_module("app.ingestion.sources")
    path = manifest(tmp_path, documents if documents is not None else [entry(governed)])
    transport = httpx.MockTransport(
        handler
        or (
            lambda req: httpx.Response(
                200, content=CONTENT, headers={"content-type": "text/html; charset=utf-8"}
            )
        )
    )
    return module.collect_manifest(governed[0], path, transport=transport, **options)


def test_collects_reviewed_bytes_and_preserves_provenance_without_following_links(
    governed, tmp_path
):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        assert request.headers["accept-encoding"] == "identity"
        assert "authorization" not in request.headers
        assert request.extensions["timeout"]["read"] <= 10
        return httpx.Response(200, content=CONTENT, headers={"content-type": "text/html"})

    (result,) = collect(governed, tmp_path, handler=handler)
    assert calls == [URL]
    assert result.content == CONTENT and not result.review_required
    assert (result.source_id, result.revision_id) == (governed[1].id, governed[2].id)
    assert result.canonical_url == URL and result.fetched_url == URL
    assert result.title == "Registrar" and result.owner_office == "Registrar"
    assert result.subject_area == "Registration"
    assert (result.campus, result.program, result.course, result.academic_term) == (
        "hammond",
        "MS CS",
        "CS 50000",
        "Fall 2026",
    )
    assert result.sha256 == governed[2].content_hash
    assert result.retrieved_at.utcoffset() is not None
    assert governed[2].status == "approved"  # collection never activates


@pytest.mark.parametrize(
    "url",
    [
        "http://www.pnw.edu/",
        "https://pnw.edu.evil.test/",
        "https://evilpnw.edu/",
        "https://www.pnw.edu@evil.test/",
        "https://user@www.pnw.edu/",
        "https://127.0.0.1/",
        "https://www.pnw.edu:8443/",
        "https://www.pnw.edu/a#fragment",
        "https://www.pnw.edu/\\evil",
        "https://www.pnw.edu/\npath",
    ],
)
def test_rejects_unofficial_or_ambiguous_urls_before_fetch(governed, tmp_path, url):
    module = import_module("app.ingestion.sources")
    doc = entry(governed)
    doc["canonical_url"] = url
    with pytest.raises(module.SourceCollectionError):
        collect(governed, tmp_path, documents=[doc], handler=lambda req: pytest.fail("fetch"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("owner_office", "Housing"),
        ("sha256", "a" * 64),
        ("source_id", str(uuid4())),
        ("revision_id", str(uuid4())),
        ("canonical_url", "https://www.pnw.edu/linked.pdf"),
    ],
)
def test_manifest_cannot_override_governed_identity(governed, tmp_path, field, value):
    module = import_module("app.ingestion.sources")
    doc = entry(governed)
    doc[field] = value
    with pytest.raises(module.SourceCollectionError):
        collect(governed, tmp_path, documents=[doc], handler=lambda req: pytest.fail("fetch"))


@pytest.mark.parametrize(
    "state", ["draft", "rejected", "retired", "superseded", "pending_review", "conflicting"]
)
def test_ineligible_records_are_rejected_before_fetch(governed, tmp_path, state):
    module = import_module("app.ingestion.sources")
    session, source, revision = governed
    if state in {"draft", "rejected"}:
        source.approval_status = state
    elif state in {"retired", "superseded"}:
        source.lifecycle_status = state
    elif state == "conflicting":
        revision.conflict_group = uuid4()
    else:
        other = SourceRevision(source=source, content_hash="a" * 64, retrieved_at=utc_now())
        session.add(other)
        session.flush()
        governed = session, source, other
    session.commit()
    with pytest.raises(module.SourceCollectionError):
        collect(governed, tmp_path, handler=lambda req: pytest.fail("fetch"))


def test_status_without_revision_approval_event_is_not_authorization(governed, tmp_path):
    module = import_module("app.ingestion.sources")
    session, source, _ = governed
    row = SourceRevision(source=source, content_hash="a" * 64, retrieved_at=utc_now())
    session.add(row)
    session.flush()
    row.status = "approved"
    session.commit()
    with pytest.raises(module.SourceCollectionError):
        collect((session, source, row), tmp_path, handler=lambda req: pytest.fail("fetch"))


def test_local_snapshot_is_bounded_and_hash_verified(governed, tmp_path):
    snapshot = tmp_path / "policy.html"
    snapshot.write_bytes(CONTENT)
    retrieved = "2026-09-01T12:00:00Z"
    (result,) = collect(
        governed,
        tmp_path,
        documents=[entry(governed, path="policy.html", retrieved_at=retrieved)],
        handler=lambda req: pytest.fail("network"),
    )
    assert result.content == CONTENT and result.fetched_url is None
    assert result.retrieved_at.isoformat() == "2026-09-01T12:00:00+00:00"


@pytest.mark.parametrize("path", ["../outside.html", "/tmp/outside.html", "missing.html"])
def test_local_path_must_be_a_regular_file_inside_manifest_directory(governed, tmp_path, path):
    module = import_module("app.ingestion.sources")
    with pytest.raises(module.SourceCollectionError):
        collect(
            governed,
            tmp_path,
            documents=[entry(governed, path=path, retrieved_at="2026-09-01T12:00:00Z")],
        )


def test_snapshot_symlink_cannot_escape_manifest_directory(governed, tmp_path):
    module = import_module("app.ingestion.sources")
    outside = tmp_path.parent / (tmp_path.name + "-outside.html")
    outside.write_bytes(CONTENT)
    (tmp_path / "link.html").symlink_to(outside)
    with pytest.raises(module.SourceCollectionError):
        collect(
            governed,
            tmp_path,
            documents=[entry(governed, path="link.html", retrieved_at="2026-09-01T12:00:00Z")],
        )


def test_changed_bytes_create_only_pending_review_and_retries_reuse_it(governed, tmp_path):
    session, source, old = governed
    changed = CONTENT + b"changed"

    def handler(req):
        return httpx.Response(200, content=changed, headers={"content-type": "text/html"})

    (result,) = collect(governed, tmp_path, handler=handler)
    session.commit()
    pending = session.get(SourceRevision, result.revision_id)
    assert result.review_required and result.content is None
    assert pending.status == "pending_review" and not pending.readable
    assert pending.content_hash == hashlib.sha256(changed).hexdigest()
    assert pending.campus == old.campus and pending.academic_term == old.academic_term
    assert old.content_hash == hashlib.sha256(CONTENT).hexdigest() and old.status == "approved"
    (again,) = collect(governed, tmp_path, handler=handler)
    assert again.revision_id == pending.id and again.review_required
    assert len(list(session.scalars(select(SourceRevision)))) == 2
    assert len(list(session.scalars(select(SourceReviewEvent)))) == 2
    assert source.approval_status == "approved"


@pytest.mark.parametrize(
    "location",
    [
        "http://www.pnw.edu/",
        "https://evil.test/policy",
        "https://user@www.pnw.edu/",
        "https://127.0.0.1/",
    ],
)
def test_validates_redirect_before_following(governed, tmp_path, location):
    module = import_module("app.ingestion.sources")
    calls = []

    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(302, headers={"location": location})

    with pytest.raises(module.SourceCollectionError):
        collect(governed, tmp_path, handler=handler)
    assert calls == [URL]


def test_official_redirect_preserves_canonical_citation_and_checks_hash(governed, tmp_path):
    def handler(req):
        if str(req.url) == URL:
            return httpx.Response(302, headers={"location": "/updated-policy"})
        return httpx.Response(200, content=CONTENT, headers={"content-type": "text/html"})

    (result,) = collect(governed, tmp_path, handler=handler)
    assert result.canonical_url == URL
    assert result.fetched_url == "https://www.pnw.edu/updated-policy"


@pytest.mark.parametrize(
    "failure",
    ["size", "declared-size", "timeout", "status", "type", "redirect-loop", "empty", "encoding"],
)
def test_fetch_failures_return_no_content_or_new_revision(governed, tmp_path, failure):
    module = import_module("app.ingestion.sources")

    def handler(req):
        if failure == "timeout":
            raise httpx.ReadTimeout("private transport detail")
        if failure == "redirect-loop":
            return httpx.Response(302, headers={"location": URL})
        headers = {"content-type": "text/plain" if failure == "type" else "text/html"}
        if failure == "declared-size":
            headers["content-length"] = "1000000"
        if failure == "encoding":
            headers["content-encoding"] = "unknown"
        return httpx.Response(
            503 if failure == "status" else 200,
            content=b"" if failure == "empty" else CONTENT,
            headers=headers,
        )

    with pytest.raises(module.SourceCollectionError) as exc:
        collect(governed, tmp_path, handler=handler, max_bytes=4 if failure == "size" else 1000)
    assert "private transport detail" not in str(exc.value)
    assert len(list(governed[0].scalars(select(SourceRevision)))) == 1


def test_rechecks_governance_after_fetch(governed, tmp_path):
    module = import_module("app.ingestion.sources")

    def handler(req):
        governed[1].lifecycle_status = "retired"
        governed[0].flush()
        return httpx.Response(200, content=CONTENT, headers={"content-type": "text/html"})

    with pytest.raises(module.SourceCollectionError):
        collect(governed, tmp_path, handler=handler)


@pytest.mark.parametrize(
    "extra",
    [{"approval": "approved"}, {"path": "a.html"}, {"media_type": "application/octet-stream"}],
)
def test_invalid_manifest_is_rejected(governed, tmp_path, extra):
    module = import_module("app.ingestion.sources")
    doc = entry(governed)
    doc.update(extra)
    with pytest.raises(module.SourceCollectionError):
        collect(governed, tmp_path, documents=[doc], handler=lambda req: pytest.fail("fetch"))


@pytest.mark.parametrize(
    "options",
    [
        {"max_bytes": 0},
        {"max_bytes": -1},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": float("inf")},
        {"max_redirects": -1},
    ],
)
def test_invalid_limits_are_rejected(governed, tmp_path, options):
    module = import_module("app.ingestion.sources")
    with pytest.raises(module.SourceCollectionError):
        collect(governed, tmp_path, handler=lambda req: pytest.fail("fetch"), **options)


def test_streaming_byte_limit_without_content_length_closes_response(governed, tmp_path):
    module = import_module("app.ingestion.sources")
    closed = []

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"1234"
            yield b"5678"
            pytest.fail("Must stop consuming oversized content")

        def close(self):
            closed.append(True)

    with pytest.raises(module.SourceCollectionError, match="byte limit"):
        collect(
            governed,
            tmp_path,
            max_bytes=6,
            handler=lambda req: httpx.Response(
                200, stream=Stream(), headers={"content-type": "text/html"}
            ),
        )
    assert closed == [True]


def test_elapsed_deadline_stops_slow_stream(governed, tmp_path, monkeypatch):
    module = import_module("app.ingestion.sources")
    clock = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            clock[0] = 11
            yield CONTENT
            pytest.fail("Must stop consuming after deadline")

    with pytest.raises(module.SourceCollectionError, match="time limit"):
        collect(
            governed,
            tmp_path,
            handler=lambda req: httpx.Response(
                200, stream=Stream(), headers={"content-type": "text/html"}
            ),
        )


def test_redirect_chain_is_bounded(governed, tmp_path):
    module = import_module("app.ingestion.sources")
    calls = []

    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(302, headers={"location": f"/next/{len(calls)}"})

    with pytest.raises(module.SourceCollectionError, match="redirect limit"):
        collect(governed, tmp_path, handler=handler, max_redirects=2)
    assert len(calls) == 3


def test_snapshot_size_limit(governed, tmp_path):
    module = import_module("app.ingestion.sources")
    (tmp_path / "policy.html").write_bytes(CONTENT)
    with pytest.raises(module.SourceCollectionError, match="byte limit"):
        collect(
            governed,
            tmp_path,
            max_bytes=4,
            documents=[entry(governed, path="policy.html", retrieved_at="2026-09-01T12:00:00Z")],
        )


def test_manifest_duplicates_and_fixture_approval_format_are_not_accepted(governed, tmp_path):
    module = import_module("app.ingestion.sources")
    with pytest.raises(module.SourceCollectionError):
        collect(
            governed,
            tmp_path,
            documents=[entry(governed), entry(governed)],
            handler=lambda req: pytest.fail("fetch"),
        )
    path = tmp_path / "fixture.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "usage": "local_test_only",
                "documents": [entry(governed)],
                "cases": [],
            }
        )
    )
    with pytest.raises(module.SourceCollectionError):
        module.load_manifest(path)


def test_linked_document_needs_its_own_approved_revision_before_any_fetch(governed, tmp_path):
    module = import_module("app.ingestion.sources")
    session, _, _ = governed
    linked = ApprovedSource(
        canonical_url="https://www.pnw.edu/linked.pdf",
        title="Linked",
        owner_office="Registrar",
        subject_area="Registration",
    )
    row = SourceRevision(source=linked, content_hash="a" * 64, retrieved_at=utc_now())
    session.add(row)
    session.commit()
    doc = entry((session, linked, row))
    doc["canonical_url"] = linked.canonical_url
    with pytest.raises(module.SourceCollectionError):
        collect(
            governed,
            tmp_path,
            documents=[entry(governed), doc],
            handler=lambda req: pytest.fail("Unapproved linked document must block collection"),
        )


def test_changed_hash_does_not_reactivate_a_retired_revision(governed, tmp_path):
    session, source, _ = governed
    old_bytes = b"Previously retired content"
    old = SourceRevision(
        source=source, content_hash=hashlib.sha256(old_bytes).hexdigest(), retrieved_at=utc_now()
    )
    session.add(old)
    session.flush()
    old.status = "approved"
    old.status = "active"
    old.status = "retired"
    session.commit()
    (result,) = collect(
        governed,
        tmp_path,
        handler=lambda req: httpx.Response(
            200, content=old_bytes, headers={"content-type": "text/html"}
        ),
    )
    assert result.content is None and result.review_required
    assert result.revision_id == old.id and old.status == "retired"
