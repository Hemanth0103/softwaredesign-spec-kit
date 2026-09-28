"""Deterministic safety gates; no database, network, AI, logging or transcript storage.

Call before retrieval/generation and again with the evidence/provider result.
None permits the next stage; it never means an answer has been verified.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

from pydantic import TypeAdapter

from app.api.schemas.chat import ChatResponse

# Official office landing pages verified 2026-09-27. These contain no policy answers.
DEFAULT_CONTACTS: tuple[dict[str, Any], ...] = (
    dict(
        topic="registration",
        office="PNW Registrar",
        contact_url="https://www.pnw.edu/registrar/",
        campus="all",
        active=True,
    ),
    dict(
        topic="financial_aid",
        office="PNW Financial Aid",
        contact_url="https://www.pnw.edu/financial-aid/",
        campus="all",
        active=True,
    ),
    dict(
        topic="housing",
        office="PNW Housing",
        contact_url="https://www.pnw.edu/housing/",
        campus="all",
        active=True,
    ),
    dict(
        topic="general",
        office="Dean of Students",
        contact_url="https://www.pnw.edu/dean-of-students/",
        campus="all",
        active=True,
    ),
    dict(
        topic="emergency",
        office="PNW Public Safety",
        contact_url="https://www.pnw.edu/public-safety/",
        phone="911",
        campus="all",
        active=True,
    ),
    dict(
        topic="emergency",
        office="PNW Public Safety",
        contact_url="https://www.pnw.edu/public-safety/",
        phone="(219) 989-2222",
        campus="all",
        active=True,
    ),
)

DANGER = re.compile(
    r"\b(kill myself|end my life|hurt myself|harm myself|want to die|"
    r"don't want to live|do not want to live|kill me|suicidal|self[- ]harm|"
    r"overdos(?:e|ed|ing)|cannot breathe|can\'t breathe|not breathing|"
    r"active shooter|shots fired|being attacked|being assaulted|"
    r"(?:someone|he|she|they).{0,40}(?:gun|knife|threatening)|"
    r"(?:i am|i\'m|we are|we\'re).{0,20}(?:in danger|going to hurt|going to kill)|"
    r"(?:fire|smoke).{0,30}(?:building|dorm|room)|"
    r"(?:building|dorm|room).{0,20}(?:on fire|burning))\b",
    re.IGNORECASE,
)
PERSONAL = re.compile(r"\b(my|mine|am i|can i|do i|did i|have i|will i|i am|i'm|i have|i've)\b")
RECORD = re.compile(
    r"\b(eligib\w*|enroll\w*|degree|graduat\w*|financial aid|fafsa|scholarship|"
    r"disciplin\w*|housing|room assignment|registration|register\w*|hold|"
    r"transcript|grade\w*|gpa|student record|account balance)\b"
)
PROMPTS = {
    "campus": "Which campus does your question concern: Hammond or Westville?",
    "program": "Which academic program does your question concern?",
    "course": "Which course does your question concern?",
    "academicTerm": "Which academic term does your question concern?",
}
LIMITATIONS = {
    "unsupported": "I do not have approved information that supports a reliable answer.",
    "unreadable": "The available source cannot be read reliably, so I cannot confirm the policy.",
    "ambiguous": "The available information is ambiguous, so I cannot provide a reliable answer.",
    "conflicting": (
        "The approved sources conflict. Dean of Students review is needed before I can answer."
    ),
    "provider_failure": "The answer service is unavailable, so I cannot provide a verified answer.",
}


class DirectoryUnavailable(ValueError):
    """No safe referral is available; the API must return its generic safe service error."""


def _valid_contact(row: Mapping[str, Any], campus: str | None) -> bool:
    url = row.get("contact_url")
    if not isinstance(url, str):
        return False
    try:
        parsed = urlsplit(url)
        valid_url = (
            parsed.scheme == "https"
            and parsed.hostname is not None
            and (parsed.hostname == "pnw.edu" or parsed.hostname.endswith(".pnw.edu"))
            and not parsed.username
            and not parsed.password
            and not any(char.isspace() for char in url)
        )
        _ = parsed.port
    except ValueError:
        return False
    return bool(
        valid_url
        and row.get("active") is True
        and isinstance(row.get("office"), str)
        and row["office"].strip()
        and row.get("campus") in ({"all", campus} if campus else {"all"})
    )


def _contacts(
    topic: str, campus: str | None, directory: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    # Explicit directory entries override defaults, including inactive entries.
    candidates = [row for row in directory if row.get("topic") == topic]
    if not candidates:
        candidates = [row for row in DEFAULT_CONTACTS if row["topic"] == topic]
    valid = [row for row in candidates if _valid_contact(row, campus)]
    valid.sort(key=lambda row: row.get("campus") != campus)
    return [
        dict(
            officeName=row["office"],
            contactUrl=row["contact_url"],
            **{
                field: row[field]
                for field in ("phone", "email")
                if isinstance(row.get(field), str) and row[field].strip()
            },
        )
        for row in valid
    ]


def _outcome(payload: dict[str, Any]) -> dict[str, Any]:
    return TypeAdapter(ChatResponse).validate_python(payload).model_dump(exclude_none=True)


def evaluate_gates(
    *,
    question: str,
    context: Mapping[str, str | None],
    required_context: Sequence[str],
    evidence_status: str,
    contacts: Sequence[Mapping[str, Any]],
    emergency: bool = False,
    account_specific: bool = False,
    topic: str | None = None,
) -> dict[str, Any] | None:
    """Material context and evidence state come from trusted application code, not user flags.

    Text indicators are conservative English heuristics; callers may raise explicit
    safety/account flags. A false flag never overrides a detected safety indicator.
    """
    normalized = " ".join(question.casefold().replace("’", "'").split())
    campus = context.get("campus")
    if emergency or DANGER.search(normalized):
        emergency_contacts = _contacts("emergency", campus, contacts)
        # Emergency guidance must remain available even if the directory is broken.
        if not any(row.get("phone") == "911" for row in emergency_contacts):
            emergency_contacts.insert(
                0,
                dict(
                    officeName="Emergency services",
                    contactUrl="https://www.pnw.edu/public-safety/",
                    phone="911",
                ),
            )
        return _outcome(
            dict(
                outcome="emergency",
                guidance="If you or someone else is in immediate danger, call 911 now. "
                "Move to a safer place if you can and contact PNW Public Safety.",
                contacts=emergency_contacts,
            )
        )
    if topic is None:
        if re.search(r"financial aid|fafsa|scholarship|loan", normalized):
            topic = "financial_aid"
        elif re.search(r"housing|dorm|room assignment", normalized):
            topic = "housing"
        elif re.search(r"disciplin|conduct case", normalized):
            topic = "general"
        elif RECORD.search(normalized) or "registrar" in normalized:
            topic = "registration"
        else:
            topic = "general"
    personal = account_specific or bool(
        PERSONAL.search(normalized)
        and RECORD.search(normalized)
        or re.search(
            r"\b(?:enroll|register|withdraw|drop|change|remove|update).{0,25}\bme\b", normalized
        )
    )
    if personal:
        outcome, limitation = (
            "referral",
            (
                "I cannot access student records, change your account, "
                "or determine your individual "
                "eligibility or status. Please contact the office for help with your situation."
            ),
        )
    else:
        if any(field not in PROMPTS for field in required_context):
            raise ValueError("Unsupported material context field")
        missing = [
            field
            for field in dict.fromkeys(required_context)
            if not context.get(field)
            or not str(context[field]).strip()
            or field == "campus"
            and context[field] not in {"hammond", "westville"}
        ]
        if missing:
            field = missing[0]
            return _outcome(
                dict(outcome="needs_context", question=PROMPTS[field], requiredFields=[field])
            )
        if evidence_status == "available":
            return None
        outcome = (
            "unresolved"
            if evidence_status in {"unreadable", "ambiguous", "conflicting"}
            else "referral"
        )
        limitation = LIMITATIONS.get(
            evidence_status, "I cannot verify the available information safely."
        )
        if evidence_status == "conflicting":
            topic = "general"
    referrals = _contacts(topic, campus, contacts)
    if not referrals and topic != "general":
        referrals = _contacts("general", campus, contacts)
    if not referrals:
        raise DirectoryUnavailable("No active appropriate referral is available")
    referral = referrals[0]
    return _outcome(
        dict(
            outcome=outcome,
            limitation=limitation,
            officeName=referral["officeName"],
            contactUrl=referral["contactUrl"],
        )
    )
