"""Tests for app.workflows.dispatch — flag-aware routing (AAA pattern)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import inngest
import pytest

from app.workflows import dispatch


def _settings(engine: str) -> SimpleNamespace:
    return SimpleNamespace(workflow_engine=engine)


@pytest.mark.asyncio
async def test_inline_calls_jobs_start_process_job() -> None:
    # Arrange
    mock_start = AsyncMock()
    mock_client = MagicMock()
    mock_client.send = AsyncMock()
    with (
        patch.object(dispatch, "get_settings", return_value=_settings("inline")),
        patch.object(dispatch.jobs, "start_process_job", mock_start),
        patch.object(dispatch, "get_inngest_client", return_value=mock_client),
    ):
        # Act
        await dispatch.dispatch_process_start("proc-1")

    # Assert
    mock_start.assert_awaited_once_with("proc-1", force_persist=False)
    mock_client.send.assert_not_called()


@pytest.mark.asyncio
async def test_inline_propagates_force_persist() -> None:
    # Arrange
    mock_start = AsyncMock()
    with (
        patch.object(dispatch, "get_settings", return_value=_settings("inline")),
        patch.object(dispatch.jobs, "start_process_job", mock_start),
    ):
        # Act
        await dispatch.dispatch_process_start("proc-1", force_persist=True)

    # Assert
    mock_start.assert_awaited_once_with("proc-1", force_persist=True)


@pytest.mark.asyncio
async def test_inngest_sends_event() -> None:
    # Arrange
    mock_start = AsyncMock()
    mock_client = MagicMock()
    mock_client.send = AsyncMock()
    with (
        patch.object(dispatch, "get_settings", return_value=_settings("inngest")),
        patch.object(dispatch.jobs, "start_process_job", mock_start),
        patch.object(dispatch, "get_inngest_client", return_value=mock_client),
    ):
        # Act
        await dispatch.dispatch_process_start("proc-1")

    # Assert
    mock_start.assert_not_called()
    mock_client.send.assert_awaited_once()
    event = mock_client.send.call_args.args[0]
    assert isinstance(event, inngest.Event)
    assert event.name == "app/process.start"
    assert event.data == {"process_id": "proc-1", "force_persist": False}


@pytest.mark.asyncio
async def test_inngest_propagates_force_persist() -> None:
    # Arrange
    mock_client = MagicMock()
    mock_client.send = AsyncMock()
    with (
        patch.object(dispatch, "get_settings", return_value=_settings("inngest")),
        patch.object(dispatch, "get_inngest_client", return_value=mock_client),
    ):
        # Act
        await dispatch.dispatch_process_start("proc-1", force_persist=True)

    # Assert
    event = mock_client.send.call_args.args[0]
    assert isinstance(event, inngest.Event)
    assert event.data == {"process_id": "proc-1", "force_persist": True}


@pytest.mark.asyncio
async def test_unknown_engine_raises() -> None:
    # Arrange
    with patch.object(dispatch, "get_settings", return_value=_settings("garbage")):
        # Act / Assert
        with pytest.raises(ValueError, match="Unknown workflow_engine"):
            await dispatch.dispatch_process_start("proc-1")
