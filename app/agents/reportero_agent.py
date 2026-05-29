"""
Agente Reportero (Reporter / Financial Analyst)

Role (docs/Diseño de arquitectura de agente):
  - Triggered by GET /reports/* and GET /tax/* API endpoints via mode="reporting".
  - Queries SQL Libro Mayor (JournalEntryLine) and returns structured reports.
  - Read-only database access (never modifies data).

Supported report types (state["report_type"]):
  - "balance"      → Balance General (Balance Sheet)
  - "pnl"          → Estado de Resultados (Profit & Loss)
  - "cashflow"     → Flujo de Caja (Cash Flow — direct method, class 11 accounts)
  - "iva"          → Reporte IVA (accounts 240805 / 240802)
  - "withholdings" → Retenciones (accounts 2365 / 2368)
  - "analysis"     → Análisis Financiero Integral (ratios, predicciones, LLM narrative)

Filter params (state["report_params"]):
  - start_date: ISO date string "YYYY-MM-DD" (optional)
  - end_date:   ISO date string "YYYY-MM-DD" (optional)
  - include_analysis: bool (optional, adds LLM narrative to standard reports)

PDF export improvements:
  - Account descriptions are word-wrapped using ReportLab Paragraph objects to prevent
    text overflow in table cells (common with long Colombian account names).
  - Column widths optimized for full page width with proper text wrapping.
  See app/services/report_export_service.py for implementation details.
"""

import logging

from app.agents.agent_utils import append_log
from app.agents.state import AgentState
from app.services.report_builders import (
    build_balance,
    build_pnl,
    build_cashflow,
    build_iva,
    build_withholdings,
    build_analysis,
    build_libro_diario,
    build_libro_auxiliar,
    build_cambios_patrimonio,
    build_notas_eeff,
)

# db_service and SessionLocal are imported lazily inside reportero_node to
# avoid loading psycopg2 / SQLAlchemy engine at module import time.

logger = logging.getLogger(__name__)


_VALID_REPORT_TYPES = frozenset(
    {
        "balance",
        "pnl",
        "cashflow",
        "iva",
        "withholdings",
        "analysis",
        "libro_diario",
        "libro_auxiliar",
        "cambios_patrimonio",
        "notas_eeff",
    }
)

# ---------------------------------------------------------------------------
# RAG enrichment helper (non-fatal)
# ---------------------------------------------------------------------------


def _fetch_rag_context_text(query: str, n_results: int = 5) -> str:
    """Return RAG results as a single text block for LLM context."""
    try:
        from app.services.rag_service import get_rag_service  # noqa: PLC0415

        rag_svc = get_rag_service()
        results = rag_svc.search_normativo(query, n_results=n_results)
        parts = []
        for r in results:
            articulo = r.metadata.get("articulo", "")
            fuente = r.metadata.get("fuente", "")
            header = f"[{articulo} - {fuente}]" if articulo else ""
            parts.append(f"{header}\n{r.content[:500]}")
        return "\n\n".join(parts)
    except Exception:  # noqa: BLE001
        return ""


_BUILDERS = {
    "balance": build_balance,
    "pnl": build_pnl,
    "cashflow": build_cashflow,
    "iva": build_iva,
    "withholdings": build_withholdings,
    "analysis": build_analysis,
    "libro_diario": build_libro_diario,
    "libro_auxiliar": build_libro_auxiliar,
    "cambios_patrimonio": build_cambios_patrimonio,
    "notas_eeff": build_notas_eeff,
}


# ---------------------------------------------------------------------------
# Brief analysis helper (for include_analysis=true on standard reports)
# ---------------------------------------------------------------------------


def _enrich_with_brief_analysis(report_data: dict, report_type: str) -> dict:
    """Append a brief LLM analysis to a standard report (non-fatal)."""
    try:
        from app.core.llm_client import get_llm_client  # noqa: PLC0415

        llm = get_llm_client()
        rag_text = _fetch_rag_context_text(
            f"{report_type} análisis financiero NIIF Colombia"
        )
        analysis = llm.generate_brief_report_analysis(
            report_type=report_type,
            report_data=report_data,
            rag_context=rag_text,
        )
        report_data["analysis"] = analysis
    except Exception as err:  # noqa: BLE001
        logger.warning("reportero: brief analysis failed (non-fatal): %s", err)
        report_data["analysis"] = {"error": f"Análisis LLM no disponible: {err}"}
    return report_data


# ---------------------------------------------------------------------------
# Node entry point
# ---------------------------------------------------------------------------


def reportero_node(state: AgentState) -> AgentState:
    """
    Reportero node: queries Libro Mayor and formats financial reports.

    Reads:
        state["report_type"]   – one of the valid report types
        state["report_params"] – filter dict with start_date, end_date, include_analysis
    Writes:
        state["result"]["report"] – structured report data dict
        state["current_stage"]    – "reporting_complete"
        state["current_agent"]    – "reportero"
    """
    if state.get("error"):
        logger.warning("reportero: skipping due to upstream error: %s", state["error"])
        return state

    report_type = state.get("report_type")
    if not report_type:
        state["error"] = "reportero: report_type is required in state"
        logger.error(state["error"])
        return state

    if report_type not in _VALID_REPORT_TYPES:
        state["error"] = (
            f"reportero: unknown report_type '{report_type}'. "
            f"Valid values: {sorted(_VALID_REPORT_TYPES)}"
        )
        logger.error(state["error"])
        return state

    params: dict = state.get("report_params") or {}
    if state.get("company_nit") and not params.get("company_nit"):
        params["company_nit"] = state.get("company_nit")
    include_analysis = params.get("include_analysis", False)
    state["current_agent"] = "reportero"
    state["current_stage"] = "reportero"

    append_log(
        state,
        "reportero",
        "node_start",
        {
            "report_type": report_type,
            "params": params,
            "include_analysis": include_analysis,
        },
    )

    try:
        from app.core.database import SessionLocal  # noqa: PLC0415
        from app.services import db_service  # noqa: PLC0415
    except ImportError as import_exc:
        state["error"] = f"reportero: database dependencies not available: {import_exc}"
        logger.error(state["error"])
        return state

    db = SessionLocal()
    try:
        builder = _BUILDERS[report_type]
        report_data = builder(db, params, db_service)

        # Add brief LLM analysis to standard reports if requested
        if include_analysis and report_type != "analysis":
            report_data = _enrich_with_brief_analysis(report_data, report_type)

    except Exception as exc:
        state["error"] = f"reportero: failed to generate '{report_type}' report: {exc}"
        logger.error(state["error"], exc_info=True)
        append_log(state, "reportero", "node_error", {"error": str(exc)})
        if not state.get("result"):
            state["result"] = {}
        state["result"]["status"] = "error"
        state["result"]["error"] = state["error"]
        return state
    finally:
        db.close()

    if not state.get("result"):
        state["result"] = {}
    state["result"]["report"] = report_data
    state["result"]["status"] = "ok"
    state["current_stage"] = "reporting_complete"

    append_log(
        state,
        "reportero",
        "node_complete",
        {
            "report_type": report_type,
            "generated_at": report_data.get("generated_at"),
            "has_analysis": "analysis" in report_data,
        },
    )
    logger.info("reportero: '%s' report generated successfully", report_type)
    return state
