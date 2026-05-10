"""Ingest prompt builders — one function per supported Colombian document type.

Each function returns a ready-to-send prompt string for the LLM extraction layer.
"""

from __future__ import annotations

from app.core.prompts._base import _build_prompt

__all__ = [
    "factura_venta",
    "factura_compra",
    "nota_credito",
    "nota_debito",
    "bank_statement",
    "tax_declaration",
    "tax_annex",
    "auxiliary_ledger",
    "financial_statement",
    "balance_general",
    "estado_resultados",
    "declaracion_ica",
    "autorretencion_ica",
    "anexo_iva",
    "auxiliar_iva",
    "libro_diario",
    "flujo_caja",
    "cambios_patrimonio",
    "notas_financieras",
    "comprobante_egreso",
    "documento_soporte",
    "recibo_caja",
    "nomina",
    "conciliacion_bancaria",
    "cuenta_cobro",
    "planilla_seg_social",
    "recibo_pago_impuesto",
    "extract_transactions",
]


def factura_venta(text: str, *, correction_feedback: str | None = None) -> str:
    instructions = (
        "Eres un experto contable colombiano. Extrae la información de esta "
        "FACTURA DE VENTA electrónica.\n\n"
        "Extrae obligatoriamente: número de factura (consecutivo con prefijo), CUFE, "
        "URL del código QR (campo qr_code), fecha de emisión, fecha de vencimiento (para cartera), "
        "datos del emisor (NIT con DV, razón social, régimen, resolución de facturación), "
        "datos del receptor (NIT, razón social), forma de pago, medio de pago, plazo en días, "
        "ítems con descripción/cantidad/valor unitario/impuestos, totales desglosados "
        "(subtotal, IVA, retenciones, total a pagar), y retenciones aplicadas "
        "(retefuente, reteIVA, reteICA)."
    )
    return _build_prompt(instructions, text, correction_feedback=correction_feedback)


def factura_compra(text: str, *, correction_feedback: str | None = None) -> str:
    instructions = (
        "Eres un experto contable colombiano. Extrae la información de esta "
        "FACTURA DE COMPRA.\n\n"
        "Extrae obligatoriamente: número de factura, CUFE, URL del código QR (campo qr_code), "
        "fecha de emisión, fecha de vencimiento (para cuentas por pagar), datos del proveedor "
        "(NIT con DV, razón social, régimen), datos de la empresa receptora, condiciones de pago "
        '(texto libre: "30 días netos", "2/10 neto 30", etc.), plazo en días, ítems con detalle '
        "de IVA y retenciones, totales desglosados, y si aplica, indica si es documento soporte "
        "(adquisición a no obligado a facturar)."
    )
    return _build_prompt(instructions, text, correction_feedback=correction_feedback)


def nota_credito(text: str, *, correction_feedback: str | None = None) -> str:
    instructions = (
        "Eres un experto contable colombiano. Extrae la información de esta "
        "NOTA CRÉDITO electrónica.\n\n"
        "Extrae obligatoriamente: consecutivo, CUDE, fecha de emisión, referencia a la factura "
        "original (número y CUFE), concepto de la nota (devolución/descuento/anulación/corrección), "
        "emisor, receptor, ítems ajustados con sus impuestos, y totales ajustados."
    )
    return _build_prompt(instructions, text, correction_feedback=correction_feedback)


def nota_debito(text: str, *, correction_feedback: str | None = None) -> str:
    instructions = (
        "Eres un experto contable colombiano. Extrae la información de esta "
        "NOTA DÉBITO electrónica.\n\n"
        "Extrae obligatoriamente: consecutivo, CUDE, fecha, referencia a la factura original, "
        "concepto (intereses/ajuste precio/penalización), emisor, receptor, ítems adicionados con impuestos, "
        "y totales adicionados."
    )
    return _build_prompt(instructions, text, correction_feedback=correction_feedback)


def extract_transactions(text: str, *, correction_feedback: str | None = None) -> str:
    """Legacy alias — routes to factura_venta for backward compatibility."""
    return factura_venta(text, correction_feedback=correction_feedback)


