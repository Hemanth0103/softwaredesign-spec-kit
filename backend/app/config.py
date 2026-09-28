"""Validated runtime environment; no file reads, network calls or import-time secrets."""

import json
import os
from collections.abc import Mapping
from ipaddress import ip_network
from typing import Annotated, Self
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

NonEmpty = Annotated[str, Field(min_length=1)]
Timeout = Annotated[float, Field(gt=0, allow_inf_nan=False)]


class ConfigurationError(ValueError):
    """Invalid configuration, with environment names but never supplied values."""


class Settings(BaseModel):
    """Call load_settings at startup and pass the result to application components."""

    model_config = ConfigDict(str_strip_whitespace=True, hide_input_in_errors=True)

    database_url: SecretStr
    database_timeout_seconds: Timeout = 2.0
    ai_provider: NonEmpty
    ai_api_key: SecretStr
    ai_generation_model: NonEmpty
    ai_embedding_model: NonEmpty
    embedding_version: NonEmpty
    embedding_dimension: Annotated[int, Field(gt=0)]
    chunk_size: Annotated[int, Field(gt=0)] = 800
    chunk_overlap: Annotated[int, Field(ge=0)] = 100
    ai_timeout_seconds: Timeout = 6.0
    source_timeout_seconds: Timeout = 10.0
    oidc_timeout_seconds: Timeout = 3.0
    oidc_issuer: NonEmpty
    oidc_jwks_url: str | None = None
    oidc_roles_claim: NonEmpty = "roles"
    oidc_office_roles: dict[str, str] = Field(default_factory=dict)
    oidc_audience: NonEmpty

    @field_validator("oidc_jwks_url")
    @classmethod
    def validate_jwks(cls, value: str | None) -> str | None:
        if value is None:
            return None
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.fragment
            or url.query
        ):
            raise ValueError("JWKS requires an HTTPS URL without credentials or query")
        return value

    @field_validator("oidc_office_roles")
    @classmethod
    def validate_roles(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not role.strip() or not office.strip() for role, office in value.items()):
            raise ValueError("Role and office names must be non-empty")
        return value

    cors_origins: list[str] = Field(default_factory=list)
    trusted_proxy_networks: list[str] = Field(default_factory=list)
    require_https: bool = False
    rate_limit_requests: Annotated[int, Field(gt=0)] = 120
    rate_limit_window_seconds: Timeout = 60.0

    @field_validator("trusted_proxy_networks")
    @classmethod
    def validate_proxy_networks(cls, values: list[str]) -> list[str]:
        for value in values:
            network = ip_network(value)
            if network.prefixlen == 0:
                raise ValueError("Trust must be limited to explicit proxy addresses or subnets")
        return values

    @field_validator("database_url")
    @classmethod
    def validate_database(cls, value: SecretStr) -> SecretStr:
        url = urlsplit(value.get_secret_value())
        if url.scheme != "postgresql+psycopg" or not url.hostname or not url.path.strip("/"):
            raise ValueError("requires a PostgreSQL psycopg URL with host and database")
        return value

    @field_validator("ai_api_key")
    @classmethod
    def validate_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("requires a non-empty key")
        return value

    @field_validator("oidc_issuer")
    @classmethod
    def validate_issuer(cls, value: str) -> str:
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("requires an HTTPS issuer without credentials, query or fragment")
        return value

    @field_validator("cors_origins")
    @classmethod
    def validate_origins(cls, values: list[str]) -> list[str]:
        for value in values:
            url = urlsplit(value)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or "*" in value
                or url.username
                or url.password
                or url.path
                or url.query
                or url.fragment
                or any(c.isspace() for c in value)
            ):
                raise ValueError("requires exact HTTP(S) origins without paths or wildcards")
            # Also reject malformed ports before middleware consumes the origin.
            _ = url.port
        return values

    @model_validator(mode="after")
    def validate_chunking(self) -> Self:
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")
        return self


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Read uppercase field names from the environment; CORS_ORIGINS is a JSON array.

    Deployment tooling must inject variables (including secrets) at runtime. This
    deliberately does not read .env automatically or cache values across callers.
    """
    source = os.environ if environ is None else environ
    values: dict[str, object] = {
        name: source[name.upper()] for name in Settings.model_fields if name.upper() in source
    }
    for field in ("cors_origins", "trusted_proxy_networks", "oidc_office_roles"):
        if field in values:
            try:
                values[field] = json.loads(source[field.upper()])
            except ValueError:
                raise ConfigurationError("Invalid configuration: " + field.upper()) from None
    try:
        return Settings.model_validate(values)
    except ValidationError as error:
        fields = sorted(
            {
                str(item["loc"][0]).upper() if item["loc"] else "CHUNK_SIZE/CHUNK_OVERLAP"
                for item in error.errors(include_input=False, include_context=False)
            }
        )
        raise ConfigurationError("Invalid configuration: " + ", ".join(fields)) from None
