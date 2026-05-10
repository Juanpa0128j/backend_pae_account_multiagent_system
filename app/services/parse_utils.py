"""Defensive parsing helpers for values coming from uploaded documents.

The LLM-extracted JSON usually stores numbers as plain strings (e.g. "5000000"),
but a future ingestion path or a different document type might produce
locale-formatted strings (e.g. "1.234,56"). This module centralizes a
forgiving float parser so endpoints that read FinancialStatement.data don't
500 on unexpected formats.
"""

from __future__ import annotations

from typing import Any


def safe_float(value: Any, default: float = 0.0) -> float:
    """Best-effort cast to ``float`` that never raises.

    Accepts:
    - None / missing values → returns ``default``.
    - Plain numeric values → returns ``float(value)``.
    - Strings with Colombian locale formatting ("1.234,56") → normalizes the
      thousands separator (".") and decimal comma before casting.
    - Strings with surrounding whitespace, "$" prefix, or trailing non-digits.

    Anything else returns ``default``.
    """
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().replace("$", "").replace(" ", "")
        if not s:
            return default
        # Colombian format: thousands "." + decimal ","  →  drop "." then swap "," → "."
        if "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".")
        elif "," in s:
            # Could be "1234,56" (comma decimal) — convert.
            s = s.replace(",", ".")
        try:
            return float(s)
        except ValueError:
            return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
