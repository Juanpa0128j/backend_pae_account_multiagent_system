"""Catalog registry — resolve a FormCatalog by form_type and year.

When an exact year is not registered, falls back to the most recent catalog for
that form_type (form structures are stable year to year; the builder still fills
year-specific values).
"""

from __future__ import annotations

from typing import Optional

from app.services.dian_forms.catalog import FormCatalog
from app.services.dian_forms import f110_2024, f300_2025, f350_2026, ica_generic

# form_type -> {year: catalog}
_REGISTRY: dict[str, dict[int, FormCatalog]] = {
    "F300": {f300_2025.CATALOG.year: f300_2025.CATALOG},
    "F350": {f350_2026.CATALOG.year: f350_2026.CATALOG},
    "F110": {f110_2024.CATALOG.year: f110_2024.CATALOG},
    "ICA": {ica_generic.CATALOG.year: ica_generic.CATALOG},
}


def has_catalog(form_type: str) -> bool:
    return form_type in _REGISTRY


def get_catalog(form_type: str, year: Optional[int] = None) -> FormCatalog:
    """Return the catalog for ``form_type``.

    Uses the exact ``year`` when registered, otherwise the latest available.
    Raises ``KeyError`` for an unknown form_type.
    """
    by_year = _REGISTRY[form_type]
    if year is not None and year in by_year:
        return by_year[year]
    latest = max(by_year)
    return by_year[latest]
