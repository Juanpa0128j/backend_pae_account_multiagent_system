"""Tests for the withholdings report builder."""

from unittest.mock import MagicMock


from app.services.report_builders.withholdings import build_withholdings

_START = "2026-01-01"
_END = "2026-01-31"
_COMPANY_NIT = "900123456"


def test_empty_ledger_returns_zeros():
    """No journal entries → all withholding values zero."""
    svc = MagicMock()
    svc.get_general_ledger.return_value = []

    result = build_withholdings(None, {"start_date": _START, "end_date": _END}, svc)

    assert result["retencion_en_la_fuente"] == 0.0
    assert result["retencion_ica"] == 0.0
    assert result["total_retenciones"] == 0.0
    assert result["total_retenciones_status"] == "saldo_cero"
    assert result["report_type"] == "withholdings_report"


def test_single_account_type_only():
    """Only Retefuente present → ReteICA is zero."""
    svc = MagicMock()
    svc.get_general_ledger.return_value = [
        {
            "account": "2365",
            "name": "Retefuente por Pagar",
            "total_debit": 0.0,
            "total_credit": 235_000.0,
            "net_balance": -235_000.0,
        }
    ]

    result = build_withholdings(None, {"start_date": _START, "end_date": _END}, svc)

    assert result["retencion_en_la_fuente"] == 235_000.0
    assert result["retencion_ica"] == 0.0
    assert result["total_retenciones"] == 235_000.0
    assert result["total_retenciones_status"] == "saldo_a_pagar"


def test_mixed_entries():
    """Both retention accounts present → total calculated correctly."""
    svc = MagicMock()
    svc.get_general_ledger.return_value = [
        {
            "account": "2365",
            "name": "Retefuente por Pagar",
            "total_debit": 0.0,
            "total_credit": 235_000.0,
            "net_balance": -235_000.0,
        },
        {
            "account": "2368",
            "name": "ReteICA por Pagar",
            "total_debit": 0.0,
            "total_credit": 65_000.0,
            "net_balance": -65_000.0,
        },
    ]

    result = build_withholdings(None, {"start_date": _START, "end_date": _END}, svc)

    assert result["retencion_en_la_fuente"] == 235_000.0
    assert result["retencion_ica"] == 65_000.0
    assert result["total_retenciones"] == 300_000.0
    assert result["total_retenciones_status"] == "saldo_a_pagar"


def test_period_filtering():
    """start_date and end_date are forwarded to the DB service."""
    svc = MagicMock()
    svc.get_general_ledger.return_value = []

    build_withholdings(None, {"start_date": _START, "end_date": _END}, svc)

    call = svc.get_general_ledger.call_args
    assert call.kwargs["start_date"].isoformat().startswith(_START)
    assert call.kwargs["end_date"].isoformat().startswith(_END)


def test_company_nit_filtering():
    """company_nit is forwarded to the DB service."""
    svc = MagicMock()
    svc.get_general_ledger.return_value = []

    build_withholdings(
        None,
        {"start_date": _START, "end_date": _END, "company_nit": _COMPANY_NIT},
        svc,
    )

    call = svc.get_general_ledger.call_args
    assert call.kwargs["company_nit"] == _COMPANY_NIT
