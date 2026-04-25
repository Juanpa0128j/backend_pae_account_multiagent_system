"""
Spanish-language copy for audit findings shown to accountants.

Single source of truth for all user-facing messages. Keyed by rule_id.
Engineers maintain this file; accounting team reviews it each release.

Entry format:
    rule_id -> {
        "user_message_es": str — plain Spanish explanation (may use {var} placeholders),
        "suggested_action_es": str | None — what the accountant should do next,
    }

Placeholder substitution: call get_message(rule_id, evidence) which will
.format(**evidence) against both strings. Missing keys fall back to generic
phrases and emit a logger.warning so gaps are visible.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Generic per-agent summaries (used by trace service for each step)
# ---------------------------------------------------------------------------

AGENT_SUMMARIES_ES: dict[str, str] = {
    "supervisor": "El supervisor coordinó el flujo de la pipeline.",
    "ingesta": "El agente de ingesta procesó el documento y extrajo las transacciones.",
    "ingest": "El agente de ingesta procesó el documento y extrajo las transacciones.",
    "contador": "El agente contador clasificó los asientos contables en el PUC colombiano.",
    "tributario": "El agente tributario calculó los impuestos aplicables (IVA, retención, ICA).",
    "auditor": "El agente auditor verificó la consistencia contable y fiscal de los registros.",
    "persist": "Los registros fueron almacenados exitosamente en la base de datos.",
    "db_persist": "Los registros fueron almacenados exitosamente en la base de datos.",
    "reportero": "El agente reportero generó los estados financieros solicitados.",
}

AGENT_SUMMARIES_ES_FAILED: dict[str, str] = {
    "supervisor": "El supervisor encontró un problema que no pudo resolver automáticamente.",
    "ingesta": "El agente de ingesta no pudo extraer la información del documento correctamente.",
    "ingest": "El agente de ingesta no pudo extraer la información del documento correctamente.",
    "contador": "El agente contador no pudo clasificar los asientos contables correctamente.",
    "tributario": "El agente tributario encontró inconsistencias fiscales que no pudo corregir.",
    "auditor": "El agente auditor detectó problemas que requieren revisión manual.",
    "persist": "Ocurrió un error al guardar los registros en la base de datos.",
    "db_persist": "Ocurrió un error al guardar los registros en la base de datos.",
    "reportero": "El agente reportero no pudo generar el reporte solicitado.",
}

_GENERIC_MESSAGE = "Se detectó un problema en el procesamiento del documento."
_GENERIC_ACTION = "Revise el documento y vuelva a intentarlo. Si el problema persiste, contacte al soporte."

# ---------------------------------------------------------------------------
# Rule-specific messages
# ---------------------------------------------------------------------------

MESSAGES: dict[str, dict[str, Optional[str]]] = {
    # Phase 2 — silent failure surfacing
    "CONT-RAG-MISS": {
        "user_message_es": (
            "No se encontró la cuenta contable en el plan PUC; se usó la regla por defecto. "
            "El contexto normativo no estaba disponible en el momento del procesamiento."
        ),
        "suggested_action_es": (
            "Verifique que el plan de cuentas esté correctamente configurado y "
            "vuelva a procesar el documento."
        ),
    },
    "PERS-STATEMENT-DERIVATION-FAIL": {
        "user_message_es": (
            "No fue posible generar los estados financieros automáticamente "
            "porque los registros contables no están balanceados."
        ),
        "suggested_action_es": (
            "Revise que los débitos y créditos estén balanceados en todos los asientos, "
            "luego solicite la generación de estados financieros nuevamente."
        ),
    },
    "PERS-VIA-B-PARTIAL": {
        "user_message_es": (
            "La derivación automática de estados financieros (Vía B) fue omitida "
            "porque no se encontraron todos los documentos fuente requeridos."
        ),
        "suggested_action_es": (
            "Asegúrese de haber subido todos los documentos del período antes de "
            "solicitar la generación automática de estados financieros."
        ),
    },
    "ING-EXTRACTION-PARTIAL": {
        "user_message_es": (
            "La extracción del documento fue parcial: algunos campos no pudieron "
            "ser leídos correctamente."
        ),
        "suggested_action_es": (
            "Revise que el documento esté legible y no tenga páginas cortadas. "
            "Si es posible, suba una versión de mayor calidad."
        ),
    },
    "ING-EMPTY-EXTRACTION": {
        "user_message_es": (
            "No se pudo extraer información del documento. "
            "El archivo puede estar vacío, dañado o en un formato no soportado."
        ),
        "suggested_action_es": (
            "Verifique que el archivo sea un PDF o Excel legible y vuelva a subirlo. "
            "Formatos soportados: PDF, XLSX, XLS."
        ),
    },
    "ING-DUPLICATE-DETECTED": {
        "user_message_es": (
            "Se detectó un posible documento duplicado: "
            "ya existe un registro con la misma fecha, NIT y valor ({amount})."
        ),
        "suggested_action_es": (
            "Verifique que no esté subiendo el mismo documento dos veces. "
            "Si es un documento diferente, revise los datos y corrija."
        ),
    },
    # Phase 3 — tributario auditor
    "TRIB-RETENCION-MISMATCH": {
        "user_message_es": (
            "La tarifa de retención en la fuente declarada ({declared_rate}%) "
            "no coincide con la tabla vigente 2026 para el concepto {concept_name} "
            "(tarifa esperada: {expected_rate}%)."
        ),
        "suggested_action_es": (
            "Verifique el concepto de retención aplicado y corrija la tarifa "
            "según el Estatuto Tributario vigente."
        ),
    },
    "TRIB-IVA-RATE-INVALID": {
        "user_message_es": (
            "La tarifa de IVA declarada ({declared_rate}%) no es una tarifa válida. "
            "Las tarifas permitidas son: 0%, 5% y 19%."
        ),
        "suggested_action_es": "Corrija la tarifa de IVA en el documento y vuelva a procesar.",
    },
    "TRIB-ICA-MUNICIPALITY-UNKNOWN": {
        "user_message_es": (
            "El municipio '{municipality}' no fue encontrado en la tabla de tarifas ICA. "
            "No se puede verificar la tarifa aplicada."
        ),
        "suggested_action_es": (
            "Verifique el municipio del establecimiento de comercio y "
            "configure la tarifa ICA correspondiente en los ajustes de la empresa."
        ),
    },
    # Phase 5 — pre-persist auditor
    "PERS-DOUBLE-ENTRY-FAIL": {
        "user_message_es": (
            "Los asientos contables no están balanceados: "
            "débitos ({total_debits}) ≠ créditos ({total_credits})."
        ),
        "suggested_action_es": (
            "Revise los asientos del documento y corrija el desbalance "
            "antes de guardar los registros."
        ),
    },
    "PERS-ACCOUNT-NOT-FOUND": {
        "user_message_es": (
            "La cuenta contable {account_code} usada en los asientos "
            "no existe en el plan de cuentas configurado."
        ),
        "suggested_action_es": (
            "Verifique el código de cuenta PUC y asegúrese de que esté "
            "registrado en el catálogo de cuentas de la empresa."
        ),
    },
}


def get_message(
    rule_id: str, evidence: dict | None = None
) -> tuple[str, Optional[str]]:
    """Return (user_message_es, suggested_action_es) for a rule_id.

    Performs .format(**evidence) on both strings if evidence is provided.
    Falls back to generic phrases when rule_id is unknown — never raises.
    """
    entry = MESSAGES.get(rule_id)
    if entry is None:
        logger.warning(
            "audit_messages_es: unknown rule_id=%r — returning generic message", rule_id
        )
        return _GENERIC_MESSAGE, _GENERIC_ACTION

    user_msg = entry.get("user_message_es") or _GENERIC_MESSAGE
    action = entry.get("suggested_action_es")

    if evidence:
        try:
            user_msg = user_msg.format(**evidence)
        except (KeyError, IndexError):
            pass
        if action:
            try:
                action = action.format(**evidence)
            except (KeyError, IndexError):
                pass

    return user_msg, action


def get_agent_summary_es(agent: str, failed: bool = False) -> str:
    """Return a one-line Spanish summary for an agent step in the trace."""
    lookup = AGENT_SUMMARIES_ES_FAILED if failed else AGENT_SUMMARIES_ES
    return lookup.get(agent, f"El agente '{agent}' completó su ejecución.")
