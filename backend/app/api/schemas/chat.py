"""Chat contract shapes; validation does not establish grounding or source approval."""

from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

Campus = Literal["hammond", "westville"]
ContextField = Literal["campus", "program", "course", "academicTerm"]
NonEmpty = Annotated[str, Field(min_length=1, pattern=r"\S")]


def https_url(value: str) -> str:
    url = urlsplit(value)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        raise ValueError("An HTTPS URL without credentials is required")
    _ = url.port
    return value


HTTPSURL = Annotated[str, AfterValidator(https_url)]


class ContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, hide_input_in_errors=True, serialize_by_alias=True
    )


class QuestionContext(ContractModel):
    campus: Campus | None = None
    program: Annotated[str, Field(max_length=160)] | None = None
    course: Annotated[str, Field(max_length=32)] | None = None
    academic_term: Annotated[str, Field(max_length=80)] | None = Field(
        default=None, alias="academicTerm"
    )


class StudentQuestion(QuestionContext):
    question: Annotated[str, Field(min_length=1, max_length=4000)]


class Citation(ContractModel):
    title: NonEmpty
    url: HTTPSURL
    context_label: NonEmpty | None = Field(default=None, alias="contextLabel")


class Contact(ContractModel):
    office_name: NonEmpty = Field(alias="officeName")
    contact_url: HTTPSURL = Field(alias="contactUrl")
    phone: NonEmpty | None = None
    email: NonEmpty | None = None


class SupportedAnswer(ContractModel):
    outcome: Literal["answer"]
    answer: NonEmpty
    citations: Annotated[list[Citation], Field(min_length=1)]
    applied_context: QuestionContext = Field(alias="appliedContext")


class NeedsContext(ContractModel):
    outcome: Literal["needs_context"]
    question: NonEmpty
    required_fields: Annotated[list[ContextField], Field(min_length=1)] = Field(
        alias="requiredFields"
    )


class Referral(ContractModel):
    outcome: Literal["referral"]
    limitation: NonEmpty
    office_name: NonEmpty = Field(alias="officeName")
    contact_url: HTTPSURL = Field(alias="contactUrl")


class Unresolved(ContractModel):
    outcome: Literal["unresolved"]
    limitation: NonEmpty
    office_name: NonEmpty = Field(alias="officeName")
    contact_url: HTTPSURL = Field(alias="contactUrl")


class Emergency(ContractModel):
    outcome: Literal["emergency"]
    guidance: NonEmpty
    contacts: Annotated[list[Contact], Field(min_length=1)]


ChatResponse = Annotated[
    SupportedAnswer | NeedsContext | Referral | Unresolved | Emergency,
    Field(discriminator="outcome"),
]
