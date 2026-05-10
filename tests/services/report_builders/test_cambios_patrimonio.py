"""Tests for the cambios en patrimonio report builder."""

from unittest.mock import MagicMock


from app.services.report_builders.cambios_patrimonio import build_cambios_patrimonio

_START = "2026-01-01"
_END = "2026-01-31"
_COMPANY_NIT = "900123456"


def test_empty_ledger_returns_zeros():
    """No journal entries → empty cambios list."""
    svc = MagicMock()
    svc.get_general_ledger.return_value = []

    result = build_cambios_patrimonio(
        None, {"start_date": _START, "end_date": _END}, svc
    )

    assert result["cambios"] == []
    assert result["total_cambios"] == 0
    assert result["report_type"] == "cambios_patrimonio"


def test_single_account_type_only():
    """One patrimonio account → one change entry."""
    svc = MagicMock()
    svc.get_general_ledger.return_value = [
        {
            "account": "3110",
            "name": "Capital",
            "total_debit": 0.0,
            "total_credit": 10_000_000.0,
            "net_balance": -10_000_000.0,
        }
    ]

    result = build_cambios_patrimonio(
        None, {"start_date": _START, "end_date": _END}, svc
    )

    assert result["total_cambios"] == 1
    assert result["cambios"][0]["codigo"] == "3110"
    assert result["cambios"][0]["saldo_final"] == 10_000_000.0


def test_mixed_entries():
    """Multiple patrimonio accounts → all included."""
    svc = MagicMock()
    svc.get_general_ledger.return_value = [
        {
            "account": "3110",
            "name": "Capital",
            "total_debit": 0.0,
            "total_credit": 10_000_000.0,
            "net_balance": -10_000_000.0,
        },
        {
            "account": "3210",
            "name": "Reservas",
            "total_debit": 0.0,
            "total_credit": 2_000_000.0,
            "net_balance": -2_000_000.0,
        },
        {
            "account": "1110",
            "name": "Bancos",
            "total_debit": 5_000_000.0,
            "total_credit": 1_000_000.0,
            "net_balance": 4_000_000.0,
        },
    ]

    result = build_cambios_patrimonio(
        None, {"start_date": _START, "end_date": _END}, svc
    )

    assert result["total_cambios"] == 2
    assert result["cambios"][0]["codigo"] == "3110"
    assert result["cambios"][1]["codigo"] == "3210"


def test_period_filtering():
    """start_date and end_date are forwarded to the DB service."""
    svc = MagicMock()
    svc.get_general_ledger.return_value = []

    build_cambios_patrimonio(None, {"start_date": _START, "end_date": _END}, svc)

    call = svc.get_general_ledger.call_args
    assert call.kwargs["start_date"].isoformat().startswith(_START)
    assert call.kwargs["end_date"].isoformat().startswith(_END)


def test_company_nit_filtering():
    """company_nit is forwarded to the DB service."""
    svc = MagicMock()
    svc.get_general_ledger.return_value = []

    build_cambios_patrimonio(
        None,
        {"start_date": _START, "end_date": _END, "company_nit": _COMPANY_NIT},
        svc,
    )

    call = svc.get_general_ledger.call_args
    assert call.kwargs["company_nit"] == _COMPANY_NIT
