"""API endpoint tests for GET/PUT /api/v1/settings/national-rates.

Uses TestClient + patch pattern (cf. test_reteica_tarifa_endpoints.py).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.core.database import get_db
from main import app

FAKE_USER = MagicMock()
FAKE_USER.id = "test-user"


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def client(mock_db):
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    yield TestClient(app)
    app.dependency_overrides.clear()


_SAMPLE_ROW = {
    "code": "retefuente_servicios",
    "value": 0.04,
    "descripcion": "Retención en la fuente — servicios generales",
    "norma_referencia": "Art. 392 ET",
    "vigente_desde": "2023-01-01",
}


class TestListNationalRates:
    def test_returns_list(self, client, mock_db):
        with patch("app.api.v1.settings.db_service.list_national_rates") as mock_list:
            mock_list.return_value = [_SAMPLE_ROW]
            resp = client.get("/api/v1/settings/national-rates")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["code"] == "retefuente_servicios"
        assert data[0]["value"] == pytest.approx(0.04)

    def test_returns_empty_list(self, client, mock_db):
        with patch("app.api.v1.settings.db_service.list_national_rates") as mock_list:
            mock_list.return_value = []
            resp = client.get("/api/v1/settings/national-rates")
        assert resp.status_code == 200
        assert resp.json() == []


class TestUpdateNationalRate:
    _VALID_BODY = {
        "value": 0.04,
        "descripcion": "Retención en la fuente — servicios generales",
        "norma_referencia": "Art. 392 ET",
        "vigente_desde": "2023-01-01",
    }

    def _make_row(self):
        row = MagicMock()
        row.code = "retefuente_servicios"
        row.value = Decimal("0.04")
        row.descripcion = "Retención en la fuente — servicios generales"
        row.norma_referencia = "Art. 392 ET"
        row.vigente_desde = date(2023, 1, 1)
        return row

    def test_updates_existing_rate(self, client, mock_db):
        fake_row = self._make_row()
        with patch(
            "app.api.v1.settings.db_service.upsert_national_rate"
        ) as mock_upsert:
            mock_upsert.return_value = fake_row
            resp = client.put(
                "/api/v1/settings/national-rates/retefuente_servicios",
                json=self._VALID_BODY,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == "retefuente_servicios"
        assert data["value"] == pytest.approx(0.04)
        assert data["vigente_desde"] == "2023-01-01"

    def test_rejects_value_above_1(self, client, mock_db):
        body = {**self._VALID_BODY, "value": 1.5}
        resp = client.put(
            "/api/v1/settings/national-rates/retefuente_servicios", json=body
        )
        assert resp.status_code == 422

    def test_rejects_value_zero(self, client, mock_db):
        body = {**self._VALID_BODY, "value": 0.0}
        resp = client.put(
            "/api/v1/settings/national-rates/retefuente_servicios", json=body
        )
        assert resp.status_code == 422

    def test_rejects_invalid_date_format(self, client, mock_db):
        body = {**self._VALID_BODY, "vigente_desde": "01/01/2023"}
        resp = client.put(
            "/api/v1/settings/national-rates/retefuente_servicios", json=body
        )
        assert resp.status_code == 422

    def test_upsert_called_with_correct_args(self, client, mock_db):
        fake_row = self._make_row()
        with patch(
            "app.api.v1.settings.db_service.upsert_national_rate"
        ) as mock_upsert:
            mock_upsert.return_value = fake_row
            client.put(
                "/api/v1/settings/national-rates/renta_general",
                json={**self._VALID_BODY, "value": 0.35},
            )
        call_kwargs = mock_upsert.call_args.kwargs
        assert call_kwargs["code"] == "renta_general"
        assert call_kwargs["value"] == Decimal("0.35")
