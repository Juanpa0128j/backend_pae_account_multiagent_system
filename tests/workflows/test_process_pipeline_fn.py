"""Tests for app.workflows.functions.process_pipeline (AAA pattern)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workflows.functions import process_pipeline as mod


def test_fn_id_is_process_pipeline() -> None:
    # Arrange / Act
    fn = mod.process_pipeline

    # Assert
    assert fn.local_id == "process-pipeline"


def test_trigger_event_name() -> None:
    # Arrange / Act
    triggers = mod.process_pipeline._triggers

    # Assert
    assert len(triggers) == 1
    assert triggers[0].event == "app/process.start"


async def _step_run_side_effect(step_id, handler):
    return await handler()


@pytest.mark.asyncio
async def test_handler_invokes_pipeline_impl_via_step() -> None:
    # Arrange
    mock_impl = AsyncMock()
    step = SimpleNamespace(
        run=AsyncMock(side_effect=_step_run_side_effect),
    )
    ctx = SimpleNamespace(
        event=SimpleNamespace(
            data={"process_id": "p1", "force_persist": True},
        ),
        logger=MagicMock(),
        step=step,
    )
    with patch(
        "app.services.jobs._run_process_job_impl",
        mock_impl,
    ):
        # Act
        result = await mod._process_pipeline_handler(ctx)

    # Assert
    mock_impl.assert_awaited_once_with("p1", force_persist=True)
    assert result == {"process_id": "p1", "ok": True}
    step.run.assert_awaited_once()
    assert step.run.await_args.args[0] == "run-process-job"


@pytest.mark.asyncio
async def test_handler_default_force_persist_false() -> None:
    # Arrange
    mock_impl = AsyncMock()
    step = SimpleNamespace(
        run=AsyncMock(side_effect=_step_run_side_effect),
    )
    ctx = SimpleNamespace(
        event=SimpleNamespace(data={"process_id": "p2"}),
        logger=MagicMock(),
        step=step,
    )
    with patch(
        "app.services.jobs._run_process_job_impl",
        mock_impl,
    ):
        # Act
        await mod._process_pipeline_handler(ctx)

    # Assert
    mock_impl.assert_awaited_once_with("p2", force_persist=False)
