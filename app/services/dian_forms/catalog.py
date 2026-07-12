"""Catalog data structures for official DIAN forms.

A ``FormCatalog`` is the ordered list of official ``Casilla`` boxes for one form
and year. Kept deliberately free of any dependency on the declaration service so
it can be imported without cycles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal, Optional

# A subtotal formula receives a getter ``v(numero) -> float`` (0.0 when the
# referenced casilla is absent) and returns the computed value. Defined as a
# plain callable so the data modules can express the exact instructivo formula
# (e.g. ``lambda v: max(v("75"), v("76")) - v("77") + v("78")``) without a parser.
FormulaFn = Callable[[Callable[[str], float]], float]

CasillaTipo = Literal["computed", "subtotal", "manual", "header"]


@dataclass(frozen=True)
class Casilla:
    """A single official box of a DIAN form.

    tipo:
      * ``computed``  — filled from the ledger by the builder when available.
      * ``subtotal``  — derived from other casillas via ``formula``.
      * ``manual``    — the accountant must fill it (source docs the system
                         does not hold, e.g. prior-period balances, sanctions).
      * ``header``    — structural label, not a value row (skipped in drafts).
    """

    numero: str
    label: str
    seccion: str
    tipo: CasillaTipo = "computed"
    formula: Optional[FormulaFn] = None
    requires_review: bool = False
    help_text: Optional[str] = None


@dataclass(frozen=True)
class FormCatalog:
    form_type: str
    year: int
    title: str
    casillas: list[Casilla] = field(default_factory=list)

    def numeros(self) -> list[str]:
        return [c.numero for c in self.casillas]

    def by_numero(self, numero: str) -> Optional[Casilla]:
        for c in self.casillas:
            if c.numero == numero:
                return c
        return None
