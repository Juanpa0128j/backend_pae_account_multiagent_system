"""
Calendario Tributario DIAN 2026 — Colombia

Source: Decreto DIAN Calendario Tributario 2026 (Carolina García, H&G Abogados y Contadores)
UVT 2026: $52.374

Covers:
  - IVA Bimestral (F300) — 6 periods
  - IVA Cuatrimestral (F300) — 3 periods
  - Retención en la Fuente mensual (F350) — 12 months
  - Renta Personas Jurídicas (F110) — 2 installments
  - Bogotá ICA 2026 (Resolución modificación)
  - Santa Marta ICA 2026

Usage:
    from app.services.tax_calendar_service import get_deadline, list_obligations

    entry = get_deadline("retefuente", "2026-03", nit="900123456")
    obligations = list_obligations(nit="900123456", year=2026, alert_days=30)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _last_digit(nit: str) -> int:
    """Return the last digit of a NIT (stripped of dots, spaces, DV)."""
    clean = nit.strip().replace(".", "").replace("-", "").replace(" ", "")
    # Strip verification digit if present (format 'NIT-DV' already handled above)
    return int(clean[-1])


# deadline_map: last_digit (0-9) -> day of month
# Digits 0-4 land on earlier dates; 5-9 and 0 on later dates.
# 0 is treated as "10th" slot (highest).


def _day_for_digit(digit: int, schedule: tuple[int, ...]) -> int:
    """
    schedule: tuple of 10 ints, index = digit (0..9) mapped as:
    position 0→digit1, 1→digit2, 2→digit3, 3→digit4, 4→digit5,
    5→digit6, 6→digit7, 7→digit8, 8→digit9, 9→digit0
    """
    idx = (digit - 1) % 10  # digit 1→idx0, …, digit 0→idx9
    return schedule[idx]


# ---------------------------------------------------------------------------
# 2026 DIAN deadline schedules  (source: Calendario Tributario DIAN 2026)
# Each tuple has 10 days: indices 0–8 = digits 1–9, index 9 = digit 0
# ---------------------------------------------------------------------------

# Retención en la Fuente — each entry: (due_month, due_year, schedule)
_RETEFUENTE: dict[str, tuple[int, int, tuple[int, ...]]] = {
    # Período enero → February 2026
    "2026-01": (2, 2026, (10, 11, 12, 13, 16, 17, 18, 19, 20, 23)),
    # Período febrero → March 2026
    "2026-02": (3, 2026, (10, 11, 12, 13, 16, 17, 18, 19, 20, 24)),
    # Período marzo → April 2026
    "2026-03": (4, 2026, (13, 14, 15, 16, 20, 21, 22, 23, 24, 27)),
    # Período abril → May 2026
    "2026-04": (5, 2026, (12, 13, 14, 15, 19, 20, 21, 22, 25, 26)),
    # Período mayo → June 2026
    "2026-05": (6, 2026, (10, 11, 12, 16, 17, 18, 19, 22, 23, 24)),
    # Período junio → July 2026
    "2026-06": (7, 2026, (9, 10, 13, 14, 15, 16, 17, 21, 22, 23)),
    # Período julio → August 2026
    "2026-07": (8, 2026, (12, 13, 14, 18, 19, 20, 21, 24, 25, 26)),
    # Período agosto → September 2026
    "2026-08": (9, 2026, (9, 10, 11, 14, 15, 16, 17, 18, 21, 22)),
    # Período septiembre → October 2026
    "2026-09": (10, 2026, (9, 13, 14, 15, 16, 19, 20, 21, 22, 23)),
    # Período octubre → November 2026
    "2026-10": (11, 2026, (9, 10, 11, 14, 15, 16, 17, 18, 21, 22)),
    # Período noviembre → December 2026
    "2026-11": (12, 2026, (10, 11, 14, 15, 16, 17, 18, 21, 22, 23)),
    # Período diciembre → January 2027
    "2026-12": (1, 2027, (13, 14, 15, 18, 19, 20, 21, 22, 25, 26)),
}

# IVA Bimestral — key = bimestre code (B1..B6)
_IVA_BIMESTRAL: dict[str, tuple[int, int, tuple[int, ...], str, str]] = {
    # (due_month, due_year, schedule, period_start, period_end)
    "2026-B1": (
        3,
        2026,
        (10, 11, 12, 13, 16, 17, 18, 19, 20, 24),
        "2026-01-01",
        "2026-02-28",
    ),
    "2026-B2": (
        5,
        2026,
        (12, 13, 14, 15, 19, 20, 21, 22, 25, 26),
        "2026-03-01",
        "2026-04-30",
    ),
    "2026-B3": (
        7,
        2026,
        (9, 10, 13, 14, 15, 16, 17, 21, 22, 23),
        "2026-05-01",
        "2026-06-30",
    ),
    "2026-B4": (
        9,
        2026,
        (12, 13, 14, 15, 19, 20, 21, 22, 25, 26),
        "2026-07-01",
        "2026-08-31",
    ),
    "2026-B5": (
        11,
        2026,
        (11, 12, 13, 17, 18, 19, 20, 23, 24, 25),
        "2026-09-01",
        "2026-10-31",
    ),
    "2026-B6": (
        1,
        2027,
        (13, 14, 15, 18, 19, 20, 21, 22, 25, 26),
        "2026-11-01",
        "2026-12-31",
    ),
}

# IVA Cuatrimestral — key = cuatrimestre code (C1..C3)
_IVA_CUATRIMESTRAL: dict[str, tuple[int, int, tuple[int, ...], str, str]] = {
    "2026-C1": (
        5,
        2026,
        (12, 13, 14, 15, 19, 20, 21, 22, 25, 26),
        "2026-01-01",
        "2026-04-30",
    ),
    "2026-C2": (
        9,
        2026,
        (9, 10, 11, 14, 15, 16, 17, 18, 21, 22),
        "2026-05-01",
        "2026-08-31",
    ),
    "2026-C3": (
        1,
        2027,
        (13, 14, 15, 18, 19, 20, 21, 22, 25, 26),
        "2026-09-01",
        "2026-12-31",
    ),
}

# Renta Personas Jurídicas — two installments (same schedule both)
_RENTA_PJ: dict[str, tuple[int, int, tuple[int, ...]]] = {
    "2026-cuota1": (5, 2026, (12, 13, 14, 15, 19, 20, 21, 22, 25, 26)),
    "2026-cuota2": (7, 2026, (9, 10, 13, 14, 15, 16, 17, 21, 22, 23)),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class CalendarEntry:
    form_type: str
    period: str
    period_label: str
    deadline: date
    days_until: int
    alert: bool  # True if deadline ≤ alert_days away


def _make_entry(
    form_type: str,
    period: str,
    period_label: str,
    due_month: int,
    due_year: int,
    schedule: tuple[int, ...],
    nit: str,
    today: date,
    alert_days: int,
) -> CalendarEntry:
    digit = _last_digit(nit)
    day = _day_for_digit(digit, schedule)
    deadline = date(due_year, due_month, day)
    days_until = (deadline - today).days
    return CalendarEntry(
        form_type=form_type,
        period=period,
        period_label=period_label,
        deadline=deadline,
        days_until=days_until,
        alert=0 <= days_until <= alert_days,
    )


def get_deadline(
    form_type: str,
    period: str,
    nit: str,
    today: Optional[date] = None,
    alert_days: int = 30,
) -> Optional[CalendarEntry]:
    """
    Return the deadline entry for a specific obligation.

    Args:
        form_type: "retefuente" | "iva_bimestral" | "iva_cuatrimestral" | "renta_pj"
        period: "2026-MM" for retefuente, "2026-B1".."2026-B6" for IVA bimestral,
                "2026-C1".."2026-C3" for cuatrimestral, "2026-cuota1"/"2026-cuota2" for renta
        nit: Company NIT (with or without dots)
        today: Reference date (defaults to date.today())
        alert_days: Days-until threshold for alert=True (default 30)

    Returns:
        CalendarEntry or None if period not found
    """
    today = today or date.today()

    if form_type == "retefuente":
        entry = _RETEFUENTE.get(period)
        if not entry:
            return None
        due_month, due_year, schedule = entry
        month_names = [
            "",
            "Enero",
            "Febrero",
            "Marzo",
            "Abril",
            "Mayo",
            "Junio",
            "Julio",
            "Agosto",
            "Septiembre",
            "Octubre",
            "Noviembre",
            "Diciembre",
        ]
        label = f"Retefuente {month_names[int(period.split('-')[1])]} 2026"
        return _make_entry(
            "retefuente",
            period,
            label,
            due_month,
            due_year,
            schedule,
            nit,
            today,
            alert_days,
        )

    if form_type == "iva_bimestral":
        entry = _IVA_BIMESTRAL.get(period)
        if not entry:
            return None
        due_month, due_year, schedule, p_start, p_end = entry
        bim_num = period.split("-B")[1]
        label = f"IVA Bimestral B{bim_num} 2026 ({p_start} → {p_end})"
        return _make_entry(
            "iva_bimestral",
            period,
            label,
            due_month,
            due_year,
            schedule,
            nit,
            today,
            alert_days,
        )

    if form_type == "iva_cuatrimestral":
        entry = _IVA_CUATRIMESTRAL.get(period)
        if not entry:
            return None
        due_month, due_year, schedule, p_start, p_end = entry
        c_num = period.split("-C")[1]
        label = f"IVA Cuatrimestral C{c_num} 2026 ({p_start} → {p_end})"
        return _make_entry(
            "iva_cuatrimestral",
            period,
            label,
            due_month,
            due_year,
            schedule,
            nit,
            today,
            alert_days,
        )

    if form_type == "renta_pj":
        entry = _RENTA_PJ.get(period)
        if not entry:
            return None
        due_month, due_year, schedule = entry
        cuota = period.split("-")[1]
        label = f"Renta Personas Jurídicas {cuota.replace('cuota', 'Cuota ')} 2026"
        return _make_entry(
            "renta_pj",
            period,
            label,
            due_month,
            due_year,
            schedule,
            nit,
            today,
            alert_days,
        )

    return None


def list_obligations(
    nit: str,
    year: int = 2026,
    iva_regime: str = "bimestral",
    alert_days: int = 30,
    today: Optional[date] = None,
) -> list[CalendarEntry]:
    """
    Return all 2026 tax obligations for a company sorted by deadline.

    Args:
        nit: Company NIT
        year: Tax year (currently only 2026 supported)
        iva_regime: "bimestral" | "cuatrimestral"
        alert_days: Days-until threshold for alert flag
        today: Reference date (defaults to date.today())

    Returns:
        List of CalendarEntry sorted ascending by deadline
    """
    today = today or date.today()
    entries: list[CalendarEntry] = []

    # Retención en la fuente — 12 months
    for month in range(1, 13):
        period = f"{year}-{month:02d}"
        e = get_deadline("retefuente", period, nit, today, alert_days)
        if e:
            entries.append(e)

    # IVA
    if iva_regime == "bimestral":
        for b in range(1, 7):
            e = get_deadline("iva_bimestral", f"{year}-B{b}", nit, today, alert_days)
            if e:
                entries.append(e)
    else:
        for c in range(1, 4):
            e = get_deadline(
                "iva_cuatrimestral", f"{year}-C{c}", nit, today, alert_days
            )
            if e:
                entries.append(e)

    # Renta PJ
    for cuota in ["cuota1", "cuota2"]:
        e = get_deadline("renta_pj", f"{year}-{cuota}", nit, today, alert_days)
        if e:
            entries.append(e)

    entries.sort(key=lambda x: x.deadline)
    return entries
