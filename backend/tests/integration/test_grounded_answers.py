"""T019 ingestion → PostgreSQL retrieval → verification → HTTP answer contracts.

Only the AI provider is substituted. Ingestion, eligibility, SQL search, citation
verification and the registered route must execute real application code. All
policy text below is synthetic test-only material, never live PNW policy.

Reuse the T018 ingest_revision interface. Proposed T028–T030 seam:
ChatService(session_factory=..., settings=..., embed=...,
            generate_grounded_answer=..., now=...).answer(StudentQuestion)
The generator accepts keyword question/excerpts/context; each excerpt exposes
revision_id, canonical_url, title and text. It returns the public answer shape.
get_chat_service in app.api.routes.chat is the HTTP dependency. These internal
interfaces are test-first proposals, not additional public API requirements.

Run with T006_TEST_DATABASE_URL pointing to dedicated PostgreSQL with pgvector.
Missing future application modules fail; only absent database configuration skips.
"""

import hashlib
import os
from copy import deepcopy
from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fixtures.corpus import SOURCES
from pydantic import TypeAdapter
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.api.middleware import configure_middleware
from app.api.schemas.chat import ChatResponse
from app.auth import Reviewer
from app.config import load_settings
from app.db.base import Base
from app.main import app as production_app
from app.models.referral import ReferralDirectoryEntry
from app.models.review_event import SourceReviewEvent
from app.models.source import ApprovedSource, SourceRevision
from app.models.source_chunk import SourceChunk
from app.services.source_governance import review_source

OWNER = Reviewer("test-owner", frozenset({"Registrar"}))
PATH = "/api/v1/chat/answers"
CONTEXT = {
    "campus": "hammond",
    "program": "MS CS",
    "course": "CS 50000",
    "academicTerm": "Fall 2026",
}
POLICIES = [
    ("parking", "What is the parking permit rule?", "Display the permit while parked."),
    ("add-drop", "What is the add/drop deadline?", "The add/drop deadline is September 30, 2026."),
    ("integrity", "What is the academic integrity rule?", "Cite sources in submitted coursework."),
    ("absence", "What is the class absence procedure?", "Notify the instructor about an absence."),
    ("standing", "What is the academic standing policy?", "Standing is reviewed after each term."),
]


@pytest.fixture
def database():
    url = os.environ.get("T006_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated PostgreSQL database with pgvector required")
    engine = create_engine(url)
    schema = "t019_" + uuid4().hex
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
                ReferralDirectoryEntry.__table__,
            ],
        )
        yield sessionmaker(scoped, expire_on_commit=False)
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def embed(texts):
    # Exact deterministic compatibility; ranking/filtering still uses PostgreSQL.
    return [[1.0, 0.5, 0.25] for _ in texts]


@pytest.fixture
def add_document(database, corpus_clock):
    def add(slug, claim, *, content=None, media_type="text/html", context=None, **identity):
        context = CONTEXT if context is None else context
        content = content or (f"<main><h1 id='policy'>{slug}</h1><p>{claim}</p></main>".encode())
        with database.begin() as session:
            source = ApprovedSource(
                canonical_url=f"https://www.pnw.edu/test-only/{slug}/",
                title=f"Fixture {slug}",
                owner_office="Registrar",
                subject_area="registration",
                approval_status="approved",
            )
            revision = SourceRevision(
                source=source,
                content_hash=hashlib.sha256(content).hexdigest(),
                retrieved_at=corpus_clock,
                effective_from=corpus_clock - timedelta(days=1),
                effective_until=corpus_clock + timedelta(days=100),
                readable=True,
                campus=context.get("campus", "all"),
                program=context.get("program"),
                course=context.get("course"),
                academic_term=context.get("academicTerm"),
            )
            session.add(revision)
            session.flush()
            review_source(
                session,
                reviewer=OWNER,
                source_id=source.id,
                action="approve",
                reason="Synthetic test-only evidence",
                revision_id=revision.id,
            )
            source_id, revision_id, url, title = (
                source.id,
                revision.id,
                source.canonical_url,
                source.title,
            )
        options = dict(model="test-embedding", version="fixture-v1", dimension=3)
        options.update(identity)
        import_module("app.ingestion.store").ingest_revision(
            database,
            revision_id=revision_id,
            content=content,
            media_type=media_type,
            embed=lambda texts: [[1.0] * options["dimension"] for _ in texts],
            chunk_size=100,
            chunk_overlap=0,
            **options,
        )
        with database.begin() as session:
            revision = session.get(SourceRevision, revision_id)
            if revision.status == "approved":
                review_source(
                    session,
                    reviewer=OWNER,
                    source_id=source_id,
                    action="activate",
                    reason="Publish prepared test content",
                    revision_id=revision_id,
                )
            assert (
                session.scalar(
                    select(SourceChunk.id)
                    .where(
                        SourceChunk.revision_id == revision_id,
                    )
                    .limit(1)
                )
                is not None
            )
        return SimpleNamespace(
            id=revision_id, source_id=source_id, url=url, title=title, claim=claim
        )

    return add


