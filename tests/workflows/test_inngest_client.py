"""
Unit tests for the Inngest client singleton.

The Inngest SDK is patched so tests never construct a real client or
touch the network. Each test runs against a fresh singleton via an
autouse reset fixture.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

import pytest

from app.workflows import inngest_client
from app.workflows.inngest_client import (
    get_inngest_client,
    reset_inngest_client,
)


def _make_settings(
    *,
    app_id: str = "pae-test",
    event_key: str = "",
    signing_key: str = "",
    inngest_dev: bool = True,
    app_env: str = "development",
    inngest_is_production: bool | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        inngest_app_id=app_id,
        inngest_event_key=event_key,
        inngest_signing_key=signing_key,
        inngest_dev=inngest_dev,
        app_env=app_env,
        is_production=(app_env == "production"),
        inngest_is_production=inngest_is_production,
    )


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_inngest_client()
    yield
    reset_inngest_client()


def test_returns_singleton_on_repeated_calls():
    with patch.object(inngest_client, "inngest") as mock_module:
        mock_module.Inngest.return_value = MagicMock(name="InngestInstance")
        first = get_inngest_client()
        second = get_inngest_client()

    assert first is second
    assert mock_module.Inngest.call_count == 1


def test_reset_clears_singleton():
    with patch.object(inngest_client, "inngest") as mock_module:
        mock_module.Inngest.side_effect = [
            MagicMock(name="first"),
            MagicMock(name="second"),
        ]
        first = get_inngest_client()
        reset_inngest_client()
        second = get_inngest_client()

    assert first is not second
    assert id(first) != id(second)


def test_init_passes_settings_to_sdk():
    stub = _make_settings(
        app_id="custom-app",
        event_key="ek123",
        signing_key="sk123",
        inngest_dev=False,
        app_env="production",
    )
    with (
        patch.object(inngest_client, "get_settings", return_value=stub),
        patch.object(inngest_client, "inngest") as mock_module,
    ):
        mock_module.Inngest.return_value = MagicMock()
        get_inngest_client()

    mock_module.Inngest.assert_called_once_with(
        app_id="custom-app",
        logger=ANY,
        event_key="ek123",
        signing_key="sk123",
        is_production=True,
    )


def test_empty_keys_passed_as_none():
    stub = _make_settings(
        app_id="pae-test",
        event_key="",
        signing_key="",
        inngest_dev=True,
        app_env="development",
    )
    with (
        patch.object(inngest_client, "get_settings", return_value=stub),
        patch.object(inngest_client, "inngest") as mock_module,
    ):
        mock_module.Inngest.return_value = MagicMock()
        get_inngest_client()

    kwargs = mock_module.Inngest.call_args.kwargs
    assert kwargs["event_key"] is None
    assert kwargs["signing_key"] is None


def test_dev_overrides_production():
    stub = _make_settings(
        app_id="pae-test",
        event_key="ek",
        signing_key="sk",
        inngest_dev=True,
        app_env="production",
    )
    with (
        patch.object(inngest_client, "get_settings", return_value=stub),
        patch.object(inngest_client, "inngest") as mock_module,
    ):
        mock_module.Inngest.return_value = MagicMock()
        get_inngest_client()

    kwargs = mock_module.Inngest.call_args.kwargs
    assert kwargs["is_production"] is False
