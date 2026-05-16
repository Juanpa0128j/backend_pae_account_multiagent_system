"""
TDD tests for app/workers/ingest_workflow.py — call task function directly,
bypassing Hatchet runtime entirely.
"""

import sys
from unittest.mock import MagicMock, patch
import pytest

from app.models.database import IngestStatus

# ---------------------------------------------------------------------------
# Patch get_hatchet BEFORE ingest_workflow is imported so the module-level
# `hatchet = get_hatchet()` call never hits real ClientConfig validation.
# ---------------------------------------------------------------------------
_mock_hatchet = MagicMock()
_mock_workflow = MagicMock()
_mock_hatchet.workflow.return_value = _mock_workflow


def _passthrough_task_decorator(**kwargs):
    def decorator(fn):
        return fn

    return decorator


_mock_workflow.task.side_effect = _passthrough_task_decorator

_hatchet_client_mod = MagicMock()
_hatchet_client_mod.get_hatchet.return_value = _mock_hatchet
sys.modules.setdefault("app.workers.hatchet_client", _hatchet_client_mod)

# Now safe to import
import app.workers.ingest_workflow  # noqa: E402,F401

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_input(ingest_id: str = "test-ingest-id") -> dict:
    return {"ingest_id": ingest_id}


def _make_db_mocks():
    """Return (mock_db, mock_session_cm)."""
    mock_db = MagicMock()
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_db)
    mock_cm.__exit__ = MagicMock(return_value=False)
    return mock_db, mock_cm


def _make_ingest_job(
    ingest_id: str = "test-ingest-id",
    file_path: str = "/tmp/pae_uploads/doc.pdf",
    company_nit: str = "800999888",
    parser_mode: str = "fast",
    multi_file_mode: str = "pages",
):
    job = MagicMock()
    job.id = ingest_id
    job.file_path = file_path
    job.company_nit = company_nit
    job.parser_mode = parser_mode
    job.multi_file_mode = multi_file_mode
    return job


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRunIngest:
    @patch("app.workers.ingest_workflow.db_service")
    @patch("app.workers.ingest_workflow.SessionLocal")
    @patch("app.workers.ingest_workflow._run_ingest_pipeline")
    @patch("app.workers.ingest_workflow.get_hatchet")
    def test_run_ingest_completes_successfully(
        self, mock_get_hatchet, mock_run_pipeline, mock_session_local, mock_db_svc
    ):
        mock_get_hatchet.return_value = MagicMock()
        mock_run_pipeline.return_value = {"status": "completed"}

        mock_db, mock_cm = _make_db_mocks()
        mock_session_local.return_value = mock_cm

        mock_ingest_job = _make_ingest_job()
        mock_db_svc.get_ingest_job.return_value = mock_ingest_job

        from app.workers.ingest_workflow import run_ingest

        result = run_ingest(_make_input(), ctx=MagicMock())

        assert result["ingest_id"] == "test-ingest-id"

        # Should update status to COMPLETED
        calls = mock_db_svc.update_ingest_job.call_args_list
        completed_call = next(
            (c for c in calls if c.kwargs.get("status") == IngestStatus.COMPLETED),
            None,
        )
        assert completed_call is not None, (
            "Expected update_ingest_job(status=COMPLETED)"
        )

    @patch("app.workers.ingest_workflow.db_service")
    @patch("app.workers.ingest_workflow.SessionLocal")
    @patch("app.workers.ingest_workflow._run_ingest_pipeline")
    @patch("app.workers.ingest_workflow.get_hatchet")
    def test_run_ingest_failed(
        self, mock_get_hatchet, mock_run_pipeline, mock_session_local, mock_db_svc
    ):
        mock_get_hatchet.return_value = MagicMock()
        mock_run_pipeline.side_effect = RuntimeError("LlamaParse timeout")

        mock_db, mock_cm = _make_db_mocks()
        mock_session_local.return_value = mock_cm

        mock_ingest_job = _make_ingest_job()
        mock_db_svc.get_ingest_job.return_value = mock_ingest_job

        from app.workers.ingest_workflow import run_ingest

        with pytest.raises(RuntimeError):
            run_ingest(_make_input(), ctx=MagicMock())

        # Should update status to FAILED
        calls = mock_db_svc.update_ingest_job.call_args_list
        failed_call = next(
            (c for c in calls if c.kwargs.get("status") == IngestStatus.FAILED),
            None,
        )
        assert failed_call is not None, "Expected update_ingest_job(status=FAILED)"

    @patch("app.workers.ingest_workflow.db_service")
    @patch("app.workers.ingest_workflow.SessionLocal")
    @patch("app.workers.ingest_workflow._run_ingest_pipeline")
    @patch("app.workers.ingest_workflow.get_hatchet")
    def test_run_ingest_uses_ingest_id_from_input(
        self, mock_get_hatchet, mock_run_pipeline, mock_session_local, mock_db_svc
    ):
        mock_get_hatchet.return_value = MagicMock()
        mock_run_pipeline.return_value = {"status": "completed"}

        mock_db, mock_cm = _make_db_mocks()
        mock_session_local.return_value = mock_cm

        specific_id = "specific-ingest-xyz"
        mock_ingest_job = _make_ingest_job(ingest_id=specific_id)
        mock_db_svc.get_ingest_job.return_value = mock_ingest_job

        from app.workers.ingest_workflow import run_ingest

        run_ingest(_make_input(ingest_id=specific_id), ctx=MagicMock())

        # Assert get_ingest_job called with the specific id
        mock_db_svc.get_ingest_job.assert_called_once_with(
            mock_db, ingest_id=specific_id
        )