def draft(document, *, answer=None, context=None):
    return {
        "outcome": "answer",
        "answer": answer or document.claim,
        "citations": [{"title": document.title, "url": document.url}],
        "appliedContext": dict(CONTEXT if context is None else context),
    }


@pytest.fixture
def harness(database, corpus_clock, test_environment, monkeypatch):
    service_module = import_module("app.services.chat_service")
    route = import_module("app.api.routes.chat")
    settings = load_settings(test_environment)
    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    state = SimpleNamespace(draft=None, calls=[], on_generate=None)
    with database.begin() as session:
        session.add_all(
            [
                ReferralDirectoryEntry(
                    topic=topic,
                    office="Registrar",
                    campus="all",
                    active=True,
                    contact_url="https://www.pnw.edu/registrar/",
                )
                for topic in ("registration", "general")
            ]
        )

    def generate(*, question, excerpts, context):
        state.calls.append(list(excerpts))
        if state.on_generate:
            state.on_generate()
        return deepcopy(state.draft)

    service = service_module.ChatService(
        session_factory=database,
        settings=settings,
        embed=embed,
        generate_grounded_answer=generate,
        now=lambda: corpus_clock,
    )
    app = FastAPI()
    app.state.settings = settings
    app.include_router(production_app.router)
    configure_middleware(app, settings)
    app.dependency_overrides[route.get_chat_service] = lambda: service
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as client:

        def ask(question, context=None):
            response = client.post(
                PATH,
                json={
                    "question": question,
                    **(CONTEXT if context is None else context),
                },
            )
            assert response.status_code == 200
            assert response.headers.get("cache-control") == "no-store"
            body = response.json()
            TypeAdapter(ChatResponse).validate_python(body)
            return body

        state.ask = ask
        yield state


def assert_safe(body):
    assert body["outcome"] in {"referral", "unresolved"}
    assert body["limitation"].strip() and body["officeName"].strip()
    assert body["contactUrl"] == "https://www.pnw.edu/registrar/"
    assert "answer" not in body and "citations" not in body


@pytest.mark.parametrize("slug,question,claim", POLICIES)
def test_ingested_policy_returns_source_backed_answer(add_document, harness, slug, question, claim):
    document = add_document(slug, claim)
    harness.draft = draft(document)
    body = harness.ask(question)
    assert body["outcome"] == "answer" and body["answer"] == claim
    assert body["appliedContext"] == CONTEXT
    assert [(c["title"], c["url"]) for c in body["citations"]] == [(document.title, document.url)]
    assert harness.calls
    evidence = [e for call in harness.calls for e in call]
    assert any(e.revision_id == document.id and claim in e.text for e in evidence)
    assert all(e.canonical_url == document.url and e.title == document.title for e in evidence)


def test_document_prerequisite_and_table_cells_survive_to_answer(add_document, harness):
    document = add_document(
        "graduate-program",
        "Prerequisite: fixture CS 40000.",
        content=(SOURCES / "program-scope.docx").read_bytes(),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    harness.draft = draft(document)
    assert harness.ask("What prerequisite applies to CS 50000?")["outcome"] == "answer"
    assert any("CS 40000" in e.text for call in harness.calls for e in call)

    table = add_document(
        "table-deadline",
        "The add/drop deadline is September 30, 2026.",
        content=b"""
        <main><h1>Add/drop</h1><table><tr><th>Campus</th><th>Deadline</th></tr>
        <tr><td>Hammond</td><td>September 30, 2026</td></tr></table></main>""",
    )
    harness.calls.clear()
    harness.draft = draft(table)
    body = harness.ask("What is the Hammond add/drop deadline?")
    assert body["outcome"] == "answer" and body["answer"] == table.claim
    assert any(
        "Hammond" in e.text and "September 30, 2026" in e.text
        for call in harness.calls
        for e in call
        if e.revision_id == table.id
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("campus", "westville"),
        ("program", "MBA"),
        ("course", "CS 60000"),
        ("academicTerm", "Spring 2026"),
    ],
)
def test_context_incompatible_chunks_never_reach_generation(add_document, harness, field, value):
    correct = add_document("parking-correct", "Display the permit while parked.")
    wrong = add_document("parking-wrong", "No permit is needed.", context={**CONTEXT, field: value})
    harness.draft = draft(correct)
    body = harness.ask("What is the parking permit rule?")
    assert body["outcome"] == "answer" and body["answer"] == correct.claim
    ids = {e.revision_id for call in harness.calls for e in call}
    assert correct.id in ids and wrong.id not in ids
    assert all(c["url"] != wrong.url for c in body["citations"])


