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
