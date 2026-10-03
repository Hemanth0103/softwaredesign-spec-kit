"""T030 orchestration ordering and fail-safe outcomes, without external services."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.api.schemas.chat import StudentQuestion
from app.config import load_settings
from app.services.retrieval import RetrievalResult, RetrievedChunk


def make_service(test_environment, monkeypatch, *, result=None):
    from app.services import chat_service as module

    events = []
    embed = Mock(side_effect=lambda texts: events.append("embed") or [[1, 0.5, 0.25]])
    generate = Mock(side_effect=lambda **kwargs: events.append("generate") or {})
    service = module.ChatService(
        session_factory=Mock(),
        settings=load_settings(test_environment),
        embed=embed,
        generate_grounded_answer=generate,
        now=lambda: datetime(2026, 10, 3, tzinfo=UTC),
    )
    monkeypatch.setattr(service, "_contacts", lambda: [])
    monkeypatch.setattr(service, "_required_context", lambda question, context: [])
    monkeypatch.setattr(
        module,
        "retrieve",
        lambda *args, **kwargs: events.append("retrieve") or (result or RetrievalResult()),
    )
    return service, module, events, embed, generate


@pytest.mark.parametrize(
    "question,outcome",
    [
        ("I want to kill myself", "emergency"),
        ("Am I eligible for financial aid?", "referral"),
    ],
)
def test_safety_and_account_gates_precede_ai(test_environment, monkeypatch, question, outcome):
    service, _, events, embed, generate = make_service(test_environment, monkeypatch)
    assert service.answer(StudentQuestion(question=question))["outcome"] == outcome
    assert events == []
    embed.assert_not_called()
    generate.assert_not_called()


def test_emergency_survives_broken_database(test_environment, monkeypatch):
    service, _, _, _, _ = make_service(test_environment, monkeypatch)
    monkeypatch.setattr(service, "_contacts", Mock(side_effect=RuntimeError("private")))
    assert (
        service.answer(StudentQuestion(question="There is an active shooter"))["outcome"]
        == "emergency"
    )


def test_material_context_precedes_embedding(test_environment, monkeypatch):
    service, _, events, _, _ = make_service(test_environment, monkeypatch)
    monkeypatch.setattr(service, "_required_context", lambda *args: ["campus"])
    body = service.answer(StudentQuestion(question="What is the parking rule?"))
    assert body["outcome"] == "needs_context" and body["requiredFields"] == ["campus"]
    assert events == []


def test_empty_retrieval_never_generates(test_environment, monkeypatch):
    service, _, events, _, generate = make_service(test_environment, monkeypatch)
    assert service.answer(StudentQuestion(question="Parking?"))["outcome"] == "referral"
    assert events == ["embed", "retrieve"]
    generate.assert_not_called()


def test_conflict_never_generates(test_environment, monkeypatch):
    service, _, _, _, generate = make_service(
        test_environment, monkeypatch, result=RetrievalResult(conflict_revision_ids=(uuid4(),))
    )
    assert service.answer(StudentQuestion(question="Parking?"))["outcome"] == "unresolved"
    generate.assert_not_called()


def chunk():
    return RetrievedChunk(
        uuid4(),
        uuid4(),
        uuid4(),
        "Display the permit.",
        "Parking",
        "https://www.pnw.edu/parking/",
        None,
        None,
        None,
        "all",
        None,
        None,
        None,
        1.0,
    )


def test_verification_is_last_and_receives_fresh_clock(test_environment, monkeypatch):
    service, module, events, _, generate = make_service(
        test_environment, monkeypatch, result=RetrievalResult(chunks=(chunk(),))
    )

    def verify(*args, **kwargs):
        events.append("verify")
        assert kwargs["now"].tzinfo is UTC
        return SimpleNamespace(model_dump=lambda **kwargs: {"outcome": "answer"})

    monkeypatch.setattr(module, "verify_answer", verify)
    assert service.answer(StudentQuestion(question="Parking?"))["outcome"] == "answer"
    assert events == ["embed", "retrieve", "generate", "verify"]
    assert generate.call_args.kwargs["excerpts"][0].text == "Display the permit."


@pytest.mark.parametrize("stage", ["embed", "generate", "verify"])
def test_failures_never_return_a_draft(test_environment, monkeypatch, stage):
    service, module, _, embed, generate = make_service(
        test_environment, monkeypatch, result=RetrievalResult(chunks=(chunk(),))
    )
    if stage == "verify":
        monkeypatch.setattr(module, "verify_answer", Mock(side_effect=ValueError("private")))
    else:
        (embed if stage == "embed" else generate).side_effect = TimeoutError("private")
    body = service.answer(StudentQuestion(question="Parking?"))
    assert body["outcome"] in {"referral", "unresolved"}
    assert "private" not in str(body) and "answer" not in body


@pytest.mark.parametrize("stage", ["embed", "generate"])
def test_uncooperative_provider_is_bounded(test_environment, monkeypatch, stage):
    from threading import Event
    from time import monotonic

    service, module, _, embed, generate = make_service(
        test_environment, monkeypatch, result=RetrievalResult(chunks=(chunk(),))
    )
    release = Event()
    service.settings.ai_timeout_seconds = 0.02
    verify = Mock()
    monkeypatch.setattr(module, "verify_answer", verify)
    (embed if stage == "embed" else generate).side_effect = lambda *args, **kwargs: release.wait(2)
    started = monotonic()
    try:
        body = service.answer(StudentQuestion(question="Parking?"))
        assert monotonic() - started < 0.5
        assert body["outcome"] == "referral"
        verify.assert_not_called()
    finally:
        release.set()
