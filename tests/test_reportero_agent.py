"""
Tests for the Reportero agent node and the GET /reports/* + /tax/* API endpoints.

Coverage:
  Unit tests — reportero_node (db_service mocked via sys.modules):
    1.  Balance report — returns structured BalanceSheetOutput-compatible dict
    2.  P&L report — aggregates class 4/5/6 PUC accounts correctly
    3.  Cash flow report — filters class 11XX accounts
    4.  IVA report — reads accounts 240808 / 240802
    5.  Withholdings report — reads accounts 240815 / 236540
    6.  Missing report_type sets error
    7.  Invalid report_type sets error
    8.  Upstream error passthrough — node returns without touching result
    9.  DB exception sets error and does not propagate exception
   10.  Empty ledger → all-zero reports (no crash)
   11.  agent_log populated with node_start and node_complete events

  API tests (FastAPI TestClient, invoke_reporting_pipeline mocked):
   12.  GET /reports/balance → 200
   13.  GET /reports/pnl → 200
   14.  GET /reports/cashflow → 200
   15.  GET /tax/iva → 200
   16.  GET /tax/withholdings → 200
   17.  Date query params forwarded correctly
   18.  Pipeline error → 500

Notes:
  - reportero_agent imports db_service and SessionLocal lazily (inside reportero_node)
    so the module can be imported without psycopg2 / a live DB connection.
  - Unit tests inject mocks via patch.dict(sys.modules) before the lazy imports run.
  - API tests mock invoke_reporting_pipeline directly (no DB needed).
  - Fixed dates throughout — never datetime.now() (lesson from PR #16 review).
"""

import sys
from decimal import Decimal
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

# reportero_agent can be imported without psycopg2 — DB imports are lazy.
from app.agents.reportero_agent import reportero_node
from tests.conftest import base_reporting_state, base_state

# ─── Constants ───────────────────────────────────────────────────────────────

# Fixed dates — deterministic tests (never datetime.now())
_START = "2026-01-01"
_END   = "2026-01-31"

# ─── Mock data ───────────────────────────────────────────────────────────────

_BALANCE_DATA = {
    "assets":        17_000_000.0,
    "liabilities":    5_000_000.0,
    "equity":        10_000_000.0,
    "revenue":        8_000_000.0,
    "expenses":       2_000_000.0,
    "cost_of_sales":  4_000_000.0,
    "net_profit":     2_000_000.0,
    "total_equity":  12_000_000.0,
    "is_balanced":   True,
}

_LEDGER = [
    # class 4 — Ingresos
    {"account": "4135",   "name": "Servicios",            "total_debit": 0.0,           "total_credit": 8_000_000.0, "net_balance": -8_000_000.0},
    # class 5 — Gastos
    {"account": "5110",   "name": "Honorarios",           "total_debit": 2_000_000.0,   "total_credit": 0.0,         "net_balance":  2_000_000.0},
    # class 6 — Costo ventas
    {"account": "6135",   "name": "Costo servicios",      "total_debit": 4_000_000.0,   "total_credit": 0.0,         "net_balance":  4_000_000.0},
    # class 11 — Efectivo
    {"account": "1110",   "name": "Bancos",                "total_debit": 5_000_000.0,   "total_credit": 1_000_000.0, "net_balance":  4_000_000.0},
    {"account": "1105",   "name": "Caja",                  "total_debit":   500_000.0,   "total_credit":   200_000.0, "net_balance":    300_000.0},
    # IVA
    {"account": "240808", "name": "IVA Generado",          "total_debit": 0.0,           "total_credit":   900_000.0, "net_balance":   -900_000.0},
    {"account": "240802", "name": "IVA Descontable",       "total_debit": 300_000.0,     "total_credit": 0.0,         "net_balance":    300_000.0},
    # Retenciones
    {"account": "240815", "name": "Retefuente por Pagar",  "total_debit": 0.0,           "total_credit":   235_000.0, "net_balance":   -235_000.0},
    {"account": "236540", "name": "ReteICA por Pagar",     "total_debit": 0.0,           "total_credit":    65_000.0, "net_balance":    -65_000.0},
]


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _mock_db_modules(svc_mock: MagicMock) -> dict:
    """
    Return a sys.modules patch dict that injects mock DB objects before the
    lazy imports inside reportero_node execute.

    reportero_node does:
        from app.core.database import SessionLocal   → needs mock_database_module.SessionLocal
        from app.services import db_service          → needs sys.modules["app.services.db_service"]

    We pre-populate sys.modules so Python resolves these to our mocks instead
    of importing the real modules (which need psycopg2).
    """
    mock_database_module = MagicMock(spec=ModuleType)
    mock_database_module.SessionLocal = MagicMock(return_value=MagicMock())

    # Always use a fresh ModuleType — never mutates the real app.services object
    # (patch.dict restores sys.modules mappings but NOT attribute mutations on an
    # existing module object, which could leak mock state to other tests).
    mock_services_pkg = ModuleType("app.services")
    mock_services_pkg.db_service = svc_mock

    return {
        "app.core.database":       mock_database_module,
        "app.services":            mock_services_pkg,
        "app.services.db_service": svc_mock,
    }


