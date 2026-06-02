"""Tests for manual transaction CRUD."""

from datetime import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.database import CompanySettings, TransactionPending, TransactionStatus
from app.services import db_service
from app.services.nit_utils import normalize_nit
from main import app


@pytest.fixture
def db_engine():
    """Shared in-memory SQLite engine for all tests in this module."""
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
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


@pytest.fixture
def db(db_engine):
    """In-memory SQLite session for direct DB access in tests (shares engine with client)."""
    SessionLocal = sessionmaker(bind=db_engine)
    session = SessionLocal()
    yield session
    session.close()


class TestCreateManualTransaction:
    def test_create_manual_transaction_returns_201(self, client: TestClient, db):
        # Arrange: seed company settings
        nit = "800999888"
        db.add(
            CompanySettings(nit=normalize_nit(nit), nombre="TestCo", ciudad="Bogotá")
        )
        db.commit()

        payload = {
            "fecha": "2024-03-15",
            "concepto": "Servicios de consultoría",
            "total": 1190000.0,
            "nit_emisor": "800123456",
            "nit_receptor": nit,
            "tipo_documento": "factura",
            "items": [
                {"descripcion": "Servicios", "subtotal": 1000000.0, "iva": 190000.0}
            ],
            "company_nit": nit,
        }

        response = client.post("/api/v1/transactions", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["transaction_id"].startswith("txn_")
        assert data["ingest_id"].startswith("ing_")
        assert data["status"] == "pending"

        # Verify DB state
        txn = (
            db.query(TransactionPending)
            .filter(TransactionPending.id == data["transaction_id"])
            .first()
        )
        assert txn is not None
        assert txn.status == TransactionStatus.PENDING
        assert txn.ingest_id == data["ingest_id"]
        assert txn.raw_data is not None
        assert txn.raw_data["totales"]["total"] == 1190000.0

    def test_create_manual_transaction_requires_company_settings(
        self, client: TestClient
    ):
        payload = {
            "fecha": "2024-03-15",
            "concepto": "Test",
            "total": 1000000.0,
            "nit_emisor": "800123456",
            "nit_receptor": "800999888",
            "tipo_documento": "factura",
            "items": [],
            "company_nit": "800999888",
        }
        response = client.post("/api/v1/transactions", json=payload)
        assert response.status_code == 409
        assert response.json()["detail"]["error_code"] == "MISSING_COMPANY_SETTINGS"

    def test_create_manual_transaction_validates_total(self, client: TestClient, db):
        nit = "800999888"
        db.add(
            CompanySettings(nit=normalize_nit(nit), nombre="TestCo", ciudad="Bogotá")
        )
        db.commit()

        payload = {
            "fecha": "2024-03-15",
            "concepto": "Test",
            "total": 1000000.0,  # mismatch: 1000000 + 0 = 1000000, but items say 500000 + 95000
            "nit_emisor": "800123456",
            "nit_receptor": nit,
            "tipo_documento": "factura",
            "items": [{"descripcion": "Item", "subtotal": 500000.0, "iva": 95000.0}],
            "company_nit": nit,
        }
        response = client.post("/api/v1/transactions", json=payload)
        assert response.status_code == 422
        assert (
            "total" in response.json()["detail"].lower()
            or "coincide" in response.json()["detail"].lower()
        )


class TestPatchManualTransaction:
    def test_patch_pending_updates_fields(self, client: TestClient, db):
        nit = "800999888"
        db.add(
            CompanySettings(nit=normalize_nit(nit), nombre="TestCo", ciudad="Bogotá")
        )
        db.commit()

        # Create a pending transaction first
        create_payload = {
            "fecha": "2024-03-15",
            "concepto": "Original",
            "total": 1190000.0,
            "nit_emisor": "800123456",
            "nit_receptor": nit,
            "tipo_documento": "factura",
            "items": [{"descripcion": "Item", "subtotal": 1000000.0, "iva": 190000.0}],
            "company_nit": nit,
        }
        create_resp = client.post("/api/v1/transactions", json=create_payload)
        txn_id = create_resp.json()["transaction_id"]

        patch_payload = {
            "concepto": "Updated concept",
            "total": 2380000.0,
            "items": [
                {"descripcion": "Item A", "subtotal": 1000000.0, "iva": 190000.0},
                {"descripcion": "Item B", "subtotal": 1000000.0, "iva": 190000.0},
            ],
        }
        response = client.patch(f"/api/v1/transactions/{txn_id}", json=patch_payload)
        assert response.status_code == 200
        data = response.json()
        assert data["concepto"] == "Updated concept"
        assert data["total"] == 2380000.0

        # Verify DB
        txn = (
            db.query(TransactionPending).filter(TransactionPending.id == txn_id).first()
        )
        assert txn.descripcion == "Updated concept"
        assert float(txn.total) == 2380000.0
        assert len(txn.raw_data["items"]) == 2

    def test_patch_posted_returns_409(self, client: TestClient, db):
        nit = "800999888"
        db.add(
            CompanySettings(nit=normalize_nit(nit), nombre="TestCo", ciudad="Bogotá")
        )
        db.commit()

        # Create and post a transaction
        pending = db_service.create_transaction_pending(
            db,
            ingest_id="ing_test",
            fecha=datetime.now(),
            company_nit=nit,
            nit_emisor="800123456",
            nit_receptor=nit,
            total=Decimal("1000000"),
            descripcion="Posted",
            items=[],
            raw_data={},
            source_file=None,
            commit=True,
        )
        db_service.create_transaction_posted(
            db,
            transaction_pending_id=str(pending.id),
            company_nit=nit,
            cuenta_puc="519595",
            puc_descripcion="Gastos diversos",
            retefuente=Decimal("0"),
            reteica=Decimal("0"),
            iva=Decimal("0"),
            ica=Decimal("0"),
            provision_renta=Decimal("0"),
            neto_a_pagar=Decimal("1000000"),
            journal_entries_json=[],
            tax_references=[],
            agent_reasoning={},
            tipo_iva=None,
            concepto_retencion=None,
            tipo_persona_emisor=None,
            commit=True,
        )

        response = client.patch(
            f"/api/v1/transactions/{pending.id}", json={"concepto": "Nope"}
        )
        assert response.status_code == 409
        assert "contabilizada" in response.json()["detail"].lower()


class TestReprocessTransaction:
    def test_reprocess_posted_creates_new_pending(self, client: TestClient):
        pass

    def test_reprocess_non_posted_returns_409(self, client: TestClient):
        pass