def bank_statement(text: str, *, correction_feedback: str | None = None) -> str:
    instructions = (
        "Eres un experto contable colombiano. Extrae la información de este "
        "EXTRACTO BANCARIO.\n\n"
        "Extrae obligatoriamente: entidad financiera, número y tipo de cuenta, titular (NIT y razón social), "
        "período (inicio y fin), saldo anterior (saldo_inicial) al comienzo del extracto, saldo actual (saldo_final) "
        "al final del extracto, todos los movimientos con fecha/descripción/referencia/tipo(débito o crédito), "
        "importe en campo `debito` si es cargo, en campo `credito` si es abono, y saldo después de cada movimiento "
        "en campo `saldo`, resumen con total de cargos (total_debitos) y total de abonos (total_creditos) — estos "
        "totales son necesarios para verificar que estén registradas todas las partidas del mes —, GMF cobrado, "
        "intereses generados, y retención en la fuente sobre rendimientos."
    )
    return _build_prompt(instructions, text, correction_feedback=correction_feedback)


def tax_declaration(text: str, *, correction_feedback: str | None = None) -> str:
    instructions = (
        "Eres un experto tributario colombiano. Extrae la información de esta "
        "DECLARACIÓN TRIBUTARIA (IVA Formulario 300 o ReteICA).\n\n"
        "Extrae obligatoriamente: número de formulario DIAN, período de la declaración, "
        "periodicidad (anual/bimestral/cuatrimestral/mensual), NIT del declarante, base gravable total, "
        "todos los renglones del formulario como dict {número_renglón: valor}, impuestos descontables "
        "detallados por concepto (compras_nacionales, importaciones, servicios, honorarios, etc.) "
        "en campo impuestos_descontables, saldo a favor (si aplica), total a pagar (si aplica), "
        "y cualquier sanción o interés de mora (incluir en informacion_adicional)."
    )
    return _build_prompt(instructions, text, correction_feedback=correction_feedback)


def tax_annex(text: str, *, correction_feedback: str | None = None) -> str:
    """Delegates to anexo_iva for backward compatibility."""
    return anexo_iva(text, correction_feedback=correction_feedback)


def auxiliary_ledger(text: str, *, correction_feedback: str | None = None) -> str:
    instructions = (
        "Eres un experto contable colombiano (PUC/NIIF). Extrae la información de este "
        "LIBRO AUXILIAR CONTABLE.\n\n"
        "Extrae obligatoriamente: entidad, cuenta principal PUC (código y nombre), período, saldo inicial, "
        "TODAS las líneas del auxiliar (fecha, comprobante con tipo y número, NIT tercero, nombre tercero, "
        "centro de costo, descripción/detalle, débito, crédito, saldo acumulado), total débitos, total créditos, "
        "y saldo final."
    )
    return _build_prompt(instructions, text, correction_feedback=correction_feedback)


def financial_statement(text: str, *, correction_feedback: str | None = None) -> str:
    """Legacy dispatcher — routes to balance_general or estado_resultados based on content."""
    lower = text[:2000].lower()
    if any(
        k in lower
        for k in ("utilidad", "ingresos", "gastos", "costo de venta", "resultado")
    ):
        return estado_resultados(text, correction_feedback=correction_feedback)
    return balance_general(text, correction_feedback=correction_feedback)


def balance_general(text: str, *, correction_feedback: str | None = None) -> str:
    instructions = (
        "Eres un experto contable colombiano (NIIF/PUC). Extrae la información de este "
        "BALANCE GENERAL (Estado de Situación Financiera).\n\n"
        "Extrae obligatoriamente: entidad (NIT, razón social), fecha de corte, marco normativo "
        "(NIIF plenas/Pymes/microempresas), activos corrientes y no corrientes con subcategorías y totales, "
        "pasivos corrientes y no corrientes con subcategorías y totales, patrimonio descompuesto "
        "(capital, reservas, resultados ejercicio, resultados acumulados), totales de activos/pasivos/patrimonio, "
        "verificación ecuación contable (activos == pasivos + patrimonio), y lista plana de todas las cuentas PUC con saldos."
    )
    return _build_prompt(instructions, text, correction_feedback=correction_feedback)


