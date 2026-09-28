"""Deployment-configured access-token verification; never log tokens or claims."""

import json
from dataclasses import dataclass

import httpx
import jwt
from fastapi import HTTPException, Request

from app.config import Settings


class AuthenticationError(ValueError):
    pass


@dataclass(frozen=True)
class Reviewer:
    subject: str
    offices: frozenset[str]


class OIDCValidator:
    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.transport = transport

    def validate(self, token: str) -> Reviewer:
        try:
            if not self.settings.oidc_jwks_url or len(token) > 16384:
                raise ValueError()
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                raise ValueError()
            # Fetch only the configured URL, never token-supplied jku/x5u locations.
            # No long-lived key cache: key rotation/revocation is observed on each call.
            with httpx.Client(
                timeout=self.settings.oidc_timeout_seconds,
                follow_redirects=False,
                transport=self.transport,
                trust_env=False,
            ) as client:
                with client.stream("GET", self.settings.oidc_jwks_url) as response:
                    response.raise_for_status()
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > 262144:
                            raise ValueError()
            keys = json.loads(data)["keys"]
            matches = [
                key
                for key in keys
                if key.get("kid") == header["kid"]
                and key.get("kty") == "RSA"
                and key.get("use", "sig") == "sig"
                and key.get("alg", "RS256") == "RS256"
                and "verify" in key.get("key_ops", ["verify"])
            ]
            if len(matches) != 1:
                raise ValueError()
            key = jwt.PyJWK.from_dict(matches[0], algorithm="RS256")
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=self.settings.oidc_issuer,
                audience=self.settings.oidc_audience,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
            subject = claims["sub"]
            roles = claims.get(self.settings.oidc_roles_claim, [])
            if not isinstance(subject, str) or not subject.strip():
                raise ValueError()
            if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
                raise ValueError()
            return Reviewer(
                subject,
                frozenset(
                    self.settings.oidc_office_roles[role]
                    for role in roles
                    if role in self.settings.oidc_office_roles
                ),
            )
        except (ValueError, TypeError, KeyError, AttributeError, httpx.HTTPError, jwt.PyJWTError):
            raise AuthenticationError("Invalid reviewer credentials") from None


def require_reviewer(request: Request) -> Reviewer:
    """Synchronous FastAPI dependency, run in its thread pool on future reviewer routes."""
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Authentication required")
    try:
        reviewer = OIDCValidator(request.app.state.settings).validate(token)
    except AuthenticationError:
        raise HTTPException(401, "Authentication required") from None
    if not reviewer.offices:
        raise HTTPException(403, "Access denied")
    return reviewer