# ─── Unit tests — reportero_node ─────────────────────────────────────────────

class TestReporteroNodeBalance:
    def test_balance_report_success(self):
        state = base_reporting_state("balance", start_date=_START, end_date=_END)
        svc = MagicMock()
        svc.get_balance_sheet.return_value = _BALANCE_DATA

        with patch.dict(sys.modules, _mock_db_modules(svc)):
            result_state = reportero_node(state)

        assert result_state.get("error") is None
        report = result_state["result"]["report"]
        assert report["report_type"] == "balance_sheet"
        assert report["cuadre"] is True
        assert report["activos"] == pytest.approx(17_000_000.0)
        assert report["utilidad_neta"] == pytest.approx(2_000_000.0)
        assert result_state["current_stage"] == "reporting_complete"

    def test_balance_mensaje_cuadre_when_balanced(self):
        state = base_reporting_state("balance", end_date=_END)
        svc = MagicMock()
        svc.get_balance_sheet.return_value = _BALANCE_DATA

        with patch.dict(sys.modules, _mock_db_modules(svc)):
            result_state = reportero_node(state)

        assert "✓" in result_state["result"]["report"]["mensaje_cuadre"]

    def test_balance_mensaje_cuadre_when_unbalanced(self):
        state = base_reporting_state("balance")
        svc = MagicMock()
        svc.get_balance_sheet.return_value = dict(_BALANCE_DATA, is_balanced=False)

        with patch.dict(sys.modules, _mock_db_modules(svc)):
            result_state = reportero_node(state)

        report = result_state["result"]["report"]
        assert report["cuadre"] is False
        assert "DESCUADRE" in report["mensaje_cuadre"]


class TestReporteroNodePnL:
    def test_pnl_report_aggregation(self):
        state = base_reporting_state("pnl", start_date=_START, end_date=_END)
        svc = MagicMock()
        svc.get_general_ledger.return_value = _LEDGER

        with patch.dict(sys.modules, _mock_db_modules(svc)):
            result_state = reportero_node(state)

        assert result_state.get("error") is None
        report = result_state["result"]["report"]
        assert report["report_type"] == "profit_and_loss"
        assert report["total_ingresos"]      == pytest.approx(8_000_000.0)
        assert report["total_costo_ventas"]  == pytest.approx(4_000_000.0)
        assert report["total_gastos"]        == pytest.approx(2_000_000.0)
        assert report["utilidad_bruta"]      == pytest.approx(4_000_000.0)  # 8M - 4M
        assert report["utilidad_neta"]       == pytest.approx(2_000_000.0)  # 4M - 2M

    def test_pnl_empty_ledger_returns_zeros(self):
        state = base_reporting_state("pnl")
        svc = MagicMock()
        svc.get_general_ledger.return_value = []

        with patch.dict(sys.modules, _mock_db_modules(svc)):
            result_state = reportero_node(state)

        assert result_state.get("error") is None
        report = result_state["result"]["report"]
        assert report["utilidad_neta"] == pytest.approx(0.0)
        assert report["ingresos"]      == []
        assert report["gastos"]        == []


