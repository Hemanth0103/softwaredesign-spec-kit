"""Provider-neutral T024 contracts; no live credentials or network."""

import time
from dataclasses import FrozenInstanceError
from threading import Event
from uuid import uuid4

import pytest

from app.config import load_settings
from app.ingestion.chunk import DocumentChunk


def ai():
    from app import ai

    return ai


@pytest.mark.parametrize("vector", [[True, 1, 2], ["1", 1, 2], [0, 0, 0], None])
def test_invalid_vectors(vector):
    module = ai()
    with pytest.raises(module.EmbeddingError):
        module.embed_chunks(
            [DocumentChunk(0, "text", "", "", None)],
            embed=lambda texts: [vector],
            model="m",
            version="v",
            dimension=3,
        )


def test_real_deadline_and_private_errors():
    module = ai()
    release = Event()

    def hung(texts):
        release.wait(2)
        return [[1, 2, 3]]

    start = time.monotonic()
    try:
        with pytest.raises(module.EmbeddingError) as error:
            module.embed_chunks(
                [DocumentChunk(0, "private", "", "", None)],
                embed=hung,
                model="m",
                version="v",
                dimension=3,
                timeout_seconds=0.02,
            )
        assert time.monotonic() - start < 0.5
        assert "private" not in str(error.value)
    finally:
        release.set()


def test_settings_selection_models_and_grounded_payload(test_environment):
    module = ai()
    calls = []

    class Provider:
        def embed(self, texts, **kwargs):
            calls.append((texts, kwargs))
            return [[1, 2, 3] for _ in texts]

        def generate_grounded_answer(self, **kwargs):
            calls.append(kwargs)
            return {"answer": "Supported text", "citations": [{"chunkId": str(excerpt.chunk_id)}]}

    settings = load_settings(test_environment)
    adapter = module.AIAdapter(settings, providers={"test": Provider()})
    chunk = DocumentChunk(0, "Supported text", "heading", "", "section", uuid4())
    result = adapter.embed_chunks([chunk])[0]
    assert result.revision_id == chunk.revision_id and result.heading == "heading"
    assert calls[0][1] == {"model": "test-embedding", "dimension": 3, "timeout_seconds": 6.0}
    with pytest.raises(FrozenInstanceError):
        result.embedding = ()
    excerpt = module.ApprovedExcerpt(
        uuid4(), chunk.revision_id, chunk.text, "Policy", "https://www.pnw.edu/policy/"
    )
    draft = adapter.generate_grounded_answer(question="Question?", excerpts=[excerpt], context={})
    assert draft["answer"] == "Supported text"
    assert calls[1]["excerpts"] == (excerpt,)
    assert calls[1]["model"] == "test-generation"
    assert "api_key" not in calls[1]


def test_unknown_provider_and_unapproved_inputs_fail_before_call(test_environment):
    module = ai()
    with pytest.raises(module.AIConfigurationError):
        module.AIAdapter(load_settings(test_environment), providers={})
    provider = module.DeterministicProvider()
    adapter = module.AIAdapter(load_settings(test_environment), providers={"test": provider})
    for excerpts in ([], [DocumentChunk(0, "draft", "", "", None)]):
        with pytest.raises(module.GenerationError):
            adapter.generate_grounded_answer(question="q", excerpts=excerpts, context={})


@pytest.mark.parametrize("draft", [None, {}, {"answer": "x"}, {"answer": "", "citations": [{}]}])
def test_malformed_generation_fails_closed(test_environment, draft):
    module = ai()

    class Provider(module.DeterministicProvider):
        def generate_grounded_answer(self, **kwargs):
            return draft

    adapter = module.AIAdapter(load_settings(test_environment), providers={"test": Provider()})
    excerpt = module.ApprovedExcerpt(uuid4(), uuid4(), "text", "Title", "https://www.pnw.edu/x")
    with pytest.raises(module.GenerationError):
        adapter.generate_grounded_answer(question="q", excerpts=[excerpt], context={})


def test_generation_deadline_and_failure_privacy(test_environment, caplog):
    module = ai()
    release = Event()

    class Provider(module.DeterministicProvider):
        def generate_grounded_answer(self, **kwargs):
            release.wait(2)
            raise RuntimeError("private-key private-question")

    settings = load_settings(test_environment).model_copy(update={"ai_timeout_seconds": 0.02})
    adapter = module.AIAdapter(settings, providers={"test": Provider()})
    excerpt = module.ApprovedExcerpt(uuid4(), uuid4(), "text", "Title", "https://www.pnw.edu/x")
    start = time.monotonic()
    try:
        with pytest.raises(module.GenerationError) as error:
            adapter.generate_grounded_answer(
                question="private-question", excerpts=[excerpt], context={}
            )
        assert time.monotonic() - start < 0.5
        assert "private" not in str(error.value) + caplog.text
    finally:
        release.set()


def test_provider_exception_is_private(test_environment, caplog):
    module = ai()

    def failing(texts):
        raise RuntimeError("private-key private-question")

    with pytest.raises(module.EmbeddingError) as error:
        module.embed_chunks(
            [DocumentChunk(0, "private-question", "", "", None)],
            embed=failing,
            model="m",
            version="v",
            dimension=3,
        )
    assert "private" not in str(error.value) + caplog.text


def test_empty_batch_never_calls_provider():
    module = ai()

    def unexpected(texts):
        pytest.fail("Empty batch reached provider")

    assert module.embed_chunks([], embed=unexpected, model="m", version="v", dimension=3) == []


@pytest.mark.parametrize("url", ["https://evil.test/x", "http://www.pnw.edu/x"])
def test_excerpt_requires_official_https(url):
    with pytest.raises(ValueError):
        ai().ApprovedExcerpt(uuid4(), uuid4(), "text", "title", url)


def test_saturated_calls_fail_closed(monkeypatch):
    from threading import BoundedSemaphore

    module = ai()
    slots = BoundedSemaphore(1)
    slots.acquire()
    monkeypatch.setattr(module, "_CALL_SLOTS", slots)

    def unexpected(texts):
        pytest.fail("Saturated adapter invoked provider")

    with pytest.raises(module.EmbeddingError):
        module.embed_chunks(
            [DocumentChunk(0, "text", "", "", None)],
            embed=unexpected,
            model="m",
            version="v",
            dimension=3,
        )
