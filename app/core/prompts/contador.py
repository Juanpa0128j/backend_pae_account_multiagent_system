"""Contador prompt builder.

Generates the prompt sent to the LLM for journal-entry (asiento contable)
generation from raw extracted transactions.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["contador_output"]

_EXCLUDED = {"_contador_asientos", "_tributario_output"}

_DOC_GUIDANCE: dict[str, str] = {
    "extracto_bancario": (
        "REGLA EXTRACTO BANCARIO: Cada movimiento genera exactamente DOS asientos: "
        "debito en cuenta bancaria (111005) y credito en la contraparte (o viceversa). "
        "NO agregues retenciones (retefuente, reteICA, IVA) — esas las calcula el agente tributario. "
        "El valor del asiento debe ser el campo 'debito' o 'credito' del movimiento, NO el saldo."
    ),
    "factura_venta": (
        "REGLA FACTURA VENTA: debita cuentas por cobrar (130505) y acredita ingresos (4xxx). "
        "NO dupliques el IVA ni las retenciones — el agente tributario las maneja."
    ),
    "factura_compra": (
        "REGLA FACTURA COMPRA: debita el gasto/activo y acredita cuentas por pagar (220505). "
        "NO dupliques el IVA ni las retenciones — el agente tributario las maneja."
    ),
    "nota_credito": (
        "REGLA NOTA CREDITO: Asiento de reversión. Debita ingresos (4xxx) o cuentas por pagar (220505) "
        "segun el concepto (devolucion/descuento/anulacion), y acredita cuentas por cobrar (130505). "
        "Referencia el numero de factura asociada en descripcion_general. "
        "NO dupliques impuestos — el agente tributario los maneja."
    ),
    "nota_debito": (
        "REGLA NOTA DEBITO: Asiento de ajuste adicional. Debita cuentas por cobrar (130505) "
        "y acredita ingresos (4xxx) o intereses por cobrar (130xxx) segun el concepto. "
        "Referencia el numero de factura asociada en descripcion_general. "
        "NO dupliques impuestos — el agente tributario los maneja."
    ),
    "comprobante_egreso": (
        "REGLA COMPROBANTE EGRESO: Debita la cuenta de gasto o proveedor correspondiente "
        "y acredita banco o caja (111005/110505). Un asiento por linea de pago. "
        "NO dupliques impuestos — el agente tributario los maneja."
    ),
    "declaracion_iva": (
        "REGLA DECLARACION IVA: Asiento de pago/liquidacion de IVA. "
        "Debita IVA por pagar (240802) por el valor neto a pagar (IVA generado menos descontable). "
        "Acredita banco (111005) por el valor efectivamente pagado. "
        "Si hay saldo a favor, acredita saldo a favor IVA (135505). "
        "NO agregues otros impuestos — este documento solo liquida IVA."
    ),
    "declaracion_ica": (
        "REGLA DECLARACION ICA: Asiento de pago ICA. "
        "Debita ICA por pagar (2368) por el total a pagar. "
        "Acredita banco (111005). "
        "NO agregues otros impuestos — este documento solo liquida ICA."
    ),
    "autorretencion_ica": (
        "REGLA AUTORRETENCION ICA: Debita gasto ICA (540101) y acredita autorretencion ICA por pagar (236540). "
        "Base gravable = ingresos brutos del periodo. "
        "NO dupliques otros impuestos — el agente tributario maneja retefuente e IVA."
    ),
    "anexo_iva": (
        "REGLA ANEXO IVA: Documento de soporte del IVA declarado. "
        "Si requiere asiento, debita IVA descontable (135510) o acredita IVA generado (240802) "
        "segun los renglones del anexo. Usualmente no genera asiento nuevo si ya fue contabilizado."
    ),
    "auxiliar_iva": (
        "REGLA AUXILIAR IVA: Libro auxiliar del IVA. Normalmente no genera asientos nuevos "
        "— es un resumen de movimientos ya contabilizados. Si hay diferencias, genera ajuste "
        "en 240802 vs 135510."
    ),
    "nomina": (
        "REGLA NOMINA: Debita gastos de personal (5105xx/5110xx) por salarios, prestaciones y parafiscales. "
        "Acredita banco (111005) por valor neto pagado, provisiones nomina (2510xx/2525xx) "
        "y aportes por pagar (237xxx). "
        "NO dupliques impuestos de renta — el agente tributario los maneja."
    ),
    "recibo_caja": (
        "REGLA RECIBO CAJA: Debita banco o caja (111005/110505) por el valor recibido. "
        "Acredita cuentas por cobrar (130505) si es cobro de cartera, o ingresos (4xxx) si es venta directa. "
        "NO dupliques impuestos — el agente tributario los maneja."
    ),
    "documento_soporte": (
        "REGLA DOCUMENTO SOPORTE: Similar a factura de compra. "
        "Debita el gasto o activo correspondiente y acredita cuentas por pagar (220505) o banco (111005). "
        "NO dupliques retenciones ni IVA — el agente tributario los maneja."
    ),
    "cuenta_cobro": (
        "REGLA CUENTA COBRO: Debita gasto o activo y acredita cuentas por pagar (220505). "
        "Similar a factura de compra sin IVA. "
        "NO dupliques retenciones — el agente tributario las maneja."
    ),
    "conciliacion_bancaria": (
        "REGLA CONCILIACION BANCARIA: Solo genera asientos de ajuste para partidas conciliatorias "
        "(cheques en transito, depositos en transito, notas bancarias no contabilizadas). "
        "Debita/acredita banco (111005) vs la cuenta contraparte correspondiente. "
        "NO reproceses movimientos ya contabilizados."
    ),
    "recibo_pago_impuesto": (
        "REGLA RECIBO PAGO IMPUESTO: Debita la cuenta de impuesto por pagar correspondiente "
        "(240802 IVA, 240815 Retefuente, 2368 ICA, 240405 Renta) y acredita banco (111005). "
        "Un asiento por impuesto pagado."
    ),
}


def _format_tx(t: dict) -> str:
    lines = []
    for k, v in t.items():
        if k not in _EXCLUDED and v not in (None, "", [], {}):
            lines.append(f"    {k}: {v}")
    return "\n".join(lines)


def _build_rag_section(rag_context: list[Any] | None) -> str:
    rag_context = rag_context or []
    rag_lines: list[str] = []
    for item in rag_context[:5]:
        if isinstance(item, dict):
            rag_lines.append(
                str(
                    item.get("content")
                    or item.get("text")
                    or item.get("document")
                    or item
                )
            )
        else:
            rag_lines.append(str(getattr(item, "content", item)))
    section = "\n".join(line for line in rag_lines if line).strip()
    if not section:
        section = "Sin contexto normativo adicional."
    return section


def contador_output(
    raw_transactions: list,
    *,
    doc_type: str = "",
    rag_context: list[dict] | None = None,
    correction_feedback: str | None = None,
    source_taxes: dict | None = None,
) -> str:
    """Return the contador journal-entry prompt string."""
    txns_text = "\n\n".join(
        f"Transaccion {i + 1}:\n{_format_tx(t)}" for i, t in enumerate(raw_transactions)
    )

    rag_section = _build_rag_section(rag_context)
    doc_type_hint = f"Tipo de documento: {doc_type}\n" if doc_type else ""
    doc_guidance = _DOC_GUIDANCE.get(doc_type, "")
    doc_guidance_section = f"\n{doc_guidance}\n" if doc_guidance else ""

    prompt = f"""Eres un contador experto en normativa colombiana (PUC).

