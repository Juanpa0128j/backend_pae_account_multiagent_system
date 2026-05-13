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

PATRONES VÁLIDOS — NO marcar como hallazgo, NO rechazar:
- Una misma cuenta puede aparecer en múltiples líneas si el tipo_movimiento es distinto
  o si representa retenciones distintas (ej. 2368 acreditado dos veces: una para Reteica
  causada y otra para ICA por pagar; o 2365 retefuente con varias bases gravables).
- 130505 (CxC) puede aparecer en >1 línea cuando una factura tiene anticipos parciales
  o múltiples plazos de cobro.
- 5xxxxx (gasto) puede coexistir con 220505 (CxP) en factura_compra a crédito
  — patrón estándar gasto + cuenta por pagar.
- IVA descontable (2408xx) y IVA generado (2408xx) pueden coexistir en notas crédito
  o documentos mixtos sin ser duplicado.

RECHAZO (aprobado=false) SOLO cuando se cumple alguno de:
- Asiento desbalanceado (Σ debitos ≠ Σ creditos).
- cuenta_puc con formato inválido (no es código PUC reconocible).
- tipo_documento inconsistente con el contenido de los asientos.
- Valores negativos sin justificación clara en la descripción.
- Falta una contraparte obligatoria (FV sin línea de ingreso, CE sin línea de banco/caja).
- Coherencia semantica gravemente rota (ej. ingreso clasificado como gasto).
- En factura_venta: 240802 (IVA descontable) aparece en DÉBITO, o 2365/2368 aparecen
  en CRÉDITO. En venta el IVA debe ir al crédito en 240805, y las retenciones que el
  comprador practica son anticipos a favor (135515 / 135517) al débito.
- En factura_venta: aparece gasto ICA (511505) o crédito a 2368 etiquetado como
  "ICA por pagar". Esos asientos corresponden a la liquidación municipal, no a la
  factura individual.

Devuelve una salida estructurada que incluya obligatoriamente:
- fecha_auditoria (YYYY-MM-DD)
- documento_referencia
- aprobado (bool)
- nivel_riesgo (bajo|medio|alto|critico)
- hallazgos (lista de objetos con codigo AUD-XXX, severidad, descripcion, campo_afectado opcional, recomendacion)
- puntaje_calidad (0-100)
- resumen
Si NO se cumple ningún criterio de rechazo, marca aprobado=true aunque encuentres mejoras menores (regístralas como hallazgos de severidad baja sin bloquear)."""

    if correction_feedback:
        prompt += f"""

=== CORRECCION REQUERIDA ===
{correction_feedback}

Corrige los errores de esquema y regenera la auditoria."""

    return prompt
