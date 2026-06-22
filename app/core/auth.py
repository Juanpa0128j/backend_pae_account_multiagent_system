import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import ExpiredSignatureError, JWTError, jwt

from app.core.config import settings

logger = logging.getLogger(__name__)
_bearer = HTTPBearer()

_JWKS_CACHE: dict[str, Any] = {"keys": None, "fetched_at": 0.0}
_JWKS_TTL_SECONDS = 3600
_ALLOWED_ALGORITHMS = {"RS256", "RS384", "RS512"}


@dataclass
class CurrentUser:
    id: str
    email: str


def _fetch_jwks() -> list[dict[str, Any]] | None:
    url = settings.clerk_jwks_url
    if not url:
        return None
    now = time.time()
    if _JWKS_CACHE["keys"] and now - _JWKS_CACHE["fetched_at"] < _JWKS_TTL_SECONDS:
        return _JWKS_CACHE["keys"]
    try:
        resp = httpx.get(url, timeout=5.0)
        resp.raise_for_status()
        keys = resp.json().get("keys", [])
        _JWKS_CACHE["keys"] = keys
        _JWKS_CACHE["fetched_at"] = now
        return keys
    except Exception as e:
        logger.error("JWKS fetch failed: %s", e)
        return _JWKS_CACHE["keys"]


def _find_jwk(keys: list[dict[str, Any]], kid: str | None) -> dict[str, Any] | None:
    if not keys:
        return None
    if kid:
        for k in keys:
            if k.get("kid") == kid:
                return k
        raise JWTError(f"No JWK for kid={kid!r}")
    return keys[0]


def _decode_token(token: str) -> dict[str, Any]:
    header = jwt.get_unverified_header(token)
    alg = str(header.get("alg") or "")
    if alg not in _ALLOWED_ALGORITHMS:
        raise JWTError(f"Unsupported alg: {alg!r}")

    keys = _fetch_jwks()
    if not keys:
        raise JWTError("JWKS unavailable")
    jwk = _find_jwk(keys, header.get("kid"))
    if not jwk:
        raise JWTError("No matching JWK")

    return jwt.decode(
        token,
        jwk,
        algorithms=sorted(_ALLOWED_ALGORITHMS),
        issuer=settings.clerk_issuer,
        options={"verify_aud": False},
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
) -> CurrentUser:
    token = credentials.credentials
    try:
        payload = _decode_token(token)
        return CurrentUser(id=payload["sub"], email=payload.get("email", ""))
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except (JWTError, KeyError, ValueError, IndexError) as e:
        logger.warning("JWT verify failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
