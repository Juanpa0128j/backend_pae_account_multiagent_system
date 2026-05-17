"""Inngest client singleton — lazy init from settings."""

from __future__ import annotations

import logging

import inngest

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_client: inngest.Inngest | None = None


def get_inngest_client() -> inngest.Inngest:
    """Return the process-wide Inngest client, initialising on first call."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = inngest.Inngest(
            app_id=settings.inngest_app_id,
            logger=logger,
            event_key=settings.inngest_event_key or None,
            signing_key=settings.inngest_signing_key or None,
            is_production=settings.is_production and not settings.inngest_dev,
        )
        logger.info("Inngest client initialised (app_id=%s)", settings.inngest_app_id)
    return _client


def reset_inngest_client() -> None:
    """Test-only — clears the singleton so the next call rebuilds it."""
    global _client
    _client = None