def estado_resultados(text: str, *, correction_feedback: str | None = None) -> str:
    instructions = (
        "Eres un experto contable colombiano (NIIF/PUC). Extrae la información de este "
        "ESTADO DE RESULTADOS (Estado de Pérdidas y Ganancias).\n\n"
        "Extrae obligatoriamente: entidad (NIT, razón social), período (fecha inicio y fin), marco normativo, "
        "ingresos ordinarios, otros ingresos, total ingresos, costo de ventas/servicios, utilidad bruta, "
        "gastos operacionales (administración y ventas por separado como totales — si el documento da un desglose, "
        "suma los componentes y pon el total en el campo correspondiente), utilidad operacional, "
        "ingresos y gastos financieros, utilidad antes de impuestos, impuesto de renta, utilidad neta, "
        "y lista plana de todas las cuentas PUC clase 4/5/6."
    )
    return _build_prompt(instructions, text, correction_feedback=correction_feedback)


def declaracion_ica(text: str, *, correction_feedback: str | None = None) -> str:
    instructions = (
        "Eres un experto tributario colombiano especializado en impuestos municipales. "
        "Extrae la información de esta DECLARACIÓN DE ICA (Impuesto de Industria y Comercio).\n\n"
        "Extrae obligatoriamente: municipio y departamento, período gravable (año, periodicidad, bimestre si aplica), "
        "NIT y razón social del declarante, actividades económicas con código CIIU y tarifa en por mil, "
        "ingresos brutos del período, deducciones aplicadas (fuera de jurisdicción, exentos, no sujetos, exportaciones), "
        "total ingresos gravables, liquidación completa (ICA, avisos y tableros 15%, sobretasa bomberil, retenciones, "
        "anticipos, sanciones, intereses, total a pagar), y tipo de declaración (inicial/corrección)."
    )
    return _build_prompt(instructions, text, correction_feedback=correction_feedback)


def autorretencion_ica(text: str, *, correction_feedback: str | None = None) -> str:
    instructions = (
        "Eres un experto tributario colombiano. Extrae la información de esta "
        "DECLARACIÓN DE AUTORRETENCIÓN DE ICA.\n\n"
        "Extrae obligatoriamente: municipio, departamento, año, periodicidad (mensual/bimestral), número de período, "
        "NIT y razón social del declarante, detalle de autorretenciones por actividad económica (CIIU, tarifa en por mil, "
        "base gravable, valor retenido), total autorretenciones, sanciones, intereses, total a pagar, "
        "y tipo de declaración."
    )
    return _build_prompt(instructions, text, correction_feedback=correction_feedback)


def anexo_iva(text: str, *, correction_feedback: str | None = None) -> str:
    instructions = (
        "Eres un experto tributario colombiano. Extrae la información de este "
        "ANEXO DE IVA.\n\n"
        "Extrae obligatoriamente: NIT y razón social del declarante, período, IVA generado desglosado por tarifa "
        "(0%, 5%, 19%) con base gravable y valor, total IVA generado, IVA descontable desglosado por concepto "
        "(compras gravadas, importaciones, servicios, honorarios) con tarifa y valor, total IVA descontable, "
        "saldo a pagar o a favor, y retenciones de IVA practicadas/sufridas."
    )
    return _build_prompt(instructions, text, correction_feedback=correction_feedback)


def auxiliar_iva(text: str, *, correction_feedback: str | None = None) -> str:
    instructions = (
        "Eres un experto contable colombiano. Extrae la información de este "
        "AUXILIAR DE IVA (libro auxiliar de cuentas de IVA).\n\n"
        "Extrae obligatoriamente: entidad, período, para cada cuenta de IVA (código PUC, nombre, "
        "tipo IVA: generado/descontable/por pagar/retenido): saldo inicial, TODOS los movimientos "
        "(fecha, comprobante, NIT tercero, nombre tercero, factura referencia, descripción, débito, crédito), "
        "total débitos, total créditos, saldo final."
    )
    return _build_prompt(instructions, text, correction_feedback=correction_feedback)