class TestReporteroNodeCashFlow:
    def test_cashflow_filters_class_11(self):
        state = base_reporting_state("cashflow", start_date=_START, end_date=_END)
        svc = MagicMock()
        svc.get_general_ledger.return_value = _LEDGER

        with patch.dict(sys.modules, _mock_db_modules(svc)):
            result_state = reportero_node(state)

        assert result_state.get("error") is None
        report = result_state["result"]["report"]
        assert report["report_type"] == "cash_flow"
        assert len(report["cuentas_efectivo"]) == 2   # 1110 + 1105
        # 1110: 5M-1M=4M; 1105: 500K-200K=300K → total=4.3M
        assert report["total_efectivo"] == pytest.approx(4_300_000.0)

    def test_cashflow_nota_present(self):
        state = base_reporting_state("cashflow")
        svc = MagicMock()
        svc.get_general_ledger.return_value = []

        with patch.dict(sys.modules, _mock_db_modules(svc)):
            result_state = reportero_node(state)

        assert "nota" in result_state["result"]["report"]


class TestReporteroNodeIVA:
    def test_iva_report(self):
        state = base_reporting_state("iva", start_date=_START, end_date=_END)
        svc = MagicMock()
        svc.get_general_ledger.return_value = _LEDGER

        with patch.dict(sys.modules, _mock_db_modules(svc)):
            result_state = reportero_node(state)

        assert result_state.get("error") is None
        report = result_state["result"]["report"]
        assert report["report_type"]      == "iva_report"
        assert report["iva_generado"]     == pytest.approx(900_000.0)
        assert report["iva_descontable"]  == pytest.approx(300_000.0)
        assert report["iva_a_pagar"]      == pytest.approx(600_000.0)
        assert isinstance(report["referencias"], list)

    def test_iva_zero_when_accounts_absent(self):
        state = base_reporting_state("iva")
        svc = MagicMock()
        svc.get_general_ledger.return_value = []

        with patch.dict(sys.modules, _mock_db_modules(svc)):
            result_state = reportero_node(state)

        report = result_state["result"]["report"]
        assert report["iva_generado"]    == pytest.approx(0.0)
        assert report["iva_descontable"] == pytest.approx(0.0)
        assert report["iva_a_pagar"]     == pytest.approx(0.0)


class TestReporteroNodeWithholdings:
    def test_withholdings_report(self):
        state = base_reporting_state("withholdings", start_date=_START, end_date=_END)
        svc = MagicMock()
        svc.get_general_ledger.return_value = _LEDGER

        with patch.dict(sys.modules, _mock_db_modules(svc)):
            result_state = reportero_node(state)

        assert result_state.get("error") is None
        report = result_state["result"]["report"]
        assert report["report_type"]             == "withholdings_report"
        assert report["retencion_en_la_fuente"]  == pytest.approx(235_000.0)
        assert report["retencion_ica"]           == pytest.approx(65_000.0)
        assert report["total_retenciones"]       == pytest.approx(300_000.0)
        assert isinstance(report["referencias"], list)


class TestReporteroNodeErrorHandling:
    def test_missing_report_type_sets_error(self):
        state = base_state(mode="reporting")  # report_type=None — no DB needed
        result_state = reportero_node(state)

        assert result_state["error"] is not None
        assert "report_type" in result_state["error"]

    def test_invalid_report_type_sets_error(self):
        state = base_reporting_state("nonexistent_report")  # no DB needed
        result_state = reportero_node(state)

        assert result_state["error"] is not None
        assert "nonexistent_report" in result_state["error"]

    def test_upstream_error_passthrough(self):
        """State with a pre-existing error must be returned unchanged (no DB call)."""
        state = base_state(mode="reporting", report_type="balance", error="upstream failure")
        result_state = reportero_node(state)

        assert result_state["error"] == "upstream failure"
        assert result_state["result"] == {}

    def test_db_exception_sets_error(self):
        state = base_reporting_state("balance")
        svc = MagicMock()
        svc.get_balance_sheet.side_effect = RuntimeError("DB connection failed")

        with patch.dict(sys.modules, _mock_db_modules(svc)):
            result_state = reportero_node(state)

        assert result_state["error"] is not None
        assert "DB connection failed" in result_state["error"]
        assert result_state["result"]["status"] == "error"  # must not re-raise

    def test_agent_log_populated(self):
        state = base_reporting_state("iva")
        svc = MagicMock()
        svc.get_general_ledger.return_value = []

        with patch.dict(sys.modules, _mock_db_modules(svc)):
            result_state = reportero_node(state)

        assert len(result_state["agent_log"]) >= 2
        events = [e["event"] for e in result_state["agent_log"]]
        assert "node_start"    in events
        assert "node_complete" in events


