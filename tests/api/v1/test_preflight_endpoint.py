"""Integration tests for GET /api/v1/tax/declarations/preflight."""

from __future__ import annotations

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


def _fake_result(
    ready=True, form_type="F300", blockers=0, warnings=0, extra_checks=None
):
    return {
        "ready": ready,
        "form_type": form_type,
        "period_start": "2026-01-01",
        "period_end": "2026-02-28",
        "checks": extra_checks
        or [
            {
                "code": "COMPANY_SETTINGS_COMPLETE",
                "severity": "blocker",
                "passed": True,
                "message": "ok",
                "cta_path": None,
            }
        ],
        "blockers": blockers,
        "warnings": warnings,
    }


class TestPreflightEndpoint:
    def test_returns_200_with_valid_query(self, client_with_db):
        client, _ = client_with_db
        with patch(
            "app.services.preflight_service.run_preflight",
            return_value=_fake_result(),
        ) as mock_run:
            resp = client.get(
                "/api/v1/tax/declarations/preflight",
                params={
                    "company_nit": "900123456",
                    "form_type": "F300",
                    "period_start": "2026-01-01",
                    "period_end": "2026-02-28",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ready"] is True
        assert body["form_type"] == "F300"
        assert len(body["checks"]) == 1
        mock_run.assert_called_once()

    def test_period_end_before_start_returns_400(self, client_with_db):
        client, _ = client_with_db
        resp = client.get(
            "/api/v1/tax/declarations/preflight",
            params={
                "company_nit": "900123456",
                "form_type": "F300",
                "period_start": "2026-03-01",
                "period_end": "2026-01-01",
            },
        )
        assert resp.status_code == 400

    def test_invalid_form_type_returns_400(self, client_with_db):
        client, _ = client_with_db
        with patch(
            "app.services.preflight_service.run_preflight",
            side_effect=ValueError("Unsupported form_type: BOGUS"),
        ):
            resp = client.get(
                "/api/v1/tax/declarations/preflight",
                params={
                    "company_nit": "900123456",
                    "form_type": "BOGUS",
                    "period_start": "2026-01-01",
                    "period_end": "2026-02-28",
                },
            )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "INVALID_FORM_TYPE"

    def test_missing_required_params_returns_422(self, client_with_db):
        client, _ = client_with_db
        resp = client.get("/api/v1/tax/declarations/preflight")
        assert resp.status_code == 422

    def test_not_ready_reports_blockers(self, client_with_db):
        client, _ = client_with_db
        with patch(
            "app.services.preflight_service.run_preflight",
            return_value=_fake_result(
                ready=False,
                blockers=2,
                warnings=1,
                extra_checks=[
                    {
                        "code": "COMPANY_SETTINGS_COMPLETE",
                        "severity": "blocker",
                        "passed": False,
                        "message": "Faltan campos",
                        "cta_path": "/settings",
                    }
                ],
            ),
        ):
            resp = client.get(
                "/api/v1/tax/declarations/preflight",
                params={
                    "company_nit": "900123456",
                    "form_type": "F300",
                    "period_start": "2026-01-01",
                    "period_end": "2026-02-28",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ready"] is False
        assert body["blockers"] == 2
        assert body["warnings"] == 1

    def test_nit_is_normalized_before_service(self, client_with_db):
        client, _ = client_with_db
        with patch(
            "app.services.preflight_service.run_preflight",
            return_value=_fake_result(),
        ) as mock_run:
            client.get(
                "/api/v1/tax/declarations/preflight",
                params={
                    "company_nit": "900.123.456",
                    "form_type": "F300",
                    "period_start": "2026-01-01",
                    "period_end": "2026-02-28",
                },
            )
        # Ensure run_preflight got a cleaned NIT (no dots)
        kwargs = mock_run.call_args.kwargs
        assert "." not in kwargs["company_nit"]
