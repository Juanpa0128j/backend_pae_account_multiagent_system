"""
XML parser for Colombian DIAN electronic documents (UBL 2.1).

Supports:
- Factura Electrónica (Invoice)
- Nota Crédito (CreditNote)
- Nota Débito (DebitNote)

Converts UBL XML into a structured plain-text representation that the
classifier and extraction LLMs can read exactly like a parsed PDF.
"""

from __future__ import annotations

import logging
from pathlib import Path
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

# UBL 2.1 namespace map (DIAN uses these)
_NS: dict[str, str] = {
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "fe":  "http://www.dian.gov.co/contratos/facturaelectronica/v1",
    "ds":  "http://www.w3.org/2000/09/xmldsig#",
    "ext": "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2",
}


def _find(node, path: str) -> str:
    """Return text of first matching element, or empty string."""
    el = node.find(path, _NS)
    return (el.text or "").strip() if el is not None else ""


def _findall_text(node, path: str) -> list[str]:
    return [(el.text or "").strip() for el in node.findall(path, _NS)]


def _party_block(party_node, label: str) -> list[str]:
    """Extract common party fields (NIT, name, address) into text lines."""
    if party_node is None:
        return []
    lines = [f"\n{label}:"]
    nit = _find(party_node, "cac:PartyTaxScheme/cbc:CompanyID")
    name = _find(party_node, "cac:PartyTaxScheme/cbc:RegistrationName") or \
           _find(party_node, "cac:PartyName/cbc:Name")
    regime = _find(party_node, "cac:PartyTaxScheme/cbc:TaxLevelCode")
    address = _find(party_node, "cac:PhysicalLocation/cac:Address/cbc:CityName") or \
              _find(party_node, "cac:PostalAddress/cbc:CityName")
    dept = _find(party_node, "cac:PhysicalLocation/cac:Address/cac:AddressLine/cbc:Line") or \
           _find(party_node, "cac:PostalAddress/cac:AddressLine/cbc:Line")
    if nit:
        lines.append(f"  NIT: {nit}")
    if name:
        lines.append(f"  Razón social: {name}")
    if regime:
        lines.append(f"  Régimen fiscal: {regime}")
    if address:
        lines.append(f"  Ciudad: {address}")
    if dept:
        lines.append(f"  Dirección: {dept}")
    return lines


