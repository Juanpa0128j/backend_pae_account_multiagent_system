"""Tests for the IVA report builder."""

from unittest.mock import MagicMock


from app.services.report_builders.iva import build_iva

_START = "2026-01-01"
_END = "2026-01-31"
_COMPANY_NIT = "900123456"


def test_empty_ledger_returns_zeros():
    """No journal entries → all IVA values zero."""
    svc = MagicMock()
    svc.get_general_ledger.return_value = []

    result = build_iva(None, {"start_date": _START, "end_date": _END}, svc)

    assert result["iva_generado"] == 0.0
    assert result["iva_descontable"] == 0.0
    assert result["iva_a_pagar"] == 0.0
    assert result["iva_status"] == "saldo_cero"
    assert result["report_type"] == "iva_report"


def test_single_account_type_only():
    """Only IVA Generado present → descontable is zero."""
    svc = MagicMock()
    svc.get_general_ledger.return_value = [
        {
            "account": "240808",
            "name": "IVA Generado",
            "total_debit": 0.0,
            "total_credit": 900_000.0,
            "net_balance": -900_000.0,
        }
    ]

    result = build_iva(None, {"start_date": _START, "end_date": _END}, svc)

    assert result["iva_generado"] == 900_000.0
    assert result["iva_descontable"] == 0.0
    assert result["iva_a_pagar"] == 900_000.0
    assert result["iva_status"] == "saldo_a_pagar"


def test_mixed_entries():
    """Both IVA accounts present → net calculated correctly."""
    svc = MagicMock()
    svc.get_general_ledger.return_value = [
        {
            "account": "240808",
            "name": "IVA Generado",
            "total_debit": 0.0,
            "total_credit": 900_000.0,
            "net_balance": -900_000.0,
        },
        {
            "account": "240802",
            "name": "IVA Descontable",
            "total_debit": 300_000.0,
            "total_credit": 0.0,
            "net_balance": 300_000.0,
        },
    ]

    result = build_iva(None, {"start_date": _START, "end_date": _END}, svc)

    assert result["iva_generado"] == 900_000.0
    assert result["iva_descontable"] == 300_000.0
    assert result["iva_a_pagar"] == 600_000.0
    assert result["iva_status"] == "saldo_a_pagar"


def test_period_filtering():
    """start_date and end_date are forwarded to the DB service."""
    svc = MagicMock()
    svc.get_general_ledger.return_value = []

    build_iva(None, {"start_date": _START, "end_date": _END}, svc)

    call = svc.get_general_ledger.call_args
    assert call.kwargs["start_date"].isoformat().startswith(_START)
    assert call.kwargs["end_date"].isoformat().startswith(_END)


def test_company_nit_filtering():
    """company_nit is forwarded to the DB service."""
    svc = MagicMock()
    svc.get_general_ledger.return_value = []

    build_iva(
        None,
        {"start_date": _START, "end_date": _END, "company_nit": _COMPANY_NIT},
        svc,
    )

    call = svc.get_general_ledger.call_args
    assert call.kwargs["company_nit"] == _COMPANY_NIT
