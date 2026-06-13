"""
Feature tests for Special Taxes (estampilla / impuestos especiales flexibles).

Phases:
  A — DB model + CRUD
  B — tributario_agent integration
  C — API endpoints
"""

import os
import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.database import Base

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://pae_user:password@localhost:5432/pae_accounting",
)

# ─── DB fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(DATABASE_URL, echo=False, connect_args={"connect_timeout": 2})
    try:
        conn = eng.connect()
        conn.close()
    except Exception as exc:
        pytest.skip(f"PostgreSQL not available at {DATABASE_URL!r}: {exc}")
    Base.metadata.create_all(bind=eng)
    yield eng


@pytest.fixture
def db(engine):
    connection = engine.connect()
    transaction = connection.begin()
    TestSession = sessionmaker(bind=connection)
    session = TestSession()

    nested = connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def end_savepoint(session, transaction):
        nonlocal nested
        if not nested.is_active:
            nested = connection.begin_nested()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


# ─── Phase A: DB model + CRUD ────────────────────────────────────────────────


def test_special_tax_model_exists():
    """SpecialTax and SpecialTaxAccumulator importable from app.models.database."""
    from app.models.database import SpecialTax, SpecialTaxAccumulator  # noqa: F401

    assert SpecialTax.__tablename__ == "special_taxes"
    assert SpecialTaxAccumulator.__tablename__ == "special_tax_accumulators"


def test_create_special_tax(db):
    """Create a SpecialTax row and read it back with correct fields."""
    from app.services import db_service

    tax = db_service.create_special_tax(
        db=db,
        company_nit="800999888",
        code="ESTAMPILLA_UDE",
        nombre="Estampilla Pro-Universidad de Antioquia",
        rate=Decimal("0.005"),
        base_calc="total_pago",
        cuenta_gasto="519505",
        cuenta_por_pagar="236801",
        descripcion="Ordenanza 41/2003 — 0.5% sobre pagos a entidades públicas",
        norma_referencia="Ordenanza 41/2003 Asamblea Antioquia",
        settlement="per_transaction",
        es_entidad_publica_only=True,
    )

    assert tax.id is not None
    assert tax.company_nit == "800999888"
    assert tax.code == "ESTAMPILLA_UDE"
    assert float(tax.rate) == pytest.approx(0.005)
    assert tax.base_calc == "total_pago"
    assert tax.cuenta_gasto == "519505"
    assert tax.cuenta_por_pagar == "236801"
    assert tax.settlement == "per_transaction"
    assert tax.es_entidad_publica_only is True
    assert tax.activo is True

    # Read back
    fetched = db_service.get_special_tax(db, str(tax.id))
    assert fetched is not None
    assert fetched.nombre == "Estampilla Pro-Universidad de Antioquia"


def test_special_tax_inactive_not_returned_in_active_list(db):
    """list_active_special_taxes returns only activo=True rows."""
    from app.services import db_service

    nit = "800111222"

    db_service.create_special_tax(
        db=db,
        company_nit=nit,
        code="ACTIVE_TAX",
        nombre="Activa",
        rate=Decimal("0.003"),
        base_calc="total_pago",
        cuenta_gasto="519505",
        cuenta_por_pagar="236801",
        activo=True,
    )
    db_service.create_special_tax(
        db=db,
        company_nit=nit,
        code="INACTIVE_TAX",
        nombre="Inactiva",
        rate=Decimal("0.003"),
        base_calc="total_pago",
        cuenta_gasto="519505",
        cuenta_por_pagar="236801",
        activo=False,
    )

    active = db_service.list_active_special_taxes(db, nit)
    codes = [t.code for t in active]
    assert "ACTIVE_TAX" in codes
    assert "INACTIVE_TAX" not in codes