def _parse_ubl(root: ET.Element) -> str:
    """Convert a UBL Invoice/CreditNote/DebitNote root element to plain text."""
    lines: list[str] = []

    # Detect document type from local tag name
    tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
    doc_label = {
        "Invoice": "FACTURA ELECTRÓNICA DE VENTA",
        "CreditNote": "NOTA CRÉDITO ELECTRÓNICA",
        "DebitNote": "NOTA DÉBITO ELECTRÓNICA",
    }.get(tag, tag.upper())
    lines.append(f"# {doc_label}")

    # Header fields
    numero = _find(root, "cbc:ID")
    cufe = _find(root, "cbc:UUID")
    fecha = _find(root, "cbc:IssueDate")
    hora = _find(root, "cbc:IssueTime")
    tipo_op = _find(root, "cbc:InvoiceTypeCode") or _find(root, "cbc:CreditNoteTypeCode")
    moneda = _find(root, "cbc:DocumentCurrencyCode")
    nota = _find(root, "cbc:Note")

    if numero:
        lines.append(f"Número: {numero}")
    if cufe:
        lines.append(f"CUFE: {cufe}")
    if fecha:
        lines.append(f"Fecha: {fecha}" + (f" {hora}" if hora else ""))
    if tipo_op:
        lines.append(f"Tipo operación: {tipo_op}")
    if moneda:
        lines.append(f"Moneda: {moneda}")
    if nota:
        lines.append(f"Nota: {nota}")

    # Período de facturación
    period = root.find("cac:InvoicePeriod", _NS)
    if period is not None:
        start = _find(period, "cbc:StartDate")
        end = _find(period, "cbc:EndDate")
        if start or end:
            lines.append(f"Período: {start} — {end}")

    # Forma y medio de pago
    payment = root.find("cac:PaymentMeans", _NS)
    if payment is not None:
        forma = _find(payment, "cbc:PaymentMeansCode")
        vencimiento = _find(payment, "cbc:PaymentDueDate")
        lines.append(f"Forma de pago: {forma}" + (f" (vence {vencimiento})" if vencimiento else ""))

    # Emisor
    supplier = root.find("cac:AccountingSupplierParty/cac:Party", _NS)
    lines.extend(_party_block(supplier, "Emisor (Vendedor)"))

    # Receptor
    customer = root.find("cac:AccountingCustomerParty/cac:Party", _NS)
    lines.extend(_party_block(customer, "Receptor (Comprador)"))

    # Resolución de facturación
    auth = root.find(".//cac:AuthorizationLine", _NS) or root.find(".//ext:UBLExtensions", _NS)
    resolucion = _find(root, ".//cbc:AuthorizationID")
    if resolucion:
        lines.append(f"\nResolución DIAN: {resolucion}")

    # Líneas de detalle
    item_paths = [
        "cac:InvoiceLine",
        "cac:CreditNoteLine",
        "cac:DebitNoteLine",
    ]
    items: list[ET.Element] = []
    for path in item_paths:
        items = root.findall(path, _NS)
        if items:
            break

    if items:
        lines.append("\n## Detalle de ítems")
        for item in items:
            desc = _find(item, "cac:Item/cbc:Description")
            qty = _find(item, "cbc:InvoicedQuantity") or _find(item, "cbc:CreditedQuantity") or _find(item, "cbc:DebitedQuantity")
            unit = ""
            qty_el = item.find("cbc:InvoicedQuantity", _NS) or item.find("cbc:CreditedQuantity", _NS)
            if qty_el is not None:
                unit = qty_el.get("unitCode", "")
            valor_linea = _find(item, "cbc:LineExtensionAmount")
            precio_unit = _find(item, "cac:Price/cbc:PriceAmount")
            code = _find(item, "cac:Item/cac:SellersItemIdentification/cbc:ID")

            tax_pct = _find(item, "cac:TaxTotal/cac:TaxSubtotal/cbc:Percent")
            tax_amount = _find(item, "cac:TaxTotal/cbc:TaxAmount")
            tax_name = _find(item, "cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cac:TaxScheme/cbc:Name")

            parts = []
            if code:
                parts.append(f"[{code}]")
            if desc:
                parts.append(desc)
            if qty:
                parts.append(f"Cant: {qty} {unit}".strip())
            if precio_unit:
                parts.append(f"P.Unit: {precio_unit}")
            if valor_linea:
                parts.append(f"Total línea: {valor_linea}")
            if tax_pct and tax_name:
                parts.append(f"{tax_name} {tax_pct}%: {tax_amount}")
            lines.append("  - " + " | ".join(parts))

    # Totales de impuestos
    tax_totals = root.findall("cac:TaxTotal", _NS)
    if tax_totals:
        lines.append("\n## Impuestos")
        for tt in tax_totals:
            total_tax = _find(tt, "cbc:TaxAmount")
            for sub in tt.findall("cac:TaxSubtotal", _NS):
                base = _find(sub, "cbc:TaxableAmount")
                amount = _find(sub, "cbc:TaxAmount")
                pct = _find(sub, "cbc:Percent")
                name = _find(sub, "cac:TaxCategory/cac:TaxScheme/cbc:Name")
                lines.append(f"  {name or 'Impuesto'} {pct}%: base {base} → {amount}")
            if total_tax:
                lines.append(f"  Total impuesto: {total_tax}")

    # Retenciones
    wh_totals = root.findall("cac:WithholdingTaxTotal", _NS)
    if wh_totals:
        lines.append("\n## Retenciones")
        for wt in wh_totals:
            total_wh = _find(wt, "cbc:TaxAmount")
            for sub in wt.findall("cac:TaxSubtotal", _NS):
                base = _find(sub, "cbc:TaxableAmount")
                amount = _find(sub, "cbc:TaxAmount")
                pct = _find(sub, "cbc:Percent")
                name = _find(sub, "cac:TaxCategory/cac:TaxScheme/cbc:Name")
                lines.append(f"  {name or 'Retención'} {pct}%: base {base} → {amount}")
            if total_wh:
                lines.append(f"  Total retención: {total_wh}")

    # Totales del documento
    totals = root.find("cac:LegalMonetaryTotal", _NS)
    if totals is not None:
        lines.append("\n## Totales")
        for field, label in [
            ("cbc:LineExtensionAmount", "Subtotal sin impuestos"),
            ("cbc:TaxExclusiveAmount",  "Base gravable"),
            ("cbc:TaxInclusiveAmount",  "Total con impuestos"),
            ("cbc:AllowanceTotalAmount","Total descuentos"),
            ("cbc:ChargeTotalAmount",   "Total cargos"),
            ("cbc:PrepaidAmount",       "Anticipo"),
            ("cbc:PayableAmount",       "Total a pagar"),
        ]:
            val = _find(totals, field)
            if val:
                lines.append(f"  {label}: {val}")

    return "\n".join(lines)


def parse_xml(file_path: str) -> str:
    """
    Parse a DIAN UBL XML electronic document and return structured plain text.

    Falls back to dumping all text nodes if the document doesn't match
    the expected UBL structure.

    Args:
        file_path: Absolute or relative path to the .xml file.

    Returns:
        Plain-text representation suitable for LLM classification and extraction.

    Raises:
        ValueError: If the file cannot be parsed as XML.
    """
    path = Path(file_path)
    try:
        tree = ET.parse(str(path))
        root = tree.getroot()
    except ET.ParseError as e:
        raise ValueError(f"XML parse error in {path.name}: {e}") from e

    tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag

    if tag in ("Invoice", "CreditNote", "DebitNote"):
        text = _parse_ubl(root)
        logger.info("xml_parser: parsed UBL %s (%d chars)", tag, len(text))
        return text

    # Generic fallback: walk all elements and collect text
    logger.warning("xml_parser: unknown root tag '%s' — using generic text extraction", tag)
    parts: list[str] = []
    for el in root.iter():
        local = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        text_val = (el.text or "").strip()
        if text_val:
            parts.append(f"{local}: {text_val}")
    return "\n".join(parts)
