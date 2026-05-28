"""Pathway branching for the /api/v1/tax/* endpoints.

When the company is locked to ``work_with_existing`` (Vía B), the four
"computable from saldos" endpoints (``/iva``, ``/withholdings``, ``/ica``,
``/renta-provision``) must route through ``via_b_service`` and tag the
response with ``source: "via_b"``. Vía A companies must keep the legacy
journal-entry pipeline untouched.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from main import app

_NIT = "800999888"


@pytest.fixture
def client():
    mock_db = MagicMock()

    def _override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _via_b_pathway(_db, _nit):
    return "work_with_existing"


def _via_a_pathway(_db, _nit):
    return "build_from_scratch"


class TestIvaBranching:
    def test_via_b_routes_to_via_b_service(self, client):
        via_b_payload = {
            "report_type": "iva_report",
            "source": "via_b",
            "period_start": None,
            "period_end": "2026-01-31",
            "company_nit": _NIT,
            "generated_at": "2026-05-28T00:00:00+00:00",
            "iva_generado": 4_298_712.0,
            "iva_descontable": 233_780.0,
            "iva_a_pagar": 4_064_932.0,
            "iva_status": "saldo_a_pagar",
            "referencias": [],
        }
        with (
            patch(
                "app.api.v1.tax.db_service.get_company_locked_pathway",
                side_effect=_via_b_pathway,
            ),
            patch(
                "app.api.v1.tax.via_b_service.get_iva_report",
                return_value=via_b_payload,
            ) as mock_via_b,
            patch("app.api.v1.tax._run_report") as mock_pipeline,
        ):
            rsp = client.get(f"/api/v1/tax/iva?company_nit={_NIT}")

        assert rsp.status_code == 200
        body = rsp.json()
        assert body["source"] == "via_b"
        assert body["iva_a_pagar"] == 4_064_932.0
        mock_via_b.assert_called_once()
        mock_pipeline.assert_not_called()

    def test_via_a_keeps_legacy_pipeline(self, client):
        via_a_payload = {
            "report_type": "iva_report",
            "period_end": "2026-05-28",
            "generated_at": "2026-05-28T00:00:00+00:00",
            "iva_generado": 100.0,
            "iva_descontable": 50.0,
            "iva_a_pagar": 50.0,
            "referencias": [],
        }
        with (
            patch(
                "app.api.v1.tax.db_service.get_company_locked_pathway",
                side_effect=_via_a_pathway,
            ),
            patch(
                "app.api.v1.tax._run_report", return_value=via_a_payload
            ) as mock_pipeline,
            patch("app.api.v1.tax.via_b_service.get_iva_report") as mock_via_b,
        ):
            rsp = client.get(f"/api/v1/tax/iva?company_nit={_NIT}")

        assert rsp.status_code == 200
        body = rsp.json()
        assert body.get("source") is None
        mock_pipeline.assert_called_once()
        mock_via_b.assert_not_called()

    def test_via_b_with_no_statement_returns_empty_state(self, client):
        with (
            patch(
                "app.api.v1.tax.db_service.get_company_locked_pathway",
                side_effect=_via_b_pathway,
            ),
            patch("app.api.v1.tax.via_b_service.get_iva_report", return_value=None),
            patch("app.api.v1.tax._run_report") as mock_pipeline,
        ):
            rsp = client.get(f"/api/v1/tax/iva?company_nit={_NIT}")

        assert rsp.status_code == 200
        body = rsp.json()
        assert body["source"] == "via_b"
        assert body["iva_a_pagar"] == 0.0
        assert body["iva_status"] == "saldo_cero"
        mock_pipeline.assert_not_called()


class TestWithholdingsBranching:
    def test_via_b_routes_to_via_b_service(self, client):
        payload = {
            "report_type": "withholdings_report",
            "source": "via_b",
            "period_start": None,
            "period_end": "2026-01-31",
            "company_nit": _NIT,
            "generated_at": "2026-05-28T00:00:00+00:00",
            "retencion_en_la_fuente": 500_000.0,
            "retencion_ica": 80_000.0,
            "total_retenciones": 580_000.0,
            "referencias": [],
        }
        with (
            patch(
                "app.api.v1.tax.db_service.get_company_locked_pathway",
                side_effect=_via_b_pathway,
            ),
            patch(
                "app.api.v1.tax.via_b_service.get_withholdings_report",
                return_value=payload,
            ) as mock_via_b,
            patch("app.api.v1.tax._run_report") as mock_pipeline,
        ):
            rsp = client.get(f"/api/v1/tax/withholdings?company_nit={_NIT}")

        assert rsp.status_code == 200
        body = rsp.json()
        assert body["source"] == "via_b"
        assert body["total_retenciones"] == 580_000.0
        mock_via_b.assert_called_once()
        mock_pipeline.assert_not_called()


class TestIcaBranching:
    def test_via_b_uses_via_b_service(self, client):
        payload = {
            "report_type": "ica_declaracion",
            "source": "via_b",
            "period_start": None,
            "period_end": "2026-01-31",
            "generated_at": "2026-05-28T00:00:00+00:00",
            "ingresos_brutos": 22_625_936.0,
            "tasa_ica": 0.0069,
            "ica_a_pagar": 156_118.96,
            "cuenta_gasto_puc": "540101",
            "cuenta_pasivo_puc": "2368",
            "referencias": [],
        }
        with (
            patch(
                "app.api.v1.tax.db_service.get_company_locked_pathway",
                side_effect=_via_b_pathway,
            ),
            patch("app.api.v1.tax.db_service.get_company_settings", return_value=None),
            patch(
                "app.api.v1.tax.via_b_service.get_ica_report", return_value=payload
            ) as mock_via_b,
        ):
            rsp = client.get(f"/api/v1/tax/ica?company_nit={_NIT}")

        assert rsp.status_code == 200
        body = rsp.json()
        assert body["source"] == "via_b"
        assert body["ica_a_pagar"] == pytest.approx(156_118.96)
        mock_via_b.assert_called_once()


class TestRentaBranching:
    def test_via_b_uses_via_b_service(self, client):
        payload = {
            "report_type": "renta_provision",
            "source": "via_b",
            "period_start": None,
            "period_end": "2026-01-31",
            "generated_at": "2026-05-28T00:00:00+00:00",
            "utilidad_antes_impuestos": 8_983_445.4,
            "tasa_renta": 0.35,
            "provision_renta": 3_144_205.89,
            "cuenta_gasto_puc": "540502",
            "cuenta_pasivo_puc": "240405",
            "referencias": [],
        }
        with (
            patch(
                "app.api.v1.tax.db_service.get_company_locked_pathway",
                side_effect=_via_b_pathway,
            ),
            patch("app.api.v1.tax.db_service.get_company_settings", return_value=None),
            patch(
                "app.api.v1.tax.via_b_service.get_renta_provision_report",
                return_value=payload,
            ) as mock_via_b,
        ):
            rsp = client.get(f"/api/v1/tax/renta-provision?company_nit={_NIT}")

        assert rsp.status_code == 200
        body = rsp.json()
        assert body["source"] == "via_b"
        assert body["provision_renta"] == pytest.approx(3_144_205.89)
        mock_via_b.assert_called_once()

    def test_via_b_with_no_statement_returns_empty_state(self, client):
        with (
            patch(
                "app.api.v1.tax.db_service.get_company_locked_pathway",
                side_effect=_via_b_pathway,
            ),
            patch("app.api.v1.tax.db_service.get_company_settings", return_value=None),
            patch(
                "app.api.v1.tax.via_b_service.get_renta_provision_report",
                return_value=None,
            ),
        ):
            rsp = client.get(f"/api/v1/tax/renta-provision?company_nit={_NIT}")

        assert rsp.status_code == 200
        body = rsp.json()
        assert body["source"] == "via_b"
        assert body["provision_renta"] == 0.0
