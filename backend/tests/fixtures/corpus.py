"""Validated offline corpus. Approval metadata is exclusively local/test authority."""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

SOURCES = Path(__file__).parent / "sources"


class FixtureModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Document(FixtureModel):
    id: str
    path: str
    canonical_url: str
    title: str
    owner_office: str
    subject: str
    media_type: str
    sha256: str
    retrieved_at: datetime
    provenance: Literal["official_download", "synthetic_test_document"]


class Case(FixtureModel):
    id: str
    document_id: str
    approval: Literal["draft", "approved", "rejected"]
    lifecycle: Literal["active", "superseded", "retired"]
    revision_status: Literal["pending_review", "approved", "active", "superseded", "retired"]
    campus: Literal["hammond", "westville", "all"]
    program: str | None = None
    course: str | None = None
    academic_term: str | None = None
    effective_from: datetime | None = None
    effective_until: datetime | None = None
    conflict_group: str | None = None
    readable: bool = True
    expected_eligible: bool
    reviewer_subject: str | None = None
    approval_reason: str | None = None
    approval_scope: Literal["local_test_only"] = "local_test_only"


class Contact(FixtureModel):
    office: str
    topic: str
    campus: Literal["hammond", "westville", "all"]
    contact_url: str
    phone: str | None = None
    email: str | None = None
    evidence_document_id: str
    active: bool = True


class Corpus(FixtureModel):
    schema_version: Literal[1]
    usage: Literal["local_test_only"]
    as_of: datetime
    documents: list[Document] = Field(min_length=1)
    cases: list[Case] = Field(min_length=1)
    contacts: list[Contact] = Field(min_length=1)


def load_corpus(*, environment: str, manifest: Path = SOURCES / "manifest.json") -> Corpus:
    """Read fresh data without network or database side effects; fail closed on tampering."""
    if environment not in {"local", "test"}:
        raise ValueError("Fixture approval is allowed only in local/test environments")
    corpus = Corpus.model_validate(json.loads(manifest.read_text()))
    root = manifest.parent.resolve()
    documents = {doc.id: doc for doc in corpus.documents}
    if len(documents) != len(corpus.documents) or len({c.id for c in corpus.cases}) != len(
        corpus.cases
    ):
        raise ValueError("Duplicate fixture identifiers")
    for doc in corpus.documents:
        path = (root / doc.path).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("Snapshot path must stay within the fixture directory")
        if hashlib.sha256(path.read_bytes()).hexdigest() != doc.sha256:
            raise ValueError("Snapshot hash mismatch")
        url = urlsplit(doc.canonical_url)
        if url.scheme != "https" or url.hostname != "www.pnw.edu":
            raise ValueError("Fixture provenance requires an official PNW HTTPS URL")
        if doc.retrieved_at.utcoffset() is None:
            raise ValueError("Snapshot timestamps must be timezone-aware")
    if corpus.as_of.utcoffset() is None:
        raise ValueError("Corpus clock must be timezone-aware")
    for case in corpus.cases:
        if case.document_id not in documents:
            raise ValueError("Unknown case document")
        if case.approval == "approved" and (not case.reviewer_subject or not case.approval_reason):
            raise ValueError("Test approval requires reviewer provenance and reason")
        for date in (case.effective_from, case.effective_until):
            if date is not None and date.utcoffset() is None:
                raise ValueError("Scope timestamps must be timezone-aware")
    for contact in corpus.contacts:
        if contact.evidence_document_id not in documents:
            raise ValueError("Unknown contact evidence")
        evidence = documents[contact.evidence_document_id]
        if (
            evidence.provenance != "official_download"
            or contact.contact_url != evidence.canonical_url
        ):
            raise ValueError("Contacts require official snapshot provenance")
    return corpus