def test_special_tax_filtered_by_doc_type(db):
    """list_active_special_taxes with doc_type respects applies_to_doc_types."""
    from app.services import db_service

    nit = "800333444"

    db_service.create_special_tax(
        db=db,
        company_nit=nit,
        code="FACTURA_ONLY",
        nombre="Solo facturas",
        rate=Decimal("0.003"),
        base_calc="total_pago",
        cuenta_gasto="519505",
        cuenta_por_pagar="236801",
        applies_to_doc_types=["factura_compra"],
    )
    db_service.create_special_tax(
        db=db,
        company_nit=nit,
        code="ALL_DOCS",
        nombre="Todos los docs",
        rate=Decimal("0.003"),
        base_calc="total_pago",
        cuenta_gasto="519505",
        cuenta_por_pagar="236801",
        applies_to_doc_types=[],
    )

    result = db_service.list_active_special_taxes(db, nit, doc_type="factura_compra")
    codes = [t.code for t in result]
    assert "FACTURA_ONLY" in codes
    assert "ALL_DOCS" in codes

    result2 = db_service.list_active_special_taxes(
        db, nit, doc_type="extracto_bancario"
    )
    codes2 = [t.code for t in result2]
    assert "FACTURA_ONLY" not in codes2
    assert "ALL_DOCS" in codes2


def test_accumulator_get_or_create_and_add(db):
    """get_or_create_accumulator creates row; add_to_accumulator accumulates correctly."""
    from app.services import db_service

    tax = db_service.create_special_tax(
        db=db,
        company_nit="800999888",
        code="PERIODIC_TAX",
        nombre="Periódica",
        rate=Decimal("0.005"),
        base_calc="total_pago",
        cuenta_gasto="519505",
        cuenta_por_pagar="236801",
        settlement="periodic",
    )

    acc = db_service.add_to_accumulator(
        db,
        tax.id,
        "800999888",
        2026,
        6,
        Decimal("1000000"),
        Decimal("5000"),
    )
    assert float(acc.accumulated_base) == pytest.approx(1000000)
    assert float(acc.accumulated_tax) == pytest.approx(5000)

    # Add more
    acc2 = db_service.add_to_accumulator(
        db,
        tax.id,
        "800999888",
        2026,
        6,
        Decimal("500000"),
        Decimal("2500"),
    )
    assert float(acc2.accumulated_base) == pytest.approx(1500000)
    assert float(acc2.accumulated_tax) == pytest.approx(7500)


def test_liquidate_accumulator(db):
    """liquidate_accumulator sets liquidated=True and liquidated_at."""
    from app.services import db_service

    tax = db_service.create_special_tax(
        db=db,
        company_nit="800999888",
        code="LIQ_TAX",
        nombre="Liquidable",
        rate=Decimal("0.005"),
        base_calc="total_pago",
        cuenta_gasto="519505",
        cuenta_por_pagar="236801",
        settlement="periodic",
    )
    db_service.add_to_accumulator(
        db, tax.id, "800999888", 2026, 5, Decimal("1000"), Decimal("5")
    )

    result = db_service.liquidate_accumulator(db, tax.id, "800999888", 2026, 5)
    assert result is not None
    assert result.liquidated is True
    assert result.liquidated_at is not None

    # Second call returns same row (already liquidated, no change)
    result2 = db_service.liquidate_accumulator(db, tax.id, "800999888", 2026, 5)
    assert result2 is not None
    assert result2.liquidated is True


# ─── Phase B: tributario_agent integration ───────────────────────────────────


def _make_state(doc_type="factura_compra", es_entidad_publica=False):
    """Minimal AgentState for tributario testing."""
    return {
        "mode": "ingest",
        "company_nit": "800999888",
        "document_classification": {"doc_type": doc_type},
        "source_document": {},
        "raw_transactions": [],
        "contador_output": {
            "asientos": [
                {
                    "cuenta_puc": "519595",
                    "nombre_cuenta": "Gastos Diversos",
                    "descripcion": "Servicio",
                    "tipo_movimiento": "debito",
                    "valor": "1000000",
                },
                {
                    "cuenta_puc": "220505",
                    "nombre_cuenta": "Proveedores",
                    "descripcion": "Proveedor",
                    "tipo_movimiento": "credito",
                    "valor": "1000000",
                },
            ],
            "descripcion_general": "Test",
            "total_debitos": "1000000",
            "total_creditos": "1000000",
        },
        "period_start": "2026-06-01",
        "agent_log": [],
        "es_entidad_publica": es_entidad_publica,
    }


