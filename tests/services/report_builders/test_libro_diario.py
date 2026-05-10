"""Tests for the libro diario report builder."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock


from app.services.report_builders.libro_diario import build_libro_diario

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
    """No journal entries → empty transacciones list."""
    svc = MagicMock()
    svc.get_daily_journal.return_value = []

    result = build_libro_diario(None, {"start_date": _START, "end_date": _END}, svc)

    assert result["transacciones"] == []
    assert result["total_transacciones"] == 0
    assert result["report_type"] == "libro_diario"


def test_single_account_type_only():
    """One journal entry → one transaction in report."""
    svc = MagicMock()
    svc.get_daily_journal.return_value = [_make_row()]

    result = build_libro_diario(None, {"start_date": _START, "end_date": _END}, svc)

    assert result["total_transacciones"] == 1
    assert result["transacciones"][0]["cuenta_puc"] == "1110"
    assert result["transacciones"][0]["debito"] == 100_000.0


def test_mixed_entries():
    """Multiple entries → all included."""
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

    result = build_libro_diario(None, {"start_date": _START, "end_date": _END}, svc)

    assert result["total_transacciones"] == 2
    assert result["transacciones"][0]["cuenta_puc"] == "1110"
    assert result["transacciones"][1]["cuenta_puc"] == "2105"


def test_period_filtering():
    """start_date and end_date are forwarded to the DB service."""
    svc = MagicMock()
    svc.get_daily_journal.return_value = []

    build_libro_diario(None, {"start_date": _START, "end_date": _END}, svc)

    call = svc.get_daily_journal.call_args
    assert call.kwargs["start_date"].isoformat().startswith(_START)
    assert call.kwargs["end_date"].isoformat().startswith(_END)


def test_company_nit_filtering():
    """company_nit is forwarded to the DB service."""
    svc = MagicMock()
    svc.get_daily_journal.return_value = []

    build_libro_diario(
        None,
        {"start_date": _START, "end_date": _END, "company_nit": _COMPANY_NIT},
        svc,
    )

    call = svc.get_daily_journal.call_args
    assert call.kwargs["company_nit"] == _COMPANY_NIT
