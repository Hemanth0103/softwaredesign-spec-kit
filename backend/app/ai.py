"""Provider-neutral, request-scoped AI calls. No persistence or provider logging."""

import math
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future
from dataclasses import dataclass
from threading import BoundedSemaphore, Thread
from typing import Protocol
from uuid import UUID

from app.config import Settings
from app.ingestion.chunk import DocumentChunk
from app.ingestion.sources import validate_official_url


class AIConfigurationError(ValueError):
    """Deployment must register the configured provider explicitly."""


class EmbeddingError(ValueError):
    """Embedding preparation failed; no partial results may be published."""


class GenerationError(ValueError):
    """No usable draft; the caller must select a safe outcome."""


@dataclass(frozen=True)
class EmbeddedChunk(DocumentChunk):
    embedding: tuple[float, ...] = ()
    embedding_model: str = ""
    embedding_version: str = ""
    embedding_dimension: int = 0


@dataclass(frozen=True)
class ApprovedExcerpt:
    """Construct ONLY from governed retrieval, never from request/provider data.

    This is an internal trust boundary, not proof of DB eligibility. Retrieval
    and answer verification must check governance before/after the call.
    """

    chunk_id: UUID
    revision_id: UUID
    text: str
    title: str
    url: str

    def __post_init__(self) -> None:
        validate_official_url(self.url)
        if not self.text.strip() or not self.title.strip():
            raise ValueError("Excerpt text and title are required")


class Provider(Protocol):
    """A deployment transport must enforce timeouts and disable sensitive logs.

    Embeddings must be returned in input order. Map any vendor indices back to
    that order in the transport. Credentials belong to the transport instance.
    """

    def embed(
        self, texts: list[str], *, model: str, dimension: int, timeout_seconds: float
    ) -> object: ...

    def generate_grounded_answer(
        self,
        *,
        question: str,
        excerpts: tuple[ApprovedExcerpt, ...],
        context: dict[str, str],
        model: str,
        timeout_seconds: float,
    ) -> object: ...


# Bound abandoned synchronous calls as well as live calls. Daemon workers do
# not delay shutdown; timeout never waits for an uncooperative transport.
_CALL_SLOTS = BoundedSemaphore(4)


def _bounded[T](call: Callable[[], T], timeout: float) -> T:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Timeout must be positive and finite")
    if not _CALL_SLOTS.acquire(blocking=False):
        raise TimeoutError("Provider capacity unavailable")
    future: Future[T] = Future()

    def run() -> None:
        try:
            future.set_result(call())
        except BaseException as error:
            future.set_exception(error)
        finally:
            _CALL_SLOTS.release()

    try:
        Thread(target=run, daemon=True).start()
    except BaseException:
        _CALL_SLOTS.release()
        raise
    return future.result(timeout=timeout)


def _vectors(raw: object, count: int, dimension: int) -> list[tuple[float, ...]]:
    if not isinstance(raw, (list, tuple)) or len(raw) != count:
        raise ValueError("Missing or extra vectors")
    vectors = []
    for row in raw:
        if not isinstance(row, (list, tuple)) or len(row) != dimension:
            raise ValueError("Incompatible vector dimension")
        if any(type(value) not in (int, float) for value in row):
            raise ValueError("Vector values must be numeric")
        vector = tuple(float(value) for value in row)
        if not all(math.isfinite(value) for value in vector) or not any(vector):
            raise ValueError("Invalid cosine vector")
        vectors.append(vector)
    return vectors


def embed_chunks(
    chunks: Sequence[DocumentChunk],
    *,
    embed: Callable[[list[str]], object],
    model: str,
    version: str,
    dimension: int,
    timeout_seconds: float = 6.0,
) -> list[EmbeddedChunk]:
    """Prepare ALL caller-filtered eligible chunks or fail without a partial result.

    The caller owns eligibility checks. This pure preparation function never
    approves or publishes; transactional rechecks belong to ingestion storage.
    """
    try:
        if not model.strip() or not version.strip() or type(dimension) is not int or dimension < 1:
            raise ValueError("Invalid embedding identity")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Invalid timeout")
        snapshot = tuple(chunks)
        if any(not chunk.text.strip() for chunk in snapshot):
            raise ValueError("Empty chunk")
        if not snapshot:
            return []
        vectors = _vectors(
            _bounded(lambda: embed([chunk.text for chunk in snapshot]), timeout_seconds),
            len(snapshot),
            dimension,
        )
        return [
            EmbeddedChunk(
                chunk.ordinal,
                chunk.text,
                chunk.heading,
                chunk.table_context,
                chunk.citation_anchor,
                chunk.revision_id,
                vector,
                model,
                version,
                dimension,
            )
            for chunk, vector in zip(snapshot, vectors, strict=True)
        ]
    except Exception:
        raise EmbeddingError("Embedding preparation unavailable.") from None


