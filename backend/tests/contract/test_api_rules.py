"""Foundation API contracts exercised on test-only routes, not the T030 endpoint."""

from importlib import import_module

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import load_settings


@pytest.mark.parametrize(
    "payload",
    [
        {"question": ""},
        {"question": "x" * 4001},
        {"question": "hello", "unexpected": True},
        {"question": "hello", "campus": "all"},
        {"question": "hello", "program": "x" * 161},
        {"question": "hello", "course": "x" * 33},
        {"question": "hello", "academicTerm": "x" * 81},
    ],
)
def test_question_rejects_invalid_payload(payload):
    schemas = import_module("app.api.schemas.chat")
    with pytest.raises(ValidationError):
        schemas.StudentQuestion.model_validate(payload)


@pytest.mark.parametrize("length", [1, 4000])
def test_question_accepts_boundary_lengths(length):
    schemas = import_module("app.api.schemas.chat")
    assert (
        schemas.StudentQuestion.model_validate({"question": "x" * length}).question == "x" * length
    )


@pytest.fixture
def client(test_environment):
    middleware = import_module("app.api.middleware")
    app = FastAPI()
    middleware.configure_middleware(app, load_settings(test_environment))

    @app.post("/api/v1/chat/probe")
    def probe():
        return {
            "outcome": "referral",
            "limitation": "Test only",
            "officeName": "Registrar",
            "contactUrl": "https://www.pnw.edu/registrar/",
        }

    @app.post("/api/v1/chat/probe/error/{code}")
    def error(code: int):
        if code == 500:
            raise RuntimeError("private-question-secret")
        raise HTTPException(status_code=code, detail="private-question-secret")

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_public_chat_response_is_not_cacheable(client):
    response = client.post("/api/v1/chat/probe")
    assert response.status_code == 200
    assert "no-store" in response.headers.get("cache-control", "")


@pytest.mark.parametrize("code", [400, 429, 500])
def test_errors_are_safe_and_not_cacheable(client, caplog, code):
    response = client.post(f"/api/v1/chat/probe/error/{code}")
    assert response.status_code == code
    assert "no-store" in response.headers.get("cache-control", "")
    assert "private-question-secret" not in response.text
    assert "private-question-secret" not in caplog.text
    assert "traceback" not in response.text.lower()
    assert response.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize(
    "payload",
    [
        {"outcome": "answer", "answer": "Policy", "citations": [], "appliedContext": {}},
        {"outcome": "needs_context", "question": "Which campus?", "requiredFields": []},
        {"outcome": "needs_context", "question": "Which campus?", "requiredFields": ["identity"]},
        {"outcome": "referral", "limitation": "Unavailable"},
        {
            "outcome": "unresolved",
            "officeName": "Registrar",
            "contactUrl": "https://www.pnw.edu/registrar/",
        },
        {"outcome": "emergency", "guidance": "Call emergency services", "contacts": []},
    ],
)
def test_outcomes_require_their_contract_fields(payload):
    from pydantic import TypeAdapter

    schemas = import_module("app.api.schemas.chat")
    with pytest.raises(ValidationError):
        TypeAdapter(schemas.ChatResponse).validate_python(payload)
