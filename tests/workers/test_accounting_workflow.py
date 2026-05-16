"""
TDD tests for app/workers/accounting_workflow.py — call task function directly,
bypassing Hatchet runtime entirely.
"""

import sys
from unittest.mock import MagicMock, patch
import pytest

from app.models.database import ProcessStatus

# ---------------------------------------------------------------------------
# Patch get_hatchet BEFORE accounting_workflow is imported so the module-level
# `hatchet = get_hatchet()` call never hits real ClientConfig validation.
# ---------------------------------------------------------------------------
_mock_hatchet = MagicMock()
_mock_workflow = MagicMock()
_mock_hatchet.workflow.return_value = _mock_workflow


# Make @_mock_workflow.task(...) return a pass-through decorator
def _passthrough_task_decorator(**kwargs):
    def decorator(fn):
        return fn

    return decorator


_mock_workflow.task.side_effect = _passthrough_task_decorator

_hatchet_client_mod = MagicMock()
_hatchet_client_mod.get_hatchet.return_value = _mock_hatchet
sys.modules.setdefault("app.workers.hatchet_client", _hatchet_client_mod)

# Now safe to import the workflow module
import app.workers.accounting_workflow  # noqa: E402,F401

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_input(
    process_id="test-process-id",
    ingest_id="test-ingest-id",
    raw_transactions=None,
    pending_transaction_id="tx-1",
    doc_type="factura",
    force_persist=False,
):
    """Return a dict-like input mimicking Hatchet workflow input."""
    return {
        "process_id": process_id,
        "ingest_id": ingest_id,
        "raw_transactions": raw_transactions or [],
        "pending_transaction_id": pending_transaction_id,
        "doc_type": doc_type,
        "force_persist": force_persist,
    }


