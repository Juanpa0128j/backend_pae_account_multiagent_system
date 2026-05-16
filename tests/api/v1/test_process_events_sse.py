"""Tests for the SSE process events endpoint."""

from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from main import app


def _make_job(status="completed", progress=100, job_id="test-id"):
    job = MagicMock()
    job.id = job_id
    job.status = status
    job.progress = progress
    return job


class TestProcessEventsSSE:
    def test_sse_endpoint_returns_event_stream(self, monkeypatch):
        """SSE endpoint returns 200 with text/event-stream content-type and data: lines."""
        mock_job = _make_job(status="completed", progress=100)
        monkeypatch.setattr(
            "app.api.v1.events.db_service.get_process_job",
            lambda db, process_id: mock_job,
        )

        client = TestClient(app)
        response = client.get("/api/v1/process/test-id/events")

        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        assert "data:" in response.text

    def test_sse_endpoint_returns_404_for_unknown_process(self, monkeypatch):
        """SSE endpoint returns 404 when process not found."""
        monkeypatch.setattr(
            "app.api.v1.events.db_service.get_process_job",
            lambda db, process_id: None,
        )

        client = TestClient(app)
        response = client.get("/api/v1/process/unknown-id/events")

        assert response.status_code == 404

    def test_sse_endpoint_streams_status_changes(self, monkeypatch):
        """SSE data payload contains status field."""
        mock_job = _make_job(status="running", progress=50)
        monkeypatch.setattr(
            "app.api.v1.events.db_service.get_process_job",
            lambda db, process_id: mock_job,
        )

        client = TestClient(app)
        response = client.get("/api/v1/process/test-id/events")

        assert response.status_code == 200
        body = response.text
        assert "data:" in body
        assert "status" in body or "running" in body
