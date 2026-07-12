"""DIAN official form catalogs.

Single source of truth for the *structure* (casillas, labels, sections and
subtotal formulas) of each DIAN form, decoupled from the *computation* of the
values (which lives in ``tax_declaration_service``). This lets the declaration
builders emit a ``{casilla_oficial -> valor}`` map and have the catalog fill in
every official box — computed ones with real values, subtotals via formula, and
the rest marked "diligenciar manualmente" — so the resulting draft mirrors the
real form casilla-by-casilla.
"""

from app.services.dian_forms.catalog import Casilla, FormCatalog
from app.services.dian_forms.registry import get_catalog, has_catalog

__all__ = ["Casilla", "FormCatalog", "get_catalog", "has_catalog"]
