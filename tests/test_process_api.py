from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.database import get_db
from app.models.database import ProcessStatus
from app.services import db_service, jobs
from main import app


def _override_db():
    yield SimpleNamespace()


def test_start_process_job_returns_process_id(monkeypatch):
    app.dependency_overrides[get_db] = _override_db
    client = TestClient(app)

    captured = {"started": None}

    monkeypatch.setattr(
        db_service,
        "get_ingest_job",
        lambda db, ingest_id: SimpleNamespace(id=ingest_id, file_path="/tmp/test.pdf"),
    )
    
    # Mock the new idempotency check function
    monkeypatch.setattr(
        db_service,
        "get_active_process_job_for_ingest",
        lambda db, ingest_id: None,  # No active job exists
    )
    
    monkeypatch.setattr(
        db_service,
        "create_process_job",
        lambda db, ingest_id: SimpleNamespace(
            id="proc_123",
            ingest_id=ingest_id,
            status=ProcessStatus.QUEUED,
        ),
    )

    async def _start(pid: str):
        captured["started"] = pid

    monkeypatch.setattr(jobs, "start_process_job", _start)

    response = client.post("/api/v1/process/accounting/ing_001")
    assert response.status_code == 200
    body = response.json()
    assert body["process_id"] == "proc_123"
    assert body["status"] == "queued"
    assert captured["started"] == "proc_123"

    app.dependency_overrides.clear()


def test_get_process_status_polling(monkeypatch):
    app.dependency_overrides[get_db] = _override_db
    client = TestClient(app)

    monkeypatch.setattr(
        db_service,
        "get_process_job",
        lambda db, process_id: SimpleNamespace(
            id=process_id,
            ingest_id="ing_001",
            status=ProcessStatus.RUNNING,
            current_stage="ingesta",
            current_agent="ingesta",
            progress=42,
            error_message=None,
            agent_log=[{"stage": "supervisor", "event": "started"}],
            created_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            completed_at=None,
        ),
    )

    response = client.get("/api/v1/process/status/proc_123")
    assert response.status_code == 200
    body = response.json()
    assert body["process_id"] == "proc_123"
    assert body["status"] == "running"
    assert body["progress"] == 42  # Progress is present when set, null when not set
    assert body["current_stage"] == "ingesta"

    app.dependency_overrides.clear()


def test_get_process_result_not_completed_returns_202(monkeypatch):
    app.dependency_overrides[get_db] = _override_db
    client = TestClient(app)

    monkeypatch.setattr(
        db_service,
        "get_process_job",
        lambda db, process_id: SimpleNamespace(
            id=process_id,
            ingest_id="ing_001",
            status=ProcessStatus.RUNNING,
        ),
    )

    response = client.get("/api/v1/process/result/proc_123")
    assert response.status_code == 202
    assert "still being processed" in response.json()["detail"]

    app.dependency_overrides.clear()


def test_get_process_result_completed_returns_transactions(monkeypatch):
    app.dependency_overrides[get_db] = _override_db
    client = TestClient(app)

    monkeypatch.setattr(
        db_service,
        "get_process_job",
        lambda db, process_id: SimpleNamespace(
            id=process_id,
            ingest_id="ing_001",
            status=ProcessStatus.COMPLETED,
        ),
    )
    monkeypatch.setattr(
        db_service,
        "get_process_result_transactions",
        lambda db, ingest_id: [
            {
                "transaction_pending_id": "txn_1",
                "transaction_posted_id": "posted_1",
                "puc_account": "5195",
                "total": 1500000.0,
            }
        ],
    )

    response = client.get("/api/v1/process/result/proc_123")
    assert response.status_code == 200
    body = response.json()
    assert body["process_id"] == "proc_123"
    assert body["status"] == "completed"
    assert len(body["transactions"]) == 1
    assert body["transactions"][0]["puc_account"] == "5195"

    app.dependency_overrides.clear()


