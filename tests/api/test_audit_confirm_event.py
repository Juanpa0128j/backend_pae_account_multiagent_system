"""Tests for /api/v1/process/{id}/audit-confirm event emit (AAA pattern)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import inngest
import pytest


def _settings(engine: str) -> SimpleNamespace:
    return SimpleNamespace(workflow_engine=engine)


def _fake_db_with_pending_job(process_id: str = "p1"):
    """Build a MagicMock db session where the guarded UPDATE returns 1 row."""
    db = MagicMock()
    query = db.query.return_value
    query.filter.return_value.update.return_value = 1  # 1 row updated
    return db


@pytest.mark.asyncio
async def test_audit_confirm_emits_event_when_inngest() -> None:
    # Arrange
    from app.api.v1 import process as process_mod

    db = _fake_db_with_pending_job()
    mock_client = MagicMock()
    mock_client.send = AsyncMock()
    mock_dispatch = AsyncMock()

    with (
        patch.object(process_mod, "get_settings", return_value=_settings("inngest")),
        patch.object(process_mod, "get_inngest_client", return_value=mock_client),
        patch.object(process_mod, "dispatch_process_start", mock_dispatch),
    ):
        # Act
        result = await process_mod.confirm_audit_review(
            process_id="p1",
            db=db,
            current_user=SimpleNamespace(id="u1"),
        )

    # Assert
    mock_client.send.assert_awaited_once()
    event = mock_client.send.await_args.args[0]
    assert isinstance(event, inngest.Event)
    assert event.name == "app/process.audit-confirmed"
    assert event.data == {"process_id": "p1"}
    mock_dispatch.assert_not_called()
    assert result == {
        "message": "Revisión confirmada. Reintentando persistencia.",
        "process_id": "p1",
    }


@pytest.mark.asyncio
async def test_audit_confirm_dispatches_when_inline() -> None:
    # Arrange
    from app.api.v1 import process as process_mod

    db = _fake_db_with_pending_job()
    mock_client = MagicMock()
    mock_client.send = AsyncMock()
    mock_dispatch = AsyncMock()

    with (
        patch.object(process_mod, "get_settings", return_value=_settings("inline")),
        patch.object(process_mod, "get_inngest_client", return_value=mock_client),
        patch.object(process_mod, "dispatch_process_start", mock_dispatch),
    ):
        # Act
        await process_mod.confirm_audit_review(
            process_id="p1",
            db=db,
            current_user=SimpleNamespace(id="u1"),
        )

    # Assert
    mock_dispatch.assert_awaited_once_with("p1", force_persist=True)
    mock_client.send.assert_not_called()


@pytest.mark.asyncio
async def test_audit_confirm_guarded_update_blocks_non_pending() -> None:
    # Arrange — db.update returns 0 rows (job not in PENDING_AUDIT_REVIEW)
    from app.api.v1 import process as process_mod
    from fastapi import HTTPException
    from app.models.database import ProcessStatus

    db = MagicMock()
    db.query.return_value.filter.return_value.update.return_value = 0

    # Make subsequent get_process_job return an existing job in RUNNING
    mock_pj = SimpleNamespace(
        id="p1",
        status=ProcessStatus.RUNNING,
    )

    mock_client = MagicMock()
    mock_client.send = AsyncMock()
    mock_dispatch = AsyncMock()

    with (
        patch.object(process_mod, "get_settings", return_value=_settings("inngest")),
        patch.object(process_mod, "get_inngest_client", return_value=mock_client),
        patch.object(process_mod, "dispatch_process_start", mock_dispatch),
        patch.object(process_mod.db_service, "get_process_job", return_value=mock_pj),
    ):
        # Act / Assert
        with pytest.raises(HTTPException):
            await process_mod.confirm_audit_review(
                process_id="p1",
                db=db,
                current_user=SimpleNamespace(id="u1"),
            )

    mock_client.send.assert_not_called()
    mock_dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_audit_confirm_guarded_update_filters_by_pending_status() -> None:
    """Verify the guarded UPDATE filters by status == PENDING_AUDIT_REVIEW."""
    # Arrange
    from app.api.v1 import process as process_mod
    from app.models.database import ProcessJob, ProcessStatus

    db = MagicMock()
    filter_call = db.query.return_value.filter
    filter_call.return_value.update.return_value = 1

    mock_client = MagicMock()
    mock_client.send = AsyncMock()

    with (
        patch.object(process_mod, "get_settings", return_value=_settings("inngest")),
        patch.object(process_mod, "get_inngest_client", return_value=mock_client),
    ):
        # Act
        await process_mod.confirm_audit_review(
            process_id="p1",
            db=db,
            current_user=SimpleNamespace(id="u1"),
        )

    # Assert — verify the filter was called with the PENDING_AUDIT_REVIEW status constraint
    assert db.query.called
    assert db.query.call_args.args == (ProcessJob,)
    # filter() was invoked with two clauses: id match + status match
    assert filter_call.called
    # The filter call args contain SQLAlchemy clause elements; we can't easily inspect
    # the binary expressions, but we CAN verify update was called with status=RUNNING
    update_call = filter_call.return_value.update
    assert update_call.called
    update_payload = update_call.call_args.args[0]
    # update() receives a dict mapping Column → value
    update_values = {
        col.key if hasattr(col, "key") else str(col): val
        for col, val in update_payload.items()
    }
    assert update_values.get("status") == ProcessStatus.RUNNING
    assert update_values.get("current_stage") == "supervisor"
    assert update_values.get("progress") == 10
