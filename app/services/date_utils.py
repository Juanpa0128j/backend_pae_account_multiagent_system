"""Date helpers for monthly accounting periods.

Used by `_auto_derive_statements` and any other code that needs to expand a
single date into the full calendar month it belongs to (1st 00:00:00 UTC to
last day 23:59:59 UTC).
"""

from __future__ import annotations

import calendar
from datetime import datetime, timezone


def _as_utc(dt: datetime) -> datetime:
    """Return ``dt`` as a tz-aware UTC datetime (converting offsets if needed)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def first_of_month(dt: datetime) -> datetime:
    """Return the first day of the month containing ``dt`` (00:00:00 UTC).

    If ``dt`` is offset-aware (non-UTC), it is converted to UTC first so the
    resulting month boundary is always anchored to UTC. This matches how the
    DB stores ``DateTime(timezone=True)`` columns and avoids mixed-offset
    period boundaries in queries.
    """
    dt_utc = _as_utc(dt)
    return datetime(dt_utc.year, dt_utc.month, 1, 0, 0, 0, tzinfo=timezone.utc)


def last_of_month(dt: datetime) -> datetime:
    """Return the last day of the month containing ``dt`` (23:59:59 UTC).

    See ``first_of_month`` for the timezone handling rationale.
    """
    dt_utc = _as_utc(dt)
    last_day = calendar.monthrange(dt_utc.year, dt_utc.month)[1]
    return datetime(
        dt_utc.year, dt_utc.month, last_day, 23, 59, 59, tzinfo=timezone.utc
    )