{doc_type_hint}Transacciones pendientes de clasificar:
{txns_text}

Genera el asiento contable siguiendo el PUC colombiano.
{doc_guidance_section}REGLAS GENERALES:
- Usa cuentas PUC reales
- OBLIGATORIO: total_debitos == total_creditos (partida doble perfecta)
- tipo_movimiento debe ser 'debito' o 'credito'
- tipo_documento debe estar en: recibo, factura, extracto, nota_credito, nota_debito, comprobante_egreso, otro
- Si el documento tiene multiples movimientos, genera un asiento por movimiento y consolida los totales

Contexto normativo/RAG:
{rag_section}"""

    if source_taxes:
        prompt += f"""

=== IMPUESTOS DEL DOCUMENTO FUENTE (solo contexto) ===
Los siguientes valores fueron extraidos directamente del documento original:
{json.dumps(source_taxes, ensure_ascii=False, indent=2)}
IMPORTANTE: Estos valores son informativos. NO los incluyas como asientos contables — el agente tributario los registrara por separado. Usaelos para entender la naturaleza fiscal del documento y clasificar correctamente las cuentas PUC."""

    if correction_feedback:
        prompt += f"""

=== CORRECCION REQUERIDA ===
{correction_feedback}

Corrige los errores indicados y regenera el asiento contable."""

    return prompt
