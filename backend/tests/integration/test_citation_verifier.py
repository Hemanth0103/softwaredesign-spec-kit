"""T029 final database boundary with real governed PostgreSQL evidence."""

from datetime import timedelta
from uuid import uuid4

import pytest

from app.models.source import ApprovedSource, SourceRevision
from app.services.citation_verifier import CitationVerificationError, verify_answer
from tests.integration.test_ingestion import database as database
from tests.integration.test_retrieval import search, seed


@pytest.mark.parametrize(
    "change",
    [
        "none",
        "retired",
        "superseded",
        "draft",
        "unreadable",
        "expired",
        "future",
        "conflict",
        "campus",
        "program",
        "course",
        "academic_term",
        "title",
        "url",
    ],
)
def test_final_read_rejects_changes_after_generation(database, corpus_clock, change):
    revision_id, source_id = seed(database, corpus_clock)
    retrieval = search(database, corpus_clock)
    chunk = retrieval.chunks[0]
    draft = {"answer": chunk.text, "citations": [{"chunkId": str(chunk.chunk_id)}]}
    with database.begin() as session:
        revision = session.get(SourceRevision, revision_id)
        source = session.get(ApprovedSource, source_id)
        if change in {"retired", "superseded"}:
            source.lifecycle_status = change
        elif change == "draft":
            source.approval_status = "draft"
        elif change == "unreadable":
            revision.readable = False
        elif change == "expired":
            revision.effective_until = corpus_clock
        elif change == "future":
            revision.effective_from = corpus_clock + timedelta(days=1)
        elif change == "conflict":
            revision.conflict_group = uuid4()
        elif change in {"campus", "program", "course", "academic_term"}:
            setattr(revision, change, "hammond" if change == "campus" else "scoped")
        elif change == "title":
            source.title = "Changed source"
        elif change == "url":
            source.canonical_url = "https://www.pnw.edu/changed/"
    if change == "none":
        assert (
            verify_answer(
                database, draft=draft, retrieval=retrieval, context={}, now=corpus_clock
            ).answer
            == chunk.text
        )
    else:
        with pytest.raises(CitationVerificationError):
            verify_answer(database, draft=draft, retrieval=retrieval, context={}, now=corpus_clock)
