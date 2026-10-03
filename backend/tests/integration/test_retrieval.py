"""T028 real PostgreSQL/pgvector search; synthetic policy text only."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, func

from app.models.source import ApprovedSource, SourceRevision
from app.models.source_chunk import SourceChunk
from app.services.retrieval import retrieve
from app.services.source_eligibility import mark_conflicting
from tests.integration.test_ingestion import database as database


def seed(
    database, clock, *, text="Parking permits must be visible.", vector=(1.0, 0.0, 0.0), **scope
):
    with database.begin() as session:
        source = ApprovedSource(
            canonical_url=f"https://www.pnw.edu/test-{uuid4()}/",
            title="Synthetic policy",
            owner_office="Registrar",
            subject_area="Parking",
            approval_status="approved",
        )
        revision = SourceRevision(
            source=source, content_hash=uuid4().hex * 2, retrieved_at=clock, readable=True, **scope
        )
        session.add(revision)
        session.flush()
        revision.status = "approved"
        revision.status = "active"
        chunk = SourceChunk(
            revision_id=revision.id,
            ordinal=0,
            text=text,
            heading="Policy",
            table_context="Campus | Rule",
            citation_anchor="parking",
            search_vector=func.to_tsvector("english", text),
            embedding=list(vector),
            embedding_model="test",
            embedding_version="v1",
            embedding_dimension=len(vector),
        )
        session.add(chunk)
        session.flush()
        return revision.id, source.id


def search(database, clock, **kwargs):
    return retrieve(
        database,
        question="parking permit",
        query_embedding=[1.0, 0.0, 0.0],
        context=kwargs.pop("context", {}),
        model="test",
        version="v1",
        dimension=3,
        now=clock,
        **kwargs,
    )


def test_hybrid_search_preserves_content_and_compatible_vectors(database, corpus_clock):
    both, _ = seed(database, corpus_clock)
    lexical, _ = seed(database, corpus_clock, vector=(0.0, 1.0))
    semantic, _ = seed(database, corpus_clock, text="Display the vehicle authorization.")
    seed(database, corpus_clock, text="Library books are renewable.", vector=(0.0, 1.0, 0.0))
    result = search(database, corpus_clock)
    assert result.chunks[0].revision_id == both
    assert {c.revision_id for c in result.chunks} == {both, lexical, semantic}
    chunk = result.chunks[0]
    assert chunk.text == "Parking permits must be visible."
    assert chunk.heading == "Policy" and chunk.table_context == "Campus | Rule"
    assert chunk.citation_anchor == "parking"
    assert chunk.as_excerpt().url == chunk.canonical_url


@pytest.mark.parametrize(
    "state",
    [
        "draft",
        "retired",
        "superseded",
        "unreadable",
        "expired",
        "future",
        "campus",
        "program",
        "course",
        "term",
    ],
)
def test_filters_apply_before_ranking(database, corpus_clock, state):
    identifier, source_id = seed(database, corpus_clock)
    with database.begin() as session:
        revision = session.get(SourceRevision, identifier)
        source = session.get(ApprovedSource, source_id)
        if state == "draft":
            source.approval_status = "draft"
        elif state in {"retired", "superseded"}:
            source.lifecycle_status = state
        elif state == "unreadable":
            revision.readable = False
        elif state == "expired":
            revision.effective_until = corpus_clock
        elif state == "future":
            revision.effective_from = corpus_clock + timedelta(days=1)
        else:
            setattr(
                revision,
                {"term": "academic_term"}.get(state, state),
                "hammond" if state == "campus" else "scoped",
            )
    assert search(database, corpus_clock).chunks == ()


def test_context_matches_all_scopes_and_effective_boundary(database, corpus_clock):
    identifier, _ = seed(
        database,
        corpus_clock,
        campus="hammond",
        program="MS CS",
        course="CS 50000",
        academic_term="Fall 2026",
        effective_from=corpus_clock,
    )
    result = search(
        database,
        corpus_clock,
        context=dict(
            campus="hammond", program="MS CS", course="CS 50000", academicTerm="Fall 2026"
        ),
    )
    assert [c.revision_id for c in result.chunks] == [identifier]


def test_conflicts_are_not_silently_replaced_or_limited_out(database, corpus_clock):
    ids = [seed(database, corpus_clock)[0] for _ in range(4)]
    with database.begin() as session:
        mark_conflicting(session, ids[:2])
    result = search(database, corpus_clock, limit=1)
    assert result.chunks == () and set(result.conflict_revision_ids) == set(ids[:2])


def test_retirement_between_ranking_and_recheck(database, corpus_clock):
    _, source_id = seed(database, corpus_clock)
    engine = database.kw["bind"]
    changed = False

    def retire(connection, cursor, statement, parameters, context, executemany):
        nonlocal changed
        if "compatible_chunks" in statement and not changed:
            changed = True
            with database.begin() as writer:
                writer.get(ApprovedSource, source_id).lifecycle_status = "retired"

    event.listen(engine, "after_cursor_execute", retire)
    try:
        assert search(database, corpus_clock).chunks == ()
        assert changed
    finally:
        event.remove(engine, "after_cursor_execute", retire)


@pytest.mark.parametrize("identity", ["model", "version", "dimension", "zero"])
def test_semantic_search_excludes_incompatible_or_zero_vectors(database, corpus_clock, identity):
    vector = (
        (0.0, 0.0, 0.0)
        if identity == "zero"
        else (1.0, 0.0)
        if identity == "dimension"
        else (1.0, 0.0, 0.0)
    )
    seed(database, corpus_clock, text="Vehicle authorization is displayed.", vector=vector)
    if identity in {"model", "version"}:
        # Bulk writes are test-only corruption; chunk ORM writes are immutable.
        from sqlalchemy import update

        with database.begin() as session:
            session.execute(update(SourceChunk).values(**{f"embedding_{identity}": "old"}))
    assert search(database, corpus_clock).chunks == ()


def test_unrelated_conflicts_do_not_block_matching_policy(database, corpus_clock):
    ids = [
        seed(database, corpus_clock, text="Library books are renewable.", vector=(0.0, 1.0, 0.0))[0]
        for _ in range(2)
    ]
    with database.begin() as session:
        mark_conflicting(session, ids)
    parking, _ = seed(database, corpus_clock)
    result = search(database, corpus_clock)
    assert result.conflict_revision_ids == ()
    assert [c.revision_id for c in result.chunks] == [parking]
