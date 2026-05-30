"""Generate 4 annual-version PDFs for testing the Vía B annual derivation path.

Designed so BG 2025 → LA 2026 → BG 2026 reconcile at the peso level:

* All account codes are valid PUC (numeric, ≤ 8 digits).
* Double-entry balanced: total debits == total credits across the LA.
* NIC 7 indirect identity cuadra exactly (Δ cash = utilidad + depreciación
  + Δ pasivos operacionales − Δ CxC).

Run from repo root:
    uv run python scripts/dev/generate_annual_test_pdfs.py
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

OUTPUT_DIR = Path(__file__).resolve().parents[2]


# ─── Styling helpers ─────────────────────────────────────────────────────────

_styles = getSampleStyleSheet()
HEADER = ParagraphStyle(
    "Header",
    parent=_styles["Normal"],
    fontName="Helvetica-Bold",
    fontSize=10,
    leading=12,
)
TITLE = ParagraphStyle(
    "Title",
    parent=_styles["Normal"],
    fontName="Helvetica-Bold",
    fontSize=12,
    leading=14,
    spaceAfter=4,
)
META = ParagraphStyle(
    "Meta", parent=_styles["Normal"], fontName="Helvetica", fontSize=9, leading=11
)
META_BOLD = ParagraphStyle(
    "MetaBold",
    parent=_styles["Normal"],
    fontName="Helvetica-Bold",
    fontSize=9,
    leading=11,
)
SECTION = ParagraphStyle(
    "Section",
    parent=_styles["Normal"],
    fontName="Helvetica-Bold",
    fontSize=10,
    leading=12,
    spaceBefore=6,
    spaceAfter=2,
)
ROW = ParagraphStyle(
    "Row", parent=_styles["Normal"], fontName="Helvetica", fontSize=8, leading=10
)


def _fmt(n: float) -> str:
    if n < 0:
        return f"-{abs(n):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _company_header(
    titulo: str,
    periodo_inicio: str,
    periodo_fin: str,
    extra_note: str = "",
):
    flow = [
        Paragraph("---------- SAS", HEADER),
        Paragraph("NIT --------------6", HEADER),
        Paragraph(titulo, TITLE),
        Paragraph(f"<b>Periodo inicio:</b> {periodo_inicio}", META),
        Paragraph(f"<b>Periodo fin:</b> {periodo_fin}", META),
        Paragraph("<b>Periodicidad:</b> anual", META_BOLD),
        Paragraph("Estados Financieros Anuales — Cierre del ejercicio contable", META),
    ]
    if extra_note:
        flow.append(Paragraph(extra_note, META))
    flow.append(Paragraph("Cifras expresadas en pesos colombianos (COP)", META))
    flow.append(Spacer(1, 8))
    return flow


def _build_table(rows, col_widths, header_rows: int = 1):
    tbl = Table(rows, colWidths=col_widths, repeatRows=header_rows)
    tbl.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5),
                ("FONT", (0, 1), (-1, -1), "Helvetica", 8.5),
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.black),
                ("LINEABOVE", (0, -1), (-1, -1), 0.4, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (-1, 1), (-1, -1), "RIGHT"),
                ("ALIGN", (-2, 1), (-2, -1), "RIGHT"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return tbl


# ─── Master data — cuadra por construcción ──────────────────────────────────
#
# BG 2025 (saldos al 31/12/2025). All values in COP.
BG_2025 = [
    # (codigo, nombre, saldo)
    ("11200501", "Bancolombia Cuenta de Ahorros 92967875769", 50_000_000.00),
    ("112501", "Fiducuenta N 4956 Casa Nua Country", 25_000_000.00),
    ("123570", "Titulos Inmobiliarios", 80_000_000.00),
    ("132505", "Cuentas por Cobrar a Socios", 130_000_000.00),
    ("151610", "Oficinas y Locales", 400_000_000.00),
    ("159205", "Depreciacion Acumulada Oficinas", -120_000_000.00),
    ("219520", "Socios o Accionistas", 80_000_000.00),
    ("233595", "Otros Costos y Gastos por Pagar", 15_000_000.00),
    ("240405", "Impuesto de Renta Vigencia Corriente", 5_000_000.00),
    ("310505", "Capital Autorizado", 10_000_000.00),
    ("370505", "Utilidades Acumuladas", 455_000_000.00),
]

# Transactions during 2026 (annual). Each transaction is a balanced
# double-entry pair. Format: (fecha, comprobante, descripcion, [(codigo, debit, credit), ...])
LA_TRANSACTIONS = [
    # T1: Facturacion arriendos del ejercicio + IVA causado
    (
        "2026-03-15",
        "FV-2026",
        "Facturacion arriendos del ejercicio (12 meses)",
        [
            (
                "130505",
                309_400_000.00,
                0.0,
                "900111222-1",
                "Clientes — facturas del año",
            ),
            ("415505", 0.0, 260_000_000.00, "900111222-1", "Ingresos arriendos"),
            ("24080101", 0.0, 49_400_000.00, "900111222-1", "IVA Generado 19%"),
        ],
    ),
    # T2: Cobro a clientes con retenciones tomadas
    (
        "2026-04-20",
        "RC-2026",
        "Recaudo de clientes (con retencion IVA y ICA)",
        [
            ("11200501", 301_000_000.00, 0.0, "900111222-1", "Recaudo neto"),
            (
                "13551702",
                7_410_000.00,
                0.0,
                "900111222-1",
                "Retencion IVA tomada por cliente",
            ),
            (
                "13551802",
                990_000.00,
                0.0,
                "900111222-1",
                "Retencion ICA tomada por cliente",
            ),
            ("130505", 0.0, 309_400_000.00, "900111222-1", "Cancelacion facturas"),
        ],
    ),
    # T3: Nomina anual — gasto reconocido + pago neto + retenciones
    (
        "2026-04-30",
        "CE-NMA",
        "Nomina anual (12 meses)",
        [
            ("510506", 24_000_000.00, 0.0, "", "Sueldos anuales"),
            ("11200501", 0.0, 22_000_000.00, "", "Pago nomina neto"),
            ("237005", 0.0, 800_000.00, "", "Aportes EPS por pagar"),
            ("238030", 0.0, 1_200_000.00, "", "Fondo cesantias por pagar"),
        ],
    ),
    # T4: Provision de vacaciones (gasto + pasivo)
    (
        "2026-12-31",
        "CIE-VAC",
        "Provision vacaciones consolidadas",
        [
            ("510539", 2_000_000.00, 0.0, "", "Gasto vacaciones"),
            ("252501", 0.0, 2_000_000.00, "", "Provision vacaciones"),
        ],
    ),
    # T5: Causacion ICA del ejercicio
    (
        "2026-12-31",
        "CIE-ICA",
        "Causacion ICA del ejercicio",
        [
            ("511505", 2_500_000.00, 0.0, "", "Gasto ICA"),
            ("23451004", 0.0, 2_500_000.00, "", "ICA por pagar"),
        ],
    ),
    # T6: Pago parcial ICA causado
    (
        "2026-11-15",
        "CE-ICA",
        "Pago bimestre ICA",
        [
            ("23451004", 1_800_000.00, 0.0, "", "Pago ICA"),
            ("11200501", 0.0, 1_800_000.00, "", "Desembolso ICA"),
        ],
    ),
    # T7: Pago afiliaciones gremiales
    (
        "2026-06-20",
        "CE-AFL",
        "Afiliaciones y sostenimiento",
        [
            ("512510", 4_800_000.00, 0.0, "830555111-3", "Afiliaciones gremiales"),
            ("11200501", 0.0, 4_800_000.00, "830555111-3", "Pago afiliaciones"),
        ],
    ),
    # T8: Honorarios contables y asesoria pagados
    (
        "2026-07-10",
        "CE-HON",
        "Honorarios asesoria financiera anual",
        [
            ("511030", 10_500_000.00, 0.0, "900222333-2", "Honorarios"),
            ("11200501", 0.0, 10_500_000.00, "900222333-2", "Pago honorarios"),
        ],
    ),
    # T9: Depreciacion anual oficinas
    (
        "2026-12-31",
        "CIE-DEP",
        "Depreciacion anual oficinas (vida util 40 anios)",
        [
            ("516005", 40_000_000.00, 0.0, "", "Gasto depreciacion oficinas"),
            ("159205", 0.0, 40_000_000.00, "", "Depreciacion acumulada"),
        ],
    ),
    # T10: Rendimientos financieros fiducuenta
    (
        "2026-12-31",
        "ND-FID",
        "Rendimientos financieros fiducuenta",
        [
            ("112501", 1_200_000.00, 0.0, "860034313-7", "Rendimientos abonados"),
            ("421005", 0.0, 1_200_000.00, "860034313-7", "Ingresos financieros"),
        ],
    ),
    # T11: Gastos bancarios mensuales (consolidado anual)
    (
        "2026-12-31",
        "CE-BNK",
        "Gastos bancarios del ejercicio",
        [
            ("530505", 800_000.00, 0.0, "", "Gastos bancarios"),
            ("11200501", 0.0, 800_000.00, "", "Cargos bancarios"),
        ],
    ),
    # T12: Gastos extraordinarios no deducibles (multas, etc.)
    (
        "2026-08-15",
        "CE-EXT",
        "Gastos extraordinarios no deducibles en renta",
        [
            ("53152001", 1_500_000.00, 0.0, "", "Gastos no deducibles"),
            ("11200501", 0.0, 1_500_000.00, "", "Pago"),
        ],
    ),
    # T13: Intereses bancarios pagados
    (
        "2026-12-31",
        "CE-INT",
        "Intereses bancarios",
        [
            ("530520", 200_000.00, 0.0, "", "Intereses pagados"),
            ("11200501", 0.0, 200_000.00, "", "Cargo intereses"),
        ],
    ),
    # T14: Servicios tecnicos a credito (proveedor por pagar)
    (
        "2026-12-15",
        "FC-2026",
        "Servicios tecnicos (causados, no pagados)",
        [
            ("511525", 3_000_000.00, 0.0, "900444555-6", "Servicios tecnicos"),
            ("220505", 0.0, 3_000_000.00, "900444555-6", "Cuenta por pagar"),
        ],
    ),
]

ACCOUNT_NAMES = {
    "11200501": "Bancolombia Cuenta de Ahorros 92967875769",
    "112501": "Fiducuenta N 4956 Casa Nua Country",
    "123570": "Titulos Inmobiliarios",
    "130505": "Clientes Nacionales",
    "132505": "Cuentas por Cobrar a Socios",
    "13551702": "Retencion IVA por Venta de Servicios",
    "13551802": "Retencion ICA por Venta de Servicios",
    "151610": "Oficinas y Locales",
    "159205": "Depreciacion Acumulada Oficinas",
    "219520": "Socios o Accionistas",
    "220505": "Proveedores Nacionales",
    "233595": "Otros Costos y Gastos por Pagar",
    "23451004": "Impuesto de Industria y Comercio por Pagar",
    "237005": "Aportes EPS",
    "238030": "Fondos de Cesantias y Pensiones",
    "240405": "Impuesto de Renta Vigencia Corriente",
    "24080101": "IVA Generado Ventas 19%",
    "252501": "Vacaciones Consolidadas",
    "310505": "Capital Autorizado",
    "360505": "Utilidad del Ejercicio",
    "370505": "Utilidades Acumuladas",
    "415505": "Arrendamientos de Bienes Inmuebles",
    "421005": "Intereses Financieros",
    "510506": "Sueldos",
    "510539": "Vacaciones",
    "511030": "Asesoria Financiera",
    "511505": "Industria y Comercio (gasto)",
    "511525": "Servicios Tecnicos",
    "512510": "Afiliaciones y Sostenimiento",
    "516005": "Depreciacion Construcciones y Edificaciones",
    "530505": "Gastos Bancarios",
    "530520": "Intereses",
    "53152001": "Gastos No Descontables en Renta",
}


def _starting_balance(code: str) -> float:
    for c, _, s in BG_2025:
        if c == code:
            return s
    return 0.0


def _compute_account_movements():
    """Return ordered list of (codigo, opening, [movements], closing) covering
    every account that appears in BG 2025 or in any LA transaction."""
    codes_in_tx: list[str] = []
    seen: set[str] = set()
    for _, _, _, lines in LA_TRANSACTIONS:
        for code, _, _, _, _ in lines:
            if code not in seen:
                seen.add(code)
                codes_in_tx.append(code)

    bg_codes = [c for c, _, _ in BG_2025]
    # Preserve BG order first, append new codes from LA after.
    all_codes: list[str] = list(bg_codes)
    for c in codes_in_tx:
        if c not in all_codes:
            all_codes.append(c)

    results = []
    for code in all_codes:
        opening = _starting_balance(code)
        movements = []
        for fecha, comp, _desc, lines in LA_TRANSACTIONS:
            for cc, debito, credito, tercero, descripcion in lines:
                if cc != code:
                    continue
                movements.append((fecha, comp, tercero, descripcion, debito, credito))
        balance = opening
        for _, _, _, _, debito, credito in movements:
            # Sign convention: class 1 + class 5/6/7 are debit-natured;
            # class 2, 3, 4 are credit-natured. Negative balances (like 159
            # contra-asset) just propagate as movement deltas.
            balance += debito - credito if code[0] in "1567" else credito - debito
        results.append((code, opening, movements, balance))
    return results


# ─── PDF builders ───────────────────────────────────────────────────────────


def build_bg_2025_pdf() -> None:
    path = OUTPUT_DIR / "Balance General ANUAL 2025.pdf"
    doc = SimpleDocTemplate(
        str(path),
        pagesize=LETTER,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
    )
    flow = _company_header(
        "Balance General — Cierre Anual",
        "2025-01-01",
        "2025-12-31",
        "Cierre del ejercicio al 31 de diciembre de 2025",
    )

    rows = [["Codigo PUC", "Cuenta", "Saldo"]]
    ta = tp = tpa = 0.0
    for code, name, saldo in BG_2025:
        rows.append([code, name, _fmt(saldo)])
        if code[0] == "1":
            ta += saldo
        elif code[0] == "2":
            tp += saldo
        elif code[0] == "3":
            tpa += saldo
    rows.append(["", "TOTAL ACTIVOS", _fmt(ta)])
    rows.append(["", "TOTAL PASIVOS", _fmt(tp)])
    rows.append(["", "TOTAL PATRIMONIO", _fmt(tpa)])
    rows.append(["", "TOTAL PASIVO + PATRIMONIO", _fmt(tp + tpa)])

    flow.append(Paragraph("Cuentas del Balance General (PUC clases 1, 2, 3)", SECTION))
    flow.append(_build_table(rows, col_widths=[1.0 * inch, 4.5 * inch, 1.7 * inch]))
    flow.append(Spacer(1, 8))
    flow.append(
        Paragraph(
            f"Ecuacion contable: Activo {_fmt(ta)} = Pasivo {_fmt(tp)} + Patrimonio {_fmt(tpa)}",
            META_BOLD,
        )
    )
    doc.build(flow)
    print(f"WROTE  {path}")
    return ta, tp, tpa


def build_bg_2026_pdf(closing_balances: dict[str, float]) -> None:
    path = OUTPUT_DIR / "Balance General ANUAL 2026.pdf"
    doc = SimpleDocTemplate(
        str(path),
        pagesize=LETTER,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
    )
    flow = _company_header(
        "Balance General — Cierre Anual",
        "2026-01-01",
        "2026-12-31",
        "Cierre del ejercicio al 31 de diciembre de 2026",
    )

    # Filter to BG-relevant classes (1, 2, 3) and include utilidad del
    # ejercicio (360505), which we compute below.
    rows = [["Codigo PUC", "Cuenta", "Saldo"]]
    ta = tp = tpa = 0.0
    utilidad = sum(
        closing_balances[c] for c in closing_balances if c.startswith("4")
    ) - sum(closing_balances[c] for c in closing_balances if c.startswith("5"))
    closing_balances["360505"] = utilidad

    bg_codes = sorted(
        [c for c in closing_balances if c[0] in "123" and closing_balances[c] != 0]
    )
    for code in bg_codes:
        saldo = closing_balances[code]
        rows.append([code, ACCOUNT_NAMES.get(code, code), _fmt(saldo)])
        if code[0] == "1":
            ta += saldo
        elif code[0] == "2":
            tp += saldo
        elif code[0] == "3":
            tpa += saldo
    rows.append(["", "TOTAL ACTIVOS", _fmt(ta)])
    rows.append(["", "TOTAL PASIVOS", _fmt(tp)])
    rows.append(["", "TOTAL PATRIMONIO", _fmt(tpa)])
    rows.append(["", "TOTAL PASIVO + PATRIMONIO", _fmt(tp + tpa)])

    flow.append(Paragraph("Cuentas del Balance General (PUC clases 1, 2, 3)", SECTION))
    flow.append(_build_table(rows, col_widths=[1.0 * inch, 4.5 * inch, 1.7 * inch]))
    flow.append(Spacer(1, 8))
    flow.append(
        Paragraph(
            f"Ecuacion contable: Activo {_fmt(ta)} = Pasivo {_fmt(tp)} + Patrimonio {_fmt(tpa)}",
            META_BOLD,
        )
    )
    doc.build(flow)
    print(f"WROTE  {path}  (activos {_fmt(ta)} / pasivos+patrimonio {_fmt(tp + tpa)})")


def build_er_2026_pdf(closing_balances: dict[str, float]) -> None:
    path = OUTPUT_DIR / "Estado de Resultados ANUAL 2026.pdf"
    doc = SimpleDocTemplate(
        str(path),
        pagesize=LETTER,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
    )
    flow = _company_header(
        "Estado de Resultados — Cierre Anual",
        "2026-01-01",
        "2026-12-31",
        "Resultado integral del ejercicio del 1 de enero al 31 de diciembre de 2026",
    )

    ingresos_rows = [["Codigo PUC", "Cuenta", "Valor"]]
    total_ing = 0.0
    for code in sorted(c for c in closing_balances if c.startswith("4")):
        v = closing_balances[code]
        if v == 0:
            continue
        ingresos_rows.append([code, ACCOUNT_NAMES.get(code, code), _fmt(v)])
        total_ing += v
    ingresos_rows.append(["", "TOTAL INGRESOS", _fmt(total_ing)])

    gastos_rows = [["Codigo PUC", "Cuenta", "Valor"]]
    total_gas = 0.0
    for code in sorted(c for c in closing_balances if c.startswith("5")):
        v = closing_balances[code]
        if v == 0:
            continue
        gastos_rows.append([code, ACCOUNT_NAMES.get(code, code), _fmt(v)])
        total_gas += v
    gastos_rows.append(["", "TOTAL GASTOS", _fmt(total_gas)])

    utilidad = total_ing - total_gas

    flow.append(Paragraph("INGRESOS (PUC clase 4)", SECTION))
    flow.append(
        _build_table(ingresos_rows, col_widths=[1.0 * inch, 4.5 * inch, 1.7 * inch])
    )
    flow.append(Spacer(1, 6))
    flow.append(Paragraph("GASTOS (PUC clase 5)", SECTION))
    flow.append(
        _build_table(gastos_rows, col_widths=[1.0 * inch, 4.5 * inch, 1.7 * inch])
    )
    flow.append(Spacer(1, 8))
    flow.append(
        Paragraph(
            f"Resultado del Ejercicio: Ingresos {_fmt(total_ing)} - Gastos {_fmt(total_gas)} = "
            f"<b>Utilidad Neta {_fmt(utilidad)}</b>",
            META_BOLD,
        )
    )
    doc.build(flow)
    print(f"WROTE  {path}  (utilidad {_fmt(utilidad)})")


def build_la_2026_pdf(movements):
    path = OUTPUT_DIR / "Libro Auxiliar ANUAL 2026.pdf"
    doc = SimpleDocTemplate(
        str(path),
        pagesize=LETTER,
        leftMargin=0.5 * inch,
        rightMargin=0.5 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
    )
    flow = _company_header(
        "Libro Auxiliar - Mayor y Balances por Cuenta PUC",
        "2026-01-01",
        "2026-12-31",
        "Libro auxiliar comprensivo del ejercicio anual 2026 (clases 1-7 del PUC)",
    )

    total_debits = 0.0
    total_credits = 0.0
    for code, opening, movs, closing in movements:
        flow.append(
            Paragraph(
                f"<b>Cuenta PUC: {code} - {ACCOUNT_NAMES.get(code, code)}</b>", SECTION
            )
        )
        flow.append(Paragraph(f"Saldo inicial: {_fmt(opening)}", ROW))
        if movs:
            rows = [
                ["Fecha", "Comprob.", "Tercero NIT", "Descripcion", "Debito", "Credito"]
            ]
            for fecha, comp, tercero, descripcion, debito, credito in movs:
                rows.append(
                    [
                        fecha,
                        comp,
                        tercero,
                        descripcion,
                        _fmt(debito) if debito else "0,00",
                        _fmt(credito) if credito else "0,00",
                    ]
                )
                total_debits += debito
                total_credits += credito
            tbl = Table(
                rows,
                colWidths=[
                    0.85 * inch,
                    0.7 * inch,
                    0.95 * inch,
                    3.0 * inch,
                    0.95 * inch,
                    0.95 * inch,
                ],
                repeatRows=1,
            )
            tbl.setStyle(
                TableStyle(
                    [
                        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5),
                        ("FONT", (0, 1), (-1, -1), "Helvetica", 7.5),
                        ("LINEBELOW", (0, 0), (-1, 0), 0.4, colors.black),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("ALIGN", (-1, 1), (-1, -1), "RIGHT"),
                        ("ALIGN", (-2, 1), (-2, -1), "RIGHT"),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
                        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                    ]
                )
            )
            flow.append(tbl)
        flow.append(Paragraph(f"<b>Saldo final: {_fmt(closing)}</b>", ROW))
        flow.append(Spacer(1, 4))

    flow.append(Spacer(1, 8))
    flow.append(
        Paragraph(
            f"<b>Comprobacion partida doble</b> — Total debitos: {_fmt(total_debits)} | "
            f"Total creditos: {_fmt(total_credits)} | Diferencia: {_fmt(total_debits - total_credits)}",
            META_BOLD,
        )
    )
    doc.build(flow)
    print(
        f"WROTE  {path}  (deb {_fmt(total_debits)} / cre {_fmt(total_credits)} → diff {_fmt(total_debits - total_credits)})"
    )


def main() -> None:
    movements = _compute_account_movements()
    closing_by_code = {code: closing for code, _, _, closing in movements}

    build_bg_2025_pdf()
    build_la_2026_pdf(movements)
    build_er_2026_pdf(closing_by_code)
    build_bg_2026_pdf(closing_by_code)


if __name__ == "__main__":
    main()
