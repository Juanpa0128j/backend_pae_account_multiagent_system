"""Tests for app.workflows.functions.ingest_pipeline (AAA pattern)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workflows.functions import ingest_pipeline as mod


def test_fn_id_is_ingest_pipeline() -> None:
    # Arrange / Act
    fn = mod.ingest_pipeline

    # Assert
    assert fn.local_id == "ingest-pipeline"


def test_trigger_event_name() -> None:
    # Arrange / Act
    triggers = mod.ingest_pipeline._triggers

    # Assert
    assert len(triggers) == 1
    assert triggers[0].event == "app/ingest.start"


def test_ingest_pipeline_has_concurrency_config() -> None:
    # Arrange / Act
    opts = mod.ingest_pipeline._opts

    # Assert
    assert opts.concurrency is not None
    assert len(opts.concurrency) >= 1
    assert opts.concurrency[0].key == "event.data.company_nit"
    assert opts.concurrency[0].limit >= 1


async def _step_run_side_effect(step_id, handler):
    return await handler()


@pytest.mark.asyncio
async def test_handler_invokes_pipeline_impl_via_step() -> None:
    # Arrange
    mock_run = MagicMock()
    step = SimpleNamespace(run=AsyncMock(side_effect=_step_run_side_effect))
    ctx = SimpleNamespace(
        event=SimpleNamespace(
            data={
                "ingest_id": "ing-1",
                "temp_file_paths": ["/tmp/a.pdf"],
                "company_nit": "900123",
                "parser_mode": "fast",
                "multi_file_mode": "pages",
            },
        ),
        logger=MagicMock(),
        step=step,
    )
    with patch("app.api.v1.ingest._run_ingest_pipeline", mock_run):
        # Act
        result = await mod._ingest_pipeline_handler(ctx)

    # Assert
    mock_run.assert_called_once_with(
        ["/tmp/a.pdf"],
        "ing-1",
        "900123",
        "fast",
        "pages",
    )
    assert result == {"ingest_id": "ing-1", "ok": True}
    step.run.assert_awaited_once()
    assert step.run.await_args.args[0] == "run-ingest-job"


@pytest.mark.asyncio
async def test_handler_default_multi_file_mode_pages() -> None:
    # Arrange
    mock_run = MagicMock()
    step = SimpleNamespace(run=AsyncMock(side_effect=_step_run_side_effect))
    ctx = SimpleNamespace(
        event=SimpleNamespace(
            data={
                "ingest_id": "ing-2",
                "temp_file_paths": ["/tmp/b.pdf"],
                "company_nit": "900123",
                "parser_mode": "fast",
            },
        ),
        logger=MagicMock(),
        step=step,
    )
    with patch("app.api.v1.ingest._run_ingest_pipeline", mock_run):
        # Act
        await mod._ingest_pipeline_handler(ctx)

    # Assert
    mock_run.assert_called_once_with(
        ["/tmp/b.pdf"],
        "ing-2",
        "900123",
        "fast",
        "pages",
    )


@pytest.mark.asyncio
async def test_handler_skips_terminal_job() -> None:
    # Arrange: job already in terminal state
    from app.models.database import IngestStatus

    step = SimpleNamespace(run=AsyncMock(side_effect=_step_run_side_effect))
    mock_job = MagicMock()
    mock_job.status = IngestStatus.COMPLETED
    ctx = SimpleNamespace(
        event=SimpleNamespace(
            data={
                "ingest_id": "ing-terminal",
                "temp_file_paths": ["/tmp/done.pdf"],
                "company_nit": "900123",
                "parser_mode": "fast",
                "multi_file_mode": "pages",
            },
        ),
        logger=MagicMock(),
        step=step,
    )

    # Act: mock db_service to return terminal job
    with (
        patch("app.core.database.SessionLocal") as mock_session_local,
        patch("app.api.v1.ingest._run_ingest_pipeline") as mock_run,
    ):
        mock_db = MagicMock()
        mock_session_local.return_value = mock_db
        with patch("app.services.db_service.get_ingest_job", return_value=mock_job):
            result = await mod._ingest_pipeline_handler(ctx)

    # Assert: pipeline not invoked, returns success no-op
    assert result == {"ingest_id": "ing-terminal", "ok": True}
    mock_run.assert_not_called()
    ctx.logger.warning.assert_called_once()
    args = ctx.logger.warning.call_args[0]
    assert "duplicate event for terminal job" in args[0].lower()
