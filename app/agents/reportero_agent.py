"""
Agente Reportero (Reporter)

Role (docs/Diseño de arquitectura de agente):
  - Triggered by GET /reports/* and GET /tax/* API endpoints via mode="reporting".
  - Queries SQL Libro Mayor (JournalEntryLine) and returns structured reports.
  - NO LLM calls — pure deterministic SQL aggregation via db_service.
  - Read-only database access (never modifies data).

Supported report types (state["report_type"]):
  - "balance"      → Balance General (Balance Sheet)
  - "pnl"          → Estado de Resultados (Profit & Loss)
  - "cashflow"     → Flujo de Caja (Cash Flow — direct method, class 11 accounts)
  - "iva"          → Reporte IVA (accounts 240808 / 240802)
  - "withholdings" → Retenciones (accounts 240815 / 236540)

Filter params (state["report_params"]):
  - start_date: ISO date string "YYYY-MM-DD" (optional)
  - end_date:   ISO date string "YYYY-MM-DD" (optional)
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from app.agents.agent_utils import append_log
from app.agents.state import AgentState

# db_service and SessionLocal are imported lazily inside reportero_node to
# avoid loading psycopg2 / SQLAlchemy engine at module import time.
# This mirrors the pattern used in tributario_agent.py.

logger = logging.getLogger(__name__)

# PUC account prefixes / codes used for report aggregation
_CLASS_ACTIVOS = "1"
_CLASS_PASIVOS = "2"
_CLASS_PATRIMONIO = "3"
_CLASS_INGRESOS = "4"
_CLASS_GASTOS = "5"
_CLASS_COSTO_VENTAS = "6"
_PREFIX_EFECTIVO = "11"          # Bancos, Caja, equivalentes de efectivo

# Specific tax retention accounts
_CUENTA_IVA_GENERADO = "240808"
_CUENTA_IVA_DESCONTABLE = "240802"
_CUENTA_RETEFUENTE = "240815"
_CUENTA_RETEICA = "236540"

_VALID_REPORT_TYPES = frozenset({"balance", "pnl", "cashflow", "iva", "withholdings"})


# ---------------------------------------------------------------------------
# RAG enrichment helper (non-fatal — follows the same pattern as contador /
# tributario agents: lazy import, warning on failure, empty list fallback)
# ---------------------------------------------------------------------------

def _fetch_rag_referencias(query: str, n_results: int = 3) -> list[str]:
    """
    Query the normativa RAG collection and return human-readable citation strings.

    Each citation is built from RAGResult.metadata['articulo'] + ['fuente']
    when available, otherwise falls back to the first 80 characters of content.

    Returns an empty list on any error so callers can use hardcoded fallbacks.
    RAG is strictly non-fatal for the reportero: a missing or unreachable
    vector DB must never prevent a financial report from being generated.
    """
    try:
        from app.services.rag_service import get_rag_service  # noqa: PLC0415
        rag_svc = get_rag_service()
        results = rag_svc.search_normativo(query, n_results=n_results)
        citations: list[str] = []
        for r in results:
            articulo = r.metadata.get("articulo")
            fuente = r.metadata.get("fuente", "")
            if articulo:
                citations.append(f"{articulo} ({fuente})" if fuente else articulo)
            else:
                citations.append(r.content[:80])
        logger.info(
            "reportero: RAG returned %d citations for query '%s'",
            len(citations),
            query[:50],
        )
        return citations
    except Exception as rag_err:  # noqa: BLE001
        logger.warning("reportero: RAG lookup failed (non-fatal): %s", rag_err)
        return []


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_date_param(value: Optional[str], end_of_day: bool = False) -> Optional[datetime]:
    """Convert an ISO date string to UTC datetime.

    start_date → midnight 00:00:00 UTC (default).
    end_date   → end of day 23:59:59.999999 UTC (pass end_of_day=True) so that
                 inclusive upper-bound filters (fecha <= end_date) include all
                 transactions that occurred on that calendar date.
    """
    if not value:
        return None
    try:
        dt = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if end_of_day:
            return dt.replace(hour=23, minute=59, second=59, microsecond=999999)
        return dt
    except ValueError:
        logger.warning("reportero: invalid date param '%s' — ignoring", value)
        return None


def _ledger_by_prefix(ledger: list[dict], prefix: str) -> list[dict]:
    """Filter general ledger rows whose account code starts with *prefix*."""
    return [row for row in ledger if row["account"].startswith(prefix)]


def _ledger_by_exact(ledger: list[dict], code: str) -> Optional[dict]:
    """Return the single ledger row for *code*, or None if absent."""
    for row in ledger:
        if row["account"] == code:
            return row
    return None


def _credit_nature_balance(row: dict) -> Decimal:
    """Net balance for credit-nature accounts: credits - debits."""
    return Decimal(str(row["total_credit"])) - Decimal(str(row["total_debit"]))


def _debit_nature_balance(row: dict) -> Decimal:
    """Net balance for debit-nature accounts: debits - credits."""
    return Decimal(str(row["total_debit"])) - Decimal(str(row["total_credit"]))


# ---------------------------------------------------------------------------
# Report builders
# ---------------------------------------------------------------------------

def _build_balance(db, params: dict, svc) -> dict:
    end_date = _parse_date_param(params.get("end_date"), end_of_day=True)
    company_nit = params.get("company_nit")
    data = svc.get_balance_sheet(db, cutoff_date=end_date, company_nit=company_nit)

    activos = Decimal(str(data["assets"]))
    pasivos = Decimal(str(data["liabilities"]))
    patrimonio = Decimal(str(data["equity"]))
    utilidad_neta = Decimal(str(data["net_profit"]))
    patrimonio_total = Decimal(str(data["total_equity"]))
    cuadre = bool(data["is_balanced"])

    if cuadre:
        mensaje = (
            f"ACTIVOS ({activos:,.0f}) == "
            f"PASIVOS ({pasivos:,.0f}) + PATRIMONIO TOTAL ({patrimonio_total:,.0f}) ✓"
        )
    else:
        diferencia = activos - (pasivos + patrimonio_total)
        mensaje = (
            f"DESCUADRE: ACTIVOS ({activos:,.0f}) - "
            f"(PASIVOS + PATRIMONIO TOTAL) = {diferencia:,.0f}"
        )

    # RAG enrichment — NIIF/PCGA notes (non-fatal; empty list if RAG unavailable)
    notas_normativas = _fetch_rag_referencias(
        "NIIF balance general activos pasivos patrimonio principios contabilidad PCGA",
        n_results=2,
    )

    return {
        "report_type": "balance_sheet",
        "period_start": params.get("start_date"),
        "period_end": params.get("end_date") or _today_iso(),
        "company_nit": company_nit,
        "generated_at": _now_iso(),
        "activos": float(activos),
        "pasivos": float(pasivos),
        "patrimonio": float(patrimonio),
        "utilidad_neta": float(utilidad_neta),
        "patrimonio_total": float(patrimonio_total),
        "cuadre": cuadre,
        "mensaje_cuadre": mensaje,
        "notas_normativas": notas_normativas,
    }


def _build_pnl(db, params: dict, svc) -> dict:
    start_date = _parse_date_param(params.get("start_date"))
    end_date = _parse_date_param(params.get("end_date"), end_of_day=True)
    company_nit = params.get("company_nit")
    ledger = svc.get_general_ledger(
        db, start_date=start_date, end_date=end_date, company_nit=company_nit
    )

    ingresos_rows = _ledger_by_prefix(ledger, _CLASS_INGRESOS)
    gastos_rows = _ledger_by_prefix(ledger, _CLASS_GASTOS)
    costo_rows = _ledger_by_prefix(ledger, _CLASS_COSTO_VENTAS)

    def to_cuenta(row: dict, balance: Decimal) -> dict:
        return {"codigo": row["account"], "nombre": row["name"], "saldo": float(balance)}

    ingresos = [to_cuenta(r, _credit_nature_balance(r)) for r in ingresos_rows]
    gastos = [to_cuenta(r, _debit_nature_balance(r)) for r in gastos_rows]
    costo_ventas = [to_cuenta(r, _debit_nature_balance(r)) for r in costo_rows]

    total_ingresos = sum(Decimal(str(c["saldo"])) for c in ingresos)
    total_gastos = sum(Decimal(str(c["saldo"])) for c in gastos)
    total_costo = sum(Decimal(str(c["saldo"])) for c in costo_ventas)
    utilidad_bruta = total_ingresos - total_costo
    utilidad_neta = utilidad_bruta - total_gastos

    # RAG enrichment — NIIF/PCGA income statement notes (non-fatal)
    notas_normativas = _fetch_rag_referencias(
        "estado resultados ingresos gastos costo ventas principio realización NIIF PCGA",
        n_results=2,
    )

    return {
        "report_type": "profit_and_loss",
        "period_start": params.get("start_date"),
        "period_end": params.get("end_date") or _today_iso(),
        "company_nit": company_nit,
        "generated_at": _now_iso(),
        "ingresos": ingresos,
        "costo_ventas": costo_ventas,
        "gastos": gastos,
        "total_ingresos": float(total_ingresos),
        "total_costo_ventas": float(total_costo),
        "total_gastos": float(total_gastos),
        "utilidad_bruta": float(utilidad_bruta),
        "utilidad_neta": float(utilidad_neta),
        "notas_normativas": notas_normativas,
    }


def _build_cashflow(db, params: dict, svc) -> dict:
    start_date = _parse_date_param(params.get("start_date"))
    end_date = _parse_date_param(params.get("end_date"), end_of_day=True)
    company_nit = params.get("company_nit")
    ledger = svc.get_general_ledger(
        db, start_date=start_date, end_date=end_date, company_nit=company_nit
    )

    efectivo_rows = _ledger_by_prefix(ledger, _PREFIX_EFECTIVO)
    cuentas_efectivo = [
        {
            "codigo": r["account"],
            "nombre": r["name"],
            "saldo": float(_debit_nature_balance(r)),
        }
        for r in efectivo_rows
    ]
    total_efectivo = sum(Decimal(str(c["saldo"])) for c in cuentas_efectivo)

    # RAG enrichment — NIIF/NIC 7 cash flow notes (non-fatal)
    notas_normativas = _fetch_rag_referencias(
        "flujo caja efectivo bancos método directo NIIF NIC 7 PCGA",
        n_results=2,
    )

    return {
        "report_type": "cash_flow",
        "period_start": params.get("start_date"),
        "period_end": params.get("end_date") or _today_iso(),
        "company_nit": company_nit,
        "generated_at": _now_iso(),
        "cuentas_efectivo": cuentas_efectivo,
        "total_efectivo": float(total_efectivo),
        "nota": (
            "Flujo de caja directo — saldo neto de cuentas de efectivo y "
            "bancos (clase 11)."
        ),
        "notas_normativas": notas_normativas,
    }


def _build_iva(db, params: dict, svc) -> dict:
    start_date = _parse_date_param(params.get("start_date"))
    end_date = _parse_date_param(params.get("end_date"), end_of_day=True)
    company_nit = params.get("company_nit")
    ledger = svc.get_general_ledger(
        db, start_date=start_date, end_date=end_date, company_nit=company_nit
    )

    generado_row = _ledger_by_exact(ledger, _CUENTA_IVA_GENERADO)
    descontable_row = _ledger_by_exact(ledger, _CUENTA_IVA_DESCONTABLE)

    # 240808 is credit-nature (IVA generado = liability)
    iva_generado = _credit_nature_balance(generado_row) if generado_row else Decimal("0")
    # 240802 is debit-nature (IVA descontable = asset)
    iva_descontable = _debit_nature_balance(descontable_row) if descontable_row else Decimal("0")
    iva_a_pagar = iva_generado - iva_descontable

    # RAG enrichment — IVA legal references from normativa (non-fatal)
    rag_refs = _fetch_rag_referencias(
        "IVA impuesto ventas tarifa general artículo 468 477 Estatuto Tributario"
    )
    referencias = rag_refs if rag_refs else ["Art. 477 ET", "Art. 24 ET"]

    return {
        "report_type": "iva_report",
        "period_start": params.get("start_date"),
        "period_end": params.get("end_date") or _today_iso(),
        "company_nit": company_nit,
        "generated_at": _now_iso(),
        "iva_generado": float(iva_generado),
        "iva_descontable": float(iva_descontable),
        "iva_a_pagar": float(iva_a_pagar),
        "referencias": referencias,
    }


def _build_withholdings(db, params: dict, svc) -> dict:
    start_date = _parse_date_param(params.get("start_date"))
    end_date = _parse_date_param(params.get("end_date"), end_of_day=True)
    company_nit = params.get("company_nit")
    ledger = svc.get_general_ledger(
        db, start_date=start_date, end_date=end_date, company_nit=company_nit
    )

    retefuente_row = _ledger_by_exact(ledger, _CUENTA_RETEFUENTE)
    reteica_row = _ledger_by_exact(ledger, _CUENTA_RETEICA)

    # Both are credit-nature liability accounts
    retefuente = _credit_nature_balance(retefuente_row) if retefuente_row else Decimal("0")
    reteica = _credit_nature_balance(reteica_row) if reteica_row else Decimal("0")
    total = retefuente + reteica

    # RAG enrichment — retenciones legal references from normativa (non-fatal)
    rag_refs = _fetch_rag_referencias(
        "retención en la fuente servicios honorarios artículo 383 392 Decreto 2048 Estatuto Tributario"
    )
    referencias = rag_refs if rag_refs else ["Art. 383 ET", "Decreto 2048/1992"]

    return {
        "report_type": "withholdings_report",
        "period_start": params.get("start_date"),
        "period_end": params.get("end_date") or _today_iso(),
        "company_nit": company_nit,
        "generated_at": _now_iso(),
        "retencion_en_la_fuente": float(retefuente),
        "retencion_ica": float(reteica),
        "total_retenciones": float(total),
        "referencias": referencias,
    }


_BUILDERS = {
    "balance": _build_balance,
    "pnl": _build_pnl,
    "cashflow": _build_cashflow,
    "iva": _build_iva,
    "withholdings": _build_withholdings,
}


# ---------------------------------------------------------------------------
# Node entry point
# ---------------------------------------------------------------------------

def reportero_node(state: AgentState) -> AgentState:
    """
    Reportero node: queries Libro Mayor and formats financial reports.

    Reads:
        state["report_type"]   – one of "balance" | "pnl" | "cashflow" | "iva" | "withholdings"
        state["report_params"] – filter dict: {"start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD"}
    Writes:
        state["result"]["report"] – structured report data dict
        state["current_stage"]    – "reporting_complete"
        state["current_agent"]    – "reportero"

    No LLM calls — pure SQL aggregation via db_service.
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
    state["current_agent"] = "reportero"
    state["current_stage"] = "reportero"

    append_log(state, "reportero", "node_start", {
        "report_type": report_type,
        "params": params,
    })

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

    append_log(state, "reportero", "node_complete", {
        "report_type": report_type,
        "generated_at": report_data.get("generated_at"),
    })
    logger.info("reportero: '%s' report generated successfully", report_type)
    return state