@pytest.mark.parametrize(
    "state",
    [
        "draft",
        "rejected",
        "retired",
        "superseded",
        "unreadable",
        "unresolved",
        "expired",
        "future",
    ],
)
def test_ineligible_previously_ingested_chunks_cannot_support_answer(
    database,
    add_document,
    harness,
    corpus_clock,
    state,
):
    document = add_document("excluded", "The add/drop deadline is September 30, 2026.")
    with database.begin() as session:
        row = session.get(SourceRevision, document.id)
        if state in {"draft", "rejected"}:
            row.source.approval_status = state
        elif state in {"retired", "superseded", "unresolved"}:
            row.status = state
            if state == "unresolved":
                row.conflict_group = uuid4()
        elif state == "unreadable":
            row.readable = False
        elif state == "expired":
            row.effective_from = corpus_clock - timedelta(days=10)
            row.effective_until = corpus_clock  # Exclusive boundary.
        else:
            row.effective_from = corpus_clock + timedelta(days=1)
    harness.draft = draft(document)
    assert_safe(harness.ask("What is the add/drop deadline?"))
    assert all(e.revision_id != document.id for call in harness.calls for e in call)


@pytest.mark.parametrize(
    "corruption",
    [
        "invented-official-url",
        "external-url",
        "wrong-title",
        "unsupported-claim",
        "wrong-context",
    ],
)
def test_generated_claims_and_citations_are_verified(add_document, harness, corruption):
    document = add_document("parking", "Display the permit while parked.")
    harness.draft = draft(document)
    if corruption == "invented-official-url":
        harness.draft["citations"][0]["url"] = "https://www.pnw.edu/invented-policy/"
    elif corruption == "external-url":
        harness.draft["citations"][0]["url"] = "https://example.com/parking/"
    elif corruption == "wrong-title":
        harness.draft["citations"][0]["title"] = "Invented policy title"
    elif corruption == "unsupported-claim":
        harness.draft["answer"] += " Parking fines are always waived for graduate students."
    else:
        harness.draft["appliedContext"]["campus"] = "westville"
    assert_safe(harness.ask("What is the parking permit rule?"))
    assert harness.calls  # Proves rejection happens after generation, not lack of evidence.
    assert any(e.revision_id == document.id for call in harness.calls for e in call)


def test_real_approved_but_irrelevant_citation_is_rejected(add_document, harness):
    parking = add_document("parking", "Display the permit while parked.")
    unrelated = add_document("library", "Library books may be renewed online.")
    harness.draft = draft(parking)
    harness.draft["citations"] = [{"title": unrelated.title, "url": unrelated.url}]
    assert_safe(harness.ask("What is the parking permit rule?"))
    assert harness.calls
    assert any(e.revision_id == parking.id for call in harness.calls for e in call)


def test_expired_deadline_cannot_be_presented_as_current(
    database, add_document, harness, corpus_clock
):
    current = add_document("current-deadline", "The add/drop deadline is September 30, 2026.")
    old = add_document(
        "old-deadline",
        "The add/drop deadline is August 20, 2025.",
        context={**CONTEXT, "academicTerm": "Fall 2025"},
    )
    with database.begin() as session:
        row = session.get(SourceRevision, old.id)
        row.effective_from = corpus_clock - timedelta(days=400)
        row.effective_until = corpus_clock - timedelta(days=300)
    # A real current URL cannot validate a historical date absent from its excerpt.
    harness.draft = draft(current, answer=old.claim)
    assert_safe(harness.ask("What is the current add/drop deadline?"))
    ids = {e.revision_id for call in harness.calls for e in call}
    assert current.id in ids and old.id not in ids


def test_retirement_after_retrieval_is_rechecked_before_return(database, add_document, harness):
    document = add_document("parking", "Display the permit while parked.")
    harness.draft = draft(document)

    def retire():
        with database.begin() as session:
            review_source(
                session,
                reviewer=OWNER,
                source_id=document.source_id,
                action="retire",
                reason="Concurrent retirement of synthetic fixture",
            )

    harness.on_generate = retire
    assert_safe(harness.ask("What is the parking permit rule?"))
    assert harness.calls
    with database() as session:
        assert session.get(ApprovedSource, document.source_id).lifecycle_status == "retired"