def libro_diario(text: str, *, correction_feedback: str | None = None) -> str:
    instructions = (
        "Eres un experto contable colombiano. Extrae la información de este "
        "LIBRO DIARIO OFICIAL.\n\n"
        "Extrae obligatoriamente: entidad, período, y para cada asiento contable: fecha, tipo y número de comprobante, "
        "descripción general, líneas con cuenta PUC, nombre de cuenta, NIT tercero, nombre tercero, débito y crédito. "
        "También extrae totales globales de débitos y créditos del período."
    )
    return _build_prompt(instructions, text, correction_feedback=correction_feedback)


def flujo_caja(text: str, *, correction_feedback: str | None = None) -> str:
    instructions = (
        "Eres un experto contable colombiano (NIIF). Extrae la información de este "
        "ESTADO DE FLUJOS DE EFECTIVO.\n\n"
        "Extrae obligatoriamente: entidad, período, método (directo/indirecto), actividades de operación con detalle "
        "línea a línea y flujo neto, actividades de inversión con detalle y flujo neto, actividades de financiación con detalle "
        "y flujo neto, variación neta total, efectivo al inicio del período, efectivo al fin del período, "
        "y verificación de cuadre (inicio + variación = fin)."
    )
    return _build_prompt(instructions, text, correction_feedback=correction_feedback)


def cambios_patrimonio(text: str, *, correction_feedback: str | None = None) -> str:
    instructions = (
        "Eres un experto contable colombiano (NIIF). Extrae la información de este "
        "ESTADO DE CAMBIOS EN EL PATRIMONIO.\n\n"
        "Extrae obligatoriamente: entidad, período, para cada componente patrimonial (capital social, prima, reservas, "
        "resultados acumulados, resultado del ejercicio, ORI): saldo inicial, movimientos del período con tipo y valor, "
        "saldo final. También extrae el total patrimonio inicio y total patrimonio fin."
    )
    return _build_prompt(instructions, text, correction_feedback=correction_feedback)


def notas_financieras(text: str, *, correction_feedback: str | None = None) -> str:
    instructions = (
        "Eres un experto contable colombiano (NIIF). Extrae la información de estas "
        "NOTAS A LOS ESTADOS FINANCIEROS.\n\n"
        "Extrae obligatoriamente: entidad, período, moneda funcional, marco de presentación (NIIF plenas/Pymes/microempresas), "
        "hipótesis de negocio en marcha, y para cada nota: número, título, categoría (políticas contables/estimaciones/"
        "detalle de partida/contingencias/hechos posteriores/partes relacionadas/impuestos/otra), resumen del contenido clave "
        "(máx. 500 palabras), cifras relevantes mencionadas, y políticas contables descritas."
    )
    return _build_prompt(instructions, text, correction_feedback=correction_feedback)


def comprobante_egreso(text: str, *, correction_feedback: str | None = None) -> str:
    instructions = (
        "Eres un experto contable colombiano. Extrae la información de este "
        "COMPROBANTE DE EGRESO.\n\n"
        "Extrae obligatoriamente: número de comprobante, fecha, beneficiario (NIT y razón social), concepto del pago, "
        "valor bruto, retenciones practicadas (tipo, base, tarifa, valor para retefuente/reteIVA/reteICA), valor neto a pagar, "
        "forma de pago (efectivo/cheque/transferencia), banco y número de cheque si aplica, cuenta contable a debitar, "
        "y quién aprobó el pago."
    )
    return _build_prompt(instructions, text, correction_feedback=correction_feedback)


def documento_soporte(text: str, *, correction_feedback: str | None = None) -> str:
    instructions = (
        "Eres un experto contable colombiano. Extrae la información de este "
        "DOCUMENTO SOPORTE EN ADQUISICIONES A NO OBLIGADOS A FACTURAR (art. 1.6.1.4.12 DUR 1625/2016).\n\n"
        "Extrae obligatoriamente: número de documento, fecha, datos del proveedor no obligado a facturar "
        "(NIT/cédula, nombre/razón social, régimen), datos de la empresa adquirente, descripción del servicio o bien adquirido, "
        "ítems con valores e impuestos, totales, y retenciones practicadas."
    )
    return _build_prompt(instructions, text, correction_feedback=correction_feedback)


