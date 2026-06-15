"""Characterization tests for 15 API handlers — SAFETY NET before the
``async def`` → plain ``def`` (threadpool offload) refactor.

These lock the CURRENT response contract (HTTP status + top-level JSON shape)
so the conversion can be proven behavior-preserving. They intentionally assert
*structure*, not brittle dynamic values.

Harness mirrors existing tests:
  * Auth + rate-limit are disabled by the autouse fixtures in tests/conftest.py.
  * Report/tax pipelines are mocked via ``invoke_reporting_pipeline`` (same as
    tests/agents/test_reportero_agent.py).
  * Dashboard/books run against an empty in-memory SQLite via a ``get_db``
    override (same as tests/api/v1/test_dashboard_nit_filter.py).
  * Chat mocks ``chat_service.handle_chat_message`` (the LLM boundary).

Covered (15): reports balance/pnl/cashflow/libro_diario/libro_auxiliar/
cambios_patrimonio/notas_eeff/analysis; tax iva/withholdings; dashboard
stats/financial-summary/monthly-trend; books get_books; chat.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.chat_schemas import ChatResponse
from main import app

_START = "2026-01-01"
_END = "2026-01-31"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    """TestClient backed by an empty in-memory SQLite DB.

    Auth + rate limits are already neutralised by the autouse fixtures in
    tests/conftest.py. Reporting/chat boundaries are mocked per-test.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    def _override_get_db():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)
    session.close()


# ---------------------------------------------------------------------------
# Mock pipeline results (shapes mirror app.services.report builders)
# ---------------------------------------------------------------------------

_MOCK_BALANCE = {
    "report_type": "balance_sheet",
    "period_start": _START,
    "period_end": _END,
    "generated_at": "2026-01-31T00:00:00+00:00",
    "activos": 17_000_000.0,
    "pasivos": 5_000_000.0,
    "patrimonio": 10_000_000.0,
    "utilidad_neta": 2_000_000.0,
    "patrimonio_total": 12_000_000.0,
    "cuadre": True,
    "mensaje_cuadre": "ACTIVOS == PASIVOS + PATRIMONIO TOTAL",
}
_MOCK_PNL = {
    "report_type": "profit_and_loss",
    "period_start": _START,
    "period_end": _END,
    "generated_at": "2026-01-31T00:00:00+00:00",
    "ingresos": [],
    "costo_ventas": [],
    "gastos": [],
    "total_ingresos": 0.0,
    "total_costo_ventas": 0.0,
    "total_gastos": 0.0,
    "utilidad_bruta": 0.0,
    "utilidad_neta": 0.0,
}
_MOCK_CASHFLOW = {
    "report_type": "cash_flow",
    "period_start": _START,
    "period_end": _END,
    "generated_at": "2026-01-31T00:00:00+00:00",
    "cuentas_efectivo": [],
    "total_efectivo": 0.0,
    "nota": "Flujo de caja directo.",
}
_MOCK_IVA = {
    "report_type": "iva_report",
    "period_start": _START,
    "period_end": _END,
    "generated_at": "2026-01-31T00:00:00+00:00",
    "iva_generado": 900_000.0,
    "iva_descontable": 300_000.0,
    "iva_a_pagar": 600_000.0,
    "referencias": ["Art. 477 ET"],
}
_MOCK_WITHHOLDINGS = {
    "report_type": "withholdings_report",
    "period_start": _START,
    "period_end": _END,
    "generated_at": "2026-01-31T00:00:00+00:00",
    "retencion_en_la_fuente": 235_000.0,
    "retencion_ica": 65_000.0,
    "total_retenciones": 300_000.0,
    "referencias": ["Art. 383 ET"],
}
# JSON GET endpoints return the raw builder dict straight through (no
# response_model). A minimal dict with period_end is enough to lock the contract.
_MOCK_GENERIC_REPORT = {
    "report_type": "generic",
    "period_start": _START,
    "period_end": _END,
    "generated_at": "2026-01-31T00:00:00+00:00",
}


def _pipeline_ok(report: dict) -> dict:
    return {"status": "ok", "report": report, "agent_log": []}


def _assert_envelope(data: dict, *keys: str) -> None:
    assert isinstance(data, dict)
    for key in keys:
        assert key in data, f"missing top-level key: {key}"


# ---------------------------------------------------------------------------
# reports.py — 8 handlers
# ---------------------------------------------------------------------------


