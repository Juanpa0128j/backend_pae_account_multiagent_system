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
    monkeypatch.setattr(
        db_service,
        "create_process_job",
        lambda db, ingest_id: SimpleNamespace(
            id="proc_123",
            ingest_id=ingest_id,
            status=ProcessStatus.QUEUED,
        ),
    )

    def _start(pid: str):
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
    assert body["progress"] == 42
    assert body["current_stage"] == "ingesta"

    app.dependency_overrides.clear()


def test_get_process_result_not_completed_returns_409(monkeypatch):
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
    assert response.status_code == 409
    assert "not completed yet" in response.json()["detail"]

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
                "cuenta_puc": "5195",
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
    assert body["transactions"][0]["cuenta_puc"] == "5195"

    app.dependency_overrides.clear()
