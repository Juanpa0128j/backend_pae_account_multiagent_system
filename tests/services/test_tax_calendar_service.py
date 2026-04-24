"""Unit tests for tax_calendar_service — DIAN 2026 deadlines."""

from datetime import date

import pytest

from app.services.tax_calendar_service import (
    CalendarEntry,
    get_deadline,
    list_obligations,
)

_NIT = "900123456"  # last digit = 6


class TestGetDeadlineRetefuente:
    def test_enero_digit6_falls_in_february(self):
        e = get_deadline("retefuente", "2026-01", _NIT, today=date(2026, 1, 15))
        assert e is not None
        assert e.deadline == date(2026, 2, 17)  # digit 6 → index5 → day 17

    def test_diciembre_digit6_falls_in_jan_2027(self):
        e = get_deadline("retefuente", "2026-12", _NIT, today=date(2026, 12, 1))
        assert e is not None
        assert e.deadline.year == 2027
        assert e.deadline.month == 1

    def test_unknown_period_returns_none(self):
        assert get_deadline("retefuente", "2025-01", _NIT) is None

    def test_unknown_form_type_returns_none(self):
        assert get_deadline("unknown_form", "2026-01", _NIT) is None

    def test_alert_true_when_within_alert_days(self):
        e = get_deadline(
            "retefuente", "2026-01", _NIT, today=date(2026, 2, 12), alert_days=10
        )
        assert e is not None
        assert e.alert is True

    def test_alert_false_when_past_deadline(self):
        e = get_deadline("retefuente", "2026-01", _NIT, today=date(2026, 3, 1))
        assert e is not None
        assert e.alert is False

    def test_digit0_maps_correctly(self):
        nit_digit0 = "900123450"
        e = get_deadline("retefuente", "2026-01", nit_digit0, today=date(2026, 1, 1))
        assert e is not None
        assert e.deadline == date(2026, 2, 23)  # digit 0 → last schedule entry

    def test_nit_with_dots_stripped(self):
        nit_with_dots = "900.123.456"
        e = get_deadline("retefuente", "2026-01", nit_with_dots, today=date(2026, 1, 1))
        assert e is not None
        assert e.deadline.month == 2


class TestGetDeadlineIVA:
    def test_bimestral_b1_digit6_march(self):
        e = get_deadline("iva_bimestral", "2026-B1", _NIT, today=date(2026, 1, 1))
        assert e is not None
        assert e.deadline.month == 3
        assert e.deadline.year == 2026

    def test_bimestral_b6_jan_2027(self):
        e = get_deadline("iva_bimestral", "2026-B6", _NIT, today=date(2026, 11, 1))
        assert e is not None
        assert e.deadline.year == 2027

    def test_cuatrimestral_c1_may(self):
        e = get_deadline("iva_cuatrimestral", "2026-C1", _NIT, today=date(2026, 1, 1))
        assert e is not None
        assert e.deadline.month == 5

    def test_cuatrimestral_c3_jan_2027(self):
        e = get_deadline("iva_cuatrimestral", "2026-C3", _NIT, today=date(2026, 12, 1))
        assert e is not None
        assert e.deadline.year == 2027


class TestGetDeadlineRentaPJ:
    def test_cuota1_may(self):
        e = get_deadline("renta_pj", "2026-cuota1", _NIT, today=date(2026, 1, 1))
        assert e is not None
        assert e.deadline.month == 5

    def test_cuota2_july(self):
        e = get_deadline("renta_pj", "2026-cuota2", _NIT, today=date(2026, 1, 1))
        assert e is not None
        assert e.deadline.month == 7

    def test_cuota2_after_cuota1(self):
        c1 = get_deadline("renta_pj", "2026-cuota1", _NIT, today=date(2026, 1, 1))
        c2 = get_deadline("renta_pj", "2026-cuota2", _NIT, today=date(2026, 1, 1))
        assert c1 is not None and c2 is not None
        assert c1.deadline < c2.deadline


class TestListObligations:
    def test_bimestral_returns_20_entries(self):
        entries = list_obligations(_NIT, iva_regime="bimestral", today=date(2026, 1, 1))
        # 12 retefuente + 6 iva bimestral + 2 renta = 20
        assert len(entries) == 20

    def test_cuatrimestral_returns_17_entries(self):
        entries = list_obligations(
            _NIT, iva_regime="cuatrimestral", today=date(2026, 1, 1)
        )
        # 12 retefuente + 3 iva cuatrimestral + 2 renta = 17
        assert len(entries) == 17

    def test_sorted_ascending_by_deadline(self):
        entries = list_obligations(_NIT, iva_regime="bimestral", today=date(2026, 1, 1))
        for i in range(len(entries) - 1):
            assert entries[i].deadline <= entries[i + 1].deadline

    def test_entries_are_calendar_entry_instances(self):
        entries = list_obligations(_NIT, today=date(2026, 1, 1))
        assert all(isinstance(e, CalendarEntry) for e in entries)

    def test_alert_flag_set_for_upcoming(self):
        # Retefuente enero is due Feb 17 for digit 6
        # today = Feb 10 → 7 days away, alert_days=10 → should alert
        entries = list_obligations(_NIT, alert_days=10, today=date(2026, 2, 10))
        alerts = [e for e in entries if e.alert]
        assert len(alerts) > 0


class TestInputValidation:
    def test_unsupported_year_raises(self):
        with pytest.raises(ValueError, match="Unsupported year"):
            list_obligations(_NIT, year=2025)

    def test_unsupported_iva_regime_raises(self):
        with pytest.raises(ValueError, match="Unsupported iva_regime"):
            list_obligations(_NIT, iva_regime="mensual")


class TestLastDigitWithDV:
    def test_strips_dv_appended_with_dash(self):
        from app.services.tax_calendar_service import _last_digit

        # Base NIT ends in 6, DV is 7 — must return 6 not 7
        assert _last_digit("900123456-7") == 6

    def test_strips_dots_and_dv(self):
        from app.services.tax_calendar_service import _last_digit

        assert _last_digit("900.123.456-7") == 6

    def test_no_dv_returns_last_digit(self):
        from app.services.tax_calendar_service import _last_digit

        assert _last_digit("900123456") == 6

    def test_empty_raises(self):
        from app.services.tax_calendar_service import _last_digit

        with pytest.raises(ValueError):
            _last_digit("")

    def test_non_digit_raises(self):
        from app.services.tax_calendar_service import _last_digit

        with pytest.raises(ValueError):
            _last_digit("abc")
