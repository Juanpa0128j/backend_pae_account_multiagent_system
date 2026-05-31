"""Tests verifying that setup_company_tax_profile reads national rates from DB.

Two scenarios:
1. DB has all 4 national rates → those rates are stored to company_settings
2. DB returns None for national rates → hardcoded fallback constants are used
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.core.database import get_db
from main import app

FAKE_USER = MagicMock()
FAKE_USER.id = "test-user"

_SETUP_BODY = {
    "nombre": "Test Corp",
    "ciudad": "bogota",
    "codigo_ciiu": "J611",
    "iva_responsable": True,
}


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def client(mock_db):
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    yield TestClient(app)
    app.dependency_overrides.clear()


def _make_national_rate(code: str, value: float) -> MagicMock:
    row = MagicMock()
    row.code = code
    row.value = Decimal(str(value))
    return row


class TestSetupUsesDBNationalRates:
    """When national_rates table has all 4 rows, /setup uses those values."""

    def test_db_rates_stored_to_company_settings(self, client, mock_db):
        _NATIONAL_RATES = {
            "retefuente_servicios": 0.038,
            "retefuente_bienes": 0.022,
            "retefuente_arrendamiento": 0.030,
            "renta_general": 0.33,
        }

        def fake_get_national_rate(db, code):
            if code in _NATIONAL_RATES:
                return _make_national_rate(code, _NATIONAL_RATES[code])
            return None

        fake_settings_row = MagicMock()
        fake_settings_row.nit = "900111222-3"
        fake_settings_row.nombre = "Test Corp"
        fake_settings_row.ciudad = "bogota"
        fake_settings_row.codigo_ciiu = "J611"
        fake_settings_row.locked_pathway = None
        fake_settings_row.created_at = None
        fake_settings_row.updated_at = None
        fake_settings_row.iva_responsable = True
        fake_settings_row.es_declarante = True
        fake_settings_row.tasa_retefuente_servicios = 0.038
        fake_settings_row.tasa_retefuente_bienes = 0.022
        fake_settings_row.tasa_retefuente_arrendamiento = 0.030
        fake_settings_row.tasa_reteica = 0.0069
        fake_settings_row.tasa_iva_general = 0.19
        fake_settings_row.tasa_ica = 0.0069
        fake_settings_row.tasa_renta = 0.33

        with (
            patch("app.api.v1.settings.db_service.get_reteica_tarifa") as mock_reteica,
            patch(
                "app.api.v1.settings.db_service.get_national_rate",
                side_effect=fake_get_national_rate,
            ),
            patch(
                "app.api.v1.settings.db_service.upsert_company_settings"
            ) as mock_upsert,
        ):
            mock_reteica.return_value = 0.0069
            mock_upsert.return_value = fake_settings_row

            resp = client.post(
                "/api/v1/settings/company/900111222-3/setup", json=_SETUP_BODY
            )

        assert resp.status_code == 200
        stored = mock_upsert.call_args[0][2]

        assert stored["tasa_retefuente_servicios"] == pytest.approx(0.038)
        assert stored["tasa_retefuente_bienes"] == pytest.approx(0.022)
        assert stored["tasa_retefuente_arrendamiento"] == pytest.approx(0.030)
        assert stored["tasa_renta"] == pytest.approx(0.33)


class TestSetupFallsBackToConstantsWhenDBEmpty:
    """When national_rates returns None for all codes, /setup uses hardcoded fallbacks."""

    def test_fallback_constants_used_when_no_db_rows(self, client, mock_db):
        fake_settings_row = MagicMock()
        fake_settings_row.nit = "900111222-3"
        fake_settings_row.nombre = "Test Corp"
        fake_settings_row.ciudad = "bogota"
        fake_settings_row.codigo_ciiu = "J611"
        fake_settings_row.locked_pathway = None
        fake_settings_row.created_at = None
        fake_settings_row.updated_at = None
        fake_settings_row.iva_responsable = True
        fake_settings_row.es_declarante = True
        fake_settings_row.tasa_retefuente_servicios = 0.04
        fake_settings_row.tasa_retefuente_bienes = 0.025
        fake_settings_row.tasa_retefuente_arrendamiento = 0.035
        fake_settings_row.tasa_reteica = 0.0069
        fake_settings_row.tasa_iva_general = 0.19
        fake_settings_row.tasa_ica = 0.0069
        fake_settings_row.tasa_renta = 0.35

        with (
            patch("app.api.v1.settings.db_service.get_reteica_tarifa") as mock_reteica,
            patch(
                "app.api.v1.settings.db_service.get_national_rate", return_value=None
            ),
            patch(
                "app.api.v1.settings.db_service.upsert_company_settings"
            ) as mock_upsert,
        ):
            mock_reteica.return_value = 0.0069
            mock_upsert.return_value = fake_settings_row

            resp = client.post(
                "/api/v1/settings/company/900111222-3/setup", json=_SETUP_BODY
            )

        assert resp.status_code == 200
        stored = mock_upsert.call_args[0][2]

        assert stored["tasa_retefuente_servicios"] == pytest.approx(0.04)
        assert stored["tasa_retefuente_bienes"] == pytest.approx(0.025)
        assert stored["tasa_retefuente_arrendamiento"] == pytest.approx(0.035)
        assert stored["tasa_renta"] == pytest.approx(0.35)
