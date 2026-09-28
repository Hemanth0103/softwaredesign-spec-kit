import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth import AuthenticationError, OIDCValidator
from app.config import load_settings


@pytest.fixture
def identity(test_environment):
    settings = load_settings(test_environment).model_copy(
        update={
            "oidc_jwks_url": "https://identity.example.test/keys",
            "oidc_office_roles": {"registrar-role": "Registrar"},
        }
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="test-key", use="sig", alg="RS256")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"keys": [jwk]}))
    validator = OIDCValidator(settings, transport=transport)
    claims = dict(
        iss=settings.oidc_issuer,
        aud=settings.oidc_audience,
        sub="reviewer",
        iat=int(time.time()),
        exp=int(time.time()) + 300,
        roles=["registrar-role"],
    )
    return validator, key, claims


def token(key, claims):
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})


def test_verified_roles(identity):
    validator, key, claims = identity
    reviewer = validator.validate(token(key, claims))
    assert reviewer.subject == "reviewer"
    assert reviewer.offices == frozenset({"Registrar"})


@pytest.mark.parametrize(
    "change",
    [
        {"iss": "https://evil.example"},
        {"aud": "other"},
        {"exp": 0},
        {"nbf": 9999999999},
        {"sub": ""},
        {"roles": "registrar-role"},
    ],
)
def test_invalid_claims_rejected(identity, change):
    validator, key, claims = identity
    claims.update(change)
    with pytest.raises(AuthenticationError):
        validator.validate(token(key, claims))


def test_invalid_signature_and_algorithm(identity):
    validator, _, claims = identity
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    for encoded in [token(other, claims), jwt.encode(claims, "x" * 32, algorithm="HS256")]:
        with pytest.raises(AuthenticationError):
            validator.validate(encoded)


def test_missing_expiration_and_unknown_role(identity):
    validator, key, claims = identity
    claims["roles"] = ["unmapped"]
    assert validator.validate(token(key, claims)).offices == frozenset()
    del claims["exp"]
    with pytest.raises(AuthenticationError):
        validator.validate(token(key, claims))


def test_jwks_failure_is_safe(identity):
    validator, key, claims = identity
    validator.transport = httpx.MockTransport(lambda request: httpx.Response(503))
    with pytest.raises(AuthenticationError, match="Invalid reviewer credentials") as error:
        validator.validate(token(key, claims))
    assert "reviewer" not in str(error.value).replace("reviewer credentials", "")


def test_jwks_rotation_is_observed(identity):
    validator, key, claims = identity
    assert validator.validate(token(key, claims)).subject == "reviewer"
    replacement = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(replacement.public_key()))
    jwk.update(kid="test-key", use="sig", alg="RS256")

    def response(request):
        assert str(request.url) == "https://identity.example.test/keys"
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"keys": [jwk]})

    validator.transport = httpx.MockTransport(response)
    assert validator.validate(token(replacement, claims)).subject == "reviewer"
    with pytest.raises(AuthenticationError):
        validator.validate(token(key, claims))
