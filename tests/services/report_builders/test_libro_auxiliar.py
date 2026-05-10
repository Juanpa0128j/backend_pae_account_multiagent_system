"""Tests for the libro auxiliar report builder."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock


from app.services.report_builders.libro_auxiliar import build_libro_auxiliar

_START = "2026-01-01"
_END = "2026-01-31"
_COMPANY_NIT = "900123456"


def _make_row(**kwargs):
    defaults = {
        "fecha": date(2026, 1, 15),
        "comprobante": "CP-001",
        "cuenta_puc": "1110",
        "cuenta_nombre": "Bancos",
        "tercero_nit": "900123456",
        "descripcion": "Pago",
        "debito": 100_000.0,
        "credito": 0.0,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_empty_ledger_returns_zeros():
    """No journal entries → empty cuentas list."""
    svc = MagicMock()
    svc.get_daily_journal.return_value = []

    result = build_libro_auxiliar(None, {"start_date": _START, "end_date": _END}, svc)

    assert result["cuentas"] == []
    assert result["total_cuentas"] == 0
    assert result["report_type"] == "libro_auxiliar"


def test_single_account_type_only():
    """One account → one group with correct totals."""
    svc = MagicMock()
    svc.get_daily_journal.return_value = [
        _make_row(debito=100_000.0, credito=0.0),
        _make_row(debito=50_000.0, credito=0.0),
    ]

    result = build_libro_auxiliar(None, {"start_date": _START, "end_date": _END}, svc)

    assert result["total_cuentas"] == 1
    cuenta = result["cuentas"][0]
    assert cuenta["cuenta"] == "1110"
    assert cuenta["total_debito"] == 150_000.0
    assert cuenta["saldo"] == 150_000.0


def test_mixed_entries():
    """Multiple accounts → grouped correctly."""
    svc = MagicMock()
    svc.get_daily_journal.return_value = [
        _make_row(debito=100_000.0, credito=0.0),
        _make_row(
            debito=0.0,
            credito=100_000.0,
            cuenta_puc="2105",
            cuenta_nombre="Obligaciones",
        ),
    ]

    result = build_libro_auxiliar(None, {"start_date": _START, "end_date": _END}, svc)

    assert result["total_cuentas"] == 2
    assert result["cuentas"][0]["cuenta"] == "1110"
    assert result["cuentas"][1]["cuenta"] == "2105"


def test_period_filtering():
    """start_date and end_date are forwarded to the DB service."""
    svc = MagicMock()
    svc.get_daily_journal.return_value = []

    build_libro_auxiliar(None, {"start_date": _START, "end_date": _END}, svc)

    call = svc.get_daily_journal.call_args
    assert call.kwargs["start_date"].isoformat().startswith(_START)
    assert call.kwargs["end_date"].isoformat().startswith(_END)


def test_company_nit_filtering():
    """company_nit is forwarded to the DB service."""
    svc = MagicMock()
    svc.get_daily_journal.return_value = []

    build_libro_auxiliar(
        None,
        {"start_date": _START, "end_date": _END, "company_nit": _COMPANY_NIT},
        svc,
    )

    call = svc.get_daily_journal.call_args
    assert call.kwargs["company_nit"] == _COMPANY_NIT