class TestReportsCharacterization:
    def test_balance(self, client):
        with patch(
            "app.api.v1.reports.invoke_reporting_pipeline",
            return_value=_pipeline_ok(_MOCK_BALANCE),
        ):
            resp = client.get("/api/v1/reports/balance")
        assert resp.status_code == 200
        _assert_envelope(resp.json(), "report_type", "period_end", "cuadre")
        assert resp.json()["report_type"] == "balance_sheet"

    def test_pnl(self, client):
        with patch(
            "app.api.v1.reports.invoke_reporting_pipeline",
            return_value=_pipeline_ok(_MOCK_PNL),
        ):
            resp = client.get("/api/v1/reports/pnl")
        assert resp.status_code == 200
        _assert_envelope(resp.json(), "report_type", "period_end", "utilidad_neta")
        assert resp.json()["report_type"] == "profit_and_loss"

    def test_cashflow(self, client):
        with patch(
            "app.api.v1.reports.invoke_reporting_pipeline",
            return_value=_pipeline_ok(_MOCK_CASHFLOW),
        ):
            resp = client.get("/api/v1/reports/cashflow")
        assert resp.status_code == 200
        _assert_envelope(resp.json(), "report_type", "period_end", "total_efectivo")
        assert resp.json()["report_type"] == "cash_flow"

    def test_libro_diario(self, client):
        with patch(
            "app.api.v1.reports.invoke_reporting_pipeline",
            return_value=_pipeline_ok(dict(_MOCK_GENERIC_REPORT)),
        ):
            resp = client.get("/api/v1/reports/libro_diario")
        assert resp.status_code == 200
        _assert_envelope(resp.json(), "report_type", "period_end")

    def test_libro_auxiliar(self, client):
        with patch(
            "app.api.v1.reports.invoke_reporting_pipeline",
            return_value=_pipeline_ok(dict(_MOCK_GENERIC_REPORT)),
        ):
            resp = client.get("/api/v1/reports/libro_auxiliar")
        assert resp.status_code == 200
        _assert_envelope(resp.json(), "report_type", "period_end")

    def test_cambios_patrimonio(self, client):
        with patch(
            "app.api.v1.reports.invoke_reporting_pipeline",
            return_value=_pipeline_ok(dict(_MOCK_GENERIC_REPORT)),
        ):
            resp = client.get("/api/v1/reports/cambios_patrimonio")
        assert resp.status_code == 200
        _assert_envelope(resp.json(), "report_type", "period_end")

    def test_notas_eeff(self, client):
        with patch(
            "app.api.v1.reports.invoke_reporting_pipeline",
            return_value=_pipeline_ok(dict(_MOCK_GENERIC_REPORT)),
        ):
            resp = client.get("/api/v1/reports/notas_estados_financieros")
        assert resp.status_code == 200
        _assert_envelope(resp.json(), "report_type", "period_end")

    def test_analysis(self, client):
        # analysis can drive an LLM narrative inside the pipeline; mocking
        # invoke_reporting_pipeline keeps it deterministic and offline.
        with patch(
            "app.api.v1.reports.invoke_reporting_pipeline",
            return_value=_pipeline_ok(dict(_MOCK_GENERIC_REPORT)),
        ):
            resp = client.get("/api/v1/reports/analysis")
        assert resp.status_code == 200
        _assert_envelope(resp.json(), "report_type", "period_end")


# ---------------------------------------------------------------------------
# tax.py — 2 handlers
# ---------------------------------------------------------------------------


class TestTaxCharacterization:
    def test_iva(self, client):
        with patch(
            "app.api.v1.tax.invoke_reporting_pipeline",
            return_value=_pipeline_ok(_MOCK_IVA),
        ):
            resp = client.get("/api/v1/tax/iva")
        assert resp.status_code == 200
        _assert_envelope(resp.json(), "report_type", "iva_a_pagar", "period_end")
        assert resp.json()["report_type"] == "iva_report"

    def test_withholdings(self, client):
        with patch(
            "app.api.v1.tax.invoke_reporting_pipeline",
            return_value=_pipeline_ok(_MOCK_WITHHOLDINGS),
        ):
            resp = client.get("/api/v1/tax/withholdings")
        assert resp.status_code == 200
        _assert_envelope(resp.json(), "report_type", "total_retenciones", "period_end")
        assert resp.json()["report_type"] == "withholdings_report"


# ---------------------------------------------------------------------------
# dashboard.py — 3 handlers (run against empty DB, no NIT → "all" branch)
# ---------------------------------------------------------------------------


class TestDashboardCharacterization:
    def test_stats(self, client):
        resp = client.get("/api/v1/dashboard/stats")
        assert resp.status_code == 200
        _assert_envelope(
            resp.json(),
            "documentos_pendientes",
            "transacciones_procesadas_mes",
            "alertas_activas",
            "transacciones_por_estado",
        )

    def test_financial_summary(self, client):
        resp = client.get("/api/v1/dashboard/financial-summary")
        assert resp.status_code == 200
        # Lock the shape: it's a dict envelope with recent-activity + tx counts.
        _assert_envelope(resp.json(), "transacciones_por_estado", "actividad_reciente")

    def test_monthly_trend(self, client):
        resp = client.get("/api/v1/dashboard/monthly-trend")
        assert resp.status_code == 200
        _assert_envelope(resp.json(), "data")
        assert isinstance(resp.json()["data"], list)


# ---------------------------------------------------------------------------
# books.py — 1 handler
# ---------------------------------------------------------------------------


class TestBooksCharacterization:
    def test_get_books_diario(self, client):
        # tipo=diario over empty DB → 200 with an empty list (Vía A path).
        resp = client.get("/api/v1/books/", params={"tipo": "diario"})
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_get_books_invalid_tipo_is_400(self, client):
        # Locks the current validation contract (not part of the 15 but cheap
        # insurance that the early-return branch is preserved).
        resp = client.get("/api/v1/books/", params={"tipo": "bogus"})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# chat.py — 1 handler (LLM boundary mocked)
# ---------------------------------------------------------------------------


class TestChatCharacterization:
    def test_chat(self, client):
        mock_response = ChatResponse(
            reply="El balance general muestra activos por 17M.",
            session_id="sess_1",
            data_cards=[],
            intent_detected="balance",
            sources=[],
            reasoning=[],
        )
        with patch(
            "app.api.v1.chat.chat_service.handle_chat_message",
            return_value=mock_response,
        ):
            resp = client.post(
                "/api/v1/chat",
                json={"message": "¿Cuál es mi balance?"},
            )
        assert resp.status_code == 200
        _assert_envelope(
            resp.json(),
            "reply",
            "session_id",
            "data_cards",
            "intent_detected",
            "sources",
            "reasoning",
        )
        assert resp.json()["session_id"] == "sess_1"
