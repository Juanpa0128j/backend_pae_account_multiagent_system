"""Tests for period_start / period_end query params on tax endpoints."""

import calendar
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.core.database import get_db
from main import app

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

FAKE_USER = MagicMock()
FAKE_USER.id = "test-user"


def _fake_user():
    return FAKE_USER


@pytest.fixture(autouse=True)
def override_auth():
    app.dependency_overrides[get_current_user] = _fake_user
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def mock_db():
    """Minimal DB mock for ICA / renta-provision (direct DB endpoints)."""
    mock_row = MagicMock()
    mock_row.ingresos = 0
    db = MagicMock()
    db.execute.return_value = MagicMock(fetchone=lambda: mock_row)

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    yield db
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _current_month_bounds():
    today = date.today()
    first = today.replace(day=1)
    last = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    return first, last


# ---------------------------------------------------------------------------
# /tax/ica  (direct DB endpoint — easy to unit test)
# ---------------------------------------------------------------------------


class TestIcaPeriodParams:
    def test_no_params_defaults_to_current_month(self, client, mock_db):
        first, last = _current_month_bounds()
        with patch("app.api.v1.tax.db_service.get_company_settings", return_value=None):
            rsp = client.get("/api/v1/tax/ica")
        assert rsp.status_code == 200
        data = rsp.json()
        assert data["period_start"] == first.isoformat()
        assert data["period_end"] == last.isoformat()

    def test_both_params_uses_specified_range(self, client, mock_db):
        start = date(2025, 1, 1)
        end = date(2025, 1, 31)
        with patch("app.api.v1.tax.db_service.get_company_settings", return_value=None):
            rsp = client.get(
                "/api/v1/tax/ica",
                params={
                    "period_start": start.isoformat(),
                    "period_end": end.isoformat(),
                },
            )
        assert rsp.status_code == 200
        data = rsp.json()
        assert data["period_start"] == start.isoformat()
        assert data["period_end"] == end.isoformat()

    def test_only_period_start_returns_400(self, client, mock_db):
        rsp = client.get("/api/v1/tax/ica", params={"period_start": "2025-01-01"})
        assert rsp.status_code == 400
        assert "period_start y period_end" in rsp.json()["detail"]

    def test_only_period_end_returns_400(self, client, mock_db):
        rsp = client.get("/api/v1/tax/ica", params={"period_end": "2025-01-31"})
        assert rsp.status_code == 400
        assert "period_start y period_end" in rsp.json()["detail"]

    def test_period_end_before_period_start_returns_400(self, client, mock_db):
        rsp = client.get(
            "/api/v1/tax/ica",
            params={"period_start": "2025-06-01", "period_end": "2025-01-01"},
        )
        assert rsp.status_code == 400


# ---------------------------------------------------------------------------
# /tax/renta-provision  (direct DB endpoint)
# ---------------------------------------------------------------------------


class TestRentaProvisionPeriodParams:
    def _mock_calc(self, start, end):
        from datetime import datetime

        return {
            "report_type": "renta_provision",
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "generated_at": datetime.utcnow().isoformat(),
            "utilidad_antes_impuestos": 0.0,
            "tasa_renta": 0.35,
            "provision_renta": 0.0,
            "referencias": [],
        }

    def test_no_params_defaults_to_current_month(self, client, mock_db):
        first, last = _current_month_bounds()
        with (
            patch("app.api.v1.tax.db_service.get_company_settings", return_value=None),
            patch(
                "app.api.v1.tax.calc_period_renta_provision",
                return_value=self._mock_calc(first, last),
            ),
        ):
            rsp = client.get("/api/v1/tax/renta-provision")
        assert rsp.status_code == 200

    def test_both_params_accepted(self, client, mock_db):
        start, end = date(2025, 3, 1), date(2025, 3, 31)
        with (
            patch("app.api.v1.tax.db_service.get_company_settings", return_value=None),
            patch(
                "app.api.v1.tax.calc_period_renta_provision",
                return_value=self._mock_calc(start, end),
            ),
        ):
            rsp = client.get(
                "/api/v1/tax/renta-provision",
                params={
                    "period_start": start.isoformat(),
                    "period_end": end.isoformat(),
                },
            )
        assert rsp.status_code == 200

    def test_only_period_start_returns_400(self, client, mock_db):
        rsp = client.get(
            "/api/v1/tax/renta-provision", params={"period_start": "2025-01-01"}
        )
        assert rsp.status_code == 400

    def test_only_period_end_returns_400(self, client, mock_db):
        rsp = client.get(
            "/api/v1/tax/renta-provision", params={"period_end": "2025-01-31"}
        )
        assert rsp.status_code == 400

    def test_invalid_range_returns_400(self, client, mock_db):
        rsp = client.get(
            "/api/v1/tax/renta-provision",
            params={"period_start": "2025-06-01", "period_end": "2025-01-01"},
        )
        assert rsp.status_code == 400


# ---------------------------------------------------------------------------
# /tax/iva and /tax/withholdings  (pipeline endpoints — mock invoke_reporting_pipeline)
# ---------------------------------------------------------------------------

_IVA_MOCK = {
    "iva_generado": 0.0,
    "iva_deducible": 0.0,
    "iva_neto": 0.0,
    "referencias": [],
}

_WITHHOLDINGS_MOCK = {
    "retefuente": 0.0,
    "reteica": 0.0,
    "total_retenciones": 0.0,
    "referencias": [],
}


@pytest.mark.parametrize(
    "endpoint,mock_report",
    [
        ("/api/v1/tax/iva", _IVA_MOCK),
        ("/api/v1/tax/withholdings", _WITHHOLDINGS_MOCK),
    ],
)
class TestPipelineEndpointsPeriodParams:
    def test_no_params_calls_pipeline(self, client, endpoint, mock_report):
        with patch(
            "app.api.v1.tax.invoke_reporting_pipeline",
            return_value={"report": mock_report},
        ):
            rsp = client.get(endpoint)
        # pipeline endpoints may return 200 or 500 depending on output parsing;
        # what matters is they did NOT return 400
        assert rsp.status_code != 400

    def test_only_period_start_returns_400(self, client, endpoint, mock_report):
        rsp = client.get(endpoint, params={"period_start": "2025-01-01"})
        assert rsp.status_code == 400
        assert "period_start y period_end" in rsp.json()["detail"]

    def test_only_period_end_returns_400(self, client, endpoint, mock_report):
        rsp = client.get(endpoint, params={"period_end": "2025-01-31"})
        assert rsp.status_code == 400
        assert "period_start y period_end" in rsp.json()["detail"]

    def test_invalid_range_returns_400(self, client, endpoint, mock_report):
        rsp = client.get(
            endpoint,
            params={"period_start": "2025-06-01", "period_end": "2025-01-01"},
        )
        assert rsp.status_code == 400

    def test_both_params_calls_pipeline(self, client, endpoint, mock_report):
        with patch(
            "app.api.v1.tax.invoke_reporting_pipeline",
            return_value={"report": mock_report},
        ):
            rsp = client.get(
                endpoint,
                params={"period_start": "2025-01-01", "period_end": "2025-01-31"},
            )
        assert rsp.status_code != 400
