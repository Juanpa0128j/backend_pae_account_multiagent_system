"""Formulario 300 — Declaración del Impuesto sobre las Ventas (IVA).

Official casilla catalog (2025 form, vigente 2026). Labels and subtotal formulas
transcribed from the DIAN instructivo. Sections group the boxes as printed on the
form. Subtotal formulas use the exact instructivo arithmetic, clamped to >= 0
where the instructivo says "si el resultado es menor a cero escriba 0".
"""

from __future__ import annotations

from app.services.dian_forms.catalog import Casilla, FormCatalog

_ING = "Ingresos"
_COM = "Compras e importaciones"
_GEN = "Impuesto generado"
_DES = "Impuesto descontable"
_LIQ = "Liquidación privada"


def _sum(v, lo, hi):
    return sum(v(str(n)) for n in range(lo, hi + 1))


CATALOG = FormCatalog(
    form_type="F300",
    year=2025,
    title="Formulario 300 — Declaración del Impuesto sobre las Ventas (IVA)",
    casillas=[
        # ── Ingresos (27-43) ────────────────────────────────────────────────
        Casilla("27", "Por operaciones gravadas al 5%", _ING),
        Casilla("28", "Por operaciones gravadas a la tarifa general", _ING),
        Casilla("29", "AIU por operaciones gravadas (base gravable especial)", _ING),
        Casilla("30", "Por exportación de bienes", _ING),
        Casilla("31", "Por exportación de servicios", _ING),
        Casilla(
            "32", "Por ventas a sociedades de comercialización internacional", _ING
        ),
        Casilla("33", "Por ventas a zona franca", _ING),
        Casilla("34", "Por juegos de suerte y azar", _ING),
        Casilla("35", "Por operaciones exentas", _ING),
        Casilla("36", "Por venta de cerveza de producción nacional o importada", _ING),
        Casilla("37", "Por ventas de gaseosas y similares", _ING),
        Casilla("38", "Por venta de licores, aperitivos, vinos y similares", _ING),
        Casilla("39", "Por operaciones excluidas", _ING),
        Casilla("40", "Por operaciones no gravadas", _ING),
        Casilla(
            "41", "Total ingresos brutos", _ING, "subtotal", lambda v: _sum(v, 27, 40)
        ),
        Casilla("42", "Devoluciones en ventas anuladas, rescindidas o resueltas", _ING),
        Casilla(
            "43",
            "Total ingresos netos recibidos durante el período",
            _ING,
            "subtotal",
            lambda v: v("41") - v("42"),
        ),
        # ── Compras e importaciones (44-57) ─────────────────────────────────
        Casilla("44", "De bienes gravados a la tarifa del 5% (importaciones)", _COM),
        Casilla("45", "De bienes gravados a la tarifa general (importaciones)", _COM),
        Casilla(
            "46", "De bienes y servicios gravados provenientes de zonas francas", _COM
        ),
        Casilla("47", "De bienes no gravados (importaciones)", _COM),
        Casilla(
            "48", "De bienes excluidos, exentos y no gravados de zonas francas", _COM
        ),
        Casilla("49", "De servicios (importaciones)", _COM),
        Casilla("50", "De bienes gravados a la tarifa del 5% (nacionales)", _COM),
        Casilla("51", "De bienes gravados a la tarifa general (nacionales)", _COM),
        Casilla("52", "De servicios gravados a la tarifa del 5% (nacionales)", _COM),
        Casilla("53", "De servicios gravados a la tarifa general (nacionales)", _COM),
        Casilla("54", "De bienes y servicios excluidos, exentos y no gravados", _COM),
        Casilla(
            "55",
            "Total compras e importaciones brutas",
            _COM,
            "subtotal",
            lambda v: _sum(v, 44, 54),
        ),
        Casilla(
            "56", "Devoluciones en compras anuladas, rescindidas o resueltas", _COM
        ),
        Casilla(
            "57",
            "Total compras netas realizadas durante el período",
            _COM,
            "subtotal",
            lambda v: v("55") - v("56"),
        ),
        # ── Impuesto generado (58-67) ───────────────────────────────────────
        Casilla("58", "A la tarifa del 5%", _GEN),
        Casilla("59", "A la tarifa general", _GEN),
        Casilla(
            "60", "Sobre AIU en operaciones gravadas (base gravable especial)", _GEN
        ),
        Casilla("61", "En juegos de suerte y azar", _GEN),
        Casilla("62", "En venta de cerveza de producción nacional o importada", _GEN),
        Casilla("63", "En venta de gaseosas y similares", _GEN),
        Casilla("64", "En venta de licores, aperitivos, vinos y similares 5%", _GEN),
        Casilla(
            "65",
            "En retiro inventario para activos fijos, consumo, muestras o donaciones",
            _GEN,
        ),
        Casilla(
            "66", "IVA recuperado en devoluciones en compras anuladas o resueltas", _GEN
        ),
        Casilla(
            "67",
            "Total impuesto generado por operaciones gravadas",
            _GEN,
            "subtotal",
            lambda v: _sum(v, 58, 66),
        ),
        # ── Impuesto descontable (68-81) ────────────────────────────────────
        Casilla("68", "Por importaciones gravadas a la tarifa del 5%", _DES),
        Casilla("69", "Por importaciones gravadas a la tarifa general", _DES),
        Casilla(
            "70", "De bienes y servicios gravados provenientes de zonas francas", _DES
        ),
        Casilla("71", "Por compras de bienes gravados a la tarifa del 5%", _DES),
        Casilla("72", "Por compras de bienes gravados a la tarifa general", _DES),
        Casilla("73", "Por licores, aperitivos, vinos y similares", _DES),
        Casilla("74", "Por servicios gravados a la tarifa del 5%", _DES),
        Casilla("75", "Por servicios gravados a la tarifa general", _DES),
        Casilla("76", "Descuento IVA exploración hidrocarburos art. 485-2 E.T.", _DES),
        Casilla(
            "77",
            "Total impuesto pagado o facturado",
            _DES,
            "subtotal",
            lambda v: _sum(v, 68, 76),
        ),
        Casilla(
            "78", "IVA retenido por servicios de no domiciliados o no residentes", _DES
        ),
        Casilla(
            "79", "IVA resultante por devoluciones en ventas anuladas o resueltas", _DES
        ),
        Casilla(
            "80",
            "Ajuste impuestos descontables (pérdidas, hurto o castigo de inventarios)",
            _DES,
        ),
        Casilla(
            "81",
            "Total impuestos descontables",
            _DES,
            "subtotal",
            lambda v: v("77") + v("78") + v("79") - v("80"),
        ),
        # ── Liquidación privada (82-93) ─────────────────────────────────────
        Casilla(
            "82",
            "Saldo a pagar por el período fiscal",
            _LIQ,
            "subtotal",
            lambda v: max(0.0, v("67") - v("81")),
        ),
        Casilla(
            "83",
            "Saldo a favor del período fiscal",
            _LIQ,
            "subtotal",
            lambda v: max(0.0, v("81") - v("67")),
        ),
        Casilla("84", "Saldo a favor del período fiscal anterior", _LIQ, "manual"),
        Casilla("85", "Retenciones por IVA que le practicaron", _LIQ, "manual"),
        Casilla(
            "86",
            "Saldo a pagar por impuesto",
            _LIQ,
            "subtotal",
            lambda v: max(0.0, v("82") - v("84") - v("85")),
        ),
        Casilla("87", "Sanciones", _LIQ, "manual"),
        Casilla(
            "88",
            "Total saldo a pagar",
            _LIQ,
            "subtotal",
            lambda v: max(0.0, (v("82") + v("87")) - v("84") - v("85")),
        ),
        Casilla(
            "89",
            "Total saldo a favor",
            _LIQ,
            "subtotal",
            lambda v: max(0.0, (v("83") + v("84") + v("85")) - v("87")),
        ),
        Casilla(
            "90",
            "Saldo a favor susceptible de devolución y/o compensación",
            _LIQ,
            "manual",
        ),
        Casilla(
            "91",
            "Saldo a favor a imputar en el período siguiente (devolución)",
            _LIQ,
            "manual",
        ),
        Casilla(
            "92",
            "Saldo a favor sin derecho a devolución susceptible a imputar",
            _LIQ,
            "manual",
        ),
        Casilla(
            "93", "Total saldo a favor a imputar al período siguiente", _LIQ, "manual"
        ),
    ],
)
