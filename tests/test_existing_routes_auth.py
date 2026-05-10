"""
TDD: Verify that existing endpoints return 401 when no Authorization header is sent.

Step 1 (RED): These tests fail because endpoints don't require auth yet.
Step 2 (GREEN): After adding get_current_user dependency, tests pass.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from main import app


@pytest.fixture()
def client_no_auth():
    """TestClient with in-memory SQLite but NO auth override — raw 401 testing."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)

    def get_db_test():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    from app.core.auth import get_current_user

    app.dependency_overrides[get_db] = get_db_test
    # Remove auth override so endpoints reject unauthenticated requests
    app.dependency_overrides.pop(get_current_user, None)
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.pop(get_db, None)


def test_dashboard_stats_returns_401_without_auth(client_no_auth):
    """GET /api/v1/dashboard/stats must return 401 when no Bearer token is sent."""
    response = client_no_auth.get("/api/v1/dashboard/stats")
    assert response.status_code == 401, (
        f"Expected 401, got {response.status_code}. "
        "Endpoint is not protected — add get_current_user dependency."
    )


def test_ingest_status_returns_401_without_auth(client_no_auth):
    """GET /api/v1/ingest/{id} must return 401 when no Bearer token is sent."""
    response = client_no_auth.get("/api/v1/ingest/nonexistent-id")
    assert response.status_code == 401, (
        f"Expected 401, got {response.status_code}. "
        "Endpoint is not protected — add get_current_user dependency."
    )


def test_process_status_returns_401_without_auth(client_no_auth):
    """GET /api/v1/process/status/{id} must return 401 when no Bearer token is sent."""
    response = client_no_auth.get("/api/v1/process/status/nonexistent-id")
    assert response.status_code == 401, (
        f"Expected 401, got {response.status_code}. "
        "Endpoint is not protected — add get_current_user dependency."
    )


def test_reports_balance_returns_401_without_auth(client_no_auth):
    """GET /api/v1/reports/balance must return 401 when no Bearer token is sent."""
    response = client_no_auth.get("/api/v1/reports/balance")
    assert response.status_code == 401, (
        f"Expected 401, got {response.status_code}. "
        "Endpoint is not protected — add get_current_user dependency."
    )
