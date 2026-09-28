"""Shared deterministic fixtures, with no implicit live service connections."""

from datetime import datetime

import pytest
from fixtures.corpus import Corpus, load_corpus


@pytest.fixture
def corpus() -> Corpus:
    return load_corpus(environment="test")


@pytest.fixture
def corpus_clock(corpus: Corpus) -> datetime:
    return corpus.as_of


@pytest.fixture
def test_environment() -> dict[str, str]:
    return {
        "DATABASE_URL": "postgresql+psycopg://test:test@invalid/test",
        "AI_PROVIDER": "test",
        "AI_API_KEY": "test-only-not-a-live-key",
        "AI_GENERATION_MODEL": "test-generation",
        "AI_EMBEDDING_MODEL": "test-embedding",
        "EMBEDDING_VERSION": "fixture-v1",
        "EMBEDDING_DIMENSION": "3",
        "OIDC_ISSUER": "https://identity.example.test",
        "OIDC_AUDIENCE": "test-api",
    }