def test_post_accounting_returns_existing_process_job_idempotent(monkeypatch):
    """Verify that calling POST twice with the same ingest_id returns the same process_id."""
    app.dependency_overrides[get_db] = _override_db
    client = TestClient(app)

    captured = {"started_count": 0}

    monkeypatch.setattr(
        db_service,
        "get_ingest_job",
        lambda db, ingest_id: SimpleNamespace(id=ingest_id, file_path="/tmp/test.pdf"),
    )

    # Existing process job (already running)
    existing_job = SimpleNamespace(
        id="proc_existing",
        ingest_id="ing_001",
        status=ProcessStatus.RUNNING,
    )
    
    call_count = {"count": 0}
    
    def _get_active_job(db, ingest_id):
        call_count["count"] += 1
        # First call to POST creates new job, so get_active returns None
        # Second call should return the existing job
        if call_count["count"] == 2:
            return existing_job
        return None

    monkeypatch.setattr(
        db_service,
        "get_active_process_job_for_ingest",
        _get_active_job,
    )

    new_job = SimpleNamespace(
        id="proc_new",
        ingest_id="ing_001",
        status=ProcessStatus.QUEUED,
    )

    monkeypatch.setattr(
        db_service,
        "create_process_job",
        lambda db, ingest_id: new_job,
    )

    async def _start(pid: str):
        captured["started_count"] += 1

    monkeypatch.setattr(jobs, "start_process_job", _start)

    # First POST - should create and start
    response1 = client.post("/api/v1/process/accounting/ing_001")
    assert response1.status_code == 200
    first_process_id = response1.json()["process_id"]
    assert first_process_id == "proc_new"
    assert captured["started_count"] == 1

    # Second POST - should return existing
    response2 = client.post("/api/v1/process/accounting/ing_001")
    assert response2.status_code == 200
    second_process_id = response2.json()["process_id"]
    assert second_process_id == "proc_existing"  # Returns the existing one
    assert captured["started_count"] == 1  # No new start_process_job call

    app.dependency_overrides.clear()


def test_post_accounting_creates_new_job_if_previous_failed(monkeypatch):
    """Verify that a new job is created if the previous one failed."""
    app.dependency_overrides[get_db] = _override_db
    client = TestClient(app)

    captured = {"started_count": 0}

    monkeypatch.setattr(
        db_service,
        "get_ingest_job",
        lambda db, ingest_id: SimpleNamespace(id=ingest_id, file_path="/tmp/test.pdf"),
    )

    # No active job exists (previous one failed)
    monkeypatch.setattr(
        db_service,
        "get_active_process_job_for_ingest",
        lambda db, ingest_id: None,  # No active job
    )

    first_job = SimpleNamespace(
        id="proc_first",
        ingest_id="ing_002",
        status=ProcessStatus.QUEUED,
    )

    call_count = {"count": 0}

    def _create_job(db, ingest_id):
        call_count["count"] += 1
        if call_count["count"] == 1:
            return first_job
        return SimpleNamespace(
            id="proc_second",
            ingest_id=ingest_id,
            status=ProcessStatus.QUEUED,
        )

    monkeypatch.setattr(
        db_service,
        "create_process_job",
        _create_job,
    )

    async def _start(pid: str):
        captured["started_count"] += 1

    monkeypatch.setattr(jobs, "start_process_job", _start)

    # First POST - creates job
    response1 = client.post("/api/v1/process/accounting/ing_002")
    assert response1.status_code == 200
    assert response1.json()["process_id"] == "proc_first"
    assert captured["started_count"] == 1

    # Second POST - creates new job (no active exists)
    response2 = client.post("/api/v1/process/accounting/ing_002")
    assert response2.status_code == 200
    assert response2.json()["process_id"] == "proc_second"
    assert captured["started_count"] == 2  # Two start calls

    app.dependency_overrides.clear()

