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


def test_process_pipeline_has_concurrency_config() -> None:
    # Arrange / Act
    opts = mod.process_pipeline._opts

    # Assert
    assert opts.concurrency is not None
    assert len(opts.concurrency) >= 1
    assert opts.concurrency[0].key == "event.data.company_nit"
    assert opts.concurrency[0].limit >= 1


def test_process_pipeline_has_throttle_config() -> None:
    # Arrange / Act
    opts = mod.process_pipeline._opts

    # Assert
    assert opts.throttle is not None
    assert opts.throttle.key == '"openai"'
    assert opts.throttle.limit >= 1
    assert opts.throttle.period.total_seconds() == 60


def test_process_pipeline_has_singleton_config() -> None:
    # Arrange / Act
    opts = mod.process_pipeline._opts

    # Assert
    assert opts.singleton is not None
    assert opts.singleton.key == "event.data.process_id"
    assert opts.singleton.mode == "skip"


def test_trigger_event_name() -> None:
    # Arrange / Act
    triggers = mod.process_pipeline._triggers

    # Assert
    assert len(triggers) == 1
    assert triggers[0].event == "app/process.start"


async def _step_run_side_effect(step_id, handler):
    return await handler()


@pytest.mark.asyncio
async def test_no_hitl_path_returns_ok() -> None:
    # Arrange
    step = SimpleNamespace(
        run=AsyncMock(side_effect=_step_run_side_effect),
        wait_for_event=AsyncMock(),
    )
    ctx = SimpleNamespace(
        event=SimpleNamespace(
            data={"process_id": "11111111-1111-1111-1111-111111111111"}
        ),
        logger=MagicMock(),
        step=step,
    )

    mock_impl = AsyncMock()
    mock_get_pj = MagicMock(
        return_value=SimpleNamespace(status=SimpleNamespace(value="completed"))
    )

    with (
        patch("app.services.jobs._run_process_job_impl", mock_impl),
        patch("app.services.db_service.get_process_job", mock_get_pj),
        patch("app.core.database.SessionLocal"),
    ):
        # Act
        result = await mod._process_pipeline_handler(ctx)

    # Assert
    assert result == {"process_id": "11111111-1111-1111-1111-111111111111", "ok": True}
    step.wait_for_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_hitl_wait_then_confirm_invokes_force_persist() -> None:
    # Arrange
    step = SimpleNamespace(
        run=AsyncMock(side_effect=_step_run_side_effect),
        wait_for_event=AsyncMock(return_value=MagicMock()),  # event received
    )
    ctx = SimpleNamespace(
        event=SimpleNamespace(
            data={"process_id": "22222222-2222-2222-2222-222222222222"}
        ),
        logger=MagicMock(),
        step=step,
    )

    impl_calls = []

    async def _impl(pid, *, force_persist=False):
        impl_calls.append((pid, force_persist))

    pj_iter = iter(
        [
            SimpleNamespace(status=SimpleNamespace(value="pending_audit_review")),
        ]
    )
    mock_get_pj = MagicMock(side_effect=lambda *a, **k: next(pj_iter))

    with (
        patch("app.services.jobs._run_process_job_impl", _impl),
        patch("app.services.db_service.get_process_job", mock_get_pj),
        patch("app.core.database.SessionLocal"),
    ):
        # Act
        result = await mod._process_pipeline_handler(ctx)

    # Assert
    assert result == {"process_id": "22222222-2222-2222-2222-222222222222", "ok": True}
    assert impl_calls == [
        ("22222222-2222-2222-2222-222222222222", False),
        ("22222222-2222-2222-2222-222222222222", True),
    ]
    step.wait_for_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_hitl_timeout_marks_job_failed_with_spanish_copy() -> None:
    # Arrange
    step = SimpleNamespace(
        run=AsyncMock(side_effect=_step_run_side_effect),
        wait_for_event=AsyncMock(return_value=None),  # timeout
    )
    ctx = SimpleNamespace(
        event=SimpleNamespace(
            data={"process_id": "33333333-3333-3333-3333-333333333333"}
        ),
        logger=MagicMock(),
        step=step,
    )

    mock_impl = AsyncMock()
    mock_get_pj = MagicMock(
        return_value=SimpleNamespace(
            status=SimpleNamespace(value="pending_audit_review")
        )
    )
    mock_mark = MagicMock()

    with (
        patch("app.services.jobs._run_process_job_impl", mock_impl),
        patch("app.services.db_service.get_process_job", mock_get_pj),
        patch("app.core.database.SessionLocal"),
        patch("app.services.jobs._mark_job_failed_safe", mock_mark),
    ):
        # Act
        result = await mod._process_pipeline_handler(ctx)

    # Assert
    assert result == {
        "process_id": "33333333-3333-3333-3333-333333333333",
        "timeout": True,
    }
    mock_mark.assert_called_once()
    args, _ = mock_mark.call_args
    assert args[0] == "33333333-3333-3333-3333-333333333333"
    assert "1 hora" in args[1]
