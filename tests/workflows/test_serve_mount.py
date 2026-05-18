"""
Unit tests for the Inngest FastAPI serve mount.

Validates that ``inngest.fast_api.serve`` is only mounted when
``settings.workflow_engine == "inngest"``. We simulate the mount logic
on a fresh FastAPI app instead of reimporting ``main`` because ``main``
is import-time side-effectful (it builds the app, mounts routers, and
makes DB calls).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI

from app.workflows.inngest_client import reset_inngest_client


@pytest.fixture(autouse=True)
def _reset_client():
    reset_inngest_client()
    yield
    reset_inngest_client()


def _mount_if_enabled(app: FastAPI, engine: str) -> None:
    """Mirror of the flag-gated block in main.py — kept in sync by test."""
    if engine == "inngest":
        import inngest.fast_api

        from app.workflows.functions.process_pipeline import process_pipeline
        from app.workflows.inngest_client import get_inngest_client

        inngest.fast_api.serve(app, get_inngest_client(), [process_pipeline])


def _has_inngest_route(app: FastAPI) -> bool:
    return any("/api/inngest" in getattr(r, "path", "") for r in app.routes)


def test_no_inngest_route_when_engine_inline():
    # Arrange
    app = FastAPI()

    # Act
    _mount_if_enabled(app, "inline")

    # Assert
    assert _has_inngest_route(app) is False


def test_inngest_route_mounted_when_engine_inngest():
    # Arrange
    app = FastAPI()

    # Act
    with patch("app.workflows.inngest_client.get_settings") as mock_get_settings:
        stub = type(
            "S",
            (),
            {
                "inngest_app_id": "pae-backend",
                "inngest_event_key": "",
                "inngest_signing_key": "",
                "inngest_dev": True,
                "is_production": False,
                "inngest_is_production": None,
            },
        )()
        mock_get_settings.return_value = stub
        _mount_if_enabled(app, "inngest")

    # Assert
    assert _has_inngest_route(app) is True