# ─── API tests ───────────────────────────────────────────────────────────────

_MOCK_BALANCE_RESULT = {
    "report_type": "balance_sheet", "period_start": _START, "period_end": _END,
    "generated_at": "2026-01-31T00:00:00+00:00",
    "activos": 17_000_000.0, "pasivos": 5_000_000.0, "patrimonio": 10_000_000.0,
    "utilidad_neta": 2_000_000.0, "patrimonio_total": 12_000_000.0,
    "cuadre": True, "mensaje_cuadre": "ACTIVOS == PASIVOS + PATRIMONIO TOTAL ✓",
}
_MOCK_PNL_RESULT = {
    "report_type": "profit_and_loss", "period_start": _START, "period_end": _END,
    "generated_at": "2026-01-31T00:00:00+00:00",
    "ingresos": [], "costo_ventas": [], "gastos": [],
    "total_ingresos": 0.0, "total_costo_ventas": 0.0, "total_gastos": 0.0,
    "utilidad_bruta": 0.0, "utilidad_neta": 0.0,
}
_MOCK_CASHFLOW_RESULT = {
    "report_type": "cash_flow", "period_start": _START, "period_end": _END,
    "generated_at": "2026-01-31T00:00:00+00:00",
    "cuentas_efectivo": [], "total_efectivo": 0.0, "nota": "Flujo de caja directo.",
}
_MOCK_IVA_RESULT = {
    "report_type": "iva_report", "period_start": _START, "period_end": _END,
    "generated_at": "2026-01-31T00:00:00+00:00",
    "iva_generado": 900_000.0, "iva_descontable": 300_000.0, "iva_a_pagar": 600_000.0,
    "referencias": ["Art. 477 ET"],
}
_MOCK_WITHHOLDINGS_RESULT = {
    "report_type": "withholdings_report", "period_start": _START, "period_end": _END,
    "generated_at": "2026-01-31T00:00:00+00:00",
    "retencion_en_la_fuente": 235_000.0, "retencion_ica": 65_000.0,
    "total_retenciones": 300_000.0, "referencias": ["Art. 383 ET"],
}


def _pipeline_ok(report_data: dict) -> dict:
    return {"status": "ok", "report": report_data, "agent_log": []}


@pytest.fixture
def client():
    """FastAPI TestClient — DB and vectordb disabled, pipeline mocked per test."""
    from fastapi.testclient import TestClient  # lazy: only available in container env
    with patch("app.core.database.SessionLocal"), \
         patch("app.core.vectordb.get_vectordb", return_value=MagicMock()):
        from main import app
        with TestClient(app) as c:
            yield c


