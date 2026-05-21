"""Tests for app.services.date_utils — monthly accounting period helpers."""

from datetime import datetime, timedelta, timezone

from app.services.date_utils import first_of_month, last_of_month


def test_first_of_month_returns_day_one_at_midnight_utc() -> None:
    dt = datetime(2026, 1, 6, 10, 26, 58, tzinfo=timezone.utc)
    assert first_of_month(dt) == datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def test_last_of_month_returns_last_day_at_end_of_day_utc() -> None:
    dt = datetime(2026, 1, 6, 10, 26, 58, tzinfo=timezone.utc)
    assert last_of_month(dt) == datetime(2026, 1, 31, 23, 59, 59, tzinfo=timezone.utc)


def test_last_of_month_handles_february_leap_and_non_leap() -> None:
    leap = datetime(2024, 2, 15, tzinfo=timezone.utc)
    assert last_of_month(leap) == datetime(2024, 2, 29, 23, 59, 59, tzinfo=timezone.utc)
    non_leap = datetime(2026, 2, 15, tzinfo=timezone.utc)
    assert last_of_month(non_leap) == datetime(
        2026, 2, 28, 23, 59, 59, tzinfo=timezone.utc
    )


def test_first_of_month_attaches_utc_to_naive_input() -> None:
    """A naive datetime is treated as already-UTC and gets UTC tzinfo attached."""
    naive = datetime(2026, 3, 10, 14, 0, 0)
    result = first_of_month(naive)
    assert result == datetime(2026, 3, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert result.tzinfo is timezone.utc


def test_first_of_month_converts_non_utc_offset_to_utc() -> None:
    """Colombian late-evening timestamps must convert to UTC before computing
    the month boundary. 2026-01-31 22:00 -05:00 == 2026-02-01 03:00 UTC, so
    the month belongs to February (UTC), not January (local Colombia).
    """
    bogota = timezone(timedelta(hours=-5))
    dt = datetime(2026, 1, 31, 22, 0, 0, tzinfo=bogota)
    result = first_of_month(dt)
    assert result == datetime(2026, 2, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert result.tzinfo is timezone.utc


def test_last_of_month_converts_non_utc_offset_to_utc() -> None:
    bogota = timezone(timedelta(hours=-5))
    dt = datetime(2026, 1, 31, 22, 0, 0, tzinfo=bogota)  # → Feb 1 03:00 UTC
    result = last_of_month(dt)
    assert result == datetime(2026, 2, 28, 23, 59, 59, tzinfo=timezone.utc)
