"""ICA — Declaración de Industria y Comercio (formato genérico municipal).

ICA has no single national form (each municipality issues its own), so this is a
canonical structure covering the universal boxes. Values are computed from the
ledger; municipality-specific lines (sobretasa bomberil, anticipos) are manual.
"""

from __future__ import annotations

from app.services.dian_forms.catalog import Casilla, FormCatalog

_S = "Liquidación ICA"

CATALOG = FormCatalog(
    form_type="ICA",
    year=2026,
    title="Declaración de Industria y Comercio (ICA) — municipal",
    casillas=[
        Casilla("1", "Ingresos brutos del período", _S),
        Casilla(
            "2", "Menos ingresos por devoluciones, exentos o no sujetos", _S, "manual"
        ),
        Casilla(
            "3", "Base gravable", _S, "subtotal", lambda v: max(0.0, v("1") - v("2"))
        ),
        Casilla("4", "Impuesto de industria y comercio", _S),
        Casilla("5", "Impuesto de avisos y tableros (15%)", _S),
        Casilla("6", "Sobretasa bomberil", _S, "manual"),
        Casilla(
            "7",
            "Total impuesto a cargo",
            _S,
            "subtotal",
            lambda v: v("4") + v("5") + v("6"),
        ),
        Casilla("8", "Retenciones y autorretenciones de ICA que le practicaron", _S),
        Casilla("9", "Anticipo del año anterior", _S, "manual"),
        Casilla("10", "Anticipo del año siguiente", _S, "manual"),
        Casilla("11", "Sanciones", _S, "manual"),
        Casilla(
            "12",
            "Total saldo a pagar",
            _S,
            "subtotal",
            lambda v: max(0.0, v("7") - v("8") - v("9")) + v("10") + v("11"),
        ),
    ],
)
