"""Formulario 350 — Declaración de Retención en la Fuente.

Official casilla catalog (2026 form). The form is a matrix: each concept has a
*base* casilla and a *retención practicada* casilla, split into "a personas
jurídicas" (29-54), "a personas naturales" (77-108), plus foreign payments,
autorretenciones, and the totals block (130-138). Built programmatically because
the concept rows repeat; each casilla still carries its exact official number.

NOTE (audit fix): ReteICA is intentionally absent — it is a municipal retention
and does not belong on the national F350. It lives only in the ICA declaration.
"""

from __future__ import annotations

from app.services.dian_forms.catalog import Casilla, FormCatalog

# Concept lists per block (official order) -----------------------------------
_PJ = [
    "Honorarios",
    "Comisiones",
    "Servicios",
    "Rendimientos financieros e intereses",
    "Arrendamientos (muebles e inmuebles)",
    "Regalías y explotación de la propiedad intelectual",
    "Dividendos y participaciones",
    "Compras",
    "Transacciones con tarjetas débito y crédito",
    "Contratos de construcción",
    "Loterías, rifas, apuestas y similares",
    "Hidrocarburos, carbón y demás productos mineros",
    "Otros pagos sujetos a retención",
]  # 13 -> base 29-41, retención 42-54

_AUTO_PJ = [
    "Contribuyentes exonerados de aportes (art. 114-1 E.T.)",
    "Ventas",
    "Honorarios",
    "Comisiones",
    "Servicios",
    "Rendimientos financieros",
    "Pagos mensuales provisionales voluntarios (hidrocarburos y demás)",
    "Exportación de hidrocarburos, carbón y demás productos mineros",
    "Otros conceptos",
]  # 9 -> base 59-67, retención 68-76

_PN = [
    "Rentas de trabajo",
    "Rentas de pensiones",
    "Honorarios",
    "Comisiones",
    "Servicios",
    "Rendimientos financieros e intereses",
    "Arrendamientos (muebles e inmuebles)",
    "Regalías y explotación de la propiedad intelectual",
    "Dividendos y participaciones",
    "Compras",
    "Transacciones con tarjetas débito y crédito",
    "Contratos de construcción",
    "Enajenación de activos fijos de PN ante notarios y autoridades de tránsito",
    "Loterías, rifas, apuestas y similares",
    "Hidrocarburos, carbón y demás productos mineros",
    "Otros pagos sujetos a retención",
]  # 16 -> base 77-92, retención 93-108

_AUTO_PN = [
    "Ventas",
    "Honorarios",
    "Comisiones",
    "Servicios",
    "Rendimientos financieros",
    "Pagos mensuales provisionales voluntarios (hidrocarburos y demás)",
    "Exportación de hidrocarburos, carbón y demás productos mineros",
    "Otros conceptos",
]  # 8 -> base 113-120, retención 121-128

_EXT = [
    "Pagos o abonos al exterior a países sin convenio",
    "Pagos o abonos al exterior a países con convenio vigente",
]


def _block(start: int, concepts: list[str], seccion: str, suffix: str) -> list[Casilla]:
    return [
        Casilla(str(start + i), f"{c} ({suffix})", seccion)
        for i, c in enumerate(concepts)
    ]


# Retención casillas summed into casilla 130 (total retenciones renta) --------
_RET_RENTA = (
    list(range(42, 55))  # PJ retención
    + [57, 58]  # exterior PJ retención
    + list(range(68, 77))  # autorret PJ retención
    + list(range(93, 109))  # PN retención
    + [111, 112]  # exterior PN retención
    + list(range(121, 129))  # autorret PN retención
)

_casillas: list[Casilla] = []
# Renta personas jurídicas
_casillas += _block(29, _PJ, "Renta personas jurídicas", "base")
_casillas += _block(42, _PJ, "Renta personas jurídicas", "retención practicada")
# Pagos al exterior (PJ)
_casillas += _block(55, _EXT, "Pagos al exterior", "base")
_casillas += _block(57, _EXT, "Pagos al exterior", "retención practicada")
# Autorretenciones (PJ)
_casillas += _block(59, _AUTO_PJ, "Autorretenciones", "base")
_casillas += _block(68, _AUTO_PJ, "Autorretenciones", "retención")
# Renta personas naturales
_casillas += _block(77, _PN, "Renta personas naturales", "base")
_casillas += _block(93, _PN, "Renta personas naturales", "retención practicada")
# Pagos al exterior (PN)
_casillas += _block(109, _EXT, "Pagos al exterior (PN)", "base")
_casillas += _block(111, _EXT, "Pagos al exterior (PN)", "retención practicada")
# Autorretenciones (PN)
_casillas += _block(113, _AUTO_PN, "Autorretenciones (PN)", "base")
_casillas += _block(121, _AUTO_PN, "Autorretenciones (PN)", "retención")

# Totals block ---------------------------------------------------------------
_LIQ = "Liquidación"
_casillas += [
    Casilla(
        "129",
        "Menos retenciones practicadas en exceso, indebidas o anuladas",
        _LIQ,
        "manual",
    ),
    Casilla(
        "130",
        "Total retenciones renta y complementario",
        _LIQ,
        "subtotal",
        lambda v: max(0.0, sum(v(str(n)) for n in _RET_RENTA) - v("129")),
    ),
    Casilla("131", "A responsables del impuesto sobre las ventas (IVA)", _LIQ),
    Casilla("132", "Practicadas por servicios a no residentes o no domiciliados", _LIQ),
    Casilla(
        "133", "Menos retenciones IVA en exceso, indebidas o anuladas", _LIQ, "manual"
    ),
    Casilla(
        "134",
        "Total retenciones IVA",
        _LIQ,
        "subtotal",
        lambda v: max(0.0, v("131") + v("132") - v("133")),
    ),
    Casilla("135", "Retenciones impuesto de timbre nacional", _LIQ),
    Casilla(
        "136",
        "Total retenciones",
        _LIQ,
        "subtotal",
        lambda v: v("130") + v("134") + v("135"),
    ),
    Casilla("137", "Sanciones", _LIQ, "manual"),
    Casilla(
        "138",
        "Total retenciones más sanciones",
        _LIQ,
        "subtotal",
        lambda v: v("136") + v("137"),
    ),
]

CATALOG = FormCatalog(
    form_type="F350",
    year=2026,
    title="Formulario 350 — Declaración de Retención en la Fuente",
    casillas=_casillas,
)

# Concept -> official casilla map (base, retención) for the builder. Keyed by
# the internal categoria/aplica_a used in tax_concepts. Only concepts the system
# actually classifies are listed; the rest of the form defaults to sin_movimiento.
CONCEPT_CASILLAS: dict[tuple[str, str], tuple[str, str]] = {
    # (categoria, aplica_a): (base_casilla, retencion_casilla)
    ("honorarios", "PJ"): ("29", "42"),
    ("honorarios", "PN"): ("79", "95"),
    ("comisiones", "PJ"): ("30", "43"),
    ("comisiones", "PN"): ("80", "96"),
    ("servicios", "PJ"): ("31", "44"),
    ("servicios", "PN"): ("81", "97"),
    ("arrendamiento", "PJ"): ("33", "46"),
    ("arrendamiento", "PN"): ("83", "99"),
    ("compras", "PJ"): ("36", "49"),
    ("compras", "PN"): ("86", "102"),
    ("salarios", "PN"): ("77", "93"),
}
