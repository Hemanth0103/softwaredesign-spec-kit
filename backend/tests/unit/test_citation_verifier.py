"""T029 fail-closed draft validation, using a substituted final database read."""

from dataclasses import replace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.services.citation_verifier import CitationVerificationError, verify_answer
from app.services.retrieval import RetrievalResult, RetrievedChunk


@pytest.fixture
def evidence():
    return RetrievedChunk(
        uuid4(),
        uuid4(),
        uuid4(),
        "Display the permit while parked.",
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


@pytest.fixture
def final_read(monkeypatch):
    read = Mock(return_value=True)
    monkeypatch.setattr("app.services.citation_verifier._fresh_evidence_matches", read)
    return read


def draft(evidence):
    return {"answer": evidence.text, "citations": [{"chunkId": str(evidence.chunk_id)}]}


def check(raw, evidence, **kwargs):
    return verify_answer(
        Mock(), draft=raw, retrieval=RetrievalResult((evidence,)), context={}, **kwargs
    )


def test_internal_draft_builds_canonical_public_response(evidence, final_read):
    result = check(draft(evidence), evidence)
    assert result.answer == evidence.text
    assert result.citations[0].url == evidence.url
    assert result.citations[0].title == evidence.title
    final_read.assert_called_once()


def test_public_draft_and_whitespace_are_supported(evidence, final_read):
    raw = {
        "outcome": "answer",
        "answer": "Display  the permit\nwhile parked.",
        "citations": [{"title": evidence.title, "url": evidence.url}],
        "appliedContext": {},
    }
    assert check(raw, evidence).outcome == "answer"


@pytest.mark.parametrize(
    "change",
    [
        {"answer": "Parking fines are waived."},
        {"answer": "Display the permit while parked. Fines are waived."},
        {"answer": "Display the permit."},
        {"answer": ""},
        {"citations": []},
        {"unknown": True},
        {"citations": [{"title": "Wrong", "url": "https://www.pnw.edu/parking/"}]},
        {"citations": [{"title": "Parking", "url": "https://www.pnw.edu/invented/"}]},
        {"citations": [{"title": "Parking", "url": "http://www.pnw.edu/parking/"}]},
        {"citations": [{"chunkId": "not-a-uuid"}]},
        {"appliedContext": {"campus": "westville"}},
    ],
)
def test_bad_drafts_fail_without_echoing_content(evidence, final_read, change):
    with pytest.raises(CitationVerificationError, match="^Answer verification unavailable\\.$"):
        check(draft(evidence) | change, evidence)


def test_irrelevant_citation_cannot_validate_claim(evidence, final_read):
    other = replace(evidence, chunk_id=uuid4(), text="Library books may be renewed.")
    with pytest.raises(CitationVerificationError):
        verify_answer(
            Mock(), draft=draft(evidence), retrieval=RetrievalResult((other,)), context={}
        )


def test_conflicts_and_changed_eligibility_fail(evidence, final_read):
    with pytest.raises(CitationVerificationError):
        verify_answer(
            Mock(),
            draft=draft(evidence),
            retrieval=RetrievalResult((evidence,), (uuid4(),)),
            context={},
        )
    final_read.return_value = False
    with pytest.raises(CitationVerificationError):
        check(draft(evidence), evidence)


def test_database_failure_is_private(evidence, final_read):
    final_read.side_effect = RuntimeError("private database details")
    with pytest.raises(CitationVerificationError, match="^Answer verification unavailable\\.$"):
        check(draft(evidence), evidence)


def test_negation_and_conditions_cannot_be_removed(evidence, final_read):
    for text in [
        "Do not display the permit while parked.",
        "Display the permit while parked. Only in designated areas.",
    ]:
        with pytest.raises(CitationVerificationError):
            check(draft(evidence), replace(evidence, text=text))


def test_multiple_claims_require_support_from_each_cited_excerpt(evidence, final_read):
    other = replace(
        evidence,
        chunk_id=uuid4(),
        revision_id=uuid4(),
        text="The deadline is September 30, 2026.",
        title="Calendar",
        canonical_url="https://www.pnw.edu/calendar/",
    )
    raw = {
        "answer": evidence.text + "\n" + other.text,
        "citations": [*draft(evidence)["citations"], *draft(other)["citations"]],
    }
    result = verify_answer(
        Mock(),
        draft=raw,
        retrieval=RetrievalResult((evidence, other)),
        context={"campus": "hammond", "academicTerm": "Fall 2026"},
    )
    assert len(result.citations) == 2
    assert result.applied_context.campus == "hammond"
    raw["answer"] = evidence.text + " The deadline is September 30, 2025."
    with pytest.raises(CitationVerificationError):
        verify_answer(Mock(), draft=raw, retrieval=RetrievalResult((evidence, other)), context={})