class TestReportsAPI:
    def test_get_balance_returns_200(self, client):
        with patch("app.api.v1.reports.invoke_reporting_pipeline",
                   return_value=_pipeline_ok(_MOCK_BALANCE_RESULT)) as mock_fn:
            resp = client.get("/api/v1/reports/balance")

        assert resp.status_code == 200
        assert resp.json()["report_type"] == "balance_sheet"
        assert resp.json()["cuadre"] is True
        _, kwargs = mock_fn.call_args
        assert kwargs["report_type"] == "balance"

    def test_get_pnl_returns_200(self, client):
        with patch("app.api.v1.reports.invoke_reporting_pipeline",
                   return_value=_pipeline_ok(_MOCK_PNL_RESULT)):
            resp = client.get("/api/v1/reports/pnl")

        assert resp.status_code == 200
        assert resp.json()["report_type"] == "profit_and_loss"

    def test_get_cashflow_returns_200(self, client):
        with patch("app.api.v1.reports.invoke_reporting_pipeline",
                   return_value=_pipeline_ok(_MOCK_CASHFLOW_RESULT)):
            resp = client.get("/api/v1/reports/cashflow")

        assert resp.status_code == 200
        assert resp.json()["report_type"] == "cash_flow"

    def test_get_balance_with_date_params(self, client):
        with patch("app.api.v1.reports.invoke_reporting_pipeline",
                   return_value=_pipeline_ok(_MOCK_BALANCE_RESULT)) as mock_fn:
            resp = client.get(
                "/api/v1/reports/balance",
                params={"start_date": _START, "end_date": _END},
            )

        assert resp.status_code == 200
        _, kwargs = mock_fn.call_args
        assert kwargs["report_params"]["start_date"] == _START
        assert kwargs["report_params"]["end_date"]   == _END

    def test_reports_pipeline_error_returns_500(self, client):
        with patch("app.api.v1.reports.invoke_reporting_pipeline",
                   return_value={"status": "error", "error": "DB unavailable", "agent_log": []}):
            resp = client.get("/api/v1/reports/balance")

        assert resp.status_code == 500
        assert "DB unavailable" in resp.json()["detail"]


class TestTaxAPI:
    def test_get_iva_returns_200(self, client):
        with patch("app.api.v1.tax.invoke_reporting_pipeline",
                   return_value=_pipeline_ok(_MOCK_IVA_RESULT)) as mock_fn:
            resp = client.get("/api/v1/tax/iva")

        assert resp.status_code == 200
        data = resp.json()
        assert data["report_type"] == "iva_report"
        assert data["iva_a_pagar"] == pytest.approx(600_000.0)
        _, kwargs = mock_fn.call_args
        assert kwargs["report_type"] == "iva"

    def test_get_withholdings_returns_200(self, client):
        with patch("app.api.v1.tax.invoke_reporting_pipeline",
                   return_value=_pipeline_ok(_MOCK_WITHHOLDINGS_RESULT)):
            resp = client.get("/api/v1/tax/withholdings")

        assert resp.status_code == 200
        data = resp.json()
        assert data["report_type"] == "withholdings_report"
        assert data["total_retenciones"] == pytest.approx(300_000.0)

    def test_get_tax_with_date_params(self, client):
        with patch("app.api.v1.tax.invoke_reporting_pipeline",
                   return_value=_pipeline_ok(_MOCK_IVA_RESULT)) as mock_fn:
            resp = client.get(
                "/api/v1/tax/iva",
                params={"start_date": _START, "end_date": _END},
            )

        assert resp.status_code == 200
        _, kwargs = mock_fn.call_args
        assert kwargs["report_params"]["start_date"] == _START
        assert kwargs["report_params"]["end_date"]   == _END

    def test_tax_pipeline_error_returns_500(self, client):
        with patch("app.api.v1.tax.invoke_reporting_pipeline",
                   return_value={"status": "error", "error": "ledger empty", "agent_log": []}):
            resp = client.get("/api/v1/tax/withholdings")

        assert resp.status_code == 500


# ─── RAG enrichment tests ─────────────────────────────────────────────────────

# Sample RAG result dicts used to build mock RAGResult objects
_RAG_IVA_RESULTS = [
    {"content": "Artículo 468 ET. Tarifa general IVA 19%.", "metadata": {"articulo": "Art. 468 ET", "fuente": "Estatuto Tributario"}, "score": 0.95},
    {"content": "Artículo 477 ET. Bienes exentos del IVA.", "metadata": {"articulo": "Art. 477 ET", "fuente": "Estatuto Tributario"}, "score": 0.88},
]
_RAG_WITHHOLDING_RESULTS = [
    {"content": "Artículo 392 ET. Retención sobre honorarios 11%.", "metadata": {"articulo": "Art. 392 ET", "fuente": "Estatuto Tributario"}, "score": 0.93},
    {"content": "Decreto 2048/1992. ReteICA.", "metadata": {"articulo": "Decreto 2048/1992", "fuente": "Decreto 2048 de 1992"}, "score": 0.85},
]
_RAG_NIIF_RESULTS = [
    {"content": "Principio de realización. Ley 43/1990 Art. 12.", "metadata": {"articulo": "Art. 12 Ley 43/1990", "fuente": "Ley 43/1990"}, "score": 0.87},
]


