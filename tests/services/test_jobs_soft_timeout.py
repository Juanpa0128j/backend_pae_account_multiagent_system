"""Tests for the soft-timeout branch in app/services/jobs._run_process_job_impl.

Covers PR #74 Copilot HIGH comment: when a job hits asyncio.TimeoutError after
some POSTED transactions persist, the handler must mark remaining PENDING txs
as ERROR. Otherwise they get stranded because COMPLETED is treated as "active"
by get_active_process_job_for_ingest and no follow-up ProcessJob is created.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from app.models.database import TransactionStatus


def _make_tx(tx_id: str, status: TransactionStatus) -> MagicMock:
    tx = MagicMock()
    tx.id = tx_id
    tx.status = status
    return tx


@pytest.mark.asyncio
async def test_soft_timeout_marks_pending_txs_as_error():
    """Soft-timeout path (POSTED > 0) must ERROR remaining PENDING txs."""
    txs = [
        _make_tx("tx-posted", TransactionStatus.POSTED),
        _make_tx("tx-processing", TransactionStatus.PROCESSING),
        _make_tx("tx-pending-1", TransactionStatus.PENDING),
        _make_tx("tx-pending-2", TransactionStatus.PENDING),
    ]

    process_job = MagicMock()
    process_job.id = "proc_test"
    process_job.ingest_id = "ing_test"

    ingest_job = MagicMock()
    ingest_job.id = "ing_test"
    ingest_job.document_type = "extracto_bancario"
    ingest_job.company_nit = "901016386"
    ingest_job.raw_preview = None
    ingest_job.parser_mode = "premium"

    pending_tx = MagicMock()
    pending_tx.id = "tx-pending-1"
    pending_tx.items = []

    with (
        patch("app.services.jobs.SessionLocal") as mock_session_cls,
        patch("app.services.jobs.db_service") as mock_db,
        patch("app.services.jobs._mark_pending_failed_safe") as mock_mark_pending,
        patch(
            "app.services.jobs._mark_processing_transactions_failed_safe"
        ) as mock_mark_proc,
        patch("app.services.jobs.invoke_accounting_pipeline"),
        patch("app.services.jobs.asyncio.wait_for", side_effect=asyncio.TimeoutError),
    ):
        mock_db.get_process_job.return_value = process_job
        mock_db.get_ingest_job.return_value = ingest_job
        mock_db.get_transactions_by_ingest.return_value = txs
        mock_db.get_or_create_pending_tx.return_value = pending_tx
        mock_session_cls.return_value = MagicMock()

        from app.services.jobs import _run_process_job_impl

        await _run_process_job_impl("proc_test", force_persist=False)

        marked_ids = {call.args[0] for call in mock_mark_pending.call_args_list}
        assert "tx-pending-1" in marked_ids
        assert "tx-pending-2" in marked_ids

        mock_mark_proc.assert_called()

        from app.models.database import ProcessStatus

        update_call = next(
            c
            for c in mock_db.update_process_job.call_args_list
            if c.kwargs.get("current_stage") == "completed"
        )
        assert update_call.kwargs["status"] == ProcessStatus.COMPLETED


@pytest.mark.asyncio
async def test_hard_timeout_no_posted_uses_failed_path():
    """When zero POSTED txs exist on timeout, fall through to the FAILED path."""
    txs = [_make_tx("tx-pending-only", TransactionStatus.PENDING)]

    process_job = MagicMock()
    process_job.id = "proc_test"
    process_job.ingest_id = "ing_test"

    ingest_job = MagicMock()
    ingest_job.id = "ing_test"
    ingest_job.document_type = "factura_compra"
    ingest_job.company_nit = "901016386"
    ingest_job.raw_preview = None
    ingest_job.parser_mode = "premium"

    pending_tx = MagicMock()
    pending_tx.id = "tx-pending-only"
    pending_tx.items = []

    with (
        patch("app.services.jobs.SessionLocal") as mock_session_cls,
        patch("app.services.jobs.db_service") as mock_db,
        patch("app.services.jobs._mark_pending_failed_safe"),
        patch("app.services.jobs._mark_processing_transactions_failed_safe"),
        patch("app.services.jobs.invoke_accounting_pipeline"),
        patch("app.services.jobs.asyncio.wait_for", side_effect=asyncio.TimeoutError),
    ):
        mock_db.get_process_job.return_value = process_job
        mock_db.get_ingest_job.return_value = ingest_job
        mock_db.get_transactions_by_ingest.return_value = txs
        mock_db.get_or_create_pending_tx.return_value = pending_tx
        mock_session_cls.return_value = MagicMock()

        from app.services.jobs import _run_process_job_impl

        await _run_process_job_impl("proc_test", force_persist=False)

        from app.models.database import ProcessStatus

        terminal_updates = [
            c
            for c in mock_db.update_process_job.call_args_list
            if c.kwargs.get("status") in (ProcessStatus.FAILED, ProcessStatus.COMPLETED)
        ]
        assert terminal_updates, "expected at least one terminal status update"
        assert terminal_updates[-1].kwargs["status"] == ProcessStatus.FAILED