def _make_db_mocks():
    """Return (mock_db, mock_session_cm) for patching SessionLocal."""
    mock_db = MagicMock()
    mock_session_cm = MagicMock()
    mock_session_cm.__enter__ = MagicMock(return_value=mock_db)
    mock_session_cm.__exit__ = MagicMock(return_value=False)
    return mock_db, mock_session_cm


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRunPipeline:
    def _import_task(self):
        """Import run_pipeline after all patches are in place."""
        # Re-import to pick up fresh module state
        import importlib
        import app.workers.accounting_workflow as mod

        importlib.reload(mod)
        return mod.run_pipeline

    @patch("app.workers.accounting_workflow.db_service")
    @patch("app.workers.accounting_workflow.SessionLocal")
    @patch("app.workers.accounting_workflow.invoke_accounting_pipeline")
    @patch("app.workers.accounting_workflow.get_hatchet")
    def test_run_pipeline_completes_successfully(
        self, mock_get_hatchet, mock_invoke, mock_session_local, mock_db_svc
    ):
        mock_get_hatchet.return_value = MagicMock()
        mock_invoke.return_value = {"result": {"status": "completed"}}

        mock_db, mock_cm = _make_db_mocks()
        mock_session_local.return_value = mock_cm

        mock_process_job = MagicMock()
        mock_process_job.ingest_id = "test-ingest-id"
        mock_db_svc.get_process_job.return_value = mock_process_job
        mock_db_svc.get_ingest_job.return_value = MagicMock()

        from app.workers.accounting_workflow import run_pipeline

        result = run_pipeline(_make_input(), ctx=MagicMock())

        assert result == {"status": "completed", "process_id": "test-process-id"}

        # find the update_process_job call with COMPLETED
        calls = mock_db_svc.update_process_job.call_args_list
        completed_call = next(
            (c for c in calls if c.kwargs.get("status") == ProcessStatus.COMPLETED),
            None,
        )
        assert completed_call is not None, (
            "Expected update_process_job(status=COMPLETED)"
        )
        assert completed_call.kwargs.get("progress") == 100

    @patch("app.workers.accounting_workflow.db_service")
    @patch("app.workers.accounting_workflow.SessionLocal")
    @patch("app.workers.accounting_workflow.invoke_accounting_pipeline")
    @patch("app.workers.accounting_workflow.get_hatchet")
    def test_run_pipeline_pending_audit_review(
        self, mock_get_hatchet, mock_invoke, mock_session_local, mock_db_svc
    ):
        mock_get_hatchet.return_value = MagicMock()
        mock_invoke.return_value = {"result": {"status": "pending_audit_review"}}

        mock_db, mock_cm = _make_db_mocks()
        mock_session_local.return_value = mock_cm

        mock_process_job = MagicMock()
        mock_process_job.ingest_id = "test-ingest-id"
        mock_db_svc.get_process_job.return_value = mock_process_job
        mock_db_svc.get_ingest_job.return_value = MagicMock()

        from app.workers.accounting_workflow import run_pipeline

        result = run_pipeline(_make_input(), ctx=MagicMock())

        assert result == {
            "status": "pending_audit_review",
            "process_id": "test-process-id",
        }

        calls = mock_db_svc.update_process_job.call_args_list
        audit_call = next(
            (
                c
                for c in calls
                if c.kwargs.get("status") == ProcessStatus.PENDING_AUDIT_REVIEW
            ),
            None,
        )
        assert audit_call is not None, (
            "Expected update_process_job(status=PENDING_AUDIT_REVIEW)"
        )
        assert audit_call.kwargs.get("progress") == 80

    @patch("app.workers.accounting_workflow.db_service")
    @patch("app.workers.accounting_workflow.SessionLocal")
    @patch("app.workers.accounting_workflow.invoke_accounting_pipeline")
    @patch("app.workers.accounting_workflow.get_hatchet")
    def test_run_pipeline_failed(
        self, mock_get_hatchet, mock_invoke, mock_session_local, mock_db_svc
    ):
        mock_get_hatchet.return_value = MagicMock()
        mock_invoke.return_value = {
            "result": {"status": "error"},
            "error": "PUC not found",
        }

        mock_db, mock_cm = _make_db_mocks()
        mock_session_local.return_value = mock_cm

        mock_process_job = MagicMock()
        mock_process_job.ingest_id = "test-ingest-id"
        mock_db_svc.get_process_job.return_value = mock_process_job
        mock_db_svc.get_ingest_job.return_value = MagicMock()

        from app.workers.accounting_workflow import run_pipeline

        with pytest.raises(Exception):
            run_pipeline(_make_input(), ctx=MagicMock())

        calls = mock_db_svc.update_process_job.call_args_list
        failed_call = next(
            (c for c in calls if c.kwargs.get("status") == ProcessStatus.FAILED),
            None,
        )
        assert failed_call is not None, "Expected update_process_job(status=FAILED)"

    @patch("app.workers.accounting_workflow.db_service")
    @patch("app.workers.accounting_workflow.SessionLocal")
    @patch("app.workers.accounting_workflow.invoke_accounting_pipeline")
    @patch("app.workers.accounting_workflow.get_hatchet")
    def test_run_pipeline_pipeline_exception(
        self, mock_get_hatchet, mock_invoke, mock_session_local, mock_db_svc
    ):
        mock_get_hatchet.return_value = MagicMock()
        mock_invoke.side_effect = TimeoutError("LLM timeout")

        mock_db, mock_cm = _make_db_mocks()
        mock_session_local.return_value = mock_cm

        mock_process_job = MagicMock()
        mock_process_job.ingest_id = "test-ingest-id"
        mock_db_svc.get_process_job.return_value = mock_process_job
        mock_db_svc.get_ingest_job.return_value = MagicMock()

        from app.workers.accounting_workflow import run_pipeline

        with pytest.raises(TimeoutError):
            run_pipeline(_make_input(), ctx=MagicMock())

        calls = mock_db_svc.update_process_job.call_args_list
        failed_call = next(
            (c for c in calls if c.kwargs.get("status") == ProcessStatus.FAILED),
            None,
        )
        assert failed_call is not None, "Expected update_process_job(status=FAILED)"
