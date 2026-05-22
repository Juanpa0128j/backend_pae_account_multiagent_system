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
  - "iva"          → Reporte IVA (accounts 240808 / 240802)
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
import statistics
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from app.agents.agent_utils import append_log
from app.agents.state import AgentState

# db_service and SessionLocal are imported lazily inside reportero_node to
# avoid loading psycopg2 / SQLAlchemy engine at module import time.

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PUC account prefixes / codes used for report aggregation
# ---------------------------------------------------------------------------
_CLASS_ACTIVOS = "1"
_CLASS_PASIVOS = "2"
_CLASS_PATRIMONIO = "3"
_CLASS_INGRESOS = "4"
_CLASS_GASTOS = "5"
_CLASS_COSTO_VENTAS = "6"
_PREFIX_EFECTIVO = "11"  # Bancos, Caja, equivalentes de efectivo
_PREFIX_ACTIVOS_CORRIENTES = ("11", "12", "13")  # Efectivo, Inversiones, Deudores
_PREFIX_PASIVOS_CORRIENTES = (
    "21",
    "22",
    "23",
)  # Obligaciones, Proveedores, Cuentas por pagar
_PREFIX_INVENTARIOS = "14"  # Inventarios (excluded from acid test)

# Specific tax retention accounts — corrected per Carolina García, Contadora Pública.
# Decreto 2650/1993 oficial:
#   2408   = Impuesto sobre las Ventas por Pagar (parent class)
#   240802 = IVA Descontable (subaccount, naturaleza débito = activo recuperable)
#   240805 = IVA Generado (subaccount, naturaleza crédito = pasivo)
#   240810 = IVA Retenido (anticipo a favor)
# El builder usa _PREFIX_IVA para capturar TODA subcuenta 2408* — el contador
# a veces persiste con el código padre "2408" y a veces con subcuenta explícita.
_PREFIX_IVA = "2408"
_CUENTA_IVA_GENERADO = "240805"
_CUENTA_IVA_DESCONTABLE = "240802"
_CUENTA_RETEFUENTE = "2365"  # Retención en la Fuente por pagar (pasivo)
_CUENTA_RETEICA = "2368"  # Retención ICA por pagar (pasivo)

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
# System Prompt for LLM Financial Analysis
# ---------------------------------------------------------------------------
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

## ESTRUCTURA PUC QUE MANEJAS
- **Clase 1 - Activos** (naturaleza débito): Efectivo (11), Inversiones (12), Deudores (13), Inventarios (14), Propiedad (15)
- **Clase 2 - Pasivos** (naturaleza crédito): Obligaciones financieras (21), Proveedores (22), Cuentas por pagar (23), Impuestos (24)
- **Clase 3 - Patrimonio** (naturaleza crédito): Capital (31), Reservas (32), Resultados (36)
- **Clase 4 - Ingresos** (naturaleza crédito): Operacionales (41), No operacionales (42)
- **Clase 5 - Gastos** (naturaleza débito): Operacionales (51-52), No operacionales (53)
- **Clase 6 - Costo de Ventas** (naturaleza débito): Costo mercancía (61), Costo servicios (62)

## CUENTAS FISCALES ESPECÍFICAS
- 240808: IVA Generado (pasivo, crédito) — IVA cobrado en ventas
- 240802: IVA Descontable (activo, débito) — IVA pagado en compras
- 2365: Retención en la Fuente por pagar (pasivo, crédito)
- 2368: Retención ICA por pagar (pasivo, crédito)

## TABLAS DE LA BASE DE DATOS (contexto de los datos que recibes)
- **journal_entry_lines**: fecha, comprobante, cuenta_puc, cuenta_nombre, tercero_nit, descripcion, debito, credito
- **transactions_posted**: cuenta_puc, retefuente, reteica, iva, neto_a_pagar, tax_references, agent_reasoning
- **transactions_pending**: fecha, nit_emisor, nit_receptor, total, descripcion, items, status
- **company_settings**: nit, nombre, ciudad, codigo_ciiu, tasas de impuestos configuradas
- **terceros**: nit, razon_social, tipo (proveedor/cliente), actividad_economica
- **cuentas_puc**: codigo, nombre, clase, naturaleza (debito/credito)

