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
        "REGLA EXTRACTO BANCARIO: Cada movimiento genera EXACTAMENTE DOS asientos balanceados: "
        "uno en banco 111005 (Bancos Nacionales) y otro en la contraparte. "
        "Dirección por `bank_direction` del movement:\n"
        "  - bank_direction='entrada' (abono al cliente) → D 111005 / C contraparte\n"
        "  - bank_direction='salida' (cargo al cliente) → D contraparte / C 111005\n"
        "Contraparte según descripción del movement:\n"
        "  - GMF / 4X1000 / IMPTO GOBIERNO → 530525 (Gastos bancarios) — siempre salida\n"
        "  - Cuota manejo / comisión bancaria / mantenimiento tarjeta → 530525\n"
        "  - ABONO INTERESES AHORROS / rendimientos → 421005 (Ingresos financieros) — entrada\n"
        "  - AJUSTE INTERESES AHORROS DB → 421005 (reverso) — salida\n"
        "  - PAGO PSE IMPUESTO DIAN / DIR → 240XXX (impuesto por pagar) — salida\n"
        "  - PAGO DE PROV / PAGO DE TERC → 220505 (CxP) reverso — salida\n"
        "  - TRANSFERENCIA CTA SUC VIRTUAL → 1110XX (otra cuenta propia) o 220505 según destino\n"
        "  - PAGO PSE [Empresa] → 220505 reverso — salida\n"
        "  - Consignación / depósito de cliente → 130505 (CxC) reverso — entrada\n"
        "Valor de cada asiento = campo 'debito' o 'credito' del movement, NUNCA el saldo. "
        "NO agregues retenciones (retefuente, reteICA, IVA) — tributario las omite para extracto. "
        "NUNCA uses 519595 ni 4170 como fallback genérico — elige el código específico por concepto. "
        "Si la descripción es ambigua: 530525 para salidas, 421005 para entradas."
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
        "OBLIGATORIO IVA DESCONTABLE: cuando totales.total_iva > 0 en el documento fuente, "
        "DEBES emitir una línea separada D 240810 IVA descontable por exactamente ese valor. "
        "NUNCA mezcles el IVA dentro del crédito a proveedor — el crédito a proveedor "
        "debe ser igual al subtotal sin IVA (luego tributario reduce por retenciones). "
        "Si NO booqueas la línea D 240810 cuando hay IVA, el agente tributario "
        "double-cuenta el IVA y la partida doble falla.\n"
        "NO dupliques las retenciones — el agente tributario las maneja.\n"
        "CUENTAS DE GASTO COMUNES (úsalas SIEMPRE en lugar de 5195):\n"
        "- 510505 Sueldos        - 510510 Cesantías          - 510515 Intereses cesantías\n"
        "- 510518 Prima servicios- 510521 Vacaciones         - 510527 Aportes EPS\n"
        "- 510530 Aportes ARP    - 510533 Aportes pensión    - 511505 Honorarios\n"
        "- 511510 Comisiones     - 511525 Servicios técnicos - 511595 Otros honorarios\n"
        "- 513025 Combustibles   - 513540 Servicios públicos - 514505 Mantenimiento\n"
        "- 519520 Cuotas afiliación - 521505 ICA gasto ventas - 529505 Diversos\n"
        "- 143005 Inventario     - 152405 Equipo cómputo     - 152805 Equipo oficina\n"
        "CLUBES DEPORTIVOS/SOCIALES (sostenimiento, cuotas extraordinarias, fomento "
        "deportivo, country clubs): usa 519520 Cuotas afiliación o 511595 Otros "
        "honorarios. NUNCA 5195 para clubes.\n"
        "ADMIN PROPIEDAD HORIZONTAL (edificios, conjuntos, parcelaciones — cuotas de "
        "administración, intereses de mora PH, extra ascensores): usa 511595 Otros "
        "honorarios o 511525 Servicios técnicos. NUNCA 5195 para PH.\n"
        "SOLO usa 5195 si el concepto es realmente ambiguo (último recurso) — "
        "esto disparará un warning en logs de auditoría."
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
        "REGLA RECIBO CAJA: El recibo registra un INGRESO de dinero a caja o banco.\n"
        "- Debita siempre 111005 (Banco) o 110505 (Caja) por el valor recibido.\n"
        "- Para el CRÉDITO usa la señal 'tipo_recibo' de la transacción:\n"
        "    * 'cobro_cartera': acredita 130505 (Clientes / CxC) — cancela una factura previa.\n"
        "    * 'venta_directa': acredita cuenta de ingreso (4xxx) según actividad económica "
        "(4135 comercio, 4170 servicios, 4140 arrendamiento).\n"
        "    * Ausente o 'otro': acredita 130505 por defecto (cobro de cartera es lo más común).\n"
        "- Si hay 'referencia_factura', inclúyela en descripcion_general del asiento.\n"
        "- NO dupliques impuestos — el agente tributario los maneja.\n"
        "- Validación: Σdébitos == Σcréditos == total del recibo."
    ),
    "documento_soporte": (
        "REGLA DOCUMENTO SOPORTE: Pago a proveedor NO obligado a facturar. "
        "Si el doc trae IVA explícito (totales.total_iva > 0), incluye D 240802 "
        "(IVA descontable). Si el doc trae IVA=0 (régimen R-99-PN / responsabilidad ZZ), "
        "NO incluyas 240802. Cuenta gasto según concepto:\n"
        "  - Administración de propiedad horizontal / edificios → 511595 o 511525\n"
        "  - Servicios técnicos especializados → 511525\n"
        "  - Honorarios profesionales → 511505\n"
        "  - Comisiones → 511510\n"
        "  - Pago a empleados → 510505 (Sueldos) — SOLO si es nómina\n"
        "  - Arrendamientos pagados → 511525 o 5140\n"
        "Estructura: D gasto/activo + C 220505 (CxP a proveedores) por el valor neto. "
        "El agente tributario añade retenciones (retefuente 2365, reteICA 2368). "
        "NO dupliques retenciones."
    ),
    "cuenta_cobro": (
        "REGLA CUENTA COBRO: Documento informal de cobro emitido por persona natural "
        "NO obligada a facturar y NO responsable de IVA. NUNCA incluyas D 240802 "
        "(IVA descontable) — no hay IVA por definición. Cuenta gasto según concepto del servicio:\n"
        "  - Honorarios contables, jurídicos, asesoría, consultoría → 511505 Honorarios\n"
        "  - Outsourcing (contable, administrativo, operativo) → 511505 o 511595\n"
        "  - Comisiones → 511510\n"
        "  - Servicios técnicos especializados → 511525\n"
        "  - Arrendamientos pagados → 511525 o 5140\n"
        "  - Otros honorarios no clasificados → 511595\n"
        "NUNCA uses 5305 (Gastos Financieros) — esa cuenta es exclusiva de intereses "
        "y comisiones bancarias. NUNCA uses 530505 (no existe en el catálogo). "
        "Estructura: D gasto (5110xx/5115xx) + C 220505 (CxP) por el valor neto. "
        "El agente tributario añadirá retenciones (2365 retefuente, 2368 reteICA) SI corresponde; "
        "NO las dupliques aquí."
    ),
    "conciliacion_bancaria": (
        "REGLA CONCILIACION BANCARIA: Solo genera asientos de ajuste para partidas conciliatorias "
        "(cheques en transito, depositos en transito, notas bancarias no contabilizadas). "
        "Debita/acredita banco (111005) vs la cuenta contraparte correspondiente. "
        "NO reproceses movimientos ya contabilizados."
    ),
    "recibo_pago_impuesto": (
        "REGLA RECIBO PAGO IMPUESTO: Por cada transacción genera UN par balanceado de asientos: "
        "DÉBITO en la cuenta de impuesto por pagar correspondiente al concepto "
        "(240802 IVA, 240815 Retefuente, 2368 ICA, 240405 Renta — si el concepto es desconocido usa 240805) "
        "por el valor EXACTO de esa transacción, y CRÉDITO 111005 (Banco) por el mismo valor. "
        "CRÍTICO: débito y crédito deben ser iguales en cada par. "
        "Si hay varias transacciones, genera un par por cada una. No sumes ni promedies entre transacciones."
    ),
    "liquidacion_cesantias": (
        "REGLA LIQUIDACION CESANTIAS:\n"
        "- El documento puede venir en formato resumido con dias_base, salario_base_liquidacion, auxilio_transporte y valor_cesantias.\n"
        "- Si solo hay un valor de cesantias por empleado o documento, usa ese valor como base principal del asiento.\n"
        "- PRIORIDAD DE CAMPOS: usa primero totales consolidados del raw transaction (total_cesantias_liquidadas, total_intereses_cesantias, total_prima_servicios, total_vacaciones, total_retenciones, total_neto_pagar).\n"
        "- Solo si falta algun total consolidado, calcula el valor sumando campos por empleado (p. ej. prima_servicios_liquidada, vacaciones_liquidadas, neto_pagar).\n"
        "DÉBITOS:\n"
        "- 510510 (Cesantías gasto) por total_cesantias_liquidadas. CRÍTICO: NO confundas con 510515 (intereses).\n"
        "- 510515 (Intereses cesantías) por total_intereses_cesantias. Este es un concepto diferente, no suma al anterior.\n"
        "- 510518 (Prima servicios) por total_prima_servicios SI APLICA (o suma de prima_servicios_liquidada por empleado si no hay total).\n"
        "- 521505 (Vacaciones) por total_vacaciones SI APLICA (o suma de vacaciones_liquidadas por empleado si no hay total).\n"
        "CRÉDITOS:\n"
        "- 111005 (Banco) o 110505 (Caja) por total_neto_pagar (valor efectivamente girado).\n"
        "- 2365 (Retefuente por pagar) por retenciones retefuente (usa total_retenciones o desglose por empleado si existe).\n"
        "- 236570 (Retención salud) por retención salud empleado.\n"
        "- 236545 (Retención pensión) por retención pensión empleado.\n"
        "PASIVOS DE APORTE:\n"
        "- 2510 (Provisión cesantías) si hay saldo acumulado no liquidado.\n"
        "- 251010 (Fondo cesantías privado) o 251015 (Fondo público) según tipo_fondo_cesantias.\n"
        "VALIDACIÓN OBLIGATORIA:\n"
        "Σdébitos == Σcréditos. La mayoría de débitos debe igualar total_neto_pagar al banco (ajustado por retenciones cuando aplique).\n"
        "NOTA: Si el documento trae `asientos_documento` (tabla contable pre-armada), respeta EXACTAMENTE esos códigos y montos."
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
