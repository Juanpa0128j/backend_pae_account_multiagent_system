"""Financial analysis report builder."""

import logging
import statistics
from datetime import timedelta
from typing import Any, Dict

from ._base import (
    CLASS_COSTO_VENTAS,
    CLASS_GASTOS,
    CLASS_INGRESOS,
    PREFIX_ACTIVOS_CORRIENTES,
    PREFIX_INVENTARIOS,
    PREFIX_PASIVOS_CORRIENTES,
    _credit_nature_balance,
    _debit_nature_balance,
    _fetch_rag_context_text,
    _fetch_rag_referencias,
    _ledger_by_prefix,
    _ledger_by_prefixes,
    _now_iso,
    _parse_date_param,
    _safe_divide,
    _today_iso,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_ANALISIS = """Eres un Director Financiero y Analista Contable Senior experto en contabilidad colombiana.

## TU ROL
Analizas datos financieros de empresas colombianas y generas informes ejecutivos con explicaciones claras,
interpretaciones profundas, predicciones fundamentadas y recomendaciones accionables.

## MARCO NORMATIVO QUE CONOCES
- **NIIF** (Normas Internacionales de Información Financiera) adoptadas en Colombia
- **PUC** (Plan Único de Cuentas - Decreto 2650 de 1993): estructura de 6 clases
- **Estatuto Tributario** colombiano (IVA Art. 468, Retefuente Art. 383/392/401, ReteICA municipal)
- **Ley 43 de 1990**: régimen de la profesión contable
- **PCGA**: Principios de Contabilidad Generalmente Aceptados

## INSTRUCCIONES DE OUTPUT
1. **resumen_ejecutivo**: Visión general de la salud financiera.
2. **explicaciones**: Para CADA métrica importante, explica el PORQUÉ del valor.
3. **interpretacion_ratios**: Para cada ratio, explica qué indica sobre la empresa.
4. **tendencias**: Describe cómo evolucionaron ingresos, gastos y utilidad.
5. **predicciones**: Proyecta 3 meses futuros con ingresos, gastos, utilidad y flujo de caja estimados.
6. **predicciones_narrativa**: Explica en lenguaje natural hacia dónde va la empresa.
7. **alertas**: Señales de alerta temprana.
8. **recomendaciones**: 3-5 acciones concretas.
9. **nivel_salud_financiera**: "bueno", "aceptable", "preocupante" o "critico".

## REGLAS
- Todas las respuestas en ESPAÑOL
- Usa cifras concretas, no generalidades vagas
- Nunca inventes datos
"""


def _compute_ratios(ledger: list[dict], balance: dict) -> dict:
    activos_corrientes = sum(
        float(_debit_nature_balance(r))
        for r in _ledger_by_prefixes(ledger, PREFIX_ACTIVOS_CORRIENTES)
    )
    inventarios = sum(
        float(_debit_nature_balance(r))
        for r in _ledger_by_prefix(ledger, PREFIX_INVENTARIOS)
    )
    pasivos_corrientes = sum(
        float(_debit_nature_balance(r))
        for r in _ledger_by_prefixes(ledger, PREFIX_PASIVOS_CORRIENTES)
    )

    activos = balance["assets"]
    pasivos = balance["liabilities"]
    patrimonio = balance["equity"]
    ingresos = balance["revenue"]
    utilidad = balance["net_profit"]

    return {
        "razon_corriente": _safe_divide(activos_corrientes, pasivos_corrientes),
        "prueba_acida": _safe_divide(
            activos_corrientes - inventarios, pasivos_corrientes
        ),
        "margen_neto": (
            round(_safe_divide(utilidad, ingresos) * 100, 2)
            if ingresos and _safe_divide(utilidad, ingresos) is not None
            else None
        ),
        "roa": (
            round(_safe_divide(utilidad, activos) * 100, 2)
            if activos and _safe_divide(utilidad, activos) is not None
            else None
        ),
        "razon_endeudamiento": _safe_divide(pasivos, activos) if activos else None,
        "deuda_patrimonio": _safe_divide(pasivos, patrimonio) if patrimonio else None,
        "rotacion_activos": _safe_divide(ingresos, activos) if activos else None,
    }


def _linear_regression_predict(
    data_points: list[float],
    n_predict: int = 3,
    *,
    allow_negative: bool = False,
) -> list[float]:
    n = len(data_points)
    if n < 2:
        return []
    x = list(range(n))
    x_mean = sum(x) / n
    y_mean = sum(data_points) / n
    numerator = sum((x[i] - x_mean) * (data_points[i] - y_mean) for i in range(n))
    denominator = sum((x[i] - x_mean) ** 2 for i in range(n))
    if denominator == 0:
        return [y_mean] * n_predict
    slope = numerator / denominator
    intercept = y_mean - slope * x_mean
    raw = [round(slope * (n + i) + intercept, 2) for i in range(n_predict)]
    if allow_negative:
        return raw
    return [max(0, v) for v in raw]


def _compute_predictions(monthly_data: dict) -> list[dict]:
    ingresos_trend = monthly_data.get("ingresos", [])
    gastos_trend = monthly_data.get("gastos", [])
    caja_trend = monthly_data.get("caja", [])

    ingresos_vals = [abs(m.get("net", 0)) for m in ingresos_trend]
    gastos_vals = [abs(m.get("net", 0)) for m in gastos_trend]
    caja_vals = [m.get("net", 0) for m in caja_trend]

    pred_ingresos = _linear_regression_predict(ingresos_vals, 3)
    pred_gastos = _linear_regression_predict(gastos_vals, 3)
    pred_caja = (
        _linear_regression_predict(caja_vals, 3, allow_negative=True)
        if caja_vals
        else []
    )

    if not pred_ingresos and not pred_gastos:
        return []

    last_month = None
    for data in [ingresos_trend, gastos_trend, caja_trend]:
        if data:
            last_month = data[-1].get("month")
            break

    if not last_month:
        return []

    year, month = int(last_month.split("-")[0]), int(last_month.split("-")[1])
    predictions = []
    for i in range(3):
        month += 1
        if month > 12:
            month = 1
            year += 1
        ing = pred_ingresos[i] if i < len(pred_ingresos) else 0
        gas = pred_gastos[i] if i < len(pred_gastos) else 0
        utilidad = round(ing - gas, 2)
        flujo = pred_caja[i] if i < len(pred_caja) else utilidad
        predictions.append(
            {
                "periodo": f"{year}-{month:02d}",
                "ingresos_estimados": ing,
                "gastos_estimados": gas,
                "utilidad_estimada": utilidad,
                "flujo_caja_estimado": round(flujo, 2),
            }
        )

    return predictions


def _detect_anomalies(
    ledger_current: list[dict],
    ledger_previous: list[dict],
    threshold_std: float = 2.0,
) -> list[dict]:
    prev_map = {r["account"]: r["net_balance"] for r in ledger_previous}
    changes = []
    for row in ledger_current:
        prev_val = prev_map.get(row["account"], 0.0)
        change = row["net_balance"] - prev_val
        changes.append(
            {"account": row["account"], "name": row["name"], "change": change}
        )

    if len(changes) < 3:
        return []

    change_vals = [c["change"] for c in changes]
    mean_change = statistics.mean(change_vals)
    std_change = statistics.stdev(change_vals) if len(change_vals) > 1 else 0

    if std_change == 0:
        return []

    anomalies = []
    for c in changes:
        if abs(c["change"] - mean_change) > threshold_std * std_change:
            anomalies.append(
                {
                    "cuenta": c["account"],
                    "nombre": c["name"],
                    "cambio": round(c["change"], 2),
                    "desviaciones": round(
                        abs(c["change"] - mean_change) / std_change, 2
                    ),
                }
            )

    return anomalies


def build_analysis(db, params: dict, svc) -> dict:
    """Build comprehensive financial analysis with ratios, predictions, and LLM narrative."""
    start_date = _parse_date_param(params.get("start_date"))
    end_date = _parse_date_param(params.get("end_date"), end_of_day=True)
    company_nit = params.get("company_nit")

    if start_date is not None:
        balance = svc.get_balance_sheet_for_period(
            db, start_date=start_date, end_date=end_date, company_nit=company_nit
        )
    else:
        balance = svc.get_balance_sheet(
            db, cutoff_date=end_date, company_nit=company_nit
        )

    ledger = svc.get_general_ledger(
        db, start_date=start_date, end_date=end_date, company_nit=company_nit
    )

    ingresos_rows = _ledger_by_prefix(ledger, CLASS_INGRESOS)
    gastos_rows = _ledger_by_prefix(ledger, CLASS_GASTOS)
    costo_rows = _ledger_by_prefix(ledger, CLASS_COSTO_VENTAS)
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

    ratios = _compute_ratios(ledger, balance)

    top_debit = svc.get_top_accounts(
        db, start_date, end_date, by="debit", limit=5, company_nit=company_nit
    )
    top_credit = svc.get_top_accounts(
        db, start_date, end_date, by="credit", limit=5, company_nit=company_nit
    )

    top_terceros = svc.get_top_terceros(
        db, start_date, end_date, limit=5, company_nit=company_nit
    )

    monthly_data = svc.get_monthly_totals_by_class(
        db, months=6, company_nit=company_nit
    )

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

    predicciones_num = _compute_predictions(monthly_data)

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
        logger.info("reportero: LLM analysis generated successfully")
    except Exception as llm_err:  # noqa: BLE001
        logger.warning("reportero: LLM analysis failed (non-fatal): %s", llm_err)
        report_data["analysis"] = {"error": f"Análisis LLM no disponible: {llm_err}"}

    return report_data
