"""Tests for /api/v1/tax/ajustes-fiscales endpoints."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

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


def _make_row(**overrides):
    row = MagicMock()
    row.id = overrides.get("id", "00000000-0000-0000-0000-000000000001")
    row.company_nit = overrides.get("company_nit", "900123456")
    row.year = overrides.get("year", 2026)
    row.seccion = overrides.get("seccion", "ESF_ACTIVO")
    row.concepto = overrides.get("concepto", "depreciacion_acelerada")
    row.valor_contable = Decimal(str(overrides.get("valor_contable", "100000.00")))
    row.valor_fiscal = Decimal(str(overrides.get("valor_fiscal", "120000.00")))
    row.tipo_diferencia = overrides.get("tipo_diferencia", "temporaria_imponible")
    row.descripcion = overrides.get("descripcion", None)
    return row


class TestListAjustesFiscales:
    def test_list_success(self, client_with_db):
        client, _ = client_with_db
        from app.services import db_service

        original = db_service.list_ajustes_fiscales
        db_service.list_ajustes_fiscales = MagicMock(
            return_value=[_make_row(), _make_row(id="x", seccion="ERI_GASTO")]
        )
        try:
            r = client.get(
                "/api/v1/tax/ajustes-fiscales?company_nit=900123456&year=2026"
            )
            assert r.status_code == 200
            data = r.json()
            assert len(data) == 2
            assert data[0]["seccion"] == "ESF_ACTIVO"
        finally:
            db_service.list_ajustes_fiscales = original

    def test_list_with_seccion_filter(self, client_with_db):
        client, _ = client_with_db
        from app.services import db_service

        original = db_service.list_ajustes_fiscales
        mock_fn = MagicMock(return_value=[])
        db_service.list_ajustes_fiscales = mock_fn
        try:
            r = client.get(
                "/api/v1/tax/ajustes-fiscales?company_nit=900123456&year=2026&seccion=ESF_ACTIVO"
            )
            assert r.status_code == 200
            assert (
                mock_fn.call_args.args[3] == "ESF_ACTIVO"
                or (mock_fn.call_args.kwargs.get("seccion") == "ESF_ACTIVO")
                or "ESF_ACTIVO" in mock_fn.call_args.args
            )
        finally:
            db_service.list_ajustes_fiscales = original

    def test_list_missing_nit_returns_422(self, client_with_db):
        client, _ = client_with_db
        r = client.get("/api/v1/tax/ajustes-fiscales?year=2026")
        assert r.status_code == 422

    def test_list_missing_year_returns_422(self, client_with_db):
        client, _ = client_with_db
        r = client.get("/api/v1/tax/ajustes-fiscales?company_nit=900123456")
        assert r.status_code == 422


class TestUpsertAjusteFiscal:
    def test_upsert_success(self, client_with_db):
        client, _ = client_with_db
        from app.services import db_service

        original = db_service.upsert_ajuste_fiscal
        db_service.upsert_ajuste_fiscal = MagicMock(return_value=_make_row())
        try:
            r = client.put(
                "/api/v1/tax/ajustes-fiscales",
                json={
                    "company_nit": "900123456",
                    "year": 2026,
                    "seccion": "ESF_ACTIVO",
                    "concepto": "depreciacion_acelerada",
                    "valor_contable": 100000.0,
                    "valor_fiscal": 120000.0,
                    "tipo_diferencia": "temporaria_imponible",
                },
            )
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["seccion"] == "ESF_ACTIVO"
            assert data["valor_fiscal"] == 120000.0
        finally:
            db_service.upsert_ajuste_fiscal = original

    def test_upsert_invalid_seccion_rejected(self, client_with_db):
        client, _ = client_with_db
        r = client.put(
            "/api/v1/tax/ajustes-fiscales",
            json={
                "company_nit": "900123456",
                "year": 2026,
                "seccion": "INVALID",
                "concepto": "x",
                "valor_contable": 0,
                "valor_fiscal": 0,
                "tipo_diferencia": "permanente",
            },
        )
        assert r.status_code == 422

    def test_upsert_invalid_tipo_diferencia_rejected(self, client_with_db):
        client, _ = client_with_db
        r = client.put(
            "/api/v1/tax/ajustes-fiscales",
            json={
                "company_nit": "900123456",
                "year": 2026,
                "seccion": "ESF_ACTIVO",
                "concepto": "x",
                "valor_contable": 0,
                "valor_fiscal": 0,
                "tipo_diferencia": "no_existe",
            },
        )
        assert r.status_code == 422


class TestDeleteAjusteFiscal:
    def test_delete_success(self, client_with_db):
        client, _ = client_with_db
        from app.services import db_service

        original = db_service.delete_ajuste_fiscal
        db_service.delete_ajuste_fiscal = MagicMock(return_value=True)
        try:
            r = client.delete(
                "/api/v1/tax/ajustes-fiscales/00000000-0000-0000-0000-000000000001"
            )
            assert r.status_code == 204
        finally:
            db_service.delete_ajuste_fiscal = original

    def test_delete_missing_returns_404(self, client_with_db):
        client, _ = client_with_db
        from app.services import db_service

        original = db_service.delete_ajuste_fiscal
        db_service.delete_ajuste_fiscal = MagicMock(return_value=False)
        try:
            r = client.delete("/api/v1/tax/ajustes-fiscales/missing-id")
            assert r.status_code == 404
        finally:
            db_service.delete_ajuste_fiscal = original
