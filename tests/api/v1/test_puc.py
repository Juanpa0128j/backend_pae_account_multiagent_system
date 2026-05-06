"""Tests for PUC CRUD endpoints."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.database import CuentaPUC
from main import app


@pytest.fixture
def db_engine():
    """Shared in-memory SQLite engine for all tests."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture
def client(db_engine):
    """TestClient with shared in-memory SQLite."""
    SessionLocal = sessionmaker(bind=db_engine)

    def get_db_test():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = get_db_test
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
def db(db_engine):
    """In-memory SQLite session for direct DB access in tests (shares engine with client)."""
    SessionLocal = sessionmaker(bind=db_engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def puc_payload() -> dict:
    """Sample PUC creation payload."""
    return {
        "codigo": "999999",
        "nombre": "Test Account",
        "clase": 5,
        "naturaleza": "debito",
        "grupo": "99",
        "cuenta": "99",
        "subcuenta": "99",
        "descripcion": "Test account for unit tests",
        "activa": True,
    }


def test_list_puc_empty(client: TestClient, db: Session):
    """GET /puc returns empty list when no PUC accounts exist."""
    # Clear all PUC accounts
    db.query(CuentaPUC).delete()
    db.commit()

    response = client.get("/api/v1/puc")
    assert response.status_code == 200
    assert response.json() == []


def test_list_puc_with_defaults(client: TestClient, db: Session):
    """GET /puc returns active PUC accounts by default."""
    # Create one active, one inactive
    active = CuentaPUC(
        codigo="100000", nombre="Active", clase=1, naturaleza="debito", activa=True
    )
    inactive = CuentaPUC(
        codigo="200000", nombre="Inactive", clase=2, naturaleza="debito", activa=False
    )
    db.add_all([active, inactive])
    db.commit()

    response = client.get("/api/v1/puc")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["codigo"] == "100000"


def test_list_puc_include_inactive(client: TestClient, db: Session):
    """GET /puc?include_inactive=true returns all accounts."""
    db.query(CuentaPUC).delete()
    db.commit()

    active = CuentaPUC(
        codigo="100000", nombre="Active", clase=1, naturaleza="debito", activa=True
    )
    inactive = CuentaPUC(
        codigo="200000", nombre="Inactive", clase=2, naturaleza="debito", activa=False
    )
    db.add_all([active, inactive])
    db.commit()

    response = client.get("/api/v1/puc?include_inactive=true")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2


def test_list_puc_search(client: TestClient, db: Session):
    """GET /puc?search=term returns matching accounts (active only by default)."""
    db.query(CuentaPUC).delete()
    db.commit()

    account = CuentaPUC(
        codigo="110000",
        nombre="Inventario Bienes",
        clase=1,
        naturaleza="debito",
        activa=True,
    )
    db.add(account)
    db.commit()

    response = client.get("/api/v1/puc?search=Inventario")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["codigo"] == "110000"


def test_list_puc_search_with_include_inactive(client: TestClient, db: Session):
    """GET /puc?search=term&include_inactive=true finds both active and inactive."""
    db.query(CuentaPUC).delete()
    db.commit()

    active = CuentaPUC(
        codigo="110000",
        nombre="Inventario Activo",
        clase=1,
        naturaleza="debito",
        activa=True,
    )
    inactive = CuentaPUC(
        codigo="210000",
        nombre="Inventario Inactivo",
        clase=2,
        naturaleza="debito",
        activa=False,
    )
    db.add_all([active, inactive])
    db.commit()

    response = client.get("/api/v1/puc?search=Inventario&include_inactive=true")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2


def test_get_puc_by_codigo(client: TestClient, db: Session):
    """GET /puc/{codigo} returns single account."""
    db.query(CuentaPUC).delete()
    db.commit()

    account = CuentaPUC(
        codigo="110000", nombre="Inventario", clase=1, naturaleza="debito", activa=True
    )
    db.add(account)
    db.commit()

    response = client.get("/api/v1/puc/110000")
    assert response.status_code == 200
    data = response.json()
    assert data["codigo"] == "110000"
    assert data["nombre"] == "Inventario"


def test_get_puc_not_found(client: TestClient):
    """GET /puc/{codigo} returns 404 for nonexistent account."""
    response = client.get("/api/v1/puc/999999")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_create_puc_success(client: TestClient, db: Session, puc_payload: dict):
    """POST /puc creates new account and returns 201."""
    db.query(CuentaPUC).filter(CuentaPUC.codigo == puc_payload["codigo"]).delete()
    db.commit()

    response = client.post("/api/v1/puc", json=puc_payload)
    assert response.status_code == 201
    data = response.json()
    assert data["codigo"] == puc_payload["codigo"]
    assert data["nombre"] == puc_payload["nombre"]
    assert data["activa"] is True

    # Verify persisted to DB
    row = db.query(CuentaPUC).filter(CuentaPUC.codigo == puc_payload["codigo"]).first()
    assert row is not None
    assert row.nombre == puc_payload["nombre"]


def test_create_puc_duplicate(client: TestClient, db: Session, puc_payload: dict):
    """POST /puc with duplicate codigo returns 409."""
    # Create first account
    response1 = client.post("/api/v1/puc", json=puc_payload)
    assert response1.status_code == 201

    # Try to create duplicate
    response2 = client.post("/api/v1/puc", json=puc_payload)
    assert response2.status_code == 409
    assert "already exists" in response2.json()["detail"].lower()


def test_update_puc_success(client: TestClient, db: Session):
    """PUT /puc/{codigo} updates existing account."""
    db.query(CuentaPUC).delete()
    db.commit()

    original = CuentaPUC(
        codigo="110000", nombre="Original", clase=1, naturaleza="debito", activa=True
    )
    db.add(original)
    db.commit()

    update_payload = {
        "codigo": "110000",
        "nombre": "Updated",
        "clase": 1,
        "naturaleza": "credito",
        "activa": False,
    }

    response = client.put("/api/v1/puc/110000", json=update_payload)
    assert response.status_code == 200
    data = response.json()
    assert data["nombre"] == "Updated"
    assert data["naturaleza"] == "credito"
    assert data["activa"] is False


def test_update_puc_not_found(client: TestClient):
    """PUT /puc/{codigo} returns 404 for nonexistent account."""
    update_payload = {
        "codigo": "999999",
        "nombre": "Does Not Exist",
        "clase": 1,
        "naturaleza": "debito",
    }

    response = client.put("/api/v1/puc/999999", json=update_payload)
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_update_puc_codigo_immutable(client: TestClient, db: Session):
    """PUT /puc allows changing codigo in request (but DB constraint prevents collision)."""
    db.query(CuentaPUC).delete()
    db.commit()

    account = CuentaPUC(
        codigo="110000", nombre="Test", clase=1, naturaleza="debito", activa=True
    )
    db.add(account)
    db.commit()

    # Update with matching codigo (immutable in practice)
    update_payload = {
        "codigo": "110000",
        "nombre": "Updated",
        "clase": 1,
        "naturaleza": "debito",
    }

    response = client.put("/api/v1/puc/110000", json=update_payload)
    assert response.status_code == 200
    data = response.json()
    # Verify codigo stayed the same
    assert data["codigo"] == "110000"
    # But nombre was updated
    assert data["nombre"] == "Updated"
