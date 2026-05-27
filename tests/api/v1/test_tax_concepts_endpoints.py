"""Tests for GET/PUT/DELETE /api/v1/tax/concepts endpoints."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from main import app


@pytest.fixture
def client_with_db():
    mock_db = MagicMock()

    def _override():
        yield mock_db

    app.dependency_overrides[get_db] = _override
    yield TestClient(app), mock_db
    app.dependency_overrides.pop(get_db, None)


def _row(code="compras_pj", aplica_a="PJ", categoria="compras", renglon="25"):
    return {
        "code": code,
        "label": "Compras PJ",
        "renglon_350": renglon,
        "aplica_a": aplica_a,
        "tarifa_default": 0.025,
        "base_minima_uvt": 27.0,
        "categoria": categoria,
        "art_referencia": "Art. 392 ET",
        "activo": True,
    }


def _orm_row(code="compras_pj", aplica_a="PJ", categoria="compras", renglon="25"):
    row = MagicMock()
    row.code = code
    row.label = "Compras PJ"
    row.renglon_350 = renglon
    row.aplica_a = aplica_a
    row.tarifa_default = Decimal("0.0250")
    row.base_minima_uvt = Decimal("27")
    row.categoria = categoria
    row.art_referencia = "Art. 392 ET"
    row.activo = True
    return row


# --- GET ---


def test_list_returns_rows(client_with_db):
    client, _ = client_with_db
    with patch(
        "app.services.db_service.list_tax_concepts",
        return_value=[_row(), _row(code="compras_pn", aplica_a="PN", renglon="27")],
    ):
        resp = client.get("/api/v1/tax/concepts")
    assert resp.status_code == 200
    data = resp.json()
    assert {r["code"] for r in data} == {"compras_pj", "compras_pn"}


def test_list_empty(client_with_db):
    client, _ = client_with_db
    with patch("app.services.db_service.list_tax_concepts", return_value=[]):
        resp = client.get("/api/v1/tax/concepts")
    assert resp.status_code == 200
    assert resp.json() == []


# --- PUT ---


def test_upsert_creates_row(client_with_db):
    client, _ = client_with_db
    with patch(
        "app.services.db_service.upsert_tax_concept", return_value=_orm_row()
    ) as mock_upsert:
        resp = client.put(
            "/api/v1/tax/concepts",
            json={
                "code": "compras_pj",
                "label": "Compras PJ",
                "renglon_350": "25",
                "aplica_a": "PJ",
                "categoria": "compras",
                "tarifa_default": 0.025,
                "base_minima_uvt": 27.0,
                "art_referencia": "Art. 392 ET",
            },
        )
    assert resp.status_code == 200
    assert mock_upsert.called
    data = resp.json()
    assert data["code"] == "compras_pj"
    assert data["renglon_350"] == "25"


def test_upsert_rejects_invalid_aplica_a(client_with_db):
    client, _ = client_with_db
    resp = client.put(
        "/api/v1/tax/concepts",
        json={
            "code": "x",
            "label": "x",
            "renglon_350": "1",
            "aplica_a": "ZZ",
            "categoria": "compras",
        },
    )
    assert resp.status_code == 422


def test_upsert_rejects_invalid_categoria(client_with_db):
    client, _ = client_with_db
    resp = client.put(
        "/api/v1/tax/concepts",
        json={
            "code": "x",
            "label": "x",
            "renglon_350": "1",
            "aplica_a": "PJ",
            "categoria": "invalid_cat",
        },
    )
    assert resp.status_code == 422


# --- DELETE ---


def test_delete_existing(client_with_db):
    client, _ = client_with_db
    with patch(
        "app.services.db_service.soft_delete_tax_concept",
        return_value=_orm_row(),
    ):
        resp = client.delete("/api/v1/tax/concepts/compras_pj")
    assert resp.status_code == 204


def test_delete_missing_returns_404(client_with_db):
    client, _ = client_with_db
    with patch("app.services.db_service.soft_delete_tax_concept", return_value=None):
        resp = client.delete("/api/v1/tax/concepts/nope")
    assert resp.status_code == 404