class AIAdapter:
    """Select an explicitly supplied deployment provider; never fall back to a fake."""

    def __init__(self, settings: Settings, *, providers: Mapping[str, Provider]) -> None:
        if settings.ai_provider not in providers:
            raise AIConfigurationError("Configured AI provider is not registered.")
        self._provider = providers[settings.ai_provider]
        self._model = settings.ai_embedding_model
        self._version = settings.embedding_version
        self._dimension = settings.embedding_dimension
        self._generation_model = settings.ai_generation_model
        self._timeout = settings.ai_timeout_seconds

    def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        """Also usable for query vectors; identity is the configured chunk identity."""
        return [
            item.embedding
            for item in self.embed_chunks(
                [DocumentChunk(i, text, "", "", None) for i, text in enumerate(texts)]
            )
        ]

    def embed_chunks(self, chunks: Sequence[DocumentChunk]) -> list[EmbeddedChunk]:
        return embed_chunks(
            chunks,
            embed=lambda texts: self._provider.embed(
                texts,
                model=self._model,
                dimension=self._dimension,
                timeout_seconds=self._timeout,
            ),
            model=self._model,
            version=self._version,
            dimension=self._dimension,
            timeout_seconds=self._timeout,
        )

    def generate_grounded_answer(
        self,
        *,
        question: str,
        excerpts: Sequence[ApprovedExcerpt],
        context: Mapping[str, str],
    ) -> dict[str, object]:
        """Return an untrusted structured draft, NEVER a verified student answer.

        Only explicitly retrieved excerpts cross the evidence boundary. The
        provider must treat their text as data, not instructions. T029 verifies
        claim support/citations and rechecks eligibility before answer return.
        """
        try:
            evidence = tuple(excerpts)
            if not evidence or any(type(item) is not ApprovedExcerpt for item in evidence):
                raise ValueError("Approved retrieved excerpts required")
            if not question.strip() or len(question) > 4000:
                raise ValueError("Invalid question")
            if any(key not in {"campus", "program", "course", "academicTerm"} for key in context):
                raise ValueError("Invalid context")
            raw = _bounded(
                lambda: self._provider.generate_grounded_answer(
                    question=question,
                    excerpts=evidence,
                    context=dict(context),
                    model=self._generation_model,
                    timeout_seconds=self._timeout,
                ),
                self._timeout,
            )
            if not isinstance(raw, dict) or set(raw) != {"answer", "citations"}:
                raise ValueError("Invalid draft fields")
            answer, citations = raw["answer"], raw["citations"]
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError("Missing answer")
            if not isinstance(citations, list) or not citations:
                raise ValueError("Missing citations")
            for citation in citations:
                if not isinstance(citation, dict) or not citation:
                    raise ValueError("Invalid citation")
            return dict(raw)
        except Exception:
            raise GenerationError("Grounded generation unavailable.") from None


class DeterministicProvider:
    """Explicit offline test substitute. Not registered automatically in any environment."""

    def embed(
        self, texts: list[str], *, model: str, dimension: int, timeout_seconds: float
    ) -> object:
        return [[1.0 / (index + 1) for index in range(dimension)] for _ in texts]

    def generate_grounded_answer(
        self,
        *,
        question: str,
        excerpts: tuple[ApprovedExcerpt, ...],
        context: dict[str, str],
        model: str,
        timeout_seconds: float,
    ) -> object:
        return {"answer": excerpts[0].text, "citations": [{"chunkId": str(excerpts[0].chunk_id)}]}
