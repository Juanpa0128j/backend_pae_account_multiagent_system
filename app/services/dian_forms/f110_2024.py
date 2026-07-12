"""Formulario 110 — Declaración de Renta y Complementarios (personas jurídicas).

Official casilla catalog (2024 form). Labels and subtotal formulas transcribed
from the DIAN instructivo. Monetary casillas start at 36 (patrimonio); casillas
1-35 are declaration metadata (año, corrección, actividad económica, flags) and
are not value rows.
"""

from __future__ import annotations

from app.services.dian_forms.catalog import Casilla, FormCatalog

_PAT = "Patrimonio"
_ING = "Ingresos"
_COS = "Costos y deducciones"
_REN = "Renta"
_GAN = "Ganancias ocasionales"
_IMP = "Liquidación privada — impuesto"
_DAR = "Descuentos, anticipos y retenciones"
_SAL = "Liquidación privada — saldo"


def _sum(v, lo, hi):
    return sum(v(str(n)) for n in range(lo, hi + 1))


CATALOG = FormCatalog(
    form_type="F110",
    year=2024,
    title="Formulario 110 — Declaración de Renta y Complementarios",
    casillas=[
        # ── Patrimonio (36-46) ──────────────────────────────────────────────
        Casilla("36", "Efectivo y equivalentes al efectivo", _PAT),
        Casilla("37", "Inversiones e instrumentos financieros derivados", _PAT),
        Casilla(
            "38", "Cuentas, documentos y arrendamientos financieros por cobrar", _PAT
        ),
        Casilla("39", "Inventarios", _PAT),
        Casilla("40", "Activos intangibles", _PAT),
        Casilla("41", "Activos biológicos", _PAT),
        Casilla(
            "42", "Propiedades, planta y equipo, propiedades de inversión y ANCMV", _PAT
        ),
        Casilla("43", "Otros activos", _PAT),
        Casilla(
            "44", "Total patrimonio bruto", _PAT, "subtotal", lambda v: _sum(v, 36, 43)
        ),
        Casilla("45", "Pasivos", _PAT),
        Casilla(
            "46",
            "Total patrimonio líquido",
            _PAT,
            "subtotal",
            lambda v: v("44") - v("45"),
        ),
        # ── Ingresos (47-61) ────────────────────────────────────────────────
        Casilla("47", "Ingresos brutos de actividades ordinarias", _ING),
        Casilla("48", "Ingresos financieros", _ING),
        Casilla(
            "49", "Dividendos y participaciones no constitutivos de renta ni GO", _ING
        ),
        Casilla(
            "50", "Dividendos y participaciones distribuidos por no residentes", _ING
        ),
        Casilla(
            "51", "Dividendos y participaciones gravadas a la tarifa general", _ING
        ),
        Casilla(
            "52",
            "Dividendos gravados recibidos por personas naturales sin residencia (a)",
            _ING,
        ),
        Casilla(
            "53",
            "Dividendos gravados recibidos por personas naturales sin residencia (b)",
            _ING,
        ),
        Casilla(
            "54",
            "Dividendos gravados a las tarifas de los artículos 245 o 246 E.T.",
            _ING,
        ),
        Casilla(
            "55", "Dividendos gravados a la tarifa general (EP y sociedades)", _ING
        ),
        Casilla(
            "56",
            "Dividendos provenientes de proyectos calificados como megainversión",
            _ING,
        ),
        Casilla("57", "Otros ingresos", _ING),
        Casilla(
            "58", "Total ingresos brutos", _ING, "subtotal", lambda v: _sum(v, 47, 57)
        ),
        Casilla("59", "Devoluciones, rebajas y descuentos en ventas", _ING),
        Casilla("60", "Ingresos no constitutivos de renta ni ganancia ocasional", _ING),
        Casilla(
            "61",
            "Total ingresos netos",
            _ING,
            "subtotal",
            lambda v: v("58") - v("59") - v("60"),
        ),
        # ── Costos y deducciones (62-67) ────────────────────────────────────
        Casilla("62", "Costos", _COS),
        Casilla("63", "Gastos de administración", _COS),
        Casilla("64", "Gastos de distribución y ventas", _COS),
        Casilla("65", "Gastos financieros", _COS),
        Casilla("66", "Otros gastos y deducciones", _COS),
        Casilla(
            "67",
            "Total costos y gastos deducibles",
            _COS,
            "subtotal",
            lambda v: _sum(v, 62, 66),
        ),
        # ── Renta (68-79) ───────────────────────────────────────────────────
        Casilla("68", "Inversiones efectuadas en el año", _REN),
        Casilla("69", "Inversiones liquidadas de períodos gravables anteriores", _REN),
        Casilla("70", "Renta por recuperación de deducciones", _REN),
        Casilla("71", "Renta pasiva – ECE sin residencia fiscal en Colombia", _REN),
        Casilla(
            "72",
            "Renta líquida ordinaria del ejercicio",
            _REN,
            "subtotal",
            lambda v: max(
                0.0,
                v("61")
                + v("69")
                + v("70")
                + v("71")
                - v("52")
                - v("53")
                - v("54")
                - v("55")
                - v("56")
                - v("67")
                - v("68"),
            ),
        ),
        Casilla(
            "73",
            "Pérdida líquida del ejercicio",
            _REN,
            "subtotal",
            lambda v: max(
                0.0,
                v("52")
                + v("53")
                + v("54")
                + v("55")
                + v("56")
                + v("67")
                + v("68")
                - v("61")
                - v("69")
                - v("70")
                - v("71"),
            ),
        ),
        Casilla("74", "Compensaciones", _REN, "manual"),
        Casilla(
            "75",
            "Renta líquida",
            _REN,
            "subtotal",
            lambda v: max(0.0, v("72") - v("74")),
        ),
        Casilla("76", "Renta presuntiva", _REN),
        Casilla("77", "Renta exenta", _REN, "manual"),
        Casilla("78", "Rentas gravables", _REN, "manual"),
        Casilla(
            "79",
            "Renta líquida gravable",
            _REN,
            "subtotal",
            lambda v: max(0.0, max(v("75"), v("76")) - v("77") + v("78")),
        ),
        # ── Ganancias ocasionales (80-83) ───────────────────────────────────
        Casilla("80", "Ingresos por ganancias ocasionales", _GAN),
        Casilla("81", "Costos por ganancias ocasionales", _GAN),
        Casilla("82", "Ganancias ocasionales no gravadas y exentas", _GAN),
        Casilla(
            "83",
            "Ganancias ocasionales gravables",
            _GAN,
            "subtotal",
            lambda v: max(0.0, v("80") - v("81") - v("82")),
        ),
        # ── Liquidación privada — impuesto (84-99) ──────────────────────────
        Casilla("84", "Sobre la renta líquida gravable", _IMP),
        Casilla("85", "Puntos adicionales a la tarifa del impuesto de renta", _IMP),
        Casilla(
            "86", "De dividendos y participaciones gravadas a la tarifa del 10%", _IMP
        ),
        Casilla(
            "87", "De dividendos y participaciones gravadas art. 240 E.T. (a)", _IMP
        ),
        Casilla(
            "88", "De dividendos y participaciones gravadas a la tarifa del 27%", _IMP
        ),
        Casilla(
            "89", "De dividendos y participaciones gravadas art. 240 E.T. (b)", _IMP
        ),
        Casilla(
            "90", "De dividendos y participaciones gravadas a la tarifa del 33%", _IMP
        ),
        Casilla(
            "91",
            "Total impuesto sobre las rentas líquidas gravables",
            _IMP,
            "subtotal",
            lambda v: _sum(v, 84, 90),
        ),
        Casilla("92", "Valor a adicionar (VAA)", _IMP),
        Casilla("93", "Descuentos tributarios", _IMP, "manual"),
        Casilla(
            "94",
            "Impuesto neto de renta (sin impuesto adicionado)",
            _IMP,
            "subtotal",
            lambda v: max(0.0, v("91") + v("92") - v("93")),
        ),
        Casilla("95", "Impuesto a adicionar (IA)", _IMP),
        Casilla(
            "96",
            "Impuesto neto de renta (con impuesto adicionado)",
            _IMP,
            "subtotal",
            lambda v: v("94") + v("95"),
        ),
        Casilla("97", "Impuesto de ganancias ocasionales", _IMP),
        Casilla("98", "Descuento por impuestos pagados en el exterior por GO", _IMP),
        Casilla(
            "99",
            "Total impuesto a cargo",
            _IMP,
            "subtotal",
            lambda v: max(0.0, v("96") + v("97") - v("98")),
        ),
        # ── Descuentos, anticipos y retenciones (100-110) ───────────────────
        Casilla(
            "100",
            "Valor inversión Obras por Impuestos (hasta 50% casilla 99)",
            _DAR,
            "manual",
        ),
        Casilla(
            "101",
            "Descuento efectivo inversión Obras por Impuestos (modalidad 2)",
            _DAR,
            "manual",
        ),
        Casilla("102", "Crédito fiscal (artículo 256-1 E.T.)", _DAR, "manual"),
        Casilla(
            "103", "Anticipo renta liquidado año gravable anterior", _DAR, "manual"
        ),
        Casilla(
            "104",
            "Saldo a favor año gravable anterior sin solicitud de devolución",
            _DAR,
            "manual",
        ),
        Casilla("105", "Autorretenciones", _DAR),
        Casilla("106", "Otras retenciones", _DAR),
        Casilla(
            "107",
            "Total retenciones año gravable a declarar",
            _DAR,
            "subtotal",
            lambda v: v("105") + v("106"),
        ),
        Casilla("108", "Anticipo renta para el año gravable siguiente", _DAR),
        Casilla(
            "109", "Anticipo puntos adicionales año gravable anterior", _DAR, "manual"
        ),
        Casilla(
            "110", "Anticipo puntos adicionales año gravable siguiente", _DAR, "manual"
        ),
        # ── Liquidación privada — saldo (111-114) ───────────────────────────
        Casilla(
            "111",
            "Saldo a pagar por impuesto",
            _SAL,
            "subtotal",
            lambda v: max(
                0.0,
                v("99")
                + v("108")
                + v("110")
                - v("100")
                - v("101")
                - v("102")
                - v("103")
                - v("104")
                - v("107")
                - v("109"),
            ),
        ),
        Casilla("112", "Sanciones", _SAL, "manual"),
        Casilla(
            "113",
            "Total saldo a pagar",
            _SAL,
            "subtotal",
            lambda v: max(
                0.0,
                v("99")
                + v("108")
                + v("110")
                + v("112")
                - v("100")
                - v("101")
                - v("102")
                - v("103")
                - v("104")
                - v("107")
                - v("109"),
            ),
        ),
        Casilla(
            "114",
            "Total saldo a favor",
            _SAL,
            "subtotal",
            lambda v: max(
                0.0,
                v("100")
                + v("101")
                + v("102")
                + v("103")
                + v("104")
                + v("107")
                + v("109")
                - v("99")
                - v("108")
                - v("110")
                - v("112"),
            ),
        ),
    ],
)
