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
        job.file_names = None
        job.multi_file_mode = None
        job.current_file_index = None
        return job

    def test_upload_accepts_parser_mode(self, monkeypatch):
        """POST with parser_mode=agentic should return 202 and detail should show agentic."""
        mock_job = self._make_job(parser_mode="agentic")

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
            "app.api.v1.ingest.ingest_file_service.store_files",
            lambda db, ingest_id, files: None,
        )

        client = TestClient(app)
        response = client.post(
            "/api/v1/ingest/upload",
            files=[("files", ("test.pdf", b"%PDF1.4 fake content", "application/pdf"))],
            data={"parser_mode": "agentic"},
        )
        assert response.status_code == 202
        data = response.json()
        assert data["ingest_id"] == "ing_test_123"

        detail = client.get("/api/v1/ingest/ing_test_123")
        assert detail.status_code == 200
        assert detail.json()["parser_mode"] == "agentic"

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
            "app.api.v1.ingest.ingest_file_service.store_files",
            lambda db, ingest_id, files: None,
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
            "app.api.v1.ingest.ingest_file_service.store_files",
            lambda db, ingest_id, files: None,
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
        job.file_names = None
        job.multi_file_mode = None
        job.current_file_index = None
        return job

    def test_upload_endpoint_accepts_multiple_files(self, monkeypatch):
        """POST with multiple files should return 202 and queue a single ingest job."""
        mock_job = self._make_job()
        stored_payloads = []

        def _mock_store_files(db, ingest_id, files):
            stored_payloads.extend(files)

        def _mock_create_ingest_job(db, file_name, file_path, **kwargs):
            mock_job.file_name = file_name
            mock_job.file_path = file_path
            return mock_job

        monkeypatch.setattr(
            "app.api.v1.ingest.ingest_file_service.store_files", _mock_store_files
        )
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
        assert len(stored_payloads) == 2


class TestIngestUploadFileNames:
    """Tests for file_names persistence in upload endpoint."""

    def _make_job(self, ingest_id="ing_test_123", file_names=None):
        job = MagicMock()
        job.id = ingest_id
        job.file_name = "page1.pdf"
        job.file_names = file_names
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
        job.multi_file_mode = None
        job.current_file_index = None
        return job

    def test_multi_file_upload_stores_all_file_names(self, monkeypatch):
        """POST with 2 files — create_ingest_job called with file_names=['page1.pdf','page2.pdf']."""
        mock_job = self._make_job(file_names=["page1.pdf", "page2.pdf"])
        captured_kwargs = {}

        def _mock_create_ingest_job(db, file_name, file_path, **kwargs):
            captured_kwargs.update(kwargs)
            return mock_job

        monkeypatch.setattr(
            "app.api.v1.ingest.db_service.create_ingest_job", _mock_create_ingest_job
        )
        monkeypatch.setattr(
            "app.api.v1.ingest.ingest_file_service.store_files",
            lambda db, ingest_id, files: None,
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
        assert captured_kwargs.get("file_names") == ["page1.pdf", "page2.pdf"]

    def test_single_file_upload_stores_file_name_as_list(self, monkeypatch):
        """POST with 1 file — create_ingest_job called with file_names=['test.pdf']."""
        mock_job = self._make_job(file_names=["test.pdf"])
        captured_kwargs = {}

        def _mock_create_ingest_job(db, file_name, file_path, **kwargs):
            captured_kwargs.update(kwargs)
            return mock_job

        monkeypatch.setattr(
            "app.api.v1.ingest.db_service.create_ingest_job", _mock_create_ingest_job
        )
        monkeypatch.setattr(
            "app.api.v1.ingest.ingest_file_service.store_files",
            lambda db, ingest_id, files: None,
        )

        client = TestClient(app)
        response = client.post(
            "/api/v1/ingest/upload",
            files=[("files", ("test.pdf", b"%PDF1.4 fake", "application/pdf"))],
        )
        assert response.status_code == 202
        assert captured_kwargs.get("file_names") == ["test.pdf"]

    def test_get_ingest_job_returns_file_names(self, monkeypatch):
        """GET /api/v1/ingest/{id} returns file_names list from job."""
        mock_job = self._make_job(file_names=["a.pdf", "b.pdf"])

        monkeypatch.setattr(
            "app.api.v1.ingest.db_service.get_ingest_job",
            lambda db, ingest_id: mock_job,
        )

        client = TestClient(app)
        response = client.get("/api/v1/ingest/ing_test_123")
        assert response.status_code == 200
        data = response.json()
        assert data["file_names"] == ["a.pdf", "b.pdf"]


class TestIngestUploadMultiFileMode:
    """Tests for multi_file_mode handling in upload endpoint."""

    def _make_job(
        self, ingest_id="ing_test_123", multi_file_mode="pages", current_file_index=None
    ):
        job = MagicMock()
        job.id = ingest_id
        job.file_name = "test.pdf"
        job.file_names = ["test.pdf"]
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
        job.multi_file_mode = multi_file_mode
        job.current_file_index = current_file_index
        return job

    def test_upload_defaults_to_pages_mode(self, monkeypatch):
        """POST without multi_file_mode → create_ingest_job called with multi_file_mode='pages'."""
        mock_job = self._make_job()
        captured_kwargs = {}

        def _mock_create_ingest_job(db, file_name, file_path, **kwargs):
            captured_kwargs.update(kwargs)
            return mock_job

        monkeypatch.setattr(
            "app.api.v1.ingest.db_service.create_ingest_job", _mock_create_ingest_job
        )
        monkeypatch.setattr(
            "app.api.v1.ingest.ingest_file_service.store_files",
            lambda db, ingest_id, files: None,
        )

        client = TestClient(app)
        response = client.post(
            "/api/v1/ingest/upload",
            files=[("files", ("test.pdf", b"%PDF1.4 fake", "application/pdf"))],
        )
        assert response.status_code == 202
        assert captured_kwargs.get("multi_file_mode") == "pages"

    def test_upload_accepts_documents_mode(self, monkeypatch):
        """POST with multi_file_mode='documents' → create_ingest_job called with multi_file_mode='documents'."""
        mock_job = self._make_job(multi_file_mode="documents")
        captured_kwargs = {}

        def _mock_create_ingest_job(db, file_name, file_path, **kwargs):
            captured_kwargs.update(kwargs)
            return mock_job

        monkeypatch.setattr(
            "app.api.v1.ingest.db_service.create_ingest_job", _mock_create_ingest_job
        )
        monkeypatch.setattr(
            "app.api.v1.ingest.ingest_file_service.store_files",
            lambda db, ingest_id, files: None,
        )

        client = TestClient(app)
        response = client.post(
            "/api/v1/ingest/upload",
            files=[("files", ("test.pdf", b"%PDF1.4 fake", "application/pdf"))],
            data={"multi_file_mode": "documents"},
        )
        assert response.status_code == 202
        assert captured_kwargs.get("multi_file_mode") == "documents"

    def test_get_ingest_job_returns_multi_file_fields(self, monkeypatch):
        """GET /api/v1/ingest/{id} returns multi_file_mode and current_file_index."""
        mock_job = self._make_job(multi_file_mode="documents", current_file_index=2)

        monkeypatch.setattr(
            "app.api.v1.ingest.db_service.get_ingest_job",
            lambda db, ingest_id: mock_job,
        )

        client = TestClient(app)
        response = client.get("/api/v1/ingest/ing_test_123")
        assert response.status_code == 200
        data = response.json()
        assert data["multi_file_mode"] == "documents"
        assert data["current_file_index"] == 2
