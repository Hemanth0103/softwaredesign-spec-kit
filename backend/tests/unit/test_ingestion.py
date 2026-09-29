"""T018 test-first contracts for T022–T024 (intentionally red until implemented).

extract_document(bytes, media_type=...) returns blocks with text, heading,
table_context and citation_anchor. chunk_document(blocks, size=..., overlap=...)
uses word budgets and returns those fields plus contiguous zero-based ordinals.
embed_chunks(chunks, embed=..., model=..., version=..., dimension=...) validates
one vector per chunk; the injected callable takes a list of texts.
Imports stay inside tests so missing future modules do not break collection.
"""

from importlib import import_module

import pytest
from fixtures.corpus import SOURCES

HTML = b"""<html><nav>Navigation junk</nav><script>tracking junk</script><main>
<h1>Registration</h1><h2 id="deadline">Add/drop</h2>
<p>Submit&nbsp;the form by September 30, 2026.</p>
<ul><li>Contact your advisor.</li><li>Keep a copy.</li></ul>
<table><caption>Campus deadlines</caption><tr><th>Campus</th><th>Date</th></tr>
<tr><td>Hammond</td><td>September 30, 2026</td></tr>
<tr><td>Westville</td><td>October 1, 2026</td></tr></table>
</main><footer>Footer junk</footer></html>"""


def extract(content=HTML, media_type="text/html"):
    return import_module("app.ingestion.extract").extract_document(content, media_type=media_type)


def test_html_preserves_policy_lists_table_and_anchor_without_boilerplate():
    blocks = extract()
    text = "\n".join(block.text for block in blocks)
    for phrase in (
        "Submit the form by September 30, 2026.",
        "Contact your advisor.",
        "Keep a copy.",
        "Hammond",
        "Westville",
        "October 1, 2026",
    ):
        assert phrase in text
    assert "junk" not in text
    deadline = next(block for block in blocks if "Submit" in block.text)
    assert "Add/drop" in deadline.heading
    assert deadline.citation_anchor == "deadline"
    table = next(block for block in blocks if "Westville" in block.text)
    assert "Campus deadlines" in table.table_context
    assert "Campus" in table.text and "Date" in table.text
    assert extract() == blocks


@pytest.mark.parametrize("document_id,expected", [("calendar", "2026"), ("program", "CS")])
def test_readable_pdf_and_document_snapshots(corpus, document_id, expected):
    doc = next(doc for doc in corpus.documents if doc.id == document_id)
    blocks = extract((SOURCES / doc.path).read_bytes(), doc.media_type)
    assert blocks and expected in " ".join(block.text for block in blocks)
    assert all(block.text.strip() for block in blocks)


@pytest.mark.parametrize(
    "content,media_type",
    [
        (b"", "text/html"),
        (b"<nav>Menu only</nav>", "text/html"),
        (b"%PDF-broken", "application/pdf"),
        (b"opaque", "application/octet-stream"),
        (b"<html>not a PDF</html>", "application/pdf"),
        (b"broken zip", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ],
)
def test_unreadable_unsupported_or_mismatched_content_is_rejected(content, media_type):
    module = import_module("app.ingestion.extract")
    with pytest.raises(module.ExtractionError):
        module.extract_document(content, media_type=media_type)


def test_unreadable_fixture_is_rejected():
    module = import_module("app.ingestion.extract")
    with pytest.raises(module.ExtractionError):
        extract((SOURCES / "unreadable.pdf").read_bytes(), "application/pdf")


def test_chunks_are_deterministic_contiguous_and_preserve_sections_and_tables():
    chunk = import_module("app.ingestion.chunk").chunk_document
    blocks = extract()
    chunks = chunk(blocks, size=80, overlap=10)
    assert chunks == chunk(blocks, size=80, overlap=10)
    assert chunks and [c.ordinal for c in chunks] == list(range(len(chunks)))
    assert all(c.text.strip() and len(c.text.split()) <= 80 for c in chunks)
    assert all(c.citation_anchor == "deadline" for c in chunks if "Submit" in c.text)
    table = next(c for c in chunks if "Westville" in c.text)
    assert "Hammond" in table.text and "Campus deadlines" in table.table_context
    for block in blocks:
        assert block.text in "\n".join(c.text for c in chunks)


def test_long_paragraph_has_bounded_overlap_and_no_lost_words():
    chunk = import_module("app.ingestion.chunk").chunk_document
    words = [f"word{i}" for i in range(180)]
    blocks = extract(("<main><p>" + " ".join(words) + "</p></main>").encode())
    chunks = chunk(blocks, size=40, overlap=8)
    assert len(chunks) > 1
    rebuilt = chunks[0].text.split()
    for left, right in zip(chunks, chunks[1:], strict=False):
        assert left.text.split()[-8:] == right.text.split()[:8]
        rebuilt.extend(right.text.split()[8:])
    assert rebuilt == words
    assert all(len(c.text.split()) <= 40 for c in chunks)


@pytest.mark.parametrize("size,overlap", [(0, 0), (10, -1), (10, 10), (10, 11)])
def test_invalid_chunk_configuration(size, overlap):
    chunk = import_module("app.ingestion.chunk").chunk_document
    with pytest.raises(ValueError):
        chunk([], size=size, overlap=overlap)


def test_every_chunk_is_embedded_with_identity():
    ai = import_module("app.ai")
    chunks = import_module("app.ingestion.chunk").chunk_document(extract(), size=25, overlap=0)
    calls = []

    def embed(texts):
        calls.extend(texts)
        return [[1.0, 0.5, 0.25] for _ in texts]

    result = ai.embed_chunks(chunks, embed=embed, model="test", version="v1", dimension=3)
    assert calls == [c.text for c in chunks]
    assert len(result) == len(chunks) > 0
    for item in result:
        assert list(item.embedding) == [1.0, 0.5, 0.25]
        assert (item.embedding_model, item.embedding_version, item.embedding_dimension) == (
            "test",
            "v1",
            3,
        )


@pytest.mark.parametrize(
    "vectors",
    [[], [[1.0]], [[float("nan"), 0, 1]], [[float("inf"), 0, 1]], [None], [[1, 0, 0], [1, 0, 0]]],
)
def test_embedding_rejects_missing_extra_nonfinite_and_incompatible_vectors(vectors):
    ai = import_module("app.ai")
    chunks = import_module("app.ingestion.chunk").chunk_document(
        extract(b"<main><p>One paragraph.</p></main>"),
        size=40,
        overlap=0,
    )
    with pytest.raises(ai.EmbeddingError):
        ai.embed_chunks(
            chunks, embed=lambda texts: vectors, model="test", version="v1", dimension=3
        )


def test_embedding_timeout_fails_closed():
    ai = import_module("app.ai")
    chunks = import_module("app.ingestion.chunk").chunk_document(extract(), size=80, overlap=0)

    def unavailable(texts):
        raise TimeoutError("deterministic provider timeout")

    with pytest.raises(ai.EmbeddingError):
        ai.embed_chunks(chunks, embed=unavailable, model="test", version="v1", dimension=3)
