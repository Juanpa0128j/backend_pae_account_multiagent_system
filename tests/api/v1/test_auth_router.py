"""Tests for auth router — company membership endpoints."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.auth import CurrentUser, get_current_user
from app.core.database import Base, get_db
from app.models.database import CompanySettings, UserCompany
from main import app

TEST_USER_ID = str(uuid4())
TEST_USER_EMAIL = "testuser@example.com"
TEST_NIT = "800999001"
TEST_NIT_OTHER = "800999002"


@pytest.fixture
def db_engine():
    """Shared in-memory SQLite engine."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture
def client(db_engine):
    """TestClient with SQLite DB and mocked auth."""
    SessionLocal = sessionmaker(bind=db_engine)

    def get_db_test():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    async def mock_current_user() -> CurrentUser:
        return CurrentUser(id=UUID(TEST_USER_ID), email=TEST_USER_EMAIL)

    app.dependency_overrides[get_db] = get_db_test
    app.dependency_overrides[get_current_user] = mock_current_user
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def db(db_engine):
    """Direct DB session sharing engine with client."""
    SessionLocal = sessionmaker(bind=db_engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def seeded_company(db: Session):
    """Insert a CompanySettings row for TEST_NIT."""
    company = CompanySettings(nit=TEST_NIT, nombre="Test Company SA")
    db.add(company)
    db.commit()
    return company


@pytest.fixture
def seeded_membership(db: Session, seeded_company: CompanySettings):
    """Insert a UserCompany row for (TEST_USER_ID, TEST_NIT)."""
    membership = UserCompany(
        user_id=TEST_USER_ID,
        company_nit=TEST_NIT,
        joined_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db.add(membership)
    db.commit()
    return membership


# ─── GET /auth/companies ─────────────────────────────────────────


def test_list_companies_empty(client: TestClient):
    """Returns empty list when user has no memberships."""
    resp = client.get("/api/v1/auth/companies")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_companies_returns_memberships(
    client: TestClient, seeded_membership: UserCompany
):
    """Returns list with the user's membership."""
    resp = client.get("/api/v1/auth/companies")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["user_id"] == TEST_USER_ID
    assert data[0]["company_nit"] == TEST_NIT


# ─── POST /auth/companies/join ───────────────────────────────────


def test_join_company_success(client: TestClient, seeded_company: CompanySettings):
    """201 with new membership when NIT exists and not already joined."""
    resp = client.post("/api/v1/auth/companies/join", json={"nit": TEST_NIT})
    assert resp.status_code == 201
    data = resp.json()
    assert data["user_id"] == TEST_USER_ID
    assert data["company_nit"] == TEST_NIT
    assert "joined_at" in data


def test_join_company_unknown_nit(client: TestClient):
    """404 when NIT does not exist in company_settings."""
    resp = client.post("/api/v1/auth/companies/join", json={"nit": "000000000"})
    assert resp.status_code == 404


def test_join_company_duplicate(client: TestClient, seeded_membership: UserCompany):
    """409 when user already member of the company."""
    resp = client.post("/api/v1/auth/companies/join", json={"nit": TEST_NIT})
    assert resp.status_code == 409


# ─── DELETE /auth/companies/{nit} ───────────────────────────────


def test_leave_company_success(client: TestClient, seeded_membership: UserCompany):
    """204 when membership exists."""
    resp = client.delete(f"/api/v1/auth/companies/{TEST_NIT}")
    assert resp.status_code == 204


def test_leave_company_not_found(client: TestClient):
    """404 when membership does not exist."""
    resp = client.delete(f"/api/v1/auth/companies/{TEST_NIT}")
    assert resp.status_code == 404
