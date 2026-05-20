"""Date helpers for monthly accounting periods.

Used by `_auto_derive_statements` and any other code that needs to expand a
single date into the full calendar month it belongs to (1st 00:00:00 UTC to
last day 23:59:59 UTC).
"""

from __future__ import annotations

import calendar
from datetime import datetime, timezone


def first_of_month(dt: datetime) -> datetime:
    """Return the first day of the month containing ``dt`` (00:00:00 UTC)."""
    tz = dt.tzinfo or timezone.utc
    return datetime(dt.year, dt.month, 1, 0, 0, 0, tzinfo=tz)


def last_of_month(dt: datetime) -> datetime:
    """Return the last day of the month containing ``dt`` (23:59:59 UTC)."""
    tz = dt.tzinfo or timezone.utc
    last_day = calendar.monthrange(dt.year, dt.month)[1]
    return datetime(dt.year, dt.month, last_day, 23, 59, 59, tzinfo=tz)
