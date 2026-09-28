import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from app.api.middleware import configure_middleware
from app.api.schemas.chat import ChatResponse, StudentQuestion
from app.config import load_settings

PAYLOADS = [
    {
        "outcome": "answer",
        "answer": "Supported policy",
        "citations": [
            {"title": "Policy", "url": "https://www.pnw.edu/policy", "contextLabel": "Fall 2026"}
        ],
        "appliedContext": {"campus": "hammond", "academicTerm": "Fall 2026"},
    },
    {"outcome": "needs_context", "question": "Which campus?", "requiredFields": ["campus"]},
    {
        "outcome": "referral",
        "limitation": "Cannot determine your record",
        "officeName": "Registrar",
        "contactUrl": "https://www.pnw.edu/registrar/",
    },
    {
        "outcome": "unresolved",
        "limitation": "Conflicting evidence",
        "officeName": "Dean of Students",
        "contactUrl": "https://www.pnw.edu/dean-of-students/",
    },
    {
        "outcome": "emergency",
        "guidance": "Call emergency services",
        "contacts": [
            {
                "officeName": "Public Safety",
                "contactUrl": "https://www.pnw.edu/public-safety/",
                "phone": "911",
            }
        ],
    },
]


@pytest.mark.parametrize("payload", PAYLOADS)
def test_all_outcomes_roundtrip_with_contract_names(payload):
    parsed = TypeAdapter(ChatResponse).validate_python(payload)
    assert parsed.model_dump(mode="json", exclude_none=True) == payload
    for field in payload:
        invalid = {key: value for key, value in payload.items() if key != field}
        with pytest.raises(ValidationError):
            TypeAdapter(ChatResponse).validate_python(invalid)


@pytest.mark.parametrize("field,limit", [("program", 160), ("course", 32), ("academicTerm", 80)])
def test_context_boundaries(field, limit):
    StudentQuestion.model_validate({"question": "Hello", field: "x" * limit})
    with pytest.raises(ValidationError):
        StudentQuestion.model_validate({"question": "Hello", field: "x" * (limit + 1)})


@pytest.mark.parametrize(
    "payload",
    [
        {"question": True},
        {"question": 123},
        {"question": ["text"]},
        {"question": "Hello", "academic_term": "Fall 2026"},
        {"question": "Hello", "campus": "Hammond"},
    ],
)
def test_strict_request_types(payload):
    with pytest.raises(ValidationError):
        StudentQuestion.model_validate(payload)


@pytest.mark.parametrize(
    "url",
    ["http://www.pnw.edu/", "javascript:alert(1)", "https://user:pass@www.pnw.edu/", "https://"],
)
def test_unsafe_links_rejected(url):
    with pytest.raises(ValidationError):
        TypeAdapter(ChatResponse).validate_python({**PAYLOADS[2], "contactUrl": url})


@pytest.fixture
def client(test_environment):
    app = FastAPI()
    configure_middleware(app, load_settings(test_environment))

    @app.post(
        "/api/v1/chat/schema-probe", response_model=ChatResponse, response_model_exclude_none=True
    )
    def probe(question: StudentQuestion):
        return PAYLOADS[0]

    @app.post("/api/v1/chat/bad-output", response_model=ChatResponse)
    def invalid_response():
        return {"outcome": "answer", "answer": "private-response-secret"}

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.mark.parametrize(
    "body",
    [
        '{"question":',
        '{"question":"private-input-secret","extra":1}',
        '{"question":123}',
        '{"question":"' + "x" * 4001 + '"}',
    ],
)
def test_invalid_http_request_is_private_400(client, caplog, body):
    response = client.post(
        "/api/v1/chat/schema-probe", content=body, headers={"content-type": "application/json"}
    )
    assert response.status_code == 400
    assert response.json() == {"error": "Invalid request."}
    assert response.headers["cache-control"] == "no-store"
    assert "private-input-secret" not in response.text + caplog.text


def test_http_response_aliases_and_safe_server_validation(client, caplog):
    response = client.post("/api/v1/chat/schema-probe", json={"question": "Policy?"})
    assert response.status_code == 200
    assert response.json() == PAYLOADS[0]
    response = client.post("/api/v1/chat/bad-output")
    assert response.status_code == 500
    assert response.json() == {"error": "Service unavailable."}
    assert response.headers["cache-control"] == "no-store"
    assert "private-response-secret" not in response.text + caplog.text
