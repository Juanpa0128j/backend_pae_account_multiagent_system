"""Tests for GET/POST/DELETE /api/v1/tax/tarifas-renta endpoints."""

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


def _make_tarifa_dict(
    id: int = 1,
    regimen: str = "ordinario",
    actividad: str | None = "general",
    tarifa_base: float = 0.35,
    sobretasa: float = 0.0,
    year_from: int = 2023,
    year_to: int | None = None,
    base_legal: str | None = "Art. 240 ET",
    notas: str | None = None,
) -> dict:
    return {
        "id": id,
        "regimen": regimen,
        "actividad": actividad,
        "tarifa_base": tarifa_base,
        "sobretasa": sobretasa,
        "tarifa_efectiva": tarifa_base + sobretasa,
        "year_from": year_from,
        "year_to": year_to,
        "base_legal": base_legal,
        "notas": notas,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/tax/tarifas-renta
# ---------------------------------------------------------------------------


class TestListTarifasRenta:
    def test_list_all_no_year(self, client_with_db):
        client, mock_db = client_with_db
        rows = [
            _make_tarifa_dict(id=1),
            _make_tarifa_dict(id=2, regimen="esal", tarifa_base=0.20),
        ]

        with patch("app.services.db_service.list_tarifas_renta", return_value=rows):
            resp = client.get("/api/v1/tax/tarifas-renta")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["regimen"] == "ordinario"
        assert data[1]["regimen"] == "esal"

    def test_list_with_year_filter(self, client_with_db):
        client, mock_db = client_with_db
        rows = [_make_tarifa_dict(id=1, year_from=2023)]

        with patch(
            "app.services.db_service.list_tarifas_renta", return_value=rows
        ) as mock_list:
            resp = client.get("/api/v1/tax/tarifas-renta?year=2026")

        assert resp.status_code == 200
        mock_list.assert_called_once_with(mock_db, year=2026)

    def test_empty_list(self, client_with_db):
        client, _ = client_with_db
        with patch("app.services.db_service.list_tarifas_renta", return_value=[]):
            resp = client.get("/api/v1/tax/tarifas-renta")
        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# POST /api/v1/tax/tarifas-renta
# ---------------------------------------------------------------------------


class TestUpsertTarifaRenta:
    def _make_db_row(
        self,
        regimen="ordinario",
        actividad="general",
        tarifa_base="0.3500",
        sobretasa="0.0000",
        year_from=2023,
        year_to=None,
        base_legal="Art. 240 ET",
    ):
        row = MagicMock()
        row.id = 1
        row.regimen = regimen
        row.actividad = actividad
        row.tarifa_base = Decimal(tarifa_base)
        row.sobretasa = Decimal(sobretasa)
        row.year_from = year_from
        row.year_to = year_to
        row.base_legal = base_legal
        row.notas = None
        return row

    def test_upsert_ordinario_general(self, client_with_db):
        client, mock_db = client_with_db
        row = self._make_db_row()

        with patch("app.services.db_service.upsert_tarifa_renta", return_value=row):
            resp = client.post(
                "/api/v1/tax/tarifas-renta",
                json={
                    "regimen": "ordinario",
                    "actividad": "general",
                    "tarifa_base": 0.35,
                    "sobretasa": 0.0,
                    "year_from": 2023,
                    "base_legal": "Art. 240 ET",
                },
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["regimen"] == "ordinario"
        assert data["tarifa_efectiva"] == pytest.approx(0.35)

    def test_upsert_financiero_with_sobretasa(self, client_with_db):
        client, mock_db = client_with_db
        row = self._make_db_row(
            actividad="financiero",
            tarifa_base="0.3500",
            sobretasa="0.2000",
            year_from=2026,
            year_to=2026,
            base_legal="Decreto 0150/2026",
        )

        with patch("app.services.db_service.upsert_tarifa_renta", return_value=row):
            resp = client.post(
                "/api/v1/tax/tarifas-renta",
                json={
                    "regimen": "ordinario",
                    "actividad": "financiero",
                    "tarifa_base": 0.35,
                    "sobretasa": 0.20,
                    "year_from": 2026,
                    "year_to": 2026,
                    "base_legal": "Decreto 0150/2026",
                },
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["tarifa_efectiva"] == pytest.approx(0.55)

    def test_invalid_regimen_rejected(self, client_with_db):
        client, _ = client_with_db
        resp = client.post(
            "/api/v1/tax/tarifas-renta",
            json={
                "regimen": "invalid_regime",
                "tarifa_base": 0.35,
                "year_from": 2023,
            },
        )
        assert resp.status_code == 422

    def test_invalid_actividad_rejected(self, client_with_db):
        client, _ = client_with_db
        resp = client.post(
            "/api/v1/tax/tarifas-renta",
            json={
                "regimen": "ordinario",
                "actividad": "invalid_activity",
                "tarifa_base": 0.35,
                "year_from": 2023,
            },
        )
        assert resp.status_code == 422

    def test_null_actividad_accepted(self, client_with_db):
        client, mock_db = client_with_db
        row = self._make_db_row(actividad=None)
        row.actividad = None

        with patch("app.services.db_service.upsert_tarifa_renta", return_value=row):
            resp = client.post(
                "/api/v1/tax/tarifas-renta",
                json={
                    "regimen": "ordinario",
                    "actividad": None,
                    "tarifa_base": 0.35,
                    "year_from": 2023,
                },
            )

        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# DELETE /api/v1/tax/tarifas-renta/{id}
# ---------------------------------------------------------------------------


class TestDeleteTarifaRenta:
    def test_delete_existing(self, client_with_db):
        client, mock_db = client_with_db
        from app.models.database import TarifaRenta

        row = MagicMock(spec=TarifaRenta)
        mock_db.query.return_value.filter.return_value.first.return_value = row

        resp = client.delete("/api/v1/tax/tarifas-renta/1")

        assert resp.status_code == 204
        mock_db.delete.assert_called_once_with(row)
        mock_db.commit.assert_called_once()

    def test_delete_not_found(self, client_with_db):
        client, mock_db = client_with_db
        mock_db.query.return_value.filter.return_value.first.return_value = None

        resp = client.delete("/api/v1/tax/tarifas-renta/999")

        assert resp.status_code == 404