## RATIOS FINANCIEROS QUE CALCULAS E INTERPRETAS
- **Razón Corriente** = Activos Corrientes (11+12+13) / Pasivos Corrientes (21+22+23). Ideal > 1.5
- **Prueba Ácida** = (Activos Corrientes - Inventarios 14) / Pasivos Corrientes. Ideal > 1.0
- **Margen Neto** = Utilidad Neta / Ingresos Totales × 100. Varía por sector
- **ROA** = Utilidad Neta / Activos Totales × 100. Mide eficiencia de activos
- **Razón de Endeudamiento** = Pasivos / Activos. Alerta si > 0.7
- **Deuda/Patrimonio** = Pasivos / Patrimonio. Alerta si > 2.0
- **Rotación de Activos** = Ingresos / Activos. Mayor = más eficiente

## INSTRUCCIONES DE OUTPUT
1. **resumen_ejecutivo**: Visión general de la salud financiera. Menciona las cifras más relevantes.
2. **explicaciones**: Para CADA métrica importante, explica el PORQUÉ del valor:
   - ¿Qué cuentas o terceros contribuyen más?
   - ¿Qué significa esto para la operación del negocio?
   - ¿Es positivo, neutral o negativo? ¿Por qué?
3. **interpretacion_ratios**: Para cada ratio, explica qué indica sobre la empresa en términos simples.
4. **tendencias**: Describe cómo evolucionaron ingresos, gastos y utilidad mes a mes.
5. **predicciones**: Basándote en la tendencia mensual y las predicciones numéricas (regresión lineal)
   que recibes, proyecta 3 meses futuros con:
   - Ingresos, gastos, utilidad y flujo de caja estimados por mes
   - El flujo de caja se proyecta a partir de movimientos históricos de caja (clase 11)
   - Nivel de confianza (alta si hay 4+ meses de datos consistentes, media si hay 2-3, baja si hay menos)
6. **predicciones_narrativa**: Explica en lenguaje natural hacia dónde va la empresa,
   cuándo podría haber problemas, y qué inflexiones se observan en los datos.
7. **alertas**: Señales de alerta temprana (liquidez baja, endeudamiento excesivo, caída de ingresos, etc.)
8. **recomendaciones**: 3-5 acciones concretas que la empresa debería tomar.
9. **nivel_salud_financiera**: "bueno", "aceptable", "preocupante" o "critico" basado en el análisis global.

