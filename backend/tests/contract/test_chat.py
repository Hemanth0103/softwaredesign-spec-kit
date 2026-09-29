"""T019 HTTP contract tests for the registered T030 student answer endpoint.

No test-only chat route is installed. A fresh app copies production routes and
middleware configuration to isolate rate counters. The proposed T030 dependency
get_chat_service() supplies an object with answer(StudentQuestion) -> ChatResponse.
Only that boundary is replaced here; grounding is tested with real persistence in
integration/test_grounded_answers.py. Missing future modules deliberately fail.
"""

from copy import deepcopy
from importlib import import_module
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from app.api.middleware import configure_middleware
from app.api.schemas.chat import ChatResponse
from app.config import load_settings
from app.main import app as production_app

PATH = "/api/v1/chat/answers"
ANSWER = {
    "outcome": "answer",
    "answer": "Display the parking permit while parked on campus.",
    "citations": [{"title": "Parking", "url": "https://www.pnw.edu/parking/"}],
    "appliedContext": {"campus": "hammond", "academicTerm": "Fall 2026"},
}


@pytest.fixture
def app(test_environment, monkeypatch):
    settings = load_settings({**test_environment, "RATE_LIMIT_REQUESTS": "100"})
    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    application = FastAPI()
    application.state.settings = settings
    application.include_router(production_app.router)
    configure_middleware(application, settings)
    return application


@pytest.fixture
def client(app):
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as client:
        yield client


def override_service(app, answer):
    route = import_module("app.api.routes.chat")
    app.dependency_overrides[route.get_chat_service] = lambda: SimpleNamespace(answer=answer)


def assert_json_no_store(response, status):
    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers.get("cache-control") == "no-store"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"question": ""},
        {"question": "x" * 4001},
        {"question": None},
        {"question": 42},
        {"question": ["hello"]},
        [],
        {"question": "hello", "unknown": "private-question-secret"},
        {"question": "hello", "campus": "all"},
        {"question": "hello", "campus": "Hammond"},
        {"question": "hello", "program": "x" * 161},
        {"question": "hello", "course": "x" * 33},
        {"question": "hello", "academicTerm": "x" * 81},
        {"question": "hello", "academic_term": "Fall 2026"},
    ],
)
def test_actual_endpoint_rejects_invalid_requests(client, payload, caplog):
    response = client.post(PATH, json=payload)
    assert_json_no_store(response, 400)
    assert "private-question-secret" not in response.text + caplog.text
    assert "traceback" not in response.text.lower()
    assert "detail" not in response.json()  # Do not leak Pydantic inputs/errors.


@pytest.mark.parametrize("body", ['{"question":', "not-json", ""])
def test_malformed_json_is_a_safe_400(client, body):
    response = client.post(PATH, content=body, headers={"Content-Type": "application/json"})
    assert_json_no_store(response, 400)
    assert response.json() == {"error": "Invalid request."}


@pytest.mark.parametrize("length", [1, 4000])
def test_public_endpoint_accepts_boundaries_and_passes_context(app, client, length):
    seen = []

    def answer(request):
        seen.append(request)
        return deepcopy(ANSWER)

    override_service(app, answer)
    payload = {
        "question": "x" * length,
        "campus": "hammond",
        "program": "p" * 160,
        "course": "c" * 32,
        "academicTerm": "t" * 80,
    }
    response = client.post(PATH, json=payload)  # No authentication header.
    assert_json_no_store(response, 200)
    assert len(seen) == 1
    assert seen[0].model_dump(by_alias=True, exclude_none=True) == payload
    body = response.json()
    TypeAdapter(ChatResponse).validate_python(body)
    assert body["outcome"] == ANSWER["outcome"] and body["answer"] == ANSWER["answer"]
    assert len(body["citations"]) == 1
    for key, value in ANSWER["citations"][0].items():
        assert body["citations"][0][key] == value
    for key, value in ANSWER["appliedContext"].items():
        assert body["appliedContext"][key] == value
    assert "applied_context" not in body


@pytest.mark.parametrize("field", ["answer", "citations", "appliedContext", "outcome"])
def test_supported_answer_requires_each_contract_field(field):
    payload = deepcopy(ANSWER)
    del payload[field]
    with pytest.raises(ValidationError):
        TypeAdapter(ChatResponse).validate_python(payload)


@pytest.mark.parametrize(
    "patch",
    [
        {"answer": ""},
        {"answer": " "},
        {"citations": []},
        {"citations": [{"url": "https://www.pnw.edu/parking/"}]},
        {"citations": [{"title": "Parking"}]},
        {"citations": [{"title": "Parking", "url": "http://www.pnw.edu/parking/"}]},
        {"appliedContext": {"campus": "all"}},
        {"extra": "not permitted"},
    ],
)
def test_supported_answer_rejects_invalid_fields(patch):
    with pytest.raises(ValidationError):
        TypeAdapter(ChatResponse).validate_python({**deepcopy(ANSWER), **patch})


def test_unexpected_service_error_is_private_500(app, client, caplog):
    def fail(request):
        raise RuntimeError("private-question-secret private-provider-key")

    override_service(app, fail)
    response = client.post(PATH, json={"question": "private-question-secret"})
    assert_json_no_store(response, 500)
    assert response.json() == {"error": "Service unavailable."}
    assert "private-question-secret" not in response.text + caplog.text
    assert "private-provider-key" not in response.text + caplog.text


def test_malformed_service_answer_cannot_escape_response_validation(app, client):
    override_service(app, lambda request: {"outcome": "answer", "answer": "Unsupported"})
    response = client.post(PATH, json={"question": "What is the parking policy?"})
    assert_json_no_store(response, 500)
    assert "Unsupported" not in response.text


def test_rate_limit_returns_safe_429_without_invoking_service_again(app, client):
    app.state.settings.rate_limit_requests = 1
    calls = []

    def answer(request):
        calls.append(request)
        return deepcopy(ANSWER)

    override_service(app, answer)
    assert_json_no_store(client.post(PATH, json={"question": "Parking?"}), 200)
    response = client.post(PATH, json={"question": "private-question-secret"})
    assert_json_no_store(response, 429)
    assert int(response.headers["retry-after"]) > 0
    assert response.json() == {"error": "Too many requests."}
    assert len(calls) == 1
