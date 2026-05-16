"""TDD tests for Hatchet feature-flag dispatch in jobs, ingest, and process endpoints."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from main import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_process_job(process_id="proc-test-001", ingest_id="ingest-test-001"):
    job = MagicMock()
    job.id = process_id
    job.ingest_id = ingest_id
    job.status = MagicMock()
    job.status.value = "pending_audit_review"
    return job


def _make_ingest_job(ingest_id="ingest-test-001"):
    job = MagicMock()
    job.id = ingest_id
    job.file_name = "test.pdf"
    job.file_path = "/tmp/fake.pdf"
    job.status = MagicMock()
    job.status.value = "pending_processing"
    job.document_type = "factura"
    job.doc_type = "factura"
    job.company_nit = "800999888"
    job.pathway = "via_a"
    job.parser_mode = "fast"
    job.classification_confidence = None
    job.classification_confirmed = True
    job.file_names = None
    job.multi_file_mode = None
    job.current_file_index = None
    job.created_at = datetime.now(timezone.utc)
    job.completed_at = None
    job.extraction_errors = []
    job.transactions_pending = []
    return job


def _mock_hatchet():
    hatchet = MagicMock()
    hatchet.event = MagicMock()
    hatchet.event.push = MagicMock(return_value=MagicMock())
    return hatchet


def _mock_settings(hatchet_enabled: bool):
    s = MagicMock()
    s.hatchet_enabled = hatchet_enabled
    return s


# ---------------------------------------------------------------------------
# TestJobsHatchetDispatch
# ---------------------------------------------------------------------------


class TestJobsHatchetDispatch:
    """Unit tests for start_process_job Hatchet feature flag."""

    def test_start_process_job_dispatches_hatchet_when_flag_on(self):
        """When hatchet_enabled=True, start_process_job pushes accounting:start event."""
        mock_hatchet = _mock_hatchet()
        mock_process_job = _make_process_job()
        mock_ingest_job = _make_ingest_job()

        mock_db = MagicMock()
        ctx_manager = MagicMock()
        ctx_manager.__enter__ = MagicMock(return_value=mock_db)
        ctx_manager.__exit__ = MagicMock(return_value=False)

        with (
            patch("app.services.jobs.get_settings", return_value=_mock_settings(True)),
            patch("app.workers.hatchet_client.get_hatchet", return_value=mock_hatchet),
            patch(
                "app.services.jobs.db_service.get_process_job",
                return_value=mock_process_job,
            ),
            patch(
                "app.services.jobs.db_service.get_ingest_job",
                return_value=mock_ingest_job,
            ),
            patch("app.services.jobs.SessionLocal", return_value=ctx_manager),
        ):
            from app.services.jobs import start_process_job

            asyncio.run(start_process_job("proc-test-001"))

        mock_hatchet.event.push.assert_called_once()
        call_args = mock_hatchet.event.push.call_args
        assert call_args[0][0] == "accounting:start"
        payload = call_args[0][1]
        assert payload["process_id"] == "proc-test-001"

    def test_start_process_job_uses_old_path_when_flag_off(self):
        """When hatchet_enabled=False, start_process_job creates asyncio task (old path)."""
        mock_hatchet = _mock_hatchet()

        with (
            patch("app.services.jobs.get_settings", return_value=_mock_settings(False)),
            patch("app.services.jobs.asyncio.create_task") as mock_create_task,
        ):
            from app.services.jobs import start_process_job

            mock_create_task.return_value = MagicMock()
            asyncio.run(start_process_job("proc-test-001"))

        mock_create_task.assert_called_once()
        mock_hatchet.event.push.assert_not_called()


# ---------------------------------------------------------------------------
# TestAuditConfirmHatchet
# ---------------------------------------------------------------------------


class TestAuditConfirmHatchet:
    """Integration tests for /audit-confirm endpoint Hatchet dispatch."""

    def _setup_db_override(self, mock_db):
        """Replace the get_db dependency with a mock returning mock_db."""
        from app.core.database import get_db

        app.dependency_overrides[get_db] = lambda: mock_db

    def _teardown_db_override(self):
        from app.core.database import get_db

        app.dependency_overrides.pop(get_db, None)

    def test_audit_confirm_pushes_event_when_flag_on(self):
        """When hatchet_enabled=True, audit-confirm endpoint pushes audit-confirm event."""
        mock_hatchet = _mock_hatchet()
        process_id = "proc-audit-001"

        # DB mock: rows_updated=1 means the guarded UPDATE succeeded
        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_filter = MagicMock()
        mock_filter.update.return_value = 1
        mock_query.filter.return_value = mock_filter
        mock_db.query.return_value = mock_query

        self._setup_db_override(mock_db)
        try:
            with (
                patch(
                    "app.api.v1.process.get_settings",
                    return_value=_mock_settings(True),
                ),
                patch(
                    "app.workers.hatchet_client.get_hatchet",
                    return_value=mock_hatchet,
                ),
            ):
                client = TestClient(app)
                response = client.post(f"/api/v1/process/{process_id}/audit-confirm")
        finally:
            self._teardown_db_override()

        assert response.status_code == 202
        mock_hatchet.event.push.assert_called_once()
        call_args = mock_hatchet.event.push.call_args
        assert call_args[0][0] == "audit-confirm"
        payload = call_args[0][1]
        assert payload["process_id"] == process_id
        assert payload["force_persist"] is True

    def test_audit_confirm_uses_old_path_when_flag_off(self):
        """When hatchet_enabled=False, audit-confirm calls jobs.start_process_job."""
        mock_hatchet = _mock_hatchet()
        process_id = "proc-audit-002"

        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_filter = MagicMock()
        mock_filter.update.return_value = 1
        mock_query.filter.return_value = mock_filter
        mock_db.query.return_value = mock_query

        self._setup_db_override(mock_db)
        try:
            with (
                patch(
                    "app.api.v1.process.get_settings",
                    return_value=_mock_settings(False),
                ),
                patch(
                    "app.api.v1.process.jobs.start_process_job",
                    new_callable=AsyncMock,
                ) as mock_start,
            ):
                client = TestClient(app)
                response = client.post(f"/api/v1/process/{process_id}/audit-confirm")
        finally:
            self._teardown_db_override()

        assert response.status_code == 202
        mock_start.assert_called_once_with(process_id, force_persist=True)
        mock_hatchet.event.push.assert_not_called()


# ---------------------------------------------------------------------------
# TestIngestHatchetDispatch
# ---------------------------------------------------------------------------


class TestIngestHatchetDispatch:
    """Integration tests for /ingest/upload endpoint Hatchet dispatch."""

    def _make_ingest_db_mock(self, ingest_job):
        mock_db = MagicMock()
        return mock_db

    def test_ingest_upload_dispatches_hatchet_when_flag_on(self, monkeypatch):
        """When hatchet_enabled=True, upload endpoint dispatches ingest:start to Hatchet."""
        mock_hatchet = _mock_hatchet()
        mock_job = _make_ingest_job()

        monkeypatch.setattr(
            "app.api.v1.ingest.db_service.create_ingest_job",
            lambda db, fn, fp, **kw: mock_job,
        )
        monkeypatch.setattr(
            "app.api.v1.ingest.db_service.get_ingest_job",
            lambda db, iid: mock_job,
        )
        monkeypatch.setattr(
            "app.api.v1.ingest.save_temp_file",
            lambda content, name: "/tmp/fake.pdf",
        )
        monkeypatch.setattr(
            "app.api.v1.ingest.get_settings",
            lambda: _mock_settings(True),
        )

        with patch(
            "app.workers.hatchet_client.get_hatchet",
            return_value=mock_hatchet,
        ):
            client = TestClient(app)
            response = client.post(
                "/api/v1/ingest/upload",
                files=[("files", ("test.pdf", b"%PDF-1.4 fake", "application/pdf"))],
            )

        assert response.status_code == 202
        mock_hatchet.event.push.assert_called_once()
        call_args = mock_hatchet.event.push.call_args
        assert call_args[0][0] == "ingest:start"
        payload = call_args[0][1]
        assert "ingest_id" in payload

    def test_ingest_upload_uses_background_tasks_when_flag_off(self, monkeypatch):
        """When hatchet_enabled=False, upload endpoint uses background_tasks (old path)."""
        mock_hatchet = _mock_hatchet()
        mock_job = _make_ingest_job()

        monkeypatch.setattr(
            "app.api.v1.ingest.db_service.create_ingest_job",
            lambda db, fn, fp, **kw: mock_job,
        )
        monkeypatch.setattr(
            "app.api.v1.ingest.db_service.get_ingest_job",
            lambda db, iid: mock_job,
        )
        monkeypatch.setattr(
            "app.api.v1.ingest.save_temp_file",
            lambda content, name: "/tmp/fake.pdf",
        )
        monkeypatch.setattr(
            "app.api.v1.ingest.get_settings",
            lambda: _mock_settings(False),
        )
        # Prevent real background task from running
        monkeypatch.setattr(
            "app.api.v1.ingest.process_ingest_background",
            lambda *a, **kw: None,
        )

        client = TestClient(app)
        response = client.post(
            "/api/v1/ingest/upload",
            files=[("files", ("test.pdf", b"%PDF-1.4 fake", "application/pdf"))],
        )

        assert response.status_code == 202
        mock_hatchet.event.push.assert_not_called()