def _mock_rag_modules(rag_results: list) -> dict:
    """
    Return a sys.modules patch dict injecting a mock RAG service.

    reportero_agent's _fetch_rag_referencias does:
        from app.services.rag_service import get_rag_service
    so we mock 'app.services.rag_service' in sys.modules.
    """
    mock_rag_result_objects = []
    for r in rag_results:
        mock_r = MagicMock()
        mock_r.content = r.get("content", "")
        mock_r.metadata = r.get("metadata", {})
        mock_r.score = r.get("score", 0.9)
        mock_rag_result_objects.append(mock_r)

    mock_rag_svc = MagicMock()
    mock_rag_svc.search_normativo.return_value = mock_rag_result_objects

    mock_rag_module = MagicMock(spec=ModuleType)
    mock_rag_module.get_rag_service = MagicMock(return_value=mock_rag_svc)

    return {"app.services.rag_service": mock_rag_module}


class TestReporteroNodeRAGEnrichment:
    """Verifies RAG enrichment and fallback behaviour in the reportero node."""

    def test_iva_referencias_from_rag(self):
        """When RAG returns results with articulo metadata, referencias uses them."""
        state = base_reporting_state("iva", start_date=_START, end_date=_END)
        svc = MagicMock()
        svc.get_general_ledger.return_value = _LEDGER

        all_mocks = {**_mock_db_modules(svc), **_mock_rag_modules(_RAG_IVA_RESULTS)}
        with patch.dict(sys.modules, all_mocks):
            result_state = reportero_node(state)

        assert result_state.get("error") is None
        referencias = result_state["result"]["report"]["referencias"]
        assert any("Art. 468 ET" in ref for ref in referencias)
        assert any("Art. 477 ET" in ref for ref in referencias)

    def test_iva_referencias_fallback_when_rag_fails(self):
        """When get_rag_service() raises, hardcoded fallback is used; state error stays None."""
        state = base_reporting_state("iva", start_date=_START, end_date=_END)
        svc = MagicMock()
        svc.get_general_ledger.return_value = _LEDGER

        mock_rag_module = MagicMock()
        mock_rag_module.get_rag_service.side_effect = RuntimeError("RAG unavailable")

        all_mocks = {**_mock_db_modules(svc), "app.services.rag_service": mock_rag_module}
        with patch.dict(sys.modules, all_mocks):
            result_state = reportero_node(state)

        assert result_state.get("error") is None  # RAG failure MUST NOT set error
        assert result_state["result"]["status"] == "ok"
        referencias = result_state["result"]["report"]["referencias"]
        assert "Art. 477 ET" in referencias  # hardcoded fallback

    def test_withholdings_referencias_from_rag(self):
        """Withholdings referencias are sourced from RAG when available."""
        state = base_reporting_state("withholdings", start_date=_START, end_date=_END)
        svc = MagicMock()
        svc.get_general_ledger.return_value = _LEDGER

        all_mocks = {**_mock_db_modules(svc), **_mock_rag_modules(_RAG_WITHHOLDING_RESULTS)}
        with patch.dict(sys.modules, all_mocks):
            result_state = reportero_node(state)

        assert result_state.get("error") is None
        referencias = result_state["result"]["report"]["referencias"]
        assert any("Art. 392 ET" in ref for ref in referencias)

    def test_withholdings_fallback_when_rag_empty(self):
        """Empty RAG result list triggers hardcoded fallback referencias."""
        state = base_reporting_state("withholdings")
        svc = MagicMock()
        svc.get_general_ledger.return_value = _LEDGER

        all_mocks = {**_mock_db_modules(svc), **_mock_rag_modules([])}
        with patch.dict(sys.modules, all_mocks):
            result_state = reportero_node(state)

        assert result_state.get("error") is None
        referencias = result_state["result"]["report"]["referencias"]
        assert "Art. 383 ET" in referencias  # hardcoded fallback

    def test_balance_notas_normativas_from_rag(self):
        """Balance report includes notas_normativas when RAG returns NIIF content."""
        state = base_reporting_state("balance", end_date=_END)
        svc = MagicMock()
        svc.get_balance_sheet.return_value = _BALANCE_DATA

        all_mocks = {**_mock_db_modules(svc), **_mock_rag_modules(_RAG_NIIF_RESULTS)}
        with patch.dict(sys.modules, all_mocks):
            result_state = reportero_node(state)

        assert result_state.get("error") is None
        report = result_state["result"]["report"]
        assert "notas_normativas" in report
        assert len(report["notas_normativas"]) > 0

    def test_balance_notas_normativas_empty_when_rag_fails(self):
        """notas_normativas is [] (key present, not absent) when RAG raises."""
        state = base_reporting_state("balance", end_date=_END)
        svc = MagicMock()
        svc.get_balance_sheet.return_value = _BALANCE_DATA

        mock_rag_module = MagicMock()
        mock_rag_module.get_rag_service.side_effect = Exception("connection refused")

        all_mocks = {**_mock_db_modules(svc), "app.services.rag_service": mock_rag_module}
        with patch.dict(sys.modules, all_mocks):
            result_state = reportero_node(state)

        assert result_state.get("error") is None
        report = result_state["result"]["report"]
        assert report.get("notas_normativas") == []  # empty list, not absent key

    def test_pnl_notas_normativas_field_present(self):
        """P&L report always includes notas_normativas key (may be empty)."""
        state = base_reporting_state("pnl", start_date=_START, end_date=_END)
        svc = MagicMock()
        svc.get_general_ledger.return_value = _LEDGER

        all_mocks = {**_mock_db_modules(svc), **_mock_rag_modules(_RAG_NIIF_RESULTS)}
        with patch.dict(sys.modules, all_mocks):
            result_state = reportero_node(state)

        assert result_state.get("error") is None
        assert "notas_normativas" in result_state["result"]["report"]

    def test_cashflow_notas_normativas_field_present(self):
        """CashFlow report always includes notas_normativas key (may be empty)."""
        state = base_reporting_state("cashflow", start_date=_START, end_date=_END)
        svc = MagicMock()
        svc.get_general_ledger.return_value = _LEDGER

        all_mocks = {**_mock_db_modules(svc), **_mock_rag_modules(_RAG_NIIF_RESULTS)}
        with patch.dict(sys.modules, all_mocks):
            result_state = reportero_node(state)

        assert result_state.get("error") is None
        assert "notas_normativas" in result_state["result"]["report"]

    @pytest.mark.parametrize("report_type,svc_method,mock_return", [
        ("iva", "get_general_ledger", _LEDGER),
        ("withholdings", "get_general_ledger", _LEDGER),
        ("balance", "get_balance_sheet", _BALANCE_DATA),
        ("pnl", "get_general_ledger", _LEDGER),
        ("cashflow", "get_general_ledger", _LEDGER),
    ])
    def test_rag_failure_never_sets_state_error(self, report_type, svc_method, mock_return):
        """Critical invariant: RAG RuntimeError MUST NOT set state['error'] for any report type."""
        state = base_reporting_state(report_type)
        svc = MagicMock()
        getattr(svc, svc_method).return_value = mock_return

        mock_rag_module = MagicMock()
        mock_rag_module.get_rag_service.side_effect = RuntimeError("network error")

        all_mocks = {**_mock_db_modules(svc), "app.services.rag_service": mock_rag_module}
        with patch.dict(sys.modules, all_mocks):
            result_state = reportero_node(state)

        assert result_state.get("error") is None, \
            f"RAG failure set error for report_type={report_type!r}"
        assert result_state["result"]["status"] == "ok"
