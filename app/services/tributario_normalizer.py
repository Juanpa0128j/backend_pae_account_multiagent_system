"""
Public service for normalizing TributarioOutput before strict schema validation.

Extracted from app.agents.supervisor._normalize_tributario_output.
"""

from datetime import date
from decimal import Decimal

from app.agents.state import AgentState


def normalize_tributario_output(state: AgentState, tributario_output: dict) -> dict:
    """Best-effort normalization for TributarioOutput before strict schema validation."""
    if not isinstance(tributario_output, dict):
        tributario_output = {}

    normalized = dict(tributario_output)

    impuestos = normalized.get("impuestos")
    if not isinstance(impuestos, list):
        impuestos = []

    calculated_total = Decimal("0")
    for imp in impuestos:
        if not isinstance(imp, dict):
            continue
        try:
            calculated_total += Decimal(str(imp.get("valor_impuesto", 0) or 0))
        except Exception:
            continue

    aplica_impuestos = bool(impuestos)

    total_impuestos = normalized.get("total_impuestos")
    if total_impuestos is None:
        total_impuestos = str(calculated_total)

    documento_referencia = str(normalized.get("documento_referencia") or "").strip()
    if not documento_referencia:
        contador_output = state.get("contador_output") or {}
        documento_referencia = str(
            contador_output.get("descripcion_general") or ""
        ).strip()
    if not documento_referencia:
        raw_txs = state.get("raw_transactions") or []
        if isinstance(raw_txs, list) and raw_txs:
            first_tx = raw_txs[0] if isinstance(raw_txs[0], dict) else {}
            documento_referencia = str(
                first_tx.get("referencia")
                or first_tx.get("descripcion")
                or "sin referencia"
            ).strip()

    referencias_legales = normalized.get("referencias_legales")
    if not isinstance(referencias_legales, list):
        referencias_legales = []

    asientos_enriquecidos = normalized.get("asientos_enriquecidos")
    if not isinstance(asientos_enriquecidos, list):
        contador_output = state.get("contador_output") or {}
        asientos_enriquecidos = (
            contador_output.get("asientos") if isinstance(contador_output, dict) else []
        )
    if not isinstance(asientos_enriquecidos, list):
        asientos_enriquecidos = []

    normalized["fecha_analisis"] = (
        normalized.get("fecha_analisis") or date.today().isoformat()
    )
    normalized["documento_referencia"] = documento_referencia or "sin referencia"
    normalized["impuestos"] = impuestos
    normalized["aplica_impuestos"] = aplica_impuestos
    normalized["total_impuestos"] = str(total_impuestos)
    normalized["observaciones"] = normalized.get("observaciones")
    normalized["referencias_legales"] = referencias_legales
    normalized["asientos_enriquecidos"] = asientos_enriquecidos

    return normalized
