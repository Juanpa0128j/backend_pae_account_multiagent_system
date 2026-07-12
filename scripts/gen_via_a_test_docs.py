"""
Genera PDFs de prueba para Via A con datos que garantizan partida doble.

Usa canvas directo de ReportLab con texto embebido en el PDF para que
LlamaCloud pueda extraer el contenido correctamente.

Documentos por periodo:
  1. Factura de Compra con IVA 19%
     D 511505 (Honorarios)       2.100.000
     D 240802 (IVA descontable)    399.000
     C 220505 (CxP)              2.499.000

  2. Recibo de Caja - cobro de cartera
     D 110505 (Caja)             3.500.000
     C 130505 (CxC clientes)     3.500.000

  3. Extracto Bancario (4 movimientos)
     D/C 111005 (Banco) vs contrapartidas

Uso:
    uv run --active python scripts/gen_via_a_test_docs.py

Salida: scripts/test_pdfs/via_a/
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas

OUT_DIR = Path(__file__).parent / "test_pdfs" / "via_a"
OUT_DIR.mkdir(parents=True, exist_ok=True)

W, H = letter  # 612 x 792 pt


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def fmt(value) -> str:
    d = Decimal(str(value))
    s = f"{d:,.2f}"
    # Colombian format: . for thousands, , for decimals  -> swap
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"$ {s}"


class Doc:
    """Thin wrapper around ReportLab canvas for line-by-line layout."""

    def __init__(self, path: Path):
        self.c = canvas.Canvas(str(path), pagesize=letter)
        self.y = H - 2 * cm
        self.path = path

    # -- font helpers -------------------------------------------------------

    def bold(self, size: int = 10):
        self.c.setFont("Helvetica-Bold", size)

    def normal(self, size: int = 9):
        self.c.setFont("Helvetica", size)

    def mono(self, size: int = 8):
        self.c.setFont("Courier", size)

    # -- drawing ------------------------------------------------------------

    def text(self, x_cm: float, text: str, size: int = 9, bold: bool = False):
        fn = "Helvetica-Bold" if bold else "Helvetica"
        self.c.setFont(fn, size)
        self.c.drawString(x_cm * cm, self.y, text)

    def text_right(self, x_cm: float, text: str, size: int = 9, bold: bool = False):
        fn = "Helvetica-Bold" if bold else "Helvetica"
        self.c.setFont(fn, size)
        self.c.drawRightString(x_cm * cm, self.y, text)

    def nl(self, lines: float = 1.0):
        self.y -= lines * 14

    def line(self, x1=1.8, x2=19.5):
        self.c.setLineWidth(0.5)
        self.c.line(x1 * cm, self.y, x2 * cm, self.y)
        self.nl(0.4)

    def thick_line(self, x1=1.8, x2=19.5):
        self.c.setLineWidth(1.5)
        self.c.line(x1 * cm, self.y, x2 * cm, self.y)
        self.nl(0.6)

    def row(self, cols: list[tuple[float, str]], size: int = 9, bold: bool = False):
        """Draw a row of (x_cm, text) pairs on the current y."""
        fn = "Helvetica-Bold" if bold else "Helvetica"
        self.c.setFont(fn, size)
        for x, t in cols:
            self.c.drawString(x * cm, self.y, t)
        self.nl()

    def row_right(
        self,
        cols_left: list[tuple[float, str]],
        cols_right: list[tuple[float, str]],
        size: int = 9,
        bold: bool = False,
    ):
        fn = "Helvetica-Bold" if bold else "Helvetica"
        self.c.setFont(fn, size)
        for x, t in cols_left:
            self.c.drawString(x * cm, self.y, t)
        for x, t in cols_right:
            self.c.drawRightString(x * cm, self.y, t)
        self.nl()

    def save(self):
        self.c.save()
        print(f"  OK  {self.path.name}  ({self.path.stat().st_size // 1024} KB)")


# ---------------------------------------------------------------------------
# 1. Factura de Compra
# ---------------------------------------------------------------------------


def gen_factura_compra(period: str) -> Path:
    """
    Factura electronica de venta (recibida como compra por la empresa).
    Subtotal sin IVA : 2.100.000
    IVA 19%          :   399.000
    Total            : 2.499.000
    """
    year, month = period.split("-")
    fecha = f"{period}-10"
    consecutivo = f"FE-{year}-0891"
    cufe = f"fe{year}{month}abc123def456ghi789jkl012mno345pqr678stu901vwx"
    subtotal = Decimal("2100000")
    iva = Decimal("399000")
    total = subtotal + iva
    path = OUT_DIR / f"factura_compra_{period.replace('-', '_')}.pdf"
    d = Doc(path)

    # --- Encabezado emisor ---
    d.text(1.8, "CONSULTORES ESPECIALIZADOS S.A.S.", size=13, bold=True)
    d.nl()
    d.text(1.8, "NIT: 800.456.123-5   |   Regimen: Responsable de IVA")
    d.nl()
    d.text(1.8, "Carrera 15 No. 88-64, Bogota D.C.   Tel: 601-3456789")
    d.nl()
    d.thick_line()

    d.text(1.8, f"FACTURA ELECTRONICA DE VENTA No. {consecutivo}", size=12, bold=True)
    d.nl()
    d.text(1.8, f"Fecha de emision: {fecha}")
    d.nl()
    d.text(1.8, f"CUFE: {cufe}")
    d.nl(1.2)

    # --- Adquiriente ---
    d.text(1.8, "ADQUIRIENTE", bold=True)
    d.nl()
    d.line()
    d.row([(1.8, "Razon social:"), (5.5, "CONSTRUCTORA ANDINA S.A.S.")])
    d.row([(1.8, "NIT:"), (5.5, "901.234.567-8")])
    d.row([(1.8, "Direccion:"), (5.5, "Calle 72 No. 12-34, Bogota D.C.")])
    d.row([(1.8, "Regimen:"), (5.5, "Responsable de IVA")])
    d.nl(0.5)

    # --- Items ---
    d.text(1.8, "DETALLE DE SERVICIOS", bold=True)
    d.nl()
    d.line()
    d.row(
        [
            (1.8, "#"),
            (3.0, "Descripcion"),
            (12.0, "Unidad"),
            (14.0, "Cant."),
            (16.0, "V. Unitario"),
            (18.5, "Total"),
        ],
        bold=True,
    )
    d.line()
    d.row(
        [
            (1.8, "1"),
            (3.0, "Consultoria y asesoria tecnica especializada"),
            (12.0, "HRS"),
            (14.0, "35"),
            (16.0, fmt(Decimal("60000"))),
            (18.5, fmt(subtotal)),
        ]
    )
    d.row([(3.0, "en gestion de proyectos - periodo " + period)])
    d.nl(0.3)
    d.line()

    # --- Totales ---
    d.row_right([(1.8, "Subtotal (base gravable IVA):")], [(19.5, fmt(subtotal))])
    d.row_right([(1.8, "IVA 19%:")], [(19.5, fmt(iva))])
    d.thick_line()
    d.row_right([(1.8, "TOTAL A PAGAR:")], [(19.5, fmt(total))], bold=True)
    d.nl(1.5)

    # --- Pie ---
    d.text(1.8, f"Forma de pago: Credito 30 dias   |   Periodo contable: {period}")
    d.nl()
    d.text(1.8, "Resolucion DIAN No. 18764030947208 de 2024")
    d.nl()
    d.text(1.8, f"Total debitos esperados: {fmt(subtotal)} + {fmt(iva)} = {fmt(total)}")
    d.nl()
    d.text(1.8, f"Total creditos esperados: {fmt(total)}")

    d.save()
    return path


# ---------------------------------------------------------------------------
# 2. Recibo de Caja
# ---------------------------------------------------------------------------


def gen_recibo_caja(period: str) -> Path:
    """
    Recibo de caja por cobro de cartera.
    D 110505 (Caja General)      3.500.000
    C 130505 (Cuentas x Cobrar)  3.500.000
    """
    year, month = period.split("-")
    fecha = f"{period}-14"
    numero = f"RC-{year}-0142"
    valor = Decimal("3500000")
    path = OUT_DIR / f"recibo_caja_{period.replace('-', '_')}.pdf"
    d = Doc(path)

    d.text(1.8, "CONSTRUCTORA ANDINA S.A.S.", size=13, bold=True)
    d.nl()
    d.text(1.8, "NIT: 901.234.567-8   |   Bogota D.C.")
    d.nl()
    d.text(1.8, "Calle 72 No. 12-34, Bogota D.C.")
    d.nl()
    d.thick_line()

    d.text(1.8, f"RECIBO DE CAJA No. {numero}", size=12, bold=True)
    d.nl()
    d.text(1.8, f"Fecha: {fecha}")
    d.nl(1.2)

    d.line()
    d.row([(1.8, "Recibido de:"), (6.0, "INVERSIONES TORRES LTDA")])
    d.row([(1.8, "NIT / C.C.:"), (6.0, "830.456.789-2")])
    d.row([(1.8, "Concepto:"), (6.0, f"Pago factura de venta FV-{year}-0389")])
    d.row([(1.8, "Forma de pago:"), (6.0, "Transferencia bancaria")])
    d.row([(1.8, "Tipo de recibo:"), (6.0, "cobro_cartera")])
    d.row([(1.8, "Referencia factura:"), (6.0, f"FV-{year}-0389")])
    d.nl(0.5)
    d.line()

    d.text(1.8, "Valor en letras: TRES MILLONES QUINIENTOS MIL PESOS MONEDA CORRIENTE")
    d.nl(1.2)

    d.thick_line()
    d.row_right([(1.8, "TOTAL RECIBIDO:")], [(19.5, fmt(valor))], bold=True, size=11)
    d.nl(2.0)

    d.text(1.8, "___________________________           ___________________________")
    d.nl()
    d.text(1.8, "Firma quien recibe                    Firma quien entrega")
    d.nl(1.0)
    d.text(1.8, f"Periodo contable: {period}")

    d.save()
    return path


# ---------------------------------------------------------------------------
# 3. Extracto Bancario
# ---------------------------------------------------------------------------


def gen_extracto_bancario(period: str) -> Path:
    """
    Extracto bancario con 4 movimientos.
    Saldo inicial : 5.000.000
    Movimientos   :
      +4.800.000  abono transferencia (ingreso CxC)
      -1.200.000  pago proveedor
      -    4.800  GMF 4x1000
      +   12.500  intereses
    Saldo final   : 8.607.700
    """
    year, month = period.split("-")
    period_start = f"{period}-01"
    period_end = f"{period}-30"
    saldo_inicial = Decimal("5000000")

    movs = [
        {
            "fecha": f"{period}-05",
            "descripcion": "TRANSFERENCIA INVERSIONES TORRES LTDA ABONO CARTERA",
            "credito": Decimal("4800000"),
            "debito": Decimal("0"),
        },
        {
            "fecha": f"{period}-12",
            "descripcion": "PAGO PROVEEDOR SUMINISTROS TECNICOS LTDA NIT 830123456",
            "credito": Decimal("0"),
            "debito": Decimal("1200000"),
        },
        {
            "fecha": f"{period}-12",
            "descripcion": "GMF GRAVAMEN MOVIMIENTOS FINANCIEROS 4X1000",
            "credito": Decimal("0"),
            "debito": Decimal("4800"),
        },
        {
            "fecha": f"{period}-30",
            "descripcion": "ABONO INTERESES CORRIENTES CUENTA AHORROS",
            "credito": Decimal("12500"),
            "debito": Decimal("0"),
        },
    ]

    total_creditos = sum(m["credito"] for m in movs)
    total_debitos_mov = sum(m["debito"] for m in movs)
    saldo_final = saldo_inicial + total_creditos - total_debitos_mov

    saldo = saldo_inicial
    for m in movs:
        saldo = saldo + m["credito"] - m["debito"]
        m["saldo"] = saldo

    path = OUT_DIR / f"extracto_bancario_{period.replace('-', '_')}.pdf"
    d = Doc(path)

    d.text(1.8, "BANCO DE BOGOTA", size=13, bold=True)
    d.nl()
    d.text(1.8, "Estado de Cuenta - Cuenta Corriente")
    d.nl()
    d.thick_line()

    d.text(1.8, "EXTRACTO BANCARIO", size=12, bold=True)
    d.nl(1.2)

    d.line()
    d.row([(1.8, "Entidad financiera:"), (6.5, "Banco de Bogota")])
    d.row([(1.8, "Numero de cuenta:"), (6.5, "452-123456-7 (Corriente)")])
    d.row([(1.8, "Tipo de cuenta:"), (6.5, "corriente")])
    d.row([(1.8, "Titular:"), (6.5, "CONSTRUCTORA ANDINA S.A.S.")])
    d.row([(1.8, "NIT titular:"), (6.5, "901.234.567-8")])
    d.row([(1.8, "Periodo:"), (6.5, f"{period_start} al {period_end}")])
    d.row([(1.8, "Periodo inicio:"), (6.5, period_start)])
    d.row([(1.8, "Periodo fin:"), (6.5, period_end)])
    d.row_right([(1.8, "Saldo inicial:")], [(19.5, fmt(saldo_inicial))])
    d.nl(0.5)

    # Header tabla movimientos
    d.thick_line()
    d.row(
        [
            (1.8, "Fecha"),
            (4.5, "Descripcion"),
            (13.0, "Debito"),
            (15.8, "Credito"),
            (18.5, "Saldo"),
        ],
        bold=True,
    )
    d.line()

    for m in movs:
        deb_str = fmt(m["debito"]) if m["debito"] > 0 else ""
        cred_str = fmt(m["credito"]) if m["credito"] > 0 else ""
        d.row(
            [
                (1.8, m["fecha"]),
                (4.5, m["descripcion"][:50]),
                (13.0, deb_str),
                (15.8, cred_str),
                (18.5, fmt(m["saldo"])),
            ]
        )

    d.line()
    d.row_right(
        [(1.8, "TOTALES:"), (10.5, "Total debitos:"), (15.8, "Total creditos:")],
        [(13.5, fmt(total_debitos_mov)), (19.5, fmt(total_creditos))],
        bold=True,
    )
    d.nl(0.5)
    d.thick_line()
    d.row_right([(1.8, "SALDO FINAL:")], [(19.5, fmt(saldo_final))], bold=True)
    d.nl(1.5)

    d.text(1.8, f"Total creditos del periodo: {fmt(total_creditos)}")
    d.nl()
    d.text(1.8, f"Total debitos del periodo: {fmt(total_debitos_mov)}")
    d.nl()
    d.text(1.8, f"Variacion neta: {fmt(total_creditos - total_debitos_mov)}")
    d.nl()
    d.text(1.8, f"Periodo contable: {period_start} / {period_end}")

    d.save()
    return path


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import io
    import sys

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    print("Generando documentos de prueba Via A...")
    print(f"Destino: {OUT_DIR}\n")

    for period in ["2024-06", "2024-12"]:
        print(f"-- Periodo {period} --")
        gen_factura_compra(period)
        gen_recibo_caja(period)
        gen_extracto_bancario(period)

    total = len(list(OUT_DIR.glob("*.pdf")))
    print(f"\nListo. {total} PDFs en {OUT_DIR}")
