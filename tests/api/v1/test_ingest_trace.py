"""Tests for the ingest trace endpoint."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.models.audit import AuditTarget, Severity
from app.models.database import IngestStatus
from app.services.pipeline_trace_service import build_ingest_trace


class TestBuildIngestTrace:
    """Tests for the build_ingest_trace function."""

    def _make_ingest_job(
        self,
        job_id="ingest-test-001",
        status=IngestStatus.COMPLETED,
        extraction_errors=None,
    ):
        """Create a mock ingest job."""
        job = MagicMock()
        job.id = job_id
        job.status = status
        job.extraction_errors = extraction_errors or []
        return job

    def test_returns_none_when_job_not_found(self):
        """Should return None when ingest job is not found."""
        db = MagicMock()
        with patch("app.services.pipeline_trace_service.db_service") as mock_svc:
            mock_svc.get_ingest_job.return_value = None
            result = build_ingest_trace("nonexistent", db)
        assert result is None

    def test_returns_none_for_non_terminal_states(self):
        """Should return None for PENDING_PROCESSING state."""
        db = MagicMock()
        job = self._make_ingest_job(status=IngestStatus.PENDING_PROCESSING)

        with patch("app.services.pipeline_trace_service.db_service") as mock_svc:
            mock_svc.get_ingest_job.return_value = job
            result = build_ingest_trace("ingest-test-001", db)

        assert result is None

    def test_returns_none_for_processing_state(self):
        """Should return None for PROCESSING state."""
        db = MagicMock()
        job = self._make_ingest_job(status=IngestStatus.PROCESSING)

        with patch("app.services.pipeline_trace_service.db_service") as mock_svc:
            mock_svc.get_ingest_job.return_value = job
            result = build_ingest_trace("ingest-test-001", db)

        assert result is None

    def test_empty_logs_completed(self):
        """Should return completed trace when no audit logs exist."""
        db = MagicMock()
        job = self._make_ingest_job(status=IngestStatus.COMPLETED)

        with patch("app.services.pipeline_trace_service.db_service") as mock_svc:
            mock_svc.get_ingest_job.return_value = job
            # No audit logs returned
            db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
            trace = build_ingest_trace("ingest-test-001", db)

        assert trace is not None
        assert trace.process_id == "ingest-test-001"
        assert trace.overall_status == "completed"
        assert trace.steps == []
        assert trace.blockers == []

    def test_failed_job_gives_failed_status(self):
        """Should report failed status for failed ingest jobs."""
        db = MagicMock()
        job = self._make_ingest_job(
            status=IngestStatus.FAILED,
            extraction_errors=["Failed to parse PDF"],
        )

        with patch("app.services.pipeline_trace_service.db_service") as mock_svc:
            mock_svc.get_ingest_job.return_value = job
            db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
            trace = build_ingest_trace("ingest-test-001", db)

        assert trace.overall_status == "failed"
        assert len(trace.blockers) == 1
        assert trace.blockers[0].rule_id == "INGEST_ERROR"
        assert "Failed to parse PDF" in trace.blockers[0].user_message_es

    def test_audit_finding_has_required_fields(self):
        """AuditFinding should have all required fields populated."""
        db = MagicMock()
        job = self._make_ingest_job(
            status=IngestStatus.FAILED,
            extraction_errors=["PDF corrupted"],
        )

        with patch("app.services.pipeline_trace_service.db_service") as mock_svc:
            mock_svc.get_ingest_job.return_value = job
            db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
            trace = build_ingest_trace("ingest-test-001", db)

        blocker = trace.blockers[0]
        # Verify all required AuditFinding fields are present
        assert blocker.target == AuditTarget.INGEST
        assert blocker.rule_id == "INGEST_ERROR"
        assert blocker.severity == Severity.BLOCKER
        assert blocker.fixable is False
        assert blocker.responsible_agent == "ingest"
        assert blocker.technical_message is not None
        assert blocker.user_message_es is not None
        assert blocker.suggested_action_es is not None
        assert "ingest_id" in blocker.evidence


class TestIngestTraceEndpoint:
    """Tests for the ingest trace API endpoint."""

    def test_trace_endpoint_returns_404_for_missing_job(self, monkeypatch):
        """Endpoint should return 404 when ingest job not found."""
        from fastapi.testclient import TestClient
        from main import app

        # Mock db_service to return None (job not found)
        def mock_get_job(*args, **kwargs):
            return None

        monkeypatch.setattr("app.api.v1.ingest.db_service.get_ingest_job", mock_get_job)

        client = TestClient(app)
        response = client.get("/api/v1/ingest/nonexistent/trace")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_trace_endpoint_returns_409_for_running_job(self, monkeypatch):
        """Endpoint should return 409 when job is still running."""
        from fastapi.testclient import TestClient
        from main import app

        # Mock job that is still processing
        mock_job = MagicMock()
        mock_job.id = "ingest-test-123"
        mock_job.status = IngestStatus.PROCESSING

        def mock_get_job(*args, **kwargs):
            return mock_job

        monkeypatch.setattr("app.api.v1.ingest.db_service.get_ingest_job", mock_get_job)

        client = TestClient(app)
        response = client.get("/api/v1/ingest/ingest-test-123/trace")
        assert response.status_code == 409
        data = response.json()
        assert data["detail"]["error_code"] == "INGEST_NOT_COMPLETE"

    def test_trace_endpoint_returns_200_for_completed_job(self, monkeypatch):
        """Endpoint should return 200 with trace for completed job."""
        from fastapi.testclient import TestClient
        from main import app

        # Mock completed job
        mock_job = MagicMock()
        mock_job.id = "ingest-test-123"
        mock_job.status = IngestStatus.COMPLETED
        mock_job.extraction_errors = []

        def mock_get_job(*args, **kwargs):
            return mock_job

        monkeypatch.setattr("app.api.v1.ingest.db_service.get_ingest_job", mock_get_job)

        # Mock build_ingest_trace to return a trace
        mock_trace = MagicMock()
        mock_trace.process_id = "ingest-test-123"
        mock_trace.overall_status = "completed"
        mock_trace.steps = []
        mock_trace.blockers = []
        mock_trace.give_up = None

        def mock_build_trace(*args, **kwargs):
            return mock_trace

        monkeypatch.setattr(
            "app.services.pipeline_trace_service.build_ingest_trace",
            mock_build_trace,
        )

        client = TestClient(app)
        response = client.get("/api/v1/ingest/ingest-test-123/trace")
        assert response.status_code == 200
        data = response.json()
        assert data["process_id"] == "ingest-test-123"
        assert data["overall_status"] == "completed"


class TestIngestStatusEndpoint:
    """Tests for the ingest status endpoint including trace_url."""

    def test_ingest_status_has_absolute_trace_url(self, monkeypatch):
        """Ingest status should return absolute URL for trace_url."""
        from fastapi.testclient import TestClient
        from main import app

        # Mock the service
        mock_job = MagicMock()
        mock_job.id = "ingest-test-123"
        mock_job.status = IngestStatus.COMPLETED
        mock_job.file_name = "test.pdf"
        mock_job.document_type = "factura_venta"
        mock_job.pathway = "via_a"
        mock_job.created_at = datetime.now(timezone.utc)
        mock_job.completed_at = None
        mock_job.extraction_errors = []
        mock_job.transactions_pending = []

        def mock_get_job(*args, **kwargs):
            return mock_job

        monkeypatch.setattr("app.services.db_service.get_ingest_job", mock_get_job)

        client = TestClient(app)
        response = client.get("/api/v1/ingest/ingest-test-123")

        assert response.status_code == 200
        data = response.json()
        # Should be absolute URL starting with http
        assert data["trace_url"].startswith("http://")
        assert "/api/v1/ingest/ingest-test-123/trace" in data["trace_url"]
