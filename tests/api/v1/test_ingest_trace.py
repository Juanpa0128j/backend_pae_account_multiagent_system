"""Tests for the ingest trace endpoint."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.database import AuditLog, IngestStatus
from app.services import db_service


def test_get_ingest_trace_not_found(client: TestClient):
    """Should return 404 for non-existent ingest job."""
    response = client.get("/api/v1/ingest/non-existent-id/trace")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_get_ingest_trace_success(client: TestClient, db: Session):
    """Should return trace for existing ingest job."""
    # Create a sample ingest job
    ingest_job = db_service.create_ingest_job(db, "test.pdf", "/tmp/test.pdf")

    # Add audit logs
    log = AuditLog(
        action="ingest_created",
        entity_id=ingest_job.id,
        entity_type="ingest",
        details={"agent": "ingesta", "message": "Ingest started"},
    )
    db.add(log)
    db.commit()

    response = client.get(f"/api/v1/ingest/{ingest_job.id}/trace")
    assert response.status_code == 200
    data = response.json()
    assert data["process_id"] == str(ingest_job.id)
    assert data["overall_status"] in ["running", "completed", "failed"]
    assert len(data["steps"]) == 1


def test_get_ingest_trace_with_extraction_errors(client: TestClient, db: Session):
    """Should include blockers when extraction errors exist."""
    # Create a sample ingest job with extraction errors
    ingest_job = db_service.create_ingest_job(db, "test.pdf", "/tmp/test.pdf")
    ingest_job.extraction_errors = ["Failed to parse PDF: corrupted file"]
    ingest_job.status = IngestStatus.FAILED
    db.commit()

    response = client.get(f"/api/v1/ingest/{ingest_job.id}/trace")
    assert response.status_code == 200
    data = response.json()
    assert data["overall_status"] == "failed"
    assert len(data["blockers"]) == 1
    assert data["blockers"][0]["rule_id"] == "INGEST_ERROR"
    assert "Failed to parse PDF" in data["blockers"][0]["user_message_es"]


def test_get_ingest_trace_includes_related_logs(client: TestClient, db: Session):
    """Should include audit logs related via details->>ingest_id."""
    # Create a sample ingest job
    ingest_job = db_service.create_ingest_job(db, "test.pdf", "/tmp/test.pdf")

    # Add audit log with entity_type="ingest"
    log1 = AuditLog(
        action="ingest_created",
        entity_id=ingest_job.id,
        entity_type="ingest",
        details={"agent": "ingesta", "message": "Ingest created"},
    )
    db.add(log1)

    # Add audit log with ingest_id in details (e.g., transaction_pending_created)
    log2 = AuditLog(
        action="transaction_pending_created",
        entity_id="txn-123",
        entity_type="transaction",
        details={
            "ingest_id": ingest_job.id,
            "agent": "ingesta",
            "message": "Transaction staged",
        },
    )
    db.add(log2)
    db.commit()

    response = client.get(f"/api/v1/ingest/{ingest_job.id}/trace")
    assert response.status_code == 200
    data = response.json()
    # Should have 2 steps (one from each audit log)
    assert len(data["steps"]) == 2


def test_get_ingest_trace_running_status(client: TestClient, db: Session):
    """Should report 'running' status for non-terminal states."""
    # Create a sample ingest job with PENDING_PROCESSING status
    ingest_job = db_service.create_ingest_job(db, "test.pdf", "/tmp/test.pdf")
    ingest_job.status = IngestStatus.PENDING_PROCESSING
    db.commit()

    response = client.get(f"/api/v1/ingest/{ingest_job.id}/trace")
    assert response.status_code == 200
    data = response.json()
    assert data["overall_status"] == "running"


def test_get_ingest_status_has_absolute_trace_url(client: TestClient, db: Session):
    """Should return absolute URL for trace_url in ingest status."""
    # Create a sample ingest job
    ingest_job = db_service.create_ingest_job(db, "test.pdf", "/tmp/test.pdf")
    db.commit()

    response = client.get(f"/api/v1/ingest/{ingest_job.id}")
    assert response.status_code == 200
    data = response.json()
    # Should be absolute URL starting with http
    assert data["trace_url"].startswith("http://")
    assert f"/api/v1/ingest/{ingest_job.id}/trace" in data["trace_url"]
