"""Generate 2025 Via A test fixtures, mirroring the structure of 2024.

The 2024 folder under ``via_a_test_files/`` contains 7 synthetic PDFs that
drive Via A end-to-end testing:

* ``extracto_bancario_2024_06.pdf`` + ``2024_12.pdf``
* ``factura_compra_2024_06.pdf`` + ``2024_12.pdf``
* ``recibo_caja_2024_06.pdf`` + ``2024_12.pdf``
* ``recibo_caja_cobro_2024_06.pdf``

This script writes the matching 2025 set in ``via_a_test_files/2025/`` with
the same amounts, counterparties, and concepts so two-period (prior vs
current) workflows can be exercised. Document numbers and CUFE codes
are bumped so the pipeline doesn't dedupe against 2024 ingests.

Run from repo root:
    uv run python scripts/dev/generate_via_a_test_pdfs_2025.py
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

OUT_DIR = Path(__file__).resolve().parents[2] / "via_a_test_files" / "2025"
OUT_DIR.mkdir(parents=True, exist_ok=True)


_styles = getSampleStyleSheet()
TITLE = ParagraphStyle(
    "Title",
    parent=_styles["Normal"],
    fontName="Helvetica-Bold",
    fontSize=14,
    leading=16,
    spaceAfter=4,
)
SUBTITLE = ParagraphStyle(
    "Subtitle",
    parent=_styles["Normal"],
    fontName="Helvetica-Bold",
    fontSize=11,
    leading=13,
    spaceAfter=4,
)
META = ParagraphStyle(
    "Meta",
    parent=_styles["Normal"],
    fontName="Helvetica",
    fontSize=9,
    leading=11,
)
META_BOLD = ParagraphStyle(
    "MetaBold",
    parent=_styles["Normal"],
    fontName="Helvetica-Bold",
    fontSize=9,
    leading=11,
)
SECTION_HEADER = ParagraphStyle(
    "SectionHeader",
    parent=_styles["Normal"],
    fontName="Helvetica-Bold",
    fontSize=10,
    leading=12,
    spaceBefore=6,
    spaceAfter=4,
)


def _money(n: float) -> str:
    """Colombian-style money formatter: $ 1.234.567,89."""
    formatted = f"{abs(n):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    sign = "-" if n < 0 else ""
    return f"$ {sign}{formatted}"


# ─── Extracto bancario ───────────────────────────────────────────────────────


def build_extracto_bancario(period_year: int, period_month: int) -> None:
    mm = f"{period_month:02d}"
    yyyy = period_year
    pstart = f"{yyyy}-{mm}-01"
    pend = f"{yyyy}-{mm}-30"
    path = OUT_DIR / f"extracto_bancario_{yyyy}_{mm}.pdf"

    doc = SimpleDocTemplate(
        str(path),
        pagesize=LETTER,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
    )
    flow = [
        Paragraph("BANCO DE BOGOTA", TITLE),
        Paragraph("Estado de Cuenta - Cuenta Corriente", META),
        Spacer(1, 4),
        Paragraph("EXTRACTO BANCARIO", SUBTITLE),
    ]

    meta_rows = [
        ["Entidad financiera:", "Banco de Bogota"],
        ["Numero de cuenta:", "452-123456-7 (Corriente)"],
        ["Tipo de cuenta:", "corriente"],
        ["Titular:", "CONSTRUCTORA ANDINA S.A.S."],
        ["NIT titular:", "901.234.567-8"],
        ["Periodo:", f"{pstart} al {pend}"],
        ["Periodo inicio:", pstart],
        ["Periodo fin:", pend],
        ["Saldo inicial:", _money(5_000_000)],
    ]
    meta_tbl = Table(meta_rows, colWidths=[1.6 * inch, 4.5 * inch])
    meta_tbl.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 9),
                ("FONT", (1, 0), (1, -1), "Helvetica", 9),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    flow.append(meta_tbl)
    flow.append(Spacer(1, 8))

    movs = [
        # (fecha, descripcion, debito, credito)
        (
            f"{yyyy}-{mm}-05",
            "TRANSFERENCIA INVERSIONES TORRES LTDA ABONO CARTER",
            0,
            4_800_000,
        ),
        (
            f"{yyyy}-{mm}-12",
            "PAGO PROVEEDOR SUMINISTROS TECNICOS LTDA NIT 83012",
            1_200_000,
            0,
        ),
        (
            f"{yyyy}-{mm}-12",
            "GMF GRAVAMEN MOVIMIENTOS FINANCIEROS 4X1000",
            4_800,
            0,
        ),
        (
            f"{yyyy}-{mm}-30",
            "ABONO INTERESES CORRIENTES CUENTA AHORROS",
            0,
            12_500,
        ),
    ]

    saldo = 5_000_000
    rows = [["Fecha", "Descripcion", "Debito", "Credito", "Saldo"]]
    total_deb = 0
    total_cre = 0
    for fecha, desc, debito, credito in movs:
        saldo = saldo - debito + credito
        rows.append(
            [
                fecha,
                desc,
                _money(debito) if debito else "",
                _money(credito) if credito else "",
                _money(saldo),
            ]
        )
        total_deb += debito
        total_cre += credito
    rows.append(
        [
            "TOTALES:",
            "",
            f"Total debitos: {_money(total_deb)}",
            f"Total creditos: {_money(total_cre)}",
            "",
        ]
    )
    tbl = Table(
        rows,
        colWidths=[
            0.85 * inch,
            3.0 * inch,
            1.05 * inch,
            1.05 * inch,
            1.05 * inch,
        ],
        repeatRows=1,
    )
    tbl.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
                ("FONT", (0, 1), (-1, -1), "Helvetica", 8.5),
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.black),
                ("LINEABOVE", (0, -1), (-1, -1), 0.4, colors.black),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
            ]
        )
    )
    flow.append(tbl)
    flow.append(Spacer(1, 6))

    flow.append(Paragraph(f"<b>SALDO FINAL:</b> {_money(saldo)}", META_BOLD))
    flow.append(Spacer(1, 4))
    flow.append(Paragraph(f"Total creditos del periodo: {_money(total_cre)}", META))
    flow.append(Paragraph(f"Total debitos del periodo: {_money(total_deb)}", META))
    flow.append(Paragraph(f"Variacion neta: {_money(total_cre - total_deb)}", META))
    flow.append(Paragraph(f"Periodo contable: {pstart} / {pend}", META))

    doc.build(flow)
    print(f"WROTE  {path}")


# ─── Factura de compra ───────────────────────────────────────────────────────


def build_factura_compra(period_year: int, period_month: int, fe_number: str) -> None:
    mm = f"{period_month:02d}"
    yyyy = period_year
    fecha = f"{yyyy}-{mm}-10"
    path = OUT_DIR / f"factura_compra_{yyyy}_{mm}.pdf"

    cufe = f"fe{yyyy}{mm}abc123def456ghi789jkl012mno345pqr678stu901vwx"

    doc = SimpleDocTemplate(
        str(path),
        pagesize=LETTER,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
    )
    flow = [
        Paragraph("CONSULTORES ESPECIALIZADOS S.A.S.", TITLE),
        Paragraph("NIT: 800.456.123-5 | Regimen: Responsable de IVA", META),
        Paragraph("Carrera 15 No. 88-64, Bogota D.C.    Tel: 601-3456789", META),
        Spacer(1, 4),
        Paragraph(f"FACTURA ELECTRONICA DE VENTA No. {fe_number}", SUBTITLE),
        Paragraph(f"Fecha de emision: {fecha}", META),
        Paragraph(f"CUFE: {cufe}", META),
        Spacer(1, 6),
        Paragraph("<b>ADQUIRIENTE</b>", META_BOLD),
    ]
    adq_rows = [
        ["Razon social:", "CONSTRUCTORA ANDINA S.A.S."],
        ["NIT:", "901.234.567-8"],
        ["Direccion:", "Calle 72 No. 12-34, Bogota D.C."],
        ["Regimen:", "Responsable de IVA"],
    ]
    adq = Table(adq_rows, colWidths=[1.4 * inch, 4.8 * inch])
    adq.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 9),
                ("FONT", (1, 0), (1, -1), "Helvetica", 9),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    flow.append(adq)
    flow.append(Spacer(1, 6))
    flow.append(Paragraph("<b>DETALLE DE SERVICIOS</b>", META_BOLD))

    detail_rows = [
        ["#", "Descripcion", "Unidad", "Cant.", "V. Unitario", "Total"],
        [
            "1",
            f"Consultoria y asesoria tecnica especializada\nen gestion de proyectos - periodo {yyyy}-{mm}",
            "HRS",
            "35",
            _money(60_000),
            _money(2_100_000),
        ],
    ]
    detail = Table(
        detail_rows,
        colWidths=[
            0.35 * inch,
            3.1 * inch,
            0.65 * inch,
            0.55 * inch,
            1.05 * inch,
            1.15 * inch,
        ],
        repeatRows=1,
    )
    detail.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
                ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.black),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
            ]
        )
    )
    flow.append(detail)
    flow.append(Spacer(1, 6))

    totals_rows = [
        ["Subtotal (base gravable IVA):", _money(2_100_000)],
        ["IVA 19%:", _money(399_000)],
        ["TOTAL A PAGAR:", _money(2_499_000)],
    ]
    tot = Table(totals_rows, colWidths=[5.0 * inch, 1.6 * inch])
    tot.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (0, -2), "Helvetica", 9),
                ("FONT", (1, 0), (1, -2), "Helvetica", 9),
                ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 10),
                ("LINEABOVE", (0, -1), (-1, -1), 0.6, colors.black),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    flow.append(tot)
    flow.append(Spacer(1, 6))

    flow.append(
        Paragraph(
            f"Forma de pago: Credito 30 dias    |    Periodo contable: {yyyy}-{mm}",
            META,
        )
    )
    flow.append(Paragraph("Resolucion DIAN No. 18764030947208 de 2025", META))
    flow.append(
        Paragraph(
            "Total debitos esperados: $ 2.100.000,00 + $ 399.000,00 = $ 2.499.000,00",
            META,
        )
    )
    flow.append(Paragraph("Total creditos esperados: $ 2.499.000,00", META))

    doc.build(flow)
    print(f"WROTE  {path}")


# ─── Recibo de caja (simple layout, like recibo_caja_2024_06.pdf) ───────────


def build_recibo_caja_simple(
    period_year: int, period_month: int, rc_number: str
) -> None:
    mm = f"{period_month:02d}"
    yyyy = period_year
    fecha = f"{yyyy}-{mm}-14"
    path = OUT_DIR / f"recibo_caja_{yyyy}_{mm}.pdf"

    doc = SimpleDocTemplate(
        str(path),
        pagesize=LETTER,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
    )
    flow = [
        Paragraph("CONSTRUCTORA ANDINA S.A.S.", TITLE),
        Paragraph("NIT: 901.234.567-8 | Bogota D.C.", META),
        Paragraph("Calle 72 No. 12-34, Bogota D.C.", META),
        Spacer(1, 4),
        Paragraph(f"RECIBO DE CAJA No. {rc_number}", SUBTITLE),
        Paragraph(f"Fecha: {fecha}", META),
        Spacer(1, 4),
    ]

    meta_rows = [
        ["Recibido de:", "INVERSIONES TORRES LTDA"],
        ["NIT / C.C.:", "830.456.789-2"],
        ["Concepto:", f"Pago factura de venta FV-{yyyy}-0389"],
        ["Forma de pago:", "Transferencia bancaria"],
        ["Tipo de recibo:", "cobro_cartera"],
        ["Referencia factura:", f"FV-{yyyy}-0389"],
    ]
    meta_tbl = Table(meta_rows, colWidths=[1.6 * inch, 4.8 * inch])
    meta_tbl.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 9),
                ("FONT", (1, 0), (1, -1), "Helvetica", 9),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    flow.append(meta_tbl)
    flow.append(Spacer(1, 6))

    flow.append(
        Paragraph(
            "Valor en letras: TRES MILLONES QUINIENTOS MIL PESOS MONEDA CORRIENTE",
            META,
        )
    )
    flow.append(Spacer(1, 4))
    flow.append(Paragraph(f"<b>TOTAL RECIBIDO:</b> {_money(3_500_000)}", META_BOLD))
    flow.append(Spacer(1, 16))
    flow.append(
        Paragraph("___________________________    ___________________________", META)
    )
    flow.append(Paragraph("Firma quien recibe              Firma quien entrega", META))
    flow.append(Spacer(1, 8))
    flow.append(Paragraph(f"Periodo contable: {yyyy}-{mm}", META))

    doc.build(flow)
    print(f"WROTE  {path}")


# ─── Recibo de caja (boxed layout, like recibo_caja_cobro_2024_06.pdf) ──────


def build_recibo_caja_cobro_boxed(
    period_year: int, period_month: int, rc_number: str
) -> None:
    mm = f"{period_month:02d}"
    yyyy = period_year
    fecha = f"{yyyy}-{mm}-14"
    path = OUT_DIR / f"recibo_caja_cobro_{yyyy}_{mm}.pdf"

    doc = SimpleDocTemplate(
        str(path),
        pagesize=LETTER,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
    )
    flow = [
        Paragraph("CONSTRUCTORA ANDINA S.A.S.", TITLE),
        Paragraph("NIT: 901.234.567-8 | Bogotá D.C.", META),
        Paragraph("CALLE 72 No. 12-34, BOGOTÁ", META),
        Spacer(1, 6),
        Paragraph(f"RECIBO DE CAJA No. {rc_number}", SUBTITLE),
        Paragraph(f"Fecha: {fecha}", META),
        Spacer(1, 6),
    ]

    rows = [
        ["Recibido de:", "INVERSIONES TORRES LTDA"],
        ["NIT / C.C.:", "830.456.789-2"],
        ["Valor en letras:", "TRES MILLONES QUINIENTOS MIL PESOS M/CTE"],
        ["Concepto:", f"Pago factura de venta FV-{yyyy}-0389"],
        ["Forma de pago:", "Transferencia bancaria"],
        ["Tipo de recibo:", "cobro_cartera"],
        ["Referencia factura:", f"FV-{yyyy}-0389"],
    ]
    box = Table(rows, colWidths=[1.7 * inch, 4.7 * inch])
    box.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 9.5),
                ("FONT", (1, 0), (1, -1), "Helvetica", 9.5),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    flow.append(box)
    flow.append(Spacer(1, 12))

    flow.append(Paragraph("<b>TOTAL RECIBIDO</b>", SECTION_HEADER))
    tot = Table(
        [["TOTAL", _money(3_500_000)]],
        colWidths=[4.4 * inch, 2.0 * inch],
    )
    tot.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#0A1F44")),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
                ("FONT", (0, 0), (0, 0), "Helvetica-Bold", 13),
                ("FONT", (1, 0), (1, 0), "Helvetica-Bold", 13),
                ("ALIGN", (0, 0), (0, 0), "LEFT"),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ]
        )
    )
    flow.append(tot)
    flow.append(Spacer(1, 24))
    flow.append(Paragraph("_______________________________", META))
    flow.append(Paragraph("Firma responsable", META))
    flow.append(Spacer(1, 6))
    flow.append(Paragraph(f"Período contable: {yyyy}-{mm}", META))

    doc.build(flow)
    print(f"WROTE  {path}")


def main() -> None:
    # Bank statements
    build_extracto_bancario(2025, 6)
    build_extracto_bancario(2025, 12)

    # Purchase invoices
    build_factura_compra(2025, 6, "FE-2025-0891")
    build_factura_compra(2025, 12, "FE-2025-0891")

    # Cash receipts — two layouts
    build_recibo_caja_simple(2025, 6, "RC-2025-0142")
    build_recibo_caja_simple(2025, 12, "RC-2025-0142")
    build_recibo_caja_cobro_boxed(2025, 6, "RC-2025-0142")


if __name__ == "__main__":
    main()
