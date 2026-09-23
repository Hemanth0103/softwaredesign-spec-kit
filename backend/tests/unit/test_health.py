"""The Docker readiness probe must not depend on an AI provider being online."""

from fastapi.testclient import TestClient

from app.main import app


def test_health_and_no_chat_endpoint(monkeypatch):
    # These are fake local values. This test makes no external network calls.
    values = {
        "DATABASE_URL": "postgresql+psycopg://pnw:test@db:5432/pnw",
        "AI_PROVIDER": "test",
        "AI_API_KEY": "test",
        "AI_GENERATION_MODEL": "test",
        "AI_EMBEDDING_MODEL": "test",
        "EMBEDDING_VERSION": "v1",
        "EMBEDDING_DIMENSION": "768",
        "OIDC_ISSUER": "https://identity.example.edu",
        "OIDC_AUDIENCE": "test",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    with TestClient(app) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert client.post("/api/v1/chat/answers", json={"question": "hello"}).status_code == 404
