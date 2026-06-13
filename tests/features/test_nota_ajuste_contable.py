"""
Feature tests: notas de ajuste contables.

Phase A — PDF upload path (doc type registration)
Phase B — Manual entry API endpoint
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("sqlalchemy")


# ─── Phase A: doc type registration ──────────────────────────────────────────


def test_nota_ajuste_contable_in_document_type_enum():
    from app.models.document_types import DocumentType

    assert hasattr(DocumentType, "NOTA_AJUSTE_CONTABLE")
    assert DocumentType.NOTA_AJUSTE_CONTABLE.value == "nota_ajuste_contable"


def test_nota_ajuste_contable_in_pathway_map():
    from app.models.document_types import DocumentType, IngestPathway, PATHWAY_MAP

    assert (
        PATHWAY_MAP[DocumentType.NOTA_AJUSTE_CONTABLE]
        == IngestPathway.BUILD_FROM_SCRATCH
    )


def test_nota_ajuste_not_in_via_b_types():
    from app.models.document_types import DocumentType, _VIA_B_TYPES

    assert DocumentType.NOTA_AJUSTE_CONTABLE not in _VIA_B_TYPES


def test_nota_ajuste_in_extract_method_map():
    from app.agents.ingest_agent import _EXTRACT_METHOD_MAP

    assert "nota_ajuste_contable" in _EXTRACT_METHOD_MAP


def test_nota_ajuste_not_in_via_b_statement_types():
    from app.agents.ingest_agent import _VIA_B_STATEMENT_TYPES

    assert "nota_ajuste_contable" not in _VIA_B_STATEMENT_TYPES


def test_nota_ajuste_in_doc_guidance():
    from app.core.prompts.contador import _DOC_GUIDANCE

    assert "nota_ajuste_contable" in _DOC_GUIDANCE
    guidance = _DOC_GUIDANCE["nota_ajuste_contable"]
    assert "Preserve" in guidance or "preserve" in guidance


def test_nota_ajuste_in_tributario_tax_neutral_set():
    """nota_ajuste_contable must be in the tax-declaration skip set in tributario_agent."""
    import inspect
    import app.agents.tributario_agent as trib

    source = inspect.getsource(trib)
    assert "nota_ajuste_contable" in source


# ─── Phase B: manual entry API ───────────────────────────────────────────────


def _ajuste_payload(**overrides):
    base = {
        "company_nit": "800999888",
        "fecha": "2026-06-13",
        "concepto": "Ajuste reclasificación gastos administrativos",
        "lines": [
            {
                "cuenta_puc": "511505",
                "tipo_movimiento": "debito",
                "valor": 1000000.00,
                "descripcion": "Honorarios reclasificados",
            },
            {
                "cuenta_puc": "511595",
                "tipo_movimiento": "credito",
                "valor": 1000000.00,
                "descripcion": "Reverso otros honorarios",
            },
        ],
    }
    base.update(overrides)
    return base


@pytest.fixture()
def client():
    """FastAPI TestClient with DB mocked out."""
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def _mock_db():
    db = MagicMock()
    db.add = MagicMock()
    db.commit = MagicMock()
    db.refresh = MagicMock()
    db.close = MagicMock()
    return db


def test_manual_ajuste_unbalanced_returns_422(client):
    payload = _ajuste_payload()
    # Make lines unbalanced: debito 1_000_000, credito 900_000
    payload["lines"][1]["valor"] = 900000.00
    resp = client.post("/api/v1/transactions/manual-ajuste", json=payload)
    assert resp.status_code == 422


def test_manual_ajuste_requires_at_least_two_lines(client):
    payload = _ajuste_payload()
    payload["lines"] = [payload["lines"][0]]  # only one line
    resp = client.post("/api/v1/transactions/manual-ajuste", json=payload)
    assert resp.status_code == 422


def test_manual_ajuste_balanced_creates_journal_lines(client):
    from main import app
    from app.core.database import get_db

    payload = _ajuste_payload()
    mock_db = _mock_db()

    def _override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = _override_get_db
    try:
        resp = client.post("/api/v1/transactions/manual-ajuste", json=payload)
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 201
    body = resp.json()
    assert "transaction_id" in body
    assert "lines_created" in body
    assert body["lines_created"] == 2
