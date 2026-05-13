"""Tests for the ingest upload endpoint parser_mode support."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from main import app


class TestIngestUploadParserMode:
    """Tests for parser_mode handling in upload endpoint."""

    def _make_job(self, ingest_id="ing_test_123", parser_mode="fast"):
        job = MagicMock()
        job.id = ingest_id
        job.file_name = "test.pdf"
        job.status = MagicMock()
        job.status.value = "pending_processing"
        job.document_type = None
        job.pathway = None
        job.created_at = datetime.now(timezone.utc)
        job.completed_at = None
        job.extraction_errors = []
        job.transactions_pending = []
        job.parser_mode = parser_mode
        job.classification_confidence = None
        job.classification_confirmed = None
        return job

    def test_upload_accepts_parser_mode(self, monkeypatch):
        """POST with parser_mode=premium should return 202 and detail should show premium."""
        mock_job = self._make_job(parser_mode="premium")

        def mock_create_ingest_job(db, file_name, file_path, **kwargs):
            mock_job.parser_mode = kwargs.get("parser_mode", "fast")
            return mock_job

        monkeypatch.setattr(
            "app.api.v1.ingest.db_service.create_ingest_job", mock_create_ingest_job
        )
        monkeypatch.setattr(
            "app.api.v1.ingest.db_service.get_ingest_job",
            lambda db, ingest_id: mock_job,
        )
        monkeypatch.setattr(
            "app.api.v1.ingest.save_temp_file",
            lambda content, name: "/tmp/fake.pdf",
        )

        client = TestClient(app)
        response = client.post(
            "/api/v1/ingest/upload",
            files=[("files", ("test.pdf", b"%PDF1.4 fake content", "application/pdf"))],
            data={"parser_mode": "premium"},
        )
        assert response.status_code == 202
        data = response.json()
        assert data["ingest_id"] == "ing_test_123"

        detail = client.get("/api/v1/ingest/ing_test_123")
        assert detail.status_code == 200
        assert detail.json()["parser_mode"] == "premium"

    def test_upload_defaults_to_fast_mode(self, monkeypatch):
        """POST without parser_mode should default to fast in detail response."""
        mock_job = self._make_job(parser_mode="fast")

        def mock_create_ingest_job(db, file_name, file_path, **kwargs):
            mock_job.parser_mode = kwargs.get("parser_mode", "fast")
            return mock_job

        monkeypatch.setattr(
            "app.api.v1.ingest.db_service.create_ingest_job", mock_create_ingest_job
        )
        monkeypatch.setattr(
            "app.api.v1.ingest.db_service.get_ingest_job",
            lambda db, ingest_id: mock_job,
        )
        monkeypatch.setattr(
            "app.api.v1.ingest.save_temp_file",
            lambda content, name: "/tmp/fake.pdf",
        )

        client = TestClient(app)
        response = client.post(
            "/api/v1/ingest/upload",
            files=[("files", ("test.pdf", b"%PDF1.4 fake content", "application/pdf"))],
        )
        assert response.status_code == 202

        detail = client.get("/api/v1/ingest/ing_test_123")
        assert detail.status_code == 200
        assert detail.json()["parser_mode"] == "fast"

    def test_upload_rejects_invalid_parser_mode(self, monkeypatch):
        """POST with invalid parser_mode should return 422."""
        monkeypatch.setattr(
            "app.api.v1.ingest.save_temp_file",
            lambda content, name: "/tmp/fake.pdf",
        )

        client = TestClient(app)
        response = client.post(
            "/api/v1/ingest/upload",
            files=[("files", ("test.pdf", b"%PDF1.4 fake content", "application/pdf"))],
            data={"parser_mode": "invalid"},
        )
        assert response.status_code == 422


class TestIngestUploadMultiPage:
    """Tests for multi-page / multi-file upload endpoint."""

    def _make_job(self, ingest_id="ing_test_123"):
        job = MagicMock()
        job.id = ingest_id
        job.file_name = "test.pdf"
        job.status = MagicMock()
        job.status.value = "pending_processing"
        job.document_type = None
        job.pathway = None
        job.created_at = datetime.now(timezone.utc)
        job.completed_at = None
        job.extraction_errors = []
        job.transactions_pending = []
        job.parser_mode = "fast"
        job.classification_confidence = None
        job.classification_confirmed = None
        return job

    def test_upload_endpoint_accepts_multiple_files(self, monkeypatch):
        """POST with multiple files should return 202 and queue a single ingest job."""
        mock_job = self._make_job()
        saved_paths = []

        def _mock_save_temp_file(content, name):
            path = f"/tmp/fake_{name}"
            saved_paths.append(path)
            return path

        def _mock_create_ingest_job(db, file_name, file_path, **kwargs):
            mock_job.file_name = file_name
            mock_job.file_path = file_path
            return mock_job

        monkeypatch.setattr("app.api.v1.ingest.save_temp_file", _mock_save_temp_file)
        monkeypatch.setattr(
            "app.api.v1.ingest.db_service.create_ingest_job", _mock_create_ingest_job
        )
        monkeypatch.setattr(
            "app.api.v1.ingest.db_service.get_ingest_job",
            lambda db, ingest_id: mock_job,
        )

        client = TestClient(app)
        response = client.post(
            "/api/v1/ingest/upload",
            files=[
                ("files", ("page1.pdf", b"%PDF1.4 page1", "application/pdf")),
                ("files", ("page2.pdf", b"%PDF1.4 page2", "application/pdf")),
            ],
        )
        assert response.status_code == 202
        data = response.json()
        assert data["ingest_id"] == "ing_test_123"
        assert len(saved_paths) == 2
