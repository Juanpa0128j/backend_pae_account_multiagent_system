"""Financial analysis report builder."""

from datetime import timedelta
from typing import Any, Dict

from app.services.report_builders._base import (
    _CLASS_COSTO_VENTAS,
    _CLASS_GASTOS,
    _CLASS_INGRESOS,
    _SYSTEM_PROMPT_ANALISIS,
    _compute_predictions,
    _compute_ratios,
    _credit_nature_balance,
    _debit_nature_balance,
    _detect_anomalies,
    _fetch_rag_context_text,
    _fetch_rag_referencias,
    _ledger_by_prefix,
    _now_iso,
    _parse_date_param,
    _today_iso,
)


def build_analysis(db, params: dict, svc) -> dict:
    """Build comprehensive financial analysis with ratios, predictions, and LLM narrative."""
    start_date = _parse_date_param(params.get("start_date"))
    end_date = _parse_date_param(params.get("end_date"), end_of_day=True)
    company_nit = params.get("company_nit")

    # --- Phase 1: Deterministic calculations ---

    # Balance sheet — period-scoped when start_date is provided,
    # cumulative (up to end_date) otherwise
    if start_date is not None:
        balance = svc.get_balance_sheet_for_period(
            db, start_date=start_date, end_date=end_date, company_nit=company_nit
        )
    else:
        balance = svc.get_balance_sheet(
            db, cutoff_date=end_date, company_nit=company_nit
        )

    # General ledger for current period
    ledger = svc.get_general_ledger(
        db, start_date=start_date, end_date=end_date, company_nit=company_nit
    )

    # P&L summary (period-scoped, computed from ledger for consistency)
    ingresos_rows = _ledger_by_prefix(ledger, _CLASS_INGRESOS)
    gastos_rows = _ledger_by_prefix(ledger, _CLASS_GASTOS)
    costo_rows = _ledger_by_prefix(ledger, _CLASS_COSTO_VENTAS)
    total_ingresos = sum(float(_credit_nature_balance(r)) for r in ingresos_rows)
    total_gastos = sum(float(_debit_nature_balance(r)) for r in gastos_rows)
    total_costo_ventas = sum(float(_debit_nature_balance(r)) for r in costo_rows)
    utilidad_neta_periodo = total_ingresos - total_costo_ventas - total_gastos

    pnl_summary = {
        "total_ingresos": total_ingresos,
        "total_costo_ventas": total_costo_ventas,
        "total_gastos": total_gastos,
        "utilidad_neta": utilidad_neta_periodo,
    }

    # Financial ratios
    ratios = _compute_ratios(ledger, balance)

    # Top accounts
    top_debit = svc.get_top_accounts(
        db, start_date, end_date, by="debit", limit=5, company_nit=company_nit
    )
    top_credit = svc.get_top_accounts(
        db, start_date, end_date, by="credit", limit=5, company_nit=company_nit
    )

    # Top terceros
    top_terceros = svc.get_top_terceros(
        db, start_date, end_date, limit=5, company_nit=company_nit
    )

    # Monthly trends (last 6 months)
    monthly_data = svc.get_monthly_totals_by_class(
        db, months=6, company_nit=company_nit
    )

    # Anomaly detection — compare current period ledger with previous period
    # Calculate a previous period of same length
    prev_ledger: list[dict] = []
    if start_date and end_date:
        delta = end_date - start_date
        prev_start = start_date - delta
        prev_end = start_date - timedelta(microseconds=1)
        prev_ledger = svc.get_general_ledger(
            db,
            start_date=prev_start,
            end_date=prev_end,
            company_nit=company_nit,
        )

    anomalies = _detect_anomalies(ledger, prev_ledger) if prev_ledger else []

    # Predictions (linear regression)
    predicciones_num = _compute_predictions(monthly_data)

    # RAG normative context
    notas_normativas = _fetch_rag_referencias(
        "análisis financiero NIIF indicadores liquidez rentabilidad endeudamiento Colombia PCGA",
        n_results=3,
    )

    report_data: Dict[str, Any] = {
        "report_type": "financial_analysis",
        "period_start": params.get("start_date"),
        "period_end": params.get("end_date") or _today_iso(),
        "generated_at": _now_iso(),
        "balance_summary": balance,
        "pnl_summary": pnl_summary,
        "ratios": ratios,
        "top_accounts_debit": [
            {"codigo": a["codigo"], "nombre": a["nombre"], "saldo": a["total_debito"]}
            for a in top_debit
        ],
        "top_accounts_credit": [
            {"codigo": a["codigo"], "nombre": a["nombre"], "saldo": a["total_credito"]}
            for a in top_credit
        ],
        "top_terceros": top_terceros,
        "anomalies": anomalies,
        "monthly_trends": monthly_data,
        "predicciones_numericas": predicciones_num,
        "notas_normativas": notas_normativas,
    }

    # --- Phase 2: LLM Analysis (non-fatal) ---
    try:
        from app.core.llm_client import get_llm_client  # noqa: PLC0415

        llm = get_llm_client()

        rag_text = _fetch_rag_context_text(
            "análisis financiero indicadores NIIF Colombia ratios liquidez rentabilidad"
        )

        llm_input = {
            "balance_summary": balance,
            "pnl_summary": pnl_summary,
            "ratios": ratios,
            "monthly_trends": monthly_data,
            "predicciones_numericas": predicciones_num,
            "top_accounts_debit": top_debit,
            "top_accounts_credit": top_credit,
            "top_terceros": top_terceros,
            "anomalies": anomalies,
        }

        analysis = llm.generate_financial_analysis(
            financial_data=llm_input,
            rag_context=rag_text,
            system_prompt=_SYSTEM_PROMPT_ANALISIS,
        )
        report_data["analysis"] = analysis
    except Exception:
        report_data["analysis"] = {"error": "Análisis LLM no disponible"}

    return report_data
