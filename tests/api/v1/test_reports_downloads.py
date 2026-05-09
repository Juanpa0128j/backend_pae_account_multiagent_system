from datetime import date, datetime

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


class _FakeQuery:
    def __init__(self, stmt):
        self._stmt = stmt

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._stmt


class _FakeSession:
    def __init__(self, stmt):
        self._stmt = stmt

    def query(self, *args, **kwargs):
        return _FakeQuery(self._stmt)

    def close(self):
        return None


def test_balance_download_filename_includes_date_range(monkeypatch):
    from main import app

    def _fake_resolve(*args, **kwargs):
        return (
            {
                "period_end": "2026-01-31",
                "activos": 0,
                "pasivos": 0,
                "patrimonio": 0,
                "utilidad_neta": 0,
            },
            date(2026, 1, 31),
        )

    monkeypatch.setattr("app.api.v1.reports._resolve_report", _fake_resolve)
    monkeypatch.setattr(
        "app.api.v1.reports.BalanceSheetExporter.to_pdf",
        lambda *args, **kwargs: b"%PDF-1.4\n",
    )

    client = TestClient(app)
    response = client.get(
        "/api/v1/reports/balance/download/pdf",
        params={
            "start_date": "2026-01-01",
            "end_date": "2026-01-31",
            "company_nit": "800999888-2",
        },
    )

    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert "balance_general_2026-01-01_2026-01-31.pdf" in disposition


def test_balance_download_filename_without_start_date_uses_all(monkeypatch):
    from main import app

    def _fake_resolve(*args, **kwargs):
        return (
            {
                "period_end": "2026-01-31",
                "activos": 0,
                "pasivos": 0,
                "patrimonio": 0,
                "utilidad_neta": 0,
            },
            date(2026, 1, 31),
        )

    monkeypatch.setattr("app.api.v1.reports._resolve_report", _fake_resolve)
    monkeypatch.setattr(
        "app.api.v1.reports.BalanceSheetExporter.to_pdf",
        lambda *args, **kwargs: b"%PDF-1.4\n",
    )

    client = TestClient(app)
    response = client.get(
        "/api/v1/reports/balance/download/pdf",
        params={"end_date": "2026-01-31", "company_nit": "800999888-2"},
    )

    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert "balance_general_all_2026-01-31.pdf" in disposition


def test_resolve_report_requires_company_nit_with_statement_id():
    from app.api.v1.reports import _resolve_report

    with pytest.raises(HTTPException) as exc:
        _resolve_report(
            report_type="balance",
            statement_id="stmt-1",
            start_date=None,
            end_date=None,
            company_nit=None,
        )

    assert exc.value.status_code == 422
    assert "company_nit" in str(exc.value.detail)


def test_resolve_report_rejects_invalid_company_nit_with_statement_id():
    from app.api.v1.reports import _resolve_report

    with pytest.raises(HTTPException) as exc:
        _resolve_report(
            report_type="balance",
            statement_id="stmt-1",
            start_date=None,
            end_date=None,
            company_nit="   ",
        )

    assert exc.value.status_code == 422
    assert "Invalid company_nit" in str(exc.value.detail)


def test_resolve_report_rejects_cross_tenant_statement(monkeypatch):
    from app.api.v1.reports import _resolve_report

    stmt = type(
        "Stmt",
        (),
        {
            "id": "stmt-1",
            "statement_type": "balance_general",
            "entity_nit": "900123456-1",
            "period_end": datetime(2026, 1, 31),
            "data": {},
        },
    )()

    monkeypatch.setattr("app.api.v1.reports.SessionLocal", lambda: _FakeSession(stmt))

    with pytest.raises(HTTPException) as exc:
        _resolve_report(
            report_type="balance",
            statement_id="stmt-1",
            start_date=None,
            end_date=None,
            company_nit="800999888-2",
        )

    assert exc.value.status_code == 403


def test_resolve_report_rejects_statement_type_mismatch(monkeypatch):
    from app.api.v1.reports import _resolve_report

    stmt = type(
        "Stmt",
        (),
        {
            "id": "stmt-2",
            "statement_type": "estado_resultados",
            "entity_nit": "800999888-2",
            "period_end": datetime(2026, 1, 31),
            "data": {},
        },
    )()

    monkeypatch.setattr("app.api.v1.reports.SessionLocal", lambda: _FakeSession(stmt))

    with pytest.raises(HTTPException) as exc:
        _resolve_report(
            report_type="balance",
            statement_id="stmt-2",
            start_date=None,
            end_date=None,
            company_nit="800999888-2",
        )

    assert exc.value.status_code == 422
    assert "Statement type mismatch" in str(exc.value.detail)


def test_balance_download_returns_422_on_export_failure(monkeypatch):
    from main import app

    def _fake_resolve(*args, **kwargs):
        return (
            {
                "period_end": "2026-01-31",
                "activos": 0,
                "pasivos": 0,
                "patrimonio": 0,
                "utilidad_neta": 0,
            },
            date(2026, 1, 31),
        )

    def _raise_export_failure(*args, **kwargs):
        raise ValueError("report content invalid")

    monkeypatch.setattr("app.api.v1.reports._resolve_report", _fake_resolve)
    monkeypatch.setattr(
        "app.api.v1.reports.BalanceSheetExporter.to_pdf", _raise_export_failure
    )

    client = TestClient(app)
    response = client.get(
        "/api/v1/reports/balance/download/pdf",
        params={"company_nit": "800999888-2"},
    )

    assert response.status_code == 422
    assert "Export failed" in response.json()["detail"]
