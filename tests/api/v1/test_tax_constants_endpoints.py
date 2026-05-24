"""Tests for GET/PUT /api/v1/tax/constants endpoints."""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from main import app


@pytest.fixture
def client_with_db():
    """TestClient with a mock DB session injected via dependency override."""
    mock_db = MagicMock()

    def _override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app), mock_db
    app.dependency_overrides.pop(get_db, None)


class TestGetTaxConstants:
    def test_returns_uvt_and_base_minima(self, client_with_db):
        client, mock_db = client_with_db
        expected = {
            "uvt": {"year": 2026, "value": "52374.00", "decreto": "Decreto 0024/2025"},
            "base_minima": [
                {"concepto": "retefuente_servicios", "uvt_units": "4.00", "year": 2026},
            ],
        }
        with patch("app.services.db_service.list_tax_constants", return_value=expected):
            response = client.get("/api/v1/tax/constants?year=2026")
        assert response.status_code == 200
        data = response.json()
        assert data["uvt"]["year"] == 2026
        assert data["uvt"]["value"] == "52374.00"

    def test_missing_year_param_returns_422(self, client_with_db):
        client, _ = client_with_db
        response = client.get("/api/v1/tax/constants")
        assert response.status_code == 422

    def test_empty_response_when_no_data(self, client_with_db):
        client, mock_db = client_with_db
        with patch(
            "app.services.db_service.list_tax_constants",
            return_value={"uvt": None, "base_minima": []},
        ):
            response = client.get("/api/v1/tax/constants?year=2020")
        assert response.status_code == 200
        data = response.json()
        assert data["uvt"] is None
        assert data["base_minima"] == []


class TestUpsertUvtEndpoint:
    def test_upsert_uvt_success(self, client_with_db):
        client, mock_db = client_with_db
        fake_row = MagicMock()
        fake_row.year = 2026
        fake_row.value = Decimal("52374")
        fake_row.decreto = "Decreto 0024/2025"
        fake_row.updated_at = None

        with patch("app.services.db_service.upsert_uvt", return_value=fake_row):
            response = client.put(
                "/api/v1/tax/constants/uvt",
                json={"year": 2026, "value": 52374, "decreto": "Decreto 0024/2025"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["year"] == 2026

    def test_upsert_uvt_missing_required_field(self, client_with_db):
        client, _ = client_with_db
        response = client.put(
            "/api/v1/tax/constants/uvt",
            json={"decreto": "Decreto X"},  # missing year and value
        )
        assert response.status_code == 422

    def test_upsert_uvt_negative_value_rejected(self, client_with_db):
        client, _ = client_with_db
        response = client.put(
            "/api/v1/tax/constants/uvt",
            json={"year": 2026, "value": -100},
        )
        assert response.status_code == 422


class TestUpsertBaseMinimaEndpoint:
    def test_upsert_base_minima_success(self, client_with_db):
        client, mock_db = client_with_db
        fake_row = MagicMock()
        fake_row.concepto = "retefuente_servicios"
        fake_row.uvt_units = Decimal("4")
        fake_row.year = 2026
        fake_row.updated_at = None

        with patch("app.services.db_service.upsert_base_minima", return_value=fake_row):
            response = client.put(
                "/api/v1/tax/constants/base-minima",
                json={"concepto": "retefuente_servicios", "uvt_units": 4, "year": 2026},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["concepto"] == "retefuente_servicios"

    def test_invalid_concepto_rejected(self, client_with_db):
        client, _ = client_with_db
        response = client.put(
            "/api/v1/tax/constants/base-minima",
            json={"concepto": "invalid_concepto", "uvt_units": 4, "year": 2026},
        )
        assert response.status_code == 422

    def test_all_valid_conceptos_accepted(self, client_with_db):
        client, mock_db = client_with_db
        valid_conceptos = [
            "retefuente_servicios",
            "retefuente_bienes",
            "retefuente_arrendamiento",
            "reteica",
        ]
        for concepto in valid_conceptos:
            fake_row = MagicMock()
            fake_row.concepto = concepto
            fake_row.uvt_units = Decimal("4")
            fake_row.year = 2026
            fake_row.updated_at = None
            with patch(
                "app.services.db_service.upsert_base_minima", return_value=fake_row
            ):
                response = client.put(
                    "/api/v1/tax/constants/base-minima",
                    json={"concepto": concepto, "uvt_units": 4, "year": 2026},
                )
            assert response.status_code == 200, f"Failed for concepto={concepto}"
