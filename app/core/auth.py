import logging
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import ExpiredSignatureError, JWTError, jwt

from app.core.config import settings

logger = logging.getLogger(__name__)
_bearer = HTTPBearer()

_JWKS_CACHE: dict[str, Any] = {"keys": None, "fetched_at": 0.0}
_JWKS_TTL_SECONDS = 3600


@dataclass
class CurrentUser:
    id: UUID
    email: str


def _jwks_url() -> str | None:
    base = (settings.supabase_url or "").rstrip("/")
    if not base:
        return None
    return f"{base}/auth/v1/.well-known/jwks.json"


def _fetch_jwks() -> list[dict[str, Any]] | None:
    url = _jwks_url()
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
    return keys[0]


def _decode_token(token: str) -> dict[str, Any]:
    header = jwt.get_unverified_header(token)
    alg = header.get("alg", "")

    if alg.startswith("ES") or alg.startswith("RS"):
        keys = _fetch_jwks()
        jwk = _find_jwk(keys or [], header.get("kid"))
        if not jwk:
            raise JWTError("No matching JWK")
        return jwt.decode(
            token,
            jwk,
            algorithms=[alg],
            audience="authenticated",
        )

    secret = settings.supabase_jwt_secret
    if not secret:
        raise JWTError("HS256 secret not configured")
    return jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        audience="authenticated",
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
) -> CurrentUser:
    token = credentials.credentials
    try:
        payload = _decode_token(token)
        return CurrentUser(id=UUID(payload["sub"]), email=payload.get("email", ""))
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except (JWTError, KeyError, ValueError) as e:
        logger.warning("JWT verify failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
