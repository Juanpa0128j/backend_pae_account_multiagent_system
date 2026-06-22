import time

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from jose import jwt
from jose.constants import ALGORITHMS

from app.core import auth as auth_module

ISSUER = "https://example.clerk.accounts.dev"


@pytest.fixture
def rsa_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub_numbers = key.public_key().public_numbers()
    return key, priv_pem, pub_numbers


def _jwk_from_public(pub_numbers, kid="testkid"):
    import base64

    def b64(n: int) -> str:
        b = n.to_bytes((n.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": b64(pub_numbers.n),
        "e": b64(pub_numbers.e),
    }


def _make_token(priv_pem, claims, kid="testkid"):
    return jwt.encode(
        claims, priv_pem, algorithm=ALGORITHMS.RS256, headers={"kid": kid}
    )


@pytest.fixture
def patch_settings(monkeypatch):
    monkeypatch.setattr(auth_module.settings, "clerk_issuer", ISSUER, raising=False)
    monkeypatch.setattr(
        auth_module.settings,
        "clerk_jwks_url",
        f"{ISSUER}/.well-known/jwks.json",
        raising=False,
    )


def test_decode_valid_clerk_token(rsa_keypair, patch_settings, monkeypatch):
    _, priv_pem, pub_numbers = rsa_keypair
    monkeypatch.setattr(
        auth_module, "_fetch_jwks", lambda: [_jwk_from_public(pub_numbers)]
    )
    token = _make_token(
        priv_pem,
        {
            "sub": "user_2abc123",
            "email": "tester@example.com",
            "iss": ISSUER,
            "exp": int(time.time()) + 3600,
            "iat": int(time.time()),
        },
    )
    payload = auth_module._decode_token(token)
    assert payload["sub"] == "user_2abc123"
    assert payload["email"] == "tester@example.com"


def test_current_user_id_is_string(rsa_keypair, patch_settings, monkeypatch):
    _, priv_pem, pub_numbers = rsa_keypair
    monkeypatch.setattr(
        auth_module, "_fetch_jwks", lambda: [_jwk_from_public(pub_numbers)]
    )
    token = _make_token(
        priv_pem,
        {
            "sub": "user_2abc123",
            "email": "tester@example.com",
            "iss": ISSUER,
            "exp": int(time.time()) + 3600,
        },
    )
    payload = auth_module._decode_token(token)
    user = auth_module.CurrentUser(id=payload["sub"], email=payload.get("email", ""))
    assert isinstance(user.id, str)
    assert user.id == "user_2abc123"


def test_wrong_issuer_rejected(rsa_keypair, patch_settings, monkeypatch):
    from jose import JWTError

    _, priv_pem, pub_numbers = rsa_keypair
    monkeypatch.setattr(
        auth_module, "_fetch_jwks", lambda: [_jwk_from_public(pub_numbers)]
    )
    token = _make_token(
        priv_pem,
        {
            "sub": "user_x",
            "iss": "https://evil.example.com",
            "exp": int(time.time()) + 3600,
        },
    )
    with pytest.raises(JWTError):
        auth_module._decode_token(token)


# --- Attack-vector gate tests ------------------------------------------------


def test_alg_none_rejected(patch_settings, monkeypatch):
    """alg=none token must be rejected outright (not decoded)."""
    from jose import JWTError

    # Craft a token with alg=none — jose.jwt.encode does not support alg=none,
    # so we build the raw JWT manually.
    import base64
    import json

    def _b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    payload = _b64url(
        json.dumps(
            {"sub": "attacker", "iss": ISSUER, "exp": int(time.time()) + 3600}
        ).encode()
    )
    token = f"{header}.{payload}."  # unsigned

    monkeypatch.setattr(auth_module, "_fetch_jwks", lambda: [])

    with pytest.raises(JWTError):
        auth_module._decode_token(token)


def test_alg_hs256_rejected(patch_settings, monkeypatch):
    """HS256 token (symmetric secret) must be rejected — not in allowlist."""
    from jose import JWTError

    symmetric_secret = "supersecret"
    token = jwt.encode(
        {"sub": "attacker", "iss": ISSUER, "exp": int(time.time()) + 3600},
        symmetric_secret,
        algorithm="HS256",
    )

    monkeypatch.setattr(auth_module, "_fetch_jwks", lambda: [])

    with pytest.raises(JWTError):
        auth_module._decode_token(token)


def test_unknown_kid_rejected(rsa_keypair, patch_settings, monkeypatch):
    """Valid RS256 sig + kid not in JWKS → get_current_user raises 401, not 500."""
    import asyncio
    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials

    _, priv_pem, pub_numbers = rsa_keypair
    jwk_with_different_kid = _jwk_from_public(pub_numbers, kid="other-kid")
    monkeypatch.setattr(auth_module, "_fetch_jwks", lambda: [jwk_with_different_kid])

    # Token carries kid="testkid" but JWKS only has kid="other-kid"
    token = _make_token(
        priv_pem,
        {"sub": "user_x", "iss": ISSUER, "exp": int(time.time()) + 3600},
        kid="testkid",
    )
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.get_event_loop().run_until_complete(auth_module.get_current_user(creds))

    assert exc_info.value.status_code == 401


def test_jwks_unavailable_returns_401(patch_settings, monkeypatch):
    """_fetch_jwks returning None → get_current_user raises 401, not 500."""
    import asyncio
    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials
    from cryptography.hazmat.primitives.asymmetric import rsa as _rsa
    from cryptography.hazmat.primitives import serialization as _ser

    # Generate a throwaway key just to create a structurally valid RS256 token
    key = _rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = key.private_bytes(
        _ser.Encoding.PEM,
        _ser.PrivateFormat.PKCS8,
        _ser.NoEncryption(),
    ).decode()
    token = jwt.encode(
        {"sub": "user_x", "iss": ISSUER, "exp": int(time.time()) + 3600},
        priv_pem,
        algorithm=ALGORITHMS.RS256,
        headers={"kid": "somekid"},
    )

    monkeypatch.setattr(auth_module, "_fetch_jwks", lambda: None)

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.get_event_loop().run_until_complete(auth_module.get_current_user(creds))

    assert exc_info.value.status_code == 401
