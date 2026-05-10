"""Barrel file for app.core.prompts.

Re-exports all public prompt builders so callers can import from a single location.
"""

from __future__ import annotations

from app.core.prompts._base import GENERAL_EXTRACTION_INSTRUCTIONS, _build_prompt
from app.core.prompts.auditor import auditor_output
from app.core.prompts.contador import contador_output
from app.core.prompts.ingest import (
    anexo_iva,
    auxiliar_iva,
    auxiliary_ledger,
    balance_general,
    bank_statement,
    cambios_patrimonio,
    comprobante_egreso,
    conciliacion_bancaria,
    cuenta_cobro,
    declaracion_ica,
    documento_soporte,
    estado_resultados,
    extract_transactions,
    factura_compra,
    factura_venta,
    financial_statement,
    flujo_caja,
    libro_diario,
    nomina,
    nota_credito,
    nota_debito,
    notas_financieras,
    planilla_seg_social,
    recibo_caja,
    recibo_pago_impuesto,
    tax_annex,
    tax_declaration,
)
from app.core.prompts.reportero import reportero_analysis, reportero_brief

__all__ = [
    # base
    "GENERAL_EXTRACTION_INSTRUCTIONS",
    "_build_prompt",
    # ingest
    "anexo_iva",
    "auxiliar_iva",
    "auxiliary_ledger",
    "balance_general",
    "bank_statement",
    "cambios_patrimonio",
    "comprobante_egreso",
    "conciliacion_bancaria",
    "cuenta_cobro",
    "declaracion_ica",
    "documento_soporte",
    "estado_resultados",
    "extract_transactions",
    "factura_compra",
    "factura_venta",
    "financial_statement",
    "flujo_caja",
    "libro_diario",
    "nomina",
    "nota_credito",
    "nota_debito",
    "notas_financieras",
    "planilla_seg_social",
    "recibo_caja",
    "recibo_pago_impuesto",
    "tax_annex",
    "tax_declaration",
    # contador / auditor / reportero
    "contador_output",
    "auditor_output",
    "reportero_analysis",
    "reportero_brief",
]
