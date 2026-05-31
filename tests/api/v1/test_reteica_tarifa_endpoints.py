import pytest
from decimal import Decimal
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from main import app
from app.core.auth import get_current_user
from app.core.database import get_db

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


class TestListReteicaTarifas:
    def test_returns_list(self, client, mock_db):
        with patch("app.api.v1.tax.db_service.list_reteica_tarifas") as mock_list:
            mock_list.return_value = [
                {
                    "id": 1,
                    "municipio": "bogota",
                    "ciiu_seccion": "J",
                    "tasa": 0.00966,
                    "fuente": "Acuerdo 065",
                    "base_minima_uvt": 4.0,
                }
            ]
            resp = client.get("/api/v1/tax/reteica-tarifas")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["municipio"] == "bogota"

    def test_filters_by_municipio(self, client, mock_db):
        with patch("app.api.v1.tax.db_service.list_reteica_tarifas") as mock_list:
            mock_list.return_value = []
            resp = client.get("/api/v1/tax/reteica-tarifas?municipio=cali")
        assert resp.status_code == 200
        mock_list.assert_called_once_with(mock_db, municipio="cali")


class TestUpsertReteicaTarifa:
    def test_upsert_valid(self, client, mock_db):
        fake_row = MagicMock()
        fake_row.id = 1
        fake_row.municipio = "bogota"
        fake_row.ciiu_seccion = "J"
        fake_row.tasa = Decimal("0.00966")
        fake_row.fuente = "Acuerdo 065"
        fake_row.base_minima_uvt = Decimal("4.0")
        with patch("app.api.v1.tax.db_service.upsert_reteica_tarifa") as mock_upsert:
            mock_upsert.return_value = fake_row
            resp = client.put(
                "/api/v1/tax/reteica-tarifas",
                json={
                    "municipio": "bogota",
                    "ciiu_seccion": "J",
                    "tasa": 0.00966,
                    "fuente": "Acuerdo 065",
                    "base_minima_uvt": 4.0,
                },
            )
        assert resp.status_code == 200
        assert resp.json()["municipio"] == "bogota"

    def test_rejects_invalid_tasa(self, client, mock_db):
        resp = client.put(
            "/api/v1/tax/reteica-tarifas",
            json={"municipio": "bogota", "ciiu_seccion": "J", "tasa": 0.5},
        )
        assert resp.status_code == 422

    def test_rejects_invalid_ciiu(self, client, mock_db):
        resp = client.put(
            "/api/v1/tax/reteica-tarifas",
            json={"municipio": "bogota", "ciiu_seccion": "Z", "tasa": 0.005},
        )
        assert resp.status_code == 422


class TestDeleteReteicaTarifa:
    def test_deletes_existing(self, client, mock_db):
        with patch("app.api.v1.tax.db_service.delete_reteica_tarifa") as mock_del:
            mock_del.return_value = True
            resp = client.delete("/api/v1/tax/reteica-tarifas/1")
        assert resp.status_code == 204

    def test_404_when_not_found(self, client, mock_db):
        with patch("app.api.v1.tax.db_service.delete_reteica_tarifa") as mock_del:
            mock_del.return_value = False
            resp = client.delete("/api/v1/tax/reteica-tarifas/999")
        assert resp.status_code == 404
