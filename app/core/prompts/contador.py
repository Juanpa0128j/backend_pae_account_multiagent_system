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
        "REGLA FACTURA VENTA: debita cuentas por cobrar (130505) y acredita la cuenta de ingreso "
        "específica que corresponda al concepto. Códigos PUC válidos: 4135 (Comercio al por mayor "
        "y al por menor), 4170 (Actividades inmobiliarias y de alquiler), 4175 (Servicios), "
        "4145 (Transporte). NUNCA escribas '4xxx' literal — elige el código completo. "
        "NO dupliques el IVA ni las retenciones — el agente tributario las maneja."
    ),
    "factura_compra": (
        "REGLA FACTURA COMPRA: debita el gasto/activo y acredita cuentas por pagar (220505). "
        "NO dupliques el IVA ni las retenciones — el agente tributario las maneja.\n"
        "CUENTAS DE GASTO COMUNES (úsalas SIEMPRE en lugar de 5195):\n"
        "- 510505 Sueldos        - 510510 Cesantías          - 510515 Intereses cesantías\n"
        "- 510518 Prima servicios- 510521 Vacaciones         - 510527 Aportes EPS\n"
        "- 510530 Aportes ARP    - 510533 Aportes pensión    - 511505 Honorarios\n"
        "- 511510 Comisiones     - 511525 Servicios técnicos - 511595 Otros honorarios\n"
        "- 513025 Combustibles   - 513540 Servicios públicos - 514505 Mantenimiento\n"
        "- 519520 Cuotas afiliación - 521505 ICA gasto ventas - 529505 Diversos\n"
        "- 143005 Inventario     - 152405 Equipo cómputo     - 152805 Equipo oficina\n"
        "SOLO usa 5195 si el concepto es realmente ambiguo (último recurso)."
    ),
    "nota_credito": (
        "REGLA NOTA CREDITO: Asiento de reversión. Debita la cuenta de ingreso específica "
        "(4135/4170/4175 según concepto) o cuentas por pagar (220505), "
        "y acredita cuentas por cobrar (130505). NUNCA uses '4xxx' literal. "
        "Referencia el numero de factura asociada en descripcion_general. "
        "NO dupliques impuestos — el agente tributario los maneja."
    ),
    "nota_debito": (
        "REGLA NOTA DEBITO: Asiento de ajuste adicional. Debita cuentas por cobrar (130505) "
        "y acredita la cuenta de ingreso específica (4135/4170/4175) según el concepto. "
        "Si la nota corresponde a intereses moratorios, acredita la cuenta de ingresos "
        "financieros del PUC seed (clase 42, p. ej. 4210). NUNCA uses '4xxx' o '13xxxx' literal. "
        "Referencia el numero de factura asociada en descripcion_general. "
        "NO dupliques impuestos — el agente tributario los maneja."
    ),
    "comprobante_egreso": (
        "REGLA COMPROBANTE DE EGRESO (CE):\n"
        "- SIEMPRE acredita 111005 (Banco) o 110505 (Caja). El CE representa salida de fondos.\n"
        "- NUNCA acredites 220505 (Proveedores). 220505 solo aparece en DEBITO cuando el CE "
        "salda una factura previa cuya CxP ya fue creada por la factura.\n"
        "- Patrón estándar:\n"
        "    DEBIT  5xxxxx (gasto concreto, no 5195) o 220505 (anula CxP)\n"
        "    CREDIT 111005 (Banco)\n"
        "    DEBIT/CREDIT retenciones si aplican (2365 retefuente, 2368 reteICA)\n"
        "- Un asiento por linea de pago.\n"
        "- NO dupliques impuestos — el agente tributario los maneja.\n"
        "CUENTAS DE GASTO COMUNES (úsalas SIEMPRE en lugar de 5195):\n"
        "- 510505 Sueldos        - 510510 Cesantías          - 510515 Intereses cesantías\n"
        "- 510518 Prima servicios- 510521 Vacaciones         - 510527 Aportes EPS\n"
        "- 510530 Aportes ARP    - 510533 Aportes pensión    - 511505 Honorarios\n"
        "- 511510 Comisiones     - 511525 Servicios técnicos - 511595 Otros honorarios\n"
        "- 513025 Combustibles   - 513540 Servicios públicos - 514505 Mantenimiento\n"
        "- 519520 Cuotas afiliación - 521505 ICA gasto ventas - 529505 Diversos\n"
        "SOLO usa 5195 si el concepto es realmente ambiguo (último recurso)."
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
    doc_subtype: str = "",
    rag_context: list[dict] | None = None,
    correction_feedback: str | None = None,
    source_taxes: dict | None = None,
) -> str:
    """Return the contador journal-entry prompt string.

    `doc_type` is the contador-enum value sent to the LLM as the document
    type hint. `doc_subtype` is the granular frontend value (e.g.
    factura_venta vs factura_compra) used to look up the specific
    `_DOC_GUIDANCE` rules. When `doc_subtype` is empty the lookup falls
    back to `doc_type`.
    """
    txns_text = "\n\n".join(
        f"Transaccion {i + 1}:\n{_format_tx(t)}" for i, t in enumerate(raw_transactions)
    )

    rag_section = _build_rag_section(rag_context)
    doc_type_hint = f"Tipo de documento: {doc_type}\n" if doc_type else ""
    guidance_key = doc_subtype or doc_type
    doc_guidance = _DOC_GUIDANCE.get(guidance_key, "")
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
- cuenta_puc DEBE tener 6 dígitos para clases 4 (ingresos), 5 (gastos), 6 (costos). Solo usa 4 dígitos para activos (1xxx), pasivos (2xxx) o patrimonio (3xxx) cuando el PUC no tenga subcuenta más específica disponible.
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