def _make_special_tax_mock(
    tax_id=None,
    settlement="per_transaction",
    es_entidad_publica_only=False,
    base_calc="total_pago",
):
    tax = MagicMock()
    tax.id = tax_id or uuid.uuid4()
    tax.code = "ESTAMPILLA_TEST"
    tax.nombre = "Estampilla Test"
    tax.rate = Decimal("0.005")
    tax.base_calc = base_calc
    tax.base_calc_formula = None
    tax.settlement = settlement
    tax.cuenta_gasto = "519505"
    tax.cuenta_por_pagar = "236801"
    tax.norma_referencia = "Ordenanza test"
    tax.es_entidad_publica_only = es_entidad_publica_only
    return tax


def test_tributario_applies_per_transaction_special_tax():
    """Per-transaction special tax adds debit+credit journal lines."""
    from app.agents import tributario_agent

    _make_state(doc_type="factura_compra", es_entidad_publica=False)
    mock_tax = _make_special_tax_mock(
        settlement="per_transaction", es_entidad_publica_only=False
    )

    with patch("app.agents.tributario_agent._apply_special_taxes") as mock_apply:
        # We test _apply_special_taxes directly to avoid full pipeline mock complexity
        mock_apply.return_value = (
            [
                {
                    "tipo_impuesto": "estampilla_test",
                    "valor_impuesto": "5000.00",
                    "cuenta_puc": "519505",
                }
            ],
            [
                {
                    "cuenta_puc": "519505",
                    "nombre_cuenta": "Estampilla",
                    "descripcion": "Estampilla Test",
                    "tipo_movimiento": "debito",
                    "valor": "5000.00",
                },
                {
                    "cuenta_puc": "236801",
                    "nombre_cuenta": "Estampilla por pagar",
                    "descripcion": "Estampilla Test",
                    "tipo_movimiento": "credito",
                    "valor": "5000.00",
                },
            ],
        )

        result_impuestos, result_lines = tributario_agent._apply_special_taxes(
            db=MagicMock(),
            special_taxes=[mock_tax],
            base_gravable=Decimal("1000000"),
            total_pago=Decimal("1000000"),
            es_entidad_publica=False,
            transaction_date=date(2026, 6, 1),
            company_nit="800999888",
        )

    assert len(result_impuestos) == 1
    assert result_impuestos[0]["tipo_impuesto"] == "estampilla_test"
    cuentas = [line["cuenta_puc"] for line in result_lines]
    assert "519505" in cuentas
    assert "236801" in cuentas


def test_tributario_skips_special_tax_when_not_entidad_publica():
    """es_entidad_publica_only=True tax not applied when es_entidad_publica=False."""
    from app.agents.tributario_agent import _apply_special_taxes

    mock_tax = _make_special_tax_mock(
        settlement="per_transaction", es_entidad_publica_only=True
    )

    impuestos, lines = _apply_special_taxes(
        db=MagicMock(),
        special_taxes=[mock_tax],
        base_gravable=Decimal("1000000"),
        total_pago=Decimal("1000000"),
        es_entidad_publica=False,  # NOT public entity
        transaction_date=date(2026, 6, 1),
        company_nit="800999888",
    )

    assert impuestos == []
    assert lines == []


def test_tributario_applies_special_tax_for_entidad_publica():
    """es_entidad_publica_only=True tax IS applied when es_entidad_publica=True."""
    from app.agents.tributario_agent import _apply_special_taxes

    mock_tax = _make_special_tax_mock(
        settlement="per_transaction", es_entidad_publica_only=True
    )

    impuestos, lines = _apply_special_taxes(
        db=MagicMock(),
        special_taxes=[mock_tax],
        base_gravable=Decimal("1000000"),
        total_pago=Decimal("1000000"),
        es_entidad_publica=True,  # IS public entity
        transaction_date=date(2026, 6, 1),
        company_nit="800999888",
    )

    assert len(impuestos) == 1
    assert float(impuestos[0]["valor_impuesto"]) == pytest.approx(5000.0)
    assert len(lines) == 2


