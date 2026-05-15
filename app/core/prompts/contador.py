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
        "REGLA FACTURA VENTA (la empresa es VENDEDOR / EMISOR):\n"
        "EL CAMPO 'total' DEL RAW TRANSACTION YA ES EL SUBTOTAL SIN IVA (la BASE). NO le restes el IVA otra vez. "
        "El IVA viene aparte en el bloque 'IMPUESTOS DEL DOCUMENTO FUENTE' como 'total_iva'.\n"
        "Asiento mínimo esperado:\n"
        "- DÉBITO 130505 (Clientes Nacionales) por (raw_transaction.total + total_iva). Es lo que el cliente debe pagar (total con IVA).\n"
        "- CRÉDITO cuenta_de_ingreso (grupo 4xxx) por raw_transaction.total exactamente. No restes IVA. "
        "Elige el código exacto del catálogo `cuentas_puc` y guíate por la actividad económica (CIIU) del emisor: "
        "comercio de mercancías → 4135; prestación de servicios, arrendamientos, transporte, profesionales, "
        "hoteles y similares → 4170; rendimientos financieros / intereses → 4210. NUNCA escribas '4xxx' "
        "ni un código que no exista en el catálogo.\n"
        "- CRÉDITO 240805 (IVA generado) por total_iva. NUNCA al débito en 240802 (eso es de compras).\n"
        "Si el comprador practica retenciones a la empresa, agrégalas como DÉBITOS y ajusta el débito a 130505 a "
        "(raw_transaction.total + total_iva - retefuente - reteica). Las cuentas son: 135515 (retefuente recibida) "
        "y 135517 (reteICA recibida). NUNCA acredites 2365/2368 en una factura de venta.\n"
        "NO inyectes gasto ICA (511505) en una factura de venta; el ICA propio se liquida en la declaración "
        "municipal, no en el asiento de cada venta.\n"
        "Validación obligatoria: Σdébitos == Σcréditos == raw_transaction.total + total_iva."
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
        "- PRIORIDAD MAXIMA: Si el documento muestra una tabla CODIGO CUENTA + "
        "CONCEPTO + TERCERO + DEBITO + CREDITO, esos son los asientos finales. "
        "Respeta los CODIGO CUENTA y los montos EXACTAMENTE como aparecen. NO "
        "los re-clasifiques, NO inyectes IVA ni retenciones (el doc YA esta cuadrado).\n"
        "- Si NO hay tabla con asientos pre-armados, sigue el patron estandar:\n"
        "    DEBIT  5xxxxx (gasto concreto, no 5195) o 220505 (anula CxP)\n"
        "    CREDIT 111005 (Banco) o 110505 (Caja). Salida de fondos.\n"
        "    NUNCA acredites 220505 (Proveedores) en un CE; 220505 solo va en DEBITO cuando el CE salda una CxP previa.\n"
        "    DEBIT/CREDIT retenciones si aplican (2365 retefuente, 2368 reteICA).\n"
        "- Un asiento por linea de pago. NO dupliques impuestos.\n"
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
        "REGLA NOMINA: "
        "DÉBITO: Gastos de personal (5105xx/5110xx) por el valor TOTAL DEVENGADO (salario bruto). "
        "Usa el campo 'total_devengado' de la transacción — NUNCA 'total_neto_pagar' ni la suma de neto_pagar de empleados para el débito. "
        "CRÉDITO: Banco (111005) por 'total_neto_pagar' (valor neto girado al empleado). "
        "Retenciones y deducciones del empleado (salud 4%, pensión 4%, retefuente) por 'total_deducciones' en cuentas 236xxx/2370xx. "
        "Provisiones nomina empleador (2510xx/2525xx) y aportes parafiscales (237xxx) si aplica. "
        "El débito SIEMPRE debe igualar la suma de todos los créditos: total_devengado = total_neto_pagar + total_deducciones. "
        "NO agregues IVA ni retefuente de servicios — nómina no causa IVA."
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
    company_context: dict | None = None,
    puc_ingresos_catalog: list[dict] | None = None,
) -> str:
    """Return the contador journal-entry prompt string.

    `doc_type` is the normalized contador-enum value embedded as a hint in
    the prompt. `doc_subtype` is the granular frontend doc type (e.g.
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

    if company_context:
        prompt += f"""

=== EMPRESA EMISORA / TENANT (contexto) ===
{json.dumps(company_context, ensure_ascii=False, indent=2)}
La actividad economica (CIIU) del emisor define la cuenta de ingreso operacional que debe usarse cuando este documento es una factura de venta. Por ejemplo:
- CIIU 47xx (comercio al por menor) → 4135 (Comercio al por mayor y al por menor)
- CIIU 55xx/56xx (alojamiento, restaurantes) → 4170 (Hoteles, restaurantes y similares)
- CIIU 68xx (actividades inmobiliarias) → 4140 (Actividades inmobiliarias / arrendamientos)
- CIIU 49xx/50xx/51xx/52xx (transporte) → 4140 (Transporte)
- CIIU 69xx/70xx/71xx/72xx/73xx/74xx (servicios profesionales/tecnicos) → 4165 (Servicios)
NO inventes una cuenta que no encaje con la actividad real del emisor."""

    if puc_ingresos_catalog:
        catalog_lines = "\n".join(
            f"- {row.get('codigo')}: {row.get('descripcion')}"
            for row in puc_ingresos_catalog
        )
        prompt += f"""

=== CATALOGO DE CUENTAS DE INGRESO PUC (4xxx) DISPONIBLES ===
Cuando el documento sea una factura de venta, elige la cuenta de ingreso (credito)
EXCLUSIVAMENTE de la siguiente lista de cuentas reales del PUC sembradas en el sistema:
{catalog_lines}
Si ninguna de las cuentas anteriores aplica, usa la cuenta padre de 4 digitos correspondiente al grupo."""

    if correction_feedback:
        prompt += f"""

=== CORRECCION REQUERIDA ===
{correction_feedback}

Corrige los errores indicados y regenera el asiento contable."""

    return prompt
