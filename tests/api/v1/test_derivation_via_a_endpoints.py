"""Tests for the manual Vía A derivation endpoints.

Two-step manual flow:
  1. POST /api/v1/reports/derivation/build-first-level-via-a  (BG/ER/LA/LD)
  2. POST /api/v1/reports/derivation/run-via-a                (NIC 7 secondary)
plus GET /api/v1/reports/derivation/status-via-a.

The endpoints open their own ``SessionLocal`` and call service functions that
own their DB sessions, so we patch at the ``app.api.v1.reports`` module level
rather than overriding ``get_db``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.services.financial_statement_service import BusinessRuleError
from main import app

client = TestClient(app, raise_server_exceptions=False)

BASE = "/api/v1/reports/derivation"
NIT = "900123456"


class TestBuildFirstLevelViaA:
    def _post(self, period_type, start, end):
        return client.post(
            f"{BASE}/build-first-level-via-a",
            json={
                "company_nit": NIT,
                "period_type": period_type,
                "period_start": start,
                "period_end": end,
            },
        )

    def test_annual_stamps_annual_frequency(self):
        captured = {}

        def fake_build(db, *, company_nit, period_start, period_end, frequency=None):
            captured["frequency"] = frequency
            return {"status": "built", "created": {}, "build_errors": {}}

        with (
            patch("app.api.v1.reports.SessionLocal", return_value=MagicMock()),
            patch(
                "app.api.v1.reports.build_first_level_from_journal_entries",
                side_effect=fake_build,
            ),
        ):
            rsp = self._post("annual", "2025-01-01", "2025-12-31")

        assert rsp.status_code == 200
        assert captured["frequency"] == "annual"
        assert rsp.json()["frequency"] == "annual"

    def test_monthly_stamps_monthly_frequency(self):
        captured = {}

        def fake_build(db, *, company_nit, period_start, period_end, frequency=None):
            captured["frequency"] = frequency
            return {"status": "built", "created": {}, "build_errors": {}}

        with (
            patch("app.api.v1.reports.SessionLocal", return_value=MagicMock()),
            patch(
                "app.api.v1.reports.build_first_level_from_journal_entries",
                side_effect=fake_build,
            ),
        ):
            rsp = self._post("monthly", "2025-06-01", "2025-06-30")

        assert rsp.status_code == 200
        assert captured["frequency"] == "monthly"

    def test_custom_infers_frequency_from_span(self):
        captured = {}

        def fake_build(db, *, company_nit, period_start, period_end, frequency=None):
            captured["frequency"] = frequency
            return {"status": "built", "created": {}, "build_errors": {}}

        with (
            patch("app.api.v1.reports.SessionLocal", return_value=MagicMock()),
            patch(
                "app.api.v1.reports.build_first_level_from_journal_entries",
                side_effect=fake_build,
            ),
        ):
            # Full-year custom span → inferred annual (≥ 300 days).
            rsp = self._post("custom", "2025-01-01", "2025-12-31")

        assert rsp.status_code == 200
        assert captured["frequency"] == "annual"

    def test_required_build_failure_returns_409(self):
        def fake_build(db, *, company_nit, period_start, period_end, frequency=None):
            return {
                "status": "built",
                "created": {},
                "build_errors": {"balance_general": "no journal entries"},
            }

        with (
            patch("app.api.v1.reports.SessionLocal", return_value=MagicMock()),
            patch(
                "app.api.v1.reports.build_first_level_from_journal_entries",
                side_effect=fake_build,
            ),
        ):
            rsp = self._post("annual", "2025-01-01", "2025-12-31")

        assert rsp.status_code == 409


class TestRunSecondaryViaA:
    def _post(self, start, end):
        return client.post(
            f"{BASE}/run-via-a",
            json={"company_nit": NIT, "period_start": start, "period_end": end},
        )

    def test_annual_ok(self):
        with patch(
            "app.api.v1.reports.derive_financial_statements",
            return_value={"status": "derived"},
        ) as mock_derive:
            rsp = self._post("2025-01-01", "2025-12-31")

        assert rsp.status_code == 200
        assert rsp.json()["derived"]["status"] == "derived"
        # Via A invariants: journal-sourced inputs, prior computed from journal.
        _, kwargs = mock_derive.call_args
        assert kwargs["input_source_mode"] == "derived_from_journal"
        assert kwargs["prior_from_journal"] is True

    def test_monthly_returns_409(self):
        with patch(
            "app.api.v1.reports.derive_financial_statements",
            side_effect=BusinessRuleError("requiere estados ANUALES"),
        ):
            rsp = self._post("2025-06-01", "2025-06-30")

        assert rsp.status_code == 409
        assert "ANUALES" in rsp.json()["detail"]


def _annual_bg_er(year):
    return [
        {
            "statement_type": st,
            "period_start": f"{year}-01-01T00:00:00+00:00",
            "period_end": f"{year}-12-31T23:59:59+00:00",
            "source_mode": "derived_from_journal",
            "frequency": "annual",
        }
        for st in ("balance_general", "estado_resultados")
    ]


class TestStatusViaA:
    def test_annual_with_prior_is_eligible_first_year_is_not(self):
        # 2024 (earliest, no prior) + 2025 (has 2024 as prior) + a 2025 monthly.
        rows = [
            *_annual_bg_er(2024),
            *_annual_bg_er(2025),
            {
                "statement_type": "balance_general",
                "period_start": "2025-06-01T00:00:00+00:00",
                "period_end": "2025-06-30T23:59:59+00:00",
                "source_mode": "derived_from_journal",
                "frequency": "monthly",
            },
        ]

        with (
            patch("app.api.v1.reports.list_financial_statements", return_value=rows),
            patch("app.api.v1.reports.SessionLocal", return_value=MagicMock()),
            patch(
                "app.services.db_service.get_journal_entry_period",
                return_value=(
                    __import__("datetime").datetime(2024, 1, 1),
                    __import__("datetime").datetime(2025, 12, 31),
                ),
            ),
        ):
            rsp = client.get(f"{BASE}/status-via-a", params={"company_nit": NIT})

        assert rsp.status_code == 200
        body = rsp.json()
        assert body["journal_date_range"]["earliest"] is not None
        # Only 2025 is ready (2024 is the earliest → no prior → gap → not eligible).
        assert body["is_ready"] is True
        assert len(body["ready_periods"]) == 1
        assert body["ready_periods"][0]["period_start"].startswith("2025-01-01")

        by_start = {p["period_start"][:10]: p for p in body["first_level_periods"]}
        assert by_start["2025-01-01"]["eligible_for_secondary"] is True
        assert by_start["2025-01-01"]["prior_period_gap"] is False
        # First year: no prior generated → not eligible.
        assert by_start["2024-01-01"]["eligible_for_secondary"] is False
        assert by_start["2024-01-01"]["prior_period_gap"] is True
        assert body["minimum_requirements"]["annual_only"] is True

    def test_single_annual_period_without_prior_is_not_ready(self):
        # A lone annual period with no earlier BG → cannot derive (no prior).
        with (
            patch(
                "app.api.v1.reports.list_financial_statements",
                return_value=_annual_bg_er(2025),
            ),
            patch("app.api.v1.reports.SessionLocal", return_value=MagicMock()),
            patch(
                "app.services.db_service.get_journal_entry_period",
                return_value=(
                    __import__("datetime").datetime(2025, 1, 1),
                    __import__("datetime").datetime(2025, 12, 31),
                ),
            ),
        ):
            rsp = client.get(f"{BASE}/status-via-a", params={"company_nit": NIT})

        body = rsp.json()
        assert body["is_ready"] is False
        assert body["ready_periods"] == []
        assert body["first_level_periods"][0]["prior_period_gap"] is True

    def test_empty_company_still_returns_journal_range(self):
        with (
            patch("app.api.v1.reports.list_financial_statements", return_value=[]),
            patch("app.api.v1.reports.SessionLocal", return_value=MagicMock()),
            patch(
                "app.services.db_service.get_journal_entry_period",
                return_value=None,
            ),
        ):
            rsp = client.get(f"{BASE}/status-via-a", params={"company_nit": NIT})

        assert rsp.status_code == 200
        body = rsp.json()
        assert body["journal_date_range"] == {"earliest": None, "latest": None}
        assert body["is_ready"] is False
        assert body["ready_periods"] == []
