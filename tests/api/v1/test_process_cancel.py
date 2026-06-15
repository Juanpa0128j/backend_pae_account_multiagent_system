"""Tests for the process cancel endpoint and cooperative cancellation guard."""

from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from main import app

from app.models.database import ProcessStatus, TransactionStatus


def _make_job(job_id="proc-test-123", status=ProcessStatus.QUEUED, ingest_id="ing-1"):
    job = MagicMock()
    job.id = job_id
    job.status = status
    job.ingest_id = ingest_id
    job.progress = 10
    job.current_stage = "supervisor"
    job.agent_log = []
    return job


class TestProcessCancel:
    """Tests for POST /api/v1/process/{process_id}/cancel."""

    def _patch_common(self, monkeypatch, job, updated=None):
        captured = {}

        def mock_get_job(db, process_id):
            return job

        def mock_update_job(db, process_id, **kwargs):
            captured.update(kwargs)
            if job is not None and "status" in kwargs:
                job.status = kwargs["status"]
            return job

        monkeypatch.setattr(
            "app.api.v1.process.db_service.get_process_job", mock_get_job
        )
        monkeypatch.setattr(
            "app.api.v1.process.db_service.update_process_job", mock_update_job
        )
        monkeypatch.setattr(
            "app.api.v1.process.db_service.get_transactions_by_ingest",
            lambda db, ingest_id: [],
        )
        monkeypatch.setattr(
            "app.services.jobs._mark_processing_transactions_failed_safe",
            lambda ingest_id: None,
        )
        monkeypatch.setattr(
            "app.services.jobs._mark_pending_failed_safe",
            lambda pending_id: None,
        )
        return captured

    def test_cancel_queued_returns_200(self, monkeypatch):
        job = _make_job(status=ProcessStatus.QUEUED)
        self._patch_common(monkeypatch, job)
        resp = TestClient(app).post("/api/v1/process/proc-test-123/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_cancel_running_returns_200(self, monkeypatch):
        job = _make_job(status=ProcessStatus.RUNNING)
        self._patch_common(monkeypatch, job)
        resp = TestClient(app).post("/api/v1/process/proc-test-123/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_cancel_pending_audit_review_returns_200(self, monkeypatch):
        job = _make_job(status=ProcessStatus.PENDING_AUDIT_REVIEW)
        self._patch_common(monkeypatch, job)
        resp = TestClient(app).post("/api/v1/process/proc-test-123/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_cancel_completed_returns_409(self, monkeypatch):
        job = _make_job(status=ProcessStatus.COMPLETED)
        self._patch_common(monkeypatch, job)
        resp = TestClient(app).post("/api/v1/process/proc-test-123/cancel")
        assert resp.status_code == 409
        assert "ya terminó" in resp.json()["detail"].lower()

    def test_cancel_failed_returns_409(self, monkeypatch):
        job = _make_job(status=ProcessStatus.FAILED)
        self._patch_common(monkeypatch, job)
        resp = TestClient(app).post("/api/v1/process/proc-test-123/cancel")
        assert resp.status_code == 409
        assert "ya terminó" in resp.json()["detail"].lower()

    def test_cancel_already_cancelled_returns_409(self, monkeypatch):
        job = _make_job(status=ProcessStatus.CANCELLED)
        self._patch_common(monkeypatch, job)
        resp = TestClient(app).post("/api/v1/process/proc-test-123/cancel")
        assert resp.status_code == 409
        assert "ya fue cancelado" in resp.json()["detail"].lower()

    def test_cancel_nonexistent_returns_404(self, monkeypatch):
        self._patch_common(monkeypatch, None)
        resp = TestClient(app).post("/api/v1/process/nope/cancel")
        assert resp.status_code == 404
        assert "no encontrado" in resp.json()["detail"].lower()

    def test_response_body_shape(self, monkeypatch):
        job = _make_job(status=ProcessStatus.QUEUED)
        self._patch_common(monkeypatch, job)
        resp = TestClient(app).post("/api/v1/process/proc-test-123/cancel")
        data = resp.json()
        assert set(data.keys()) == {"process_id", "status", "message"}
        assert data["process_id"] == "proc-test-123"
        assert isinstance(data["message"], str) and data["message"]

    def test_update_called_with_cancelled_status(self, monkeypatch):
        job = _make_job(status=ProcessStatus.RUNNING)
        captured = self._patch_common(monkeypatch, job)
        TestClient(app).post("/api/v1/process/proc-test-123/cancel")
        assert captured["status"] == ProcessStatus.CANCELLED
        assert captured["current_stage"] == "cancelled"
        entry = captured["agent_log_entry"]
        assert entry["event"] == "cancelled"
        assert entry["agent"] == "supervisor"
        assert "cancelado manualmente" in entry["message"].lower()

    def test_related_transactions_marked(self, monkeypatch):
        job = _make_job(status=ProcessStatus.RUNNING, ingest_id="ing-99")
        self._patch_common(monkeypatch, job)

        marked_processing = {}
        marked_pending = []
        tx = MagicMock()
        tx.id = "tx-1"
        tx.status = TransactionStatus.PENDING

        monkeypatch.setattr(
            "app.api.v1.process.db_service.get_transactions_by_ingest",
            lambda db, ingest_id: [tx],
        )
        monkeypatch.setattr(
            "app.services.jobs._mark_processing_transactions_failed_safe",
            lambda ingest_id: marked_processing.setdefault("ingest", ingest_id),
        )
        monkeypatch.setattr(
            "app.services.jobs._mark_pending_failed_safe",
            lambda pending_id: marked_pending.append(pending_id),
        )

        resp = TestClient(app).post("/api/v1/process/proc-test-123/cancel")
        assert resp.status_code == 200
        assert marked_processing["ingest"] == "ing-99"
        assert marked_pending == ["tx-1"]


class TestCooperativeGuard:
    """Unit tests for the cooperative cancellation guard in jobs.py."""

    def test_guard_skips_completed_write_when_cancelled(self, monkeypatch):
        """If the job is CANCELLED mid-run, the impl returns before writing COMPLETED."""
        import asyncio

        from app.services import jobs

        cancelled_job = _make_job(status=ProcessStatus.CANCELLED)
        ingest_job = MagicMock()
        ingest_job.company_nit = "800999888"
        ingest_job.document_type = "factura"

        tx = MagicMock()
        tx.id = "tx-1"
        tx.status = TransactionStatus.PENDING
        tx.fecha = None
        tx.nit_emisor = "1"
        tx.nit_receptor = "2"
        tx.total = 100.0
        tx.descripcion = "x"
        tx.items = []
        tx.raw_data = {}
        tx.company_nit = "800999888"

        # get_process_job returns a RUNNING-ish job first, then CANCELLED on re-fetch.
        running_job = _make_job(status=ProcessStatus.RUNNING)
        calls = {"n": 0}

        def mock_get_process_job(db, process_id):
            calls["n"] += 1
            # First call (initial load) returns running; re-fetch returns cancelled.
            return running_job if calls["n"] == 1 else cancelled_job

        updates = []

        def mock_update(db, process_id, **kwargs):
            updates.append(kwargs.get("status"))
            return running_job

        monkeypatch.setattr(jobs.db_service, "get_process_job", mock_get_process_job)
        monkeypatch.setattr(jobs.db_service, "get_ingest_job", lambda db, i: ingest_job)
        monkeypatch.setattr(
            jobs.db_service, "get_transactions_by_ingest", lambda db, i: [tx]
        )
        monkeypatch.setattr(
            jobs.db_service, "update_transaction_status", lambda *a, **k: None
        )
        monkeypatch.setattr(jobs.db_service, "update_process_job", mock_update)
        monkeypatch.setattr(jobs, "SessionLocal", lambda: MagicMock())

        # Replace the pipeline so no real graph/thread runs; it "succeeds".
        monkeypatch.setattr(
            jobs, "invoke_accounting_pipeline", lambda **kwargs: {"status": "completed"}
        )

        # run_in_executor would spawn a real thread; emulate it inline.
        async def fake_run_in_executor(executor, fn, *args):
            return fn(*args)

        async def fake_wait_for(awaitable, timeout=None):
            return await awaitable

        def fake_get_event_loop():
            loop = MagicMock()
            loop.run_in_executor = fake_run_in_executor
            return loop

        monkeypatch.setattr(jobs.asyncio, "get_event_loop", fake_get_event_loop)
        monkeypatch.setattr(jobs.asyncio, "wait_for", fake_wait_for)

        asyncio.run(jobs._run_process_job_impl("proc-test-123"))

        # The guard must have prevented any COMPLETED write.
        assert ProcessStatus.COMPLETED not in updates