def recibo_caja(text: str, *, correction_feedback: str | None = None) -> str:
    instructions = (
        "Eres un experto contable colombiano. Extrae la información de este "
        "RECIBO DE CAJA.\n\n"
        "Extrae obligatoriamente: número de recibo, fecha, quién paga (NIT/cédula y nombre), concepto del pago, "
        "valor recibido, forma de pago (efectivo/cheque/transferencia), banco y número de cheque si aplica, "
        "y cuenta contable a acreditar."
    )
    return _build_prompt(instructions, text, correction_feedback=correction_feedback)


def nomina(text: str, *, correction_feedback: str | None = None) -> str:
    instructions = (
        "Eres un experto contable colombiano especializado en nómina. Extrae la información de esta "
        "NÓMINA.\n\n"
        "Extrae obligatoriamente: empresa (NIT, razón social), período de nómina (inicio y fin), para cada empleado: "
        "nombre, cédula, cargo, salario básico, días trabajados, total devengado, deducciones (salud empleado 4%, "
        "pensión empleado 4%, retención en la fuente), otras deducciones, total deducciones, neto a pagar. "
        "También extrae los totales consolidados y los aportes patronales (salud 8.5%, pensión 12%, ARL, SENA, ICBF, "
        "caja de compensación)."
    )
    return _build_prompt(instructions, text, correction_feedback=correction_feedback)


def conciliacion_bancaria(text: str, *, correction_feedback: str | None = None) -> str:
    instructions = (
        "Eres un experto contable colombiano. Extrae la información de esta "
        "CONCILIACIÓN BANCARIA.\n\n"
        "Extrae obligatoriamente: empresa, entidad financiera, número de cuenta, fecha de corte, saldo según extracto bancario, "
        "saldo según libros contables, listado de todas las partidas conciliatorias (cheques en tránsito, depósitos en tránsito, "
        "notas bancarias no registradas en libros, errores) con descripción/fecha/tipo/valor, y el saldo conciliado resultante."
    )
    return _build_prompt(instructions, text, correction_feedback=correction_feedback)


def cuenta_cobro(text: str, *, correction_feedback: str | None = None) -> str:
    instructions = (
        "Eres un experto contable colombiano. Extrae la información de esta "
        "CUENTA DE COBRO.\n\n"
        "Extrae obligatoriamente: número, fecha, datos del prestador de servicios (cédula/NIT y nombre, persona natural no obligada a facturar), "
        "datos del contratante (NIT y razón social), descripción del servicio prestado, valor bruto cobrado, "
        "retenciones que debe practicar el contratante (retefuente según actividad, reteICA si aplica), y valor neto a pagar."
    )
    return _build_prompt(instructions, text, correction_feedback=correction_feedback)


def planilla_seg_social(text: str, *, correction_feedback: str | None = None) -> str:
    instructions = (
        "Eres un experto contable colombiano especializado en seguridad social. Extrae la información de esta "
        "PLANILLA DE APORTES A SEGURIDAD SOCIAL (PILA).\n\n"
        "Extrae obligatoriamente: empresa (NIT, razón social), período (YYYY-MM), número de planilla, para cada empleado: "
        "nombre, cédula, salario base de cotización, aportes a salud (empleado + empleador), pensión (empleado + empleador), "
        "ARL, caja de compensación. También extrae los totales por rubro (salud, pensión, ARL, caja, parafiscales SENA/ICBF) "
        "y total a pagar."
    )
    return _build_prompt(instructions, text, correction_feedback=correction_feedback)


def recibo_pago_impuesto(text: str, *, correction_feedback: str | None = None) -> str:
    instructions = (
        "Eres un experto tributario colombiano. Extrae la información de este "
        "RECIBO DE PAGO DE IMPUESTO.\n\n"
        "Extrae obligatoriamente: número de recibo, fecha de pago, tipo de impuesto pagado (IVA/renta/ICA/GMF/retefuente/reteICA/otro), "
        "entidad fiscal (DIAN o municipio), NIT y razón social del declarante, período gravable al que corresponde el pago, "
        "valor principal, sanciones e intereses si aplica, total pagado, banco donde se realizó el pago, y referencia de pago."
    )
    return _build_prompt(instructions, text, correction_feedback=correction_feedback)
