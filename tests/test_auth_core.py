"""Tests for JWT verification dependency."""

import time
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt

TEST_JWT_SECRET = "test-secret-32-chars-minimum-padded"
TEST_ALGORITHM = "HS256"
TEST_USER_ID = str(uuid4())
TEST_USER_EMAIL = "test@example.com"


def make_token(
    sub: str = TEST_USER_ID,
    email: str = TEST_USER_EMAIL,
    aud: str = "authenticated",
    exp_offset: int = 3600,
) -> str:
    payload = {
        "sub": sub,
        "email": email,
        "aud": aud,
        "exp": int(time.time()) + exp_offset,
        "iat": int(time.time()),
    }
    return jwt.encode(payload, TEST_JWT_SECRET, algorithm=TEST_ALGORITHM)


def make_credentials(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


@pytest.mark.asyncio
async def test_valid_token_returns_current_user(monkeypatch):
    """Valid JWT returns CurrentUser with correct id and email."""
    from app.core.auth import get_current_user
    from app.core import config as cfg

    monkeypatch.setattr(cfg.settings, "supabase_jwt_secret", TEST_JWT_SECRET)

    token = make_token()
    user = await get_current_user(make_credentials(token))

    assert user.id == UUID(TEST_USER_ID)
    assert user.email == TEST_USER_EMAIL


@pytest.mark.asyncio
async def test_expired_token_raises_401(monkeypatch):
    """Expired JWT raises HTTP 401."""
    from app.core.auth import get_current_user
    from app.core import config as cfg

    monkeypatch.setattr(cfg.settings, "supabase_jwt_secret", TEST_JWT_SECRET)

    token = make_token(exp_offset=-1)
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(make_credentials(token))

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_malformed_token_raises_401(monkeypatch):
    """Garbage token raises HTTP 401."""
    from app.core.auth import get_current_user
    from app.core import config as cfg

    monkeypatch.setattr(cfg.settings, "supabase_jwt_secret", TEST_JWT_SECRET)

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(make_credentials("not.a.valid.token"))

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_wrong_audience_raises_401(monkeypatch):
    """Token with wrong audience raises HTTP 401."""
    from app.core.auth import get_current_user
    from app.core import config as cfg

    monkeypatch.setattr(cfg.settings, "supabase_jwt_secret", TEST_JWT_SECRET)

    token = make_token(aud="wrong-audience")
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(make_credentials(token))

    assert exc_info.value.status_code == 401
