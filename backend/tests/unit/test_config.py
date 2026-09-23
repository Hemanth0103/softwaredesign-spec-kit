"""Environment configuration must fail closed without exposing credentials."""

import pytest

from app.config import ConfigurationError, load_settings


@pytest.fixture
def environment() -> dict[str, str]:
    return {
        "DATABASE_URL": "postgresql+psycopg://pnw:private-password@db:5432/pnw",
        "AI_PROVIDER": "test-provider",
        "AI_API_KEY": "private-api-key",
        "AI_GENERATION_MODEL": "test-generation",
        "AI_EMBEDDING_MODEL": "test-embedding",
        "EMBEDDING_VERSION": "v1",
        "EMBEDDING_DIMENSION": "768",
        "OIDC_ISSUER": "https://identity.example.edu/tenant",
        "OIDC_AUDIENCE": "pnw-api",
    }


def test_load_and_redact(environment: dict[str, str]) -> None:
    environment.update(
        CHUNK_SIZE="600", CHUNK_OVERLAP="80", CORS_ORIGINS='["https://chat.example.edu"]'
    )
    settings = load_settings(environment)
    assert settings.embedding_dimension == 768
    assert settings.chunk_size == 600
    assert settings.chunk_overlap == 80
    assert settings.cors_origins == ["https://chat.example.edu"]
    assert settings.ai_api_key.get_secret_value() == "private-api-key"
    for output in (repr(settings), settings.model_dump_json()):
        assert "private-password" not in output
        assert "private-api-key" not in output


def test_process_environment(environment: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    assert load_settings().ai_provider == "test-provider"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("EMBEDDING_DIMENSION", "0"),
        ("CHUNK_SIZE", "0"),
        ("CHUNK_OVERLAP", "-1"),
        ("CHUNK_OVERLAP", "800"),
        ("AI_TIMEOUT_SECONDS", "0"),
        ("AI_TIMEOUT_SECONDS", "nan"),
        ("DATABASE_TIMEOUT_SECONDS", "-1"),
        ("SOURCE_TIMEOUT_SECONDS", "inf"),
        ("OIDC_TIMEOUT_SECONDS", "0"),
        ("AI_PROVIDER", " "),
        ("AI_API_KEY", ""),
        ("DATABASE_URL", "sqlite:///test.db"),
        ("OIDC_ISSUER", "http://identity.example.edu"),
        ("CORS_ORIGINS", '["*"]'),
        ("CORS_ORIGINS", "not-json"),
        ("CORS_ORIGINS", '["https://chat.example.edu/path"]'),
        ("CORS_ORIGINS", '["https://user:password@chat.example.edu"]'),
    ],
)
def test_invalid_settings(environment: dict[str, str], key: str, value: str) -> None:
    environment[key] = value
    with pytest.raises(ConfigurationError):
        load_settings(environment)


def test_missing_settings() -> None:
    with pytest.raises(ConfigurationError, match="DATABASE_URL"):
        load_settings({})


def test_errors_do_not_expose_values(environment: dict[str, str]) -> None:
    environment["DATABASE_URL"] = "invalid-private-password"
    with pytest.raises(ConfigurationError) as error:
        load_settings(environment)
    assert "invalid-private-password" not in str(error.value)
    assert error.value.__suppress_context__


def test_default_cors_denies_cross_origin(environment: dict[str, str]) -> None:
    assert load_settings(environment).cors_origins == []
