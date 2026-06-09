"""Shared rate limiter instance for slowapi integration."""

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

limiter = Limiter(key_func=get_remote_address)


def rate_limit(*args, **kwargs):
    """Conditional rate-limit decorator.

    In production the underlying ``@limiter.limit()`` from slowapi is
    applied. In development / test the decorator is a no-op so the
    function signature stays unchanged and tests that call endpoints
    directly (bypassing FastAPI's request injection) are not broken.
    """

    def decorator(func):
        if settings.app_env == "production":
            return limiter.limit(*args, **kwargs)(func)
        return func

    return decorator
