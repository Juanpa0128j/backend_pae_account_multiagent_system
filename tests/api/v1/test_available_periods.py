"""Tests for GET /api/v1/reports/available-periods endpoint."""

from fastapi.testclient import TestClient


def test_available_periods_via_a_returns_empty(monkeypatch):
    from main import app

    monkeypatch.setattr(
        "app.services.db_service.get_company_locked_pathway",
        lambda db, nit: "build_from_scratch",
    )
    monkeypatch.setattr(
        "app.services.via_b_service.list_periods",
        lambda db, nit, statement_type: ["2025-12-31"],
    )

    client = TestClient(app)
    response = client.get(
        "/api/v1/reports/available-periods",
        params={"company_nit": "123"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["balance_general"] == []
    assert data["estado_resultados"] == []
    assert data["libro_auxiliar"] == []


def test_available_periods_via_b_returns_periods(monkeypatch):
    from main import app

    monkeypatch.setattr(
        "app.services.db_service.get_company_locked_pathway",
        lambda db, nit: "work_with_existing",
    )
    monkeypatch.setattr(
        "app.services.via_b_service.list_periods",
        lambda db, nit, statement_type: ["2025-12-31", "2025-06-30"],
    )

    client = TestClient(app)
    response = client.get(
        "/api/v1/reports/available-periods",
        params={"company_nit": "123"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["balance_general"] == ["2025-12-31", "2025-06-30"]
    assert data["estado_resultados"] == ["2025-12-31", "2025-06-30"]
