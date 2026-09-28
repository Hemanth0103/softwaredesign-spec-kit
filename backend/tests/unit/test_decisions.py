"""T016 must satisfy these shared decision-gate requirements without live AI calls."""

from importlib import import_module

import pytest


def decide(corpus, question, **overrides):
    gates = import_module("app.services.decisions")
    args = dict(
        question=question,
        context={},
        required_context=[],
        evidence_status="available",
        contacts=[contact.model_dump() for contact in corpus.contacts],
    )
    args.update(overrides)
    return gates.evaluate_gates(**args)


@pytest.mark.parametrize(
    "question",
    [
        "Someone has a gun and is threatening us right now.",
        "I am going to kill myself now. Can you also check my enrollment?",
        "There is a fire in the building. Which campus policy applies?",
    ],
)
def test_emergency_precedes_account_context_and_evidence(corpus, question):
    result = decide(corpus, question, required_context=["campus"], evidence_status="conflicting")
    assert result["outcome"] == "emergency"
    assert result["guidance"].strip()
    assert result["contacts"]
    assert any(contact.get("phone") == "911" for contact in result["contacts"])


@pytest.mark.parametrize(
    "question",
    [
        "Am I eligible for financial aid?",
        "Am I enrolled in CS 50000?",
        "Have I met my degree requirements?",
        "Can you remove my registration hold?",
        "What is the status of my disciplinary case?",
        "Have I been assigned housing?",
    ],
)
def test_account_specific_questions_refer_before_context(corpus, question):
    result = decide(corpus, question, required_context=["campus"])
    assert result["outcome"] == "referral"
    assert result["limitation"].strip()
    assert result["officeName"].strip()
    assert result["contactUrl"].startswith("https://www.pnw.edu/")


@pytest.mark.parametrize("field", ["campus", "program", "course", "academicTerm"])
def test_requests_only_material_missing_context(corpus, field):
    result = decide(corpus, "What is the applicable policy?", required_context=[field])
    assert result["outcome"] == "needs_context"
    assert result["requiredFields"] == [field]
    assert result["question"].strip()


def test_general_question_does_not_request_irrelevant_context(corpus):
    assert decide(corpus, "Where can I find the registrar contact details?") is None


def test_supplied_context_is_not_requested_again(corpus):
    assert (
        decide(
            corpus,
            "What is the campus policy?",
            required_context=["campus"],
            context={"campus": "hammond"},
        )
        is None
    )


@pytest.mark.parametrize(
    "status,outcome",
    [
        ("unsupported", "referral"),
        ("unreadable", "unresolved"),
        ("ambiguous", "unresolved"),
        ("conflicting", "unresolved"),
        ("provider_failure", "referral"),
    ],
)
def test_unsafe_evidence_fails_with_explicit_limitation(corpus, status, outcome):
    result = decide(corpus, "What policy applies?", evidence_status=status)
    assert result["outcome"] == outcome
    assert result["limitation"].strip()
    assert result["officeName"].strip()
    assert result["contactUrl"].startswith("https://www.pnw.edu/")
    assert "answer" not in result


@pytest.mark.parametrize(
    "question,office",
    [
        ("Am I eligible for financial aid?", "PNW Financial Aid"),
        ("Have I been assigned housing?", "PNW Housing"),
        ("What is the status of my disciplinary case?", "Dean of Students"),
        ("Can you enroll me in CS 50000?", "PNW Registrar"),
    ],
)
def test_personal_referral_uses_relevant_office(corpus, question, office):
    assert decide(corpus, question)["officeName"] == office


def test_retired_contact_is_not_reused(corpus):
    contacts = [
        dict(
            topic="financial_aid",
            office="Retired office",
            campus="all",
            active=False,
            contact_url="https://www.pnw.edu/financial-aid/",
        )
    ]
    result = decide(corpus, "Am I eligible for financial aid?", contacts=contacts)
    assert result["officeName"] == "Dean of Students"


def test_campus_specific_contact_wins(corpus):
    contacts = [
        dict(
            topic="housing",
            office="Campus office",
            campus="hammond",
            active=True,
            contact_url="https://www.pnw.edu/housing/",
        ),
        dict(
            topic="housing",
            office="General office",
            campus="all",
            active=True,
            contact_url="https://www.pnw.edu/housing/",
        ),
    ]
    result = decide(
        corpus, "Have I been assigned housing?", contacts=contacts, context={"campus": "hammond"}
    )
    assert result["officeName"] == "Campus office"


@pytest.mark.parametrize(
    "question",
    [
        "I want to die.",
        "I don't want to live.",
        "My roommate is threatening to kill me.",
        "Someone is not breathing.",
    ],
)
def test_additional_urgent_indicators(corpus, question):
    assert decide(corpus, question, contacts=[])["outcome"] == "emergency"


def test_general_policy_question_is_not_an_individual_decision(corpus):
    assert decide(corpus, "What are the financial aid application rules?") is None
    assert decide(corpus, "Where can I find the fire safety policy?") is None


def test_unknown_evidence_fails_closed(corpus):
    assert (
        decide(corpus, "What policy applies?", evidence_status="unexpected")["outcome"]
        == "referral"
    )


def test_one_focused_context_question(corpus):
    result = decide(
        corpus, "What policy applies?", required_context=["campus", "program", "campus"]
    )
    assert result["requiredFields"] == ["campus"]


def test_all_emitted_results_follow_schema(corpus):
    from pydantic import TypeAdapter

    from app.api.schemas.chat import ChatResponse

    for question, state in [
        ("I want to die", "available"),
        ("Am I enrolled?", "available"),
        ("What policy applies?", "unsupported"),
        ("What policy applies?", "conflicting"),
    ]:
        TypeAdapter(ChatResponse).validate_python(decide(corpus, question, evidence_status=state))