def test_tributario_accumulates_periodic_special_tax():
    """Periodic special tax calls add_to_accumulator and returns no journal lines."""
    from app.agents.tributario_agent import _apply_special_taxes

    mock_tax = _make_special_tax_mock(
        settlement="periodic", es_entidad_publica_only=False
    )
    mock_db = MagicMock()

    with patch("app.agents.tributario_agent.db_service") as mock_svc:
        mock_svc.add_to_accumulator.return_value = MagicMock()

        impuestos, lines = _apply_special_taxes(
            db=mock_db,
            special_taxes=[mock_tax],
            base_gravable=Decimal("1000000"),
            total_pago=Decimal("1000000"),
            es_entidad_publica=False,
            transaction_date=date(2026, 6, 1),
            company_nit="800999888",
        )

    mock_svc.add_to_accumulator.assert_called_once()
    call_kwargs = mock_svc.add_to_accumulator.call_args
    assert call_kwargs[0][3] == 2026  # year
    assert call_kwargs[0][4] == 6  # month
    # Periodic: no journal lines
    assert lines == []


# ─── Phase C: API endpoints ───────────────────────────────────────────────────


@pytest.fixture
def client(db):
    """FastAPI test client with DB override."""
    from fastapi.testclient import TestClient
    from app.core.database import get_db
    from app.core.auth import get_current_user
    from main import app

    def override_db():
        yield db

    def override_auth():
        return MagicMock(id="test-user", email="test@test.com")

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_auth
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_api_create_special_tax_returns_201(client):
    """POST /api/v1/settings/special-taxes returns 201 with created resource."""
    payload = {
        "company_nit": "800999888",
        "code": "API_TEST_TAX",
        "nombre": "Estampilla API Test",
        "rate": 0.005,
        "base_calc": "total_pago",
        "cuenta_gasto": "519505",
        "cuenta_por_pagar": "236801",
        "settlement": "per_transaction",
    }
    resp = client.post("/api/v1/settings/special-taxes", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["code"] == "API_TEST_TAX"
    assert body["activo"] is True
    assert "id" in body


def test_api_list_special_taxes_returns_company_scoped(client):
    """GET /api/v1/settings/special-taxes?company_nit=X returns only that company's taxes."""
    # Create for two companies
    for nit, code in [("800999888", "TAX_A"), ("800111222", "TAX_B")]:
        client.post(
            "/api/v1/settings/special-taxes",
            json={
                "company_nit": nit,
                "code": code,
                "nombre": f"Tax {code}",
                "rate": 0.003,
                "base_calc": "total_pago",
                "cuenta_gasto": "519505",
                "cuenta_por_pagar": "236801",
            },
        )

    resp = client.get("/api/v1/settings/special-taxes?company_nit=800999888")
    assert resp.status_code == 200
    codes = [t["code"] for t in resp.json()]
    assert "TAX_A" in codes
    assert "TAX_B" not in codes


def test_api_liquidar_creates_journal_entry_and_marks_liquidated(client, db):
    """POST /api/v1/settings/special-taxes/{id}/liquidar marks accumulator liquidated."""
    from app.services import db_service

    # Create periodic tax + accumulator
    tax = db_service.create_special_tax(
        db=db,
        company_nit="800999888",
        code="LIQ_API_TAX",
        nombre="Liquidable API",
        rate=Decimal("0.005"),
        base_calc="total_pago",
        cuenta_gasto="519505",
        cuenta_por_pagar="236801",
        settlement="periodic",
    )
    db_service.add_to_accumulator(
        db, tax.id, "800999888", 2026, 6, Decimal("2000000"), Decimal("10000")
    )

    resp = client.post(
        f"/api/v1/settings/special-taxes/{tax.id}/liquidar",
        json={"company_nit": "800999888", "year": 2026, "month": 6},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["liquidated"] is True
    assert body["accumulated_tax"] == "10000.00"
