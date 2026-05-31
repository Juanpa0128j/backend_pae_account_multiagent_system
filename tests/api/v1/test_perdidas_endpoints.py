"""Tests for GET/POST/DELETE /api/v1/tax/perdidas-acumuladas endpoints."""

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

    def _override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app), mock_db
    app.dependency_overrides.pop(get_db, None)


def _make_perdida_row(
    id: int = 1,
    company_nit: str = "900123456",
    year: int = 2023,
    monto_perdida: str = "1000000.00",
    monto_compensado: str = "0.00",
    monto_pendiente: str = "1000000.00",
    decreto: str | None = "Art. 147 ET",
    notas: str | None = None,
):
    row = MagicMock()
    row.id = id
    row.company_nit = company_nit
    row.year = year
    row.monto_perdida = Decimal(monto_perdida)
    row.monto_compensado = Decimal(monto_compensado)
    row.monto_pendiente = Decimal(monto_pendiente)
    row.decreto = decreto
    row.notas = notas
    return row


# ---------------------------------------------------------------------------
# GET /api/v1/tax/perdidas-acumuladas
# ---------------------------------------------------------------------------


class TestListPerdidasAcumuladas:
    def test_list_all_without_year_filter(self, client_with_db):
        client, mock_db = client_with_db
        rows = [_make_perdida_row(id=1, year=2022), _make_perdida_row(id=2, year=2023)]
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = (
            rows
        )

        response = client.get("/api/v1/tax/perdidas-acumuladas?nit=900123456")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    def test_list_with_year_filter_calls_get_perdidas_disponibles(self, client_with_db):
        client, mock_db = client_with_db
        rows = [_make_perdida_row(id=1, year=2022)]

        with patch(
            "app.services.db_service.get_perdidas_disponibles", return_value=rows
        ) as mock_fn:
            response = client.get(
                "/api/v1/tax/perdidas-acumuladas?nit=900123456&year=2026"
            )

        assert response.status_code == 200
        mock_fn.assert_called_once()

    def test_invalid_nit_returns_422(self, client_with_db):
        client, _ = client_with_db
        response = client.get("/api/v1/tax/perdidas-acumuladas?nit=")
        # Empty NIT should fail NIT validation or query validation
        assert response.status_code in (400, 422)

    def test_missing_nit_returns_422(self, client_with_db):
        client, _ = client_with_db
        response = client.get("/api/v1/tax/perdidas-acumuladas")
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/v1/tax/perdidas-acumuladas
# ---------------------------------------------------------------------------


class TestUpsertPerdidaAcumulada:
    def test_upsert_success(self, client_with_db):
        client, mock_db = client_with_db
        row = _make_perdida_row()

        with patch("app.services.db_service.upsert_perdida", return_value=row):
            response = client.post(
                "/api/v1/tax/perdidas-acumuladas",
                json={
                    "company_nit": "900123456",
                    "year": 2023,
                    "monto_perdida": 1000000,
                    "decreto": "Art. 147 ET",
                },
            )

        assert response.status_code == 201
        data = response.json()
        assert data["year"] == 2023
        assert data["monto_perdida"] == "1000000.00"

    def test_missing_required_fields_returns_422(self, client_with_db):
        client, _ = client_with_db
        response = client.post(
            "/api/v1/tax/perdidas-acumuladas",
            json={"company_nit": "900123456"},  # missing year and monto_perdida
        )
        assert response.status_code == 422

    def test_negative_monto_perdida_returns_422(self, client_with_db):
        client, _ = client_with_db
        response = client.post(
            "/api/v1/tax/perdidas-acumuladas",
            json={"company_nit": "900123456", "year": 2023, "monto_perdida": -100},
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /api/v1/tax/perdidas-acumuladas/{id}
# ---------------------------------------------------------------------------


class TestDeletePerdidaAcumulada:
    def test_delete_existing_returns_204(self, client_with_db):
        client, mock_db = client_with_db
        row = _make_perdida_row()
        mock_db.query.return_value.filter.return_value.first.return_value = row

        response = client.delete("/api/v1/tax/perdidas-acumuladas/1")

        assert response.status_code == 204
        mock_db.delete.assert_called_once_with(row)
        mock_db.commit.assert_called()

    def test_delete_nonexistent_returns_404(self, client_with_db):
        client, mock_db = client_with_db
        mock_db.query.return_value.filter.return_value.first.return_value = None

        response = client.delete("/api/v1/tax/perdidas-acumuladas/999")

        assert response.status_code == 404