## REGLAS
- Todas las respuestas en ESPAÑOL
- Usa cifras concretas, no generalidades vagas
- Cita artículos normativos cuando sea relevante (ej: "según Art. 383 del ET")
- Si los datos son insuficientes para una predicción confiable, dilo explícitamente
- Nunca inventes datos — si un valor es 0 o null, explica que no hay suficiente información
"""


# ---------------------------------------------------------------------------
# RAG enrichment helper (non-fatal)
# ---------------------------------------------------------------------------


def _fetch_rag_referencias(query: str, n_results: int = 3) -> list[str]:
    """Query the normativa RAG collection and return human-readable citation strings."""
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


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_date_param(
    value: Optional[str], end_of_day: bool = False
) -> Optional[datetime]:
    """Convert an ISO date string to UTC datetime."""
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


def _ledger_by_prefixes(ledger: list[dict], prefixes: tuple) -> list[dict]:
    """Filter ledger rows starting with any of the given prefixes."""
    return [
        row for row in ledger if any(row["account"].startswith(p) for p in prefixes)
    ]


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


def _safe_divide(numerator: float, denominator: float) -> Optional[float]:
    """Return numerator / denominator, or None if denominator is zero."""
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


# ---------------------------------------------------------------------------
# Financial ratio calculations (deterministic)
# ---------------------------------------------------------------------------


def _compute_ratios(ledger: list[dict], balance: dict) -> dict:
    """Compute key financial ratios from ledger data and balance summary."""
    # Current assets: classes 11, 12, 13
    activos_corrientes = sum(
        float(_debit_nature_balance(r))
        for r in _ledger_by_prefixes(ledger, _PREFIX_ACTIVOS_CORRIENTES)
    )
    # Inventories: class 14
    inventarios = sum(
        float(_debit_nature_balance(r))
        for r in _ledger_by_prefix(ledger, _PREFIX_INVENTARIOS)
    )
    # Current liabilities: classes 21, 22, 23
    pasivos_corrientes = sum(
        float(_credit_nature_balance(r))
        for r in _ledger_by_prefixes(ledger, _PREFIX_PASIVOS_CORRIENTES)
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


# ---------------------------------------------------------------------------
# Simple linear regression for predictions
# ---------------------------------------------------------------------------


def _linear_regression_predict(
    data_points: list[float],
    n_predict: int = 3,
    *,
    allow_negative: bool = False,
) -> list[float]:
    """Simple linear regression on data_points, predict next n_predict values.

    Returns empty list if fewer than 2 data points.
    Set allow_negative=True for metrics that can go below zero (e.g. cash flow).
    """
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
    """Compute 3-month predictions from monthly trend data.

    monthly_data: dict with keys like 'ingresos', 'gastos', 'caja' containing
    lists of {month, net, ...} dicts.
    """
    ingresos_trend = monthly_data.get("ingresos", [])
    gastos_trend = monthly_data.get("gastos", [])
    caja_trend = monthly_data.get("caja", [])

    # Extract net values (for ingresos, credit nature: use total_credit - total_debit)
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

    # Determine next months from last data point
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
        # Use cash flow regression if available, otherwise derive from utilidad
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


# ---------------------------------------------------------------------------
# Anomaly detection
# ---------------------------------------------------------------------------


def _detect_anomalies(
    ledger_current: list[dict],
    ledger_previous: list[dict],
    threshold_std: float = 2.0,
) -> list[dict]:
    """Detect accounts whose balance changed more than threshold_std from mean change."""
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


# ---------------------------------------------------------------------------
# Report builders
# ---------------------------------------------------------------------------


def _build_balance(db, params: dict, svc) -> dict:
    end_date = _parse_date_param(params.get("end_date"), end_of_day=True)
    company_nit = params.get("company_nit")
    data = svc.get_balance_sheet(db, cutoff_date=end_date, company_nit=company_nit)
    # Balance sheet totals are cumulative up to `end_date`, so detalle must use
    # the same cutoff-based basis to remain reconcilable. Intentionally ignore
    # any provided `start_date` for this report.
    ledger = svc.get_general_ledger(
        db,
        start_date=None,
        end_date=end_date,
        company_nit=company_nit,
    )

    def _to_cuenta(row: dict, balance: Decimal) -> dict:
        return {
            "codigo": row["account"],
            "nombre": row["name"],
            "saldo": float(balance),
        }

    # Reclasificación contable: cualquier cuenta clase 2 (pasivos) con saldo
    # DEUDOR indica un anticipo / saldo a favor / IVA recuperable contra el
    # acreedor — debe presentarse como activo, no como pasivo negativo.
    # Ejemplos comunes: 2408* IVA descontable (saldo a favor DIAN), 237005
    # Aportes EPS pagados en exceso al fondo, 238030 anticipo pensión.
    # Riesgo: si la cuenta tiene saldo deudor por ERROR contable (mal
    # asiento), la reclasificación lo enmascara. Mitigamos con warning.
    activos_detalle: list[dict] = [
        _to_cuenta(r, _debit_nature_balance(r))
        for r in _ledger_by_prefix(ledger, _CLASS_ACTIVOS)
    ]
    pasivos_detalle: list[dict] = []
    for r in _ledger_by_prefix(ledger, _CLASS_PASIVOS):
        saldo_credito = _credit_nature_balance(r)  # credit - debit
        if saldo_credito < 0:
            logger.warning(
                "_build_balance: cuenta clase 2 con saldo deudor reclasificada "
                "a activos — PUC=%s saldo=%s. Verifique si es anticipo/saldo "
                "a favor (normal) o error contable.",
                r.get("account"),
                saldo_credito,
            )
            activos_detalle.append(_to_cuenta(r, abs(saldo_credito)))
        else:
            pasivos_detalle.append(_to_cuenta(r, saldo_credito))
    patrimonio_detalle = [
        _to_cuenta(r, _credit_nature_balance(r))
        for r in _ledger_by_prefix(ledger, _CLASS_PATRIMONIO)
    ]

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
        "activos_detalle": activos_detalle,
        "pasivos_detalle": pasivos_detalle,
        "patrimonio_detalle": patrimonio_detalle,
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
        return {
            "codigo": row["account"],
            "nombre": row["name"],
            "saldo": float(balance),
        }

    ingresos = [to_cuenta(r, _credit_nature_balance(r)) for r in ingresos_rows]
    gastos = [to_cuenta(r, _debit_nature_balance(r)) for r in gastos_rows]
    costo_ventas = [to_cuenta(r, _debit_nature_balance(r)) for r in costo_rows]

    total_ingresos = sum(Decimal(str(c["saldo"])) for c in ingresos)
    total_gastos = sum(Decimal(str(c["saldo"])) for c in gastos)
    total_costo = sum(Decimal(str(c["saldo"])) for c in costo_ventas)
    utilidad_bruta = total_ingresos - total_costo
    utilidad_neta = utilidad_bruta - total_gastos

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

    # Sumar TODAS las cuentas 2408* — el contador puede persistir con código
    # padre "2408" o subcuenta explícita (240802/240805/240810). Diferenciamos
    # generado vs descontable por subcuenta cuando es identificable, y por
    # naturaleza de saldo (D/C) cuando es el padre.
    iva_generado = Decimal("0")
    iva_descontable = Decimal("0")
    for row in ledger:
        code = str(row.get("account") or "").strip()
        if not code.startswith(_PREFIX_IVA):
            continue
        debit = Decimal(str(row.get("total_debit") or 0))
        credit = Decimal(str(row.get("total_credit") or 0))
        if code.startswith("240805"):
            iva_generado += credit  # crédito = pasivo a DIAN
        elif code.startswith("240802") or code.startswith("240810"):
            iva_descontable += debit  # débito = activo recuperable
        elif code == _PREFIX_IVA:
            # Cuenta padre — naturaleza por saldo neto.
            saldo_neto = debit - credit
            if saldo_neto > 0:
                iva_descontable += saldo_neto  # saldo deudor = a favor
            else:
                iva_generado += abs(saldo_neto)
    iva_a_pagar = iva_generado - iva_descontable
    iva_status = (
        "saldo_a_pagar"
        if iva_a_pagar > 0
        else "saldo_a_favor"
        if iva_a_pagar < 0
        else "saldo_cero"
    )

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
        "iva_status": iva_status,
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

    retefuente = (
        _credit_nature_balance(retefuente_row) if retefuente_row else Decimal("0")
    )
    reteica = _credit_nature_balance(reteica_row) if reteica_row else Decimal("0")
    total = retefuente + reteica

    retefuente_status = (
        "saldo_a_pagar"
        if retefuente > 0
        else "saldo_a_favor"
        if retefuente < 0
        else "saldo_cero"
    )
    reteica_status = (
        "saldo_a_pagar"
        if reteica > 0
        else "saldo_a_favor"
        if reteica < 0
        else "saldo_cero"
    )
    total_status = (
        "saldo_a_pagar" if total > 0 else "saldo_a_favor" if total < 0 else "saldo_cero"
    )

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
        "retencion_en_la_fuente_status": retefuente_status,
        "retencion_ica": float(reteica),
        "retencion_ica_status": reteica_status,
        "total_retenciones": float(total),
        "total_retenciones_status": total_status,
        "referencias": referencias,
    }


def _build_analysis(db, params: dict, svc) -> dict:
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
        logger.info("reportero: LLM analysis generated successfully")
    except Exception as llm_err:  # noqa: BLE001
        logger.warning("reportero: LLM analysis failed (non-fatal): %s", llm_err)
        report_data["analysis"] = {"error": f"Análisis LLM no disponible: {llm_err}"}

    return report_data


def _build_libro_diario(db, params: dict, svc) -> dict:
    """Libro Diario: chronological journal of all transactions."""
    start_date = _parse_date_param(params.get("start_date"))
    end_date = _parse_date_param(params.get("end_date"), end_of_day=True)
    company_nit = params.get("company_nit")

    # get_daily_journal: company_nit optional, returns ORM objects
    rows = svc.get_daily_journal(
        db, start_date=start_date, end_date=end_date, company_nit=company_nit
    )
    lines = [
        {
            "fecha": r.fecha.isoformat() if r.fecha else None,
            "comprobante": r.comprobante or "",
            "cuenta_puc": r.cuenta_puc or "",
            "cuenta_nombre": r.cuenta_nombre or "",
            "tercero_nit": r.tercero_nit or "",
            "descripcion": r.descripcion or "",
            "debito": float(r.debito or 0),
            "credito": float(r.credito or 0),
        }
        for r in rows
    ]

    notas_normativas = _fetch_rag_referencias(
        "Libro Diario registro transacciones PUC débito crédito comprobante",
        n_results=2,
    )

    return {
        "report_type": "libro_diario",
        "period_start": params.get("start_date"),
        "period_end": params.get("end_date") or _today_iso(),
        "company_nit": company_nit,
        "generated_at": _now_iso(),
        "transacciones": lines,
        "total_transacciones": len(lines),
        "notas_normativas": notas_normativas,
    }


def _build_libro_auxiliar(db, params: dict, svc) -> dict:
    """Libro Auxiliar: ledger per account (detail by account)."""
    start_date = _parse_date_param(params.get("start_date"))
    end_date = _parse_date_param(params.get("end_date"), end_of_day=True)
    company_nit = params.get("company_nit")

    # get_daily_journal: company_nit optional, returns ORM objects
    rows = svc.get_daily_journal(
        db, start_date=start_date, end_date=end_date, company_nit=company_nit
    )

    # Group by account code
    cuentas_detalle: dict = {}
    for r in rows:
        cuenta = r.cuenta_puc or "SIN_CUENTA"
        nombre = r.cuenta_nombre or r.descripcion or ""
        if cuenta not in cuentas_detalle:
            cuentas_detalle[cuenta] = {
                "cuenta": cuenta,
                "nombre": nombre,
                "movimientos": [],
                "total_debito": 0.0,
                "total_credito": 0.0,
                "saldo": 0.0,
            }
        elif not cuentas_detalle[cuenta]["nombre"] and nombre:
            cuentas_detalle[cuenta]["nombre"] = nombre

        debito = float(r.debito or 0)
        credito = float(r.credito or 0)
        cuentas_detalle[cuenta]["movimientos"].append(
            {
                "fecha": r.fecha.isoformat() if r.fecha else None,
                "comprobante": r.comprobante or "",
                "descripcion": r.descripcion or "",
                "debito": debito,
                "credito": credito,
            }
        )
        cuentas_detalle[cuenta]["total_debito"] += debito
        cuentas_detalle[cuenta]["total_credito"] += credito
        cuentas_detalle[cuenta]["saldo"] = (
            cuentas_detalle[cuenta]["total_debito"]
            - cuentas_detalle[cuenta]["total_credito"]
        )

    notas_normativas = _fetch_rag_referencias(
        "Libro Auxiliar saldo cuenta detalle transacciones por cuenta",
        n_results=2,
    )

    return {
        "report_type": "libro_auxiliar",
        "period_start": params.get("start_date"),
        "period_end": params.get("end_date") or _today_iso(),
        "company_nit": company_nit,
        "generated_at": _now_iso(),
        "cuentas": list(cuentas_detalle.values()),
        "total_cuentas": len(cuentas_detalle),
        "notas_normativas": notas_normativas,
    }


def _build_cambios_patrimonio(db, params: dict, svc) -> dict:
    """Cambios en Patrimonio: changes to equity accounts (class 3)."""
    start_date = _parse_date_param(params.get("start_date"))
    end_date = _parse_date_param(params.get("end_date"), end_of_day=True)
    company_nit = params.get("company_nit")

    ledger = svc.get_general_ledger(
        db, start_date=start_date, end_date=end_date, company_nit=company_nit
    )

    patrimonio_rows = _ledger_by_prefix(ledger, _CLASS_PATRIMONIO)

    cambios = [
        {
            "codigo": r["account"],
            "nombre": r["name"],
            "movimiento_debito": float(r["total_debit"]),
            "movimiento_credito": float(r["total_credit"]),
            "saldo_final": float(_credit_nature_balance(r)),
        }
        for r in patrimonio_rows
    ]

    notas_normativas = _fetch_rag_referencias(
        "Cambios en Patrimonio capital reservas resultados revaluacion",
        n_results=2,
    )

    return {
        "report_type": "cambios_patrimonio",
        "period_start": params.get("start_date"),
        "period_end": params.get("end_date") or _today_iso(),
        "company_nit": company_nit,
        "generated_at": _now_iso(),
        "cambios": cambios,
        "total_cambios": len(cambios),
        "notas_normativas": notas_normativas,
    }


def _build_notas_eeff(db, params: dict, svc) -> dict:
    """Notas a los Estados Financieros: explanatory notes."""
    end_date = _parse_date_param(params.get("end_date"), end_of_day=True)
    company_nit = params.get("company_nit")

    # Fetch balance to include summary data in notes
    balance_data = svc.get_balance_sheet(
        db, cutoff_date=end_date, company_nit=company_nit
    )

    notas_normativas = _fetch_rag_referencias(
        "Notas Estados Financieros NIIF PUC políticas contables estimaciones",
        n_results=5,
    )

    # Construct notes from regulatory references
    notas_contenido = [
        {
            "numero": i + 1,
            "titulo": f"Norma Contable {i + 1}",
            "contenido": nota,
        }
        for i, nota in enumerate(notas_normativas[:5])
    ]

    return {
        "report_type": "notas_eeff",
        "period_end": params.get("end_date") or _today_iso(),
        "company_nit": company_nit,
        "generated_at": _now_iso(),
        "notas": notas_contenido,
        "total_notas": len(notas_contenido),
        "resumen_financiero": {
            "activos": balance_data.get("assets", 0),
            "pasivos": balance_data.get("liabilities", 0),
            "patrimonio": balance_data.get("equity", 0),
        },
    }


_BUILDERS = {
    "balance": _build_balance,
    "pnl": _build_pnl,
    "cashflow": _build_cashflow,
    "iva": _build_iva,
    "withholdings": _build_withholdings,
    "analysis": _build_analysis,
    "libro_diario": _build_libro_diario,
    "libro_auxiliar": _build_libro_auxiliar,
    "cambios_patrimonio": _build_cambios_patrimonio,
    "notas_eeff": _build_notas_eeff,
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
