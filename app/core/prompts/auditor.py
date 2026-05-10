"""Auditor prompt builder.

Generates the prompt sent to the LLM for auditing contador output.
"""

from __future__ import annotations

__all__ = ["auditor_output"]


def auditor_output(
    *,
    contador_output: dict,
    raw_transactions: list,
    correction_feedback: str | None = None,
) -> str:
    """Return the auditor evaluation prompt string."""
    asientos = (
        contador_output.get("asientos", []) if isinstance(contador_output, dict) else []
    )
    asientos_text = "\n".join(
        f"- cuenta={a.get('cuenta_puc', 'N/A')} "
        f"tipo={a.get('tipo_movimiento', 'N/A')} valor={a.get('valor', 0)} "
        f"desc={a.get('descripcion', '')}"
        for a in asientos[:20]
    )
    tx_text = "\n".join(
        f"- fecha={t.get('fecha', 'N/A')} nit_emisor={t.get('nit_emisor', 'N/A')} "
        f"total={t.get('total', 0)} desc={t.get('descripcion', '')}"
        for t in raw_transactions[:10]
    )

    prompt = f"""Eres un auditor contable colombiano (NIIF/DIAN).

Transacciones origen:
{tx_text or "- Sin transacciones en entrada"}

Salida del contador:
- fecha_registro: {contador_output.get("fecha_registro")}
- tipo_documento: {contador_output.get("tipo_documento")}
- total_debitos: {contador_output.get("total_debitos")}
- total_creditos: {contador_output.get("total_creditos")}
- asientos:
{asientos_text or "- Sin asientos"}

Evalua coherencia semantica, soporte documental, riesgo fiscal y calidad de la descripcion.
Devuelve una salida estructurada que incluya obligatoriamente:
- fecha_auditoria (YYYY-MM-DD)
- documento_referencia
- aprobado (bool)
- nivel_riesgo (bajo|medio|alto|critico)
- hallazgos (lista de objetos con codigo AUD-XXX, severidad, descripcion, campo_afectado opcional, recomendacion)
- puntaje_calidad (0-100)
- resumen
Si detectas errores graves, marca aprobado=false y explica claramente en resumen."""

    if correction_feedback:
        prompt += f"""

=== CORRECCION REQUERIDA ===
{correction_feedback}

Corrige los errores de esquema y regenera la auditoria."""

    return prompt
