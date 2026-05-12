"""Tests for the ingest cancel endpoint."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from main import app

from app.models.database import IngestStatus


class TestIngestCancel:
    """Tests for PATCH /api/v1/ingest/{ingest_id}/cancel."""

    def _make_job(
        self,
        job_id="ingest-test-123",
        status=IngestStatus.PENDING_REVIEW,
        file_path="/tmp/fake.pdf",
    ):
        job = MagicMock()
        job.id = job_id
        job.file_name = "test.pdf"
        job.status = status
        job.document_type = None
        job.pathway = None
        job.parser_mode = "fast"
        job.created_at = datetime.now(timezone.utc)
        job.completed_at = None
        job.extraction_errors = []
        job.transactions_pending = []
        job.classification_confidence = None
        job.classification_confirmed = None
        job.file_path = file_path
        return job

    def test_cancel_ingest_returns_202(self, monkeypatch):
        """Cancelling a PENDING_REVIEW job should return 202 and status cancelled."""
        mock_job = self._make_job(status=IngestStatus.PENDING_REVIEW)

        def mock_get_job(db, ingest_id):
            return mock_job

        def mock_update_job(db, ingest_id, status, **kwargs):
            mock_job.status = status
            return mock_job

        monkeypatch.setattr("app.api.v1.ingest.db_service.get_ingest_job", mock_get_job)
        monkeypatch.setattr(
            "app.api.v1.ingest.db_service.update_ingest_job", mock_update_job
        )

        client = TestClient(app)
        response = client.patch("/api/v1/ingest/ingest-test-123/cancel")
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "CANCELLED"

    def test_cancel_already_cancelled_returns_409(self, monkeypatch):
        """Cancelling an already cancelled job should return 409."""
        mock_job = self._make_job(status=IngestStatus.CANCELLED)

        def mock_get_job(db, ingest_id):
            return mock_job

        monkeypatch.setattr("app.api.v1.ingest.db_service.get_ingest_job", mock_get_job)

        client = TestClient(app)
        response = client.patch("/api/v1/ingest/ingest-test-123/cancel")
        assert response.status_code == 409
        assert "ya fue cancelado" in response.json()["detail"].lower()

    def test_cancel_completed_returns_409(self, monkeypatch):
        """Cancelling a completed job should return 409."""
        mock_job = self._make_job(status=IngestStatus.COMPLETED)

        def mock_get_job(db, ingest_id):
            return mock_job

        monkeypatch.setattr("app.api.v1.ingest.db_service.get_ingest_job", mock_get_job)

        client = TestClient(app)
        response = client.patch("/api/v1/ingest/ingest-test-123/cancel")
        assert response.status_code == 409
        assert "ya terminó" in response.json()["detail"].lower()

    def test_cancel_nonexistent_returns_404(self, monkeypatch):
        """Cancelling a non-existent job should return 404."""

        def mock_get_job(db, ingest_id):
            return None

        monkeypatch.setattr("app.api.v1.ingest.db_service.get_ingest_job", mock_get_job)

        client = TestClient(app)
        response = client.patch("/api/v1/ingest/nonexistent/cancel")
        assert response.status_code == 404
        assert "no encontrado" in response.json()["detail"].lower()
