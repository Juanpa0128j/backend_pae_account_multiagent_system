"""Shared helpers and constants for report builders."""

import logging
import statistics
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

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

# Specific tax retention accounts — corrected per Carolina García, Contadora Pública
_CUENTA_IVA_GENERADO = "240808"
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
            round(ratio * 100, 2)
            if ingresos and (ratio := _safe_divide(utilidad, ingresos)) is not None
            else None
        ),
        "roa": (
            round(ratio * 100, 2)
            if activos and (ratio := _safe_divide(utilidad, activos)) is not None
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
