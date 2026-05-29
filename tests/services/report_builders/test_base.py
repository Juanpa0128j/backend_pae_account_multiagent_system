from decimal import Decimal

from app.services.report_builders._base import (
    _credit_nature_balance,
    _debit_nature_balance,
    _ledger_by_exact,
    _ledger_by_prefix,
    _ledger_by_prefixes,
    _safe_divide,
)


def test_ledger_by_prefix_filters_matching():
    ledger = [{"account": "1105"}, {"account": "2205"}, {"account": "1110"}]
    result = _ledger_by_prefix(ledger, "11")
    assert len(result) == 2
    assert all(r["account"].startswith("11") for r in result)


def test_ledger_by_prefix_empty():
    assert _ledger_by_prefix([], "11") == []


def test_ledger_by_prefixes_combines():
    ledger = [{"account": "1105"}, {"account": "2205"}, {"account": "3105"}]
    result = _ledger_by_prefixes(ledger, ("11", "31"))
    assert len(result) == 2


def test_ledger_by_exact_finds_match():
    ledger = [{"account": "1105"}, {"account": "2205"}]
    result = _ledger_by_exact(ledger, "1105")
    assert result is not None
    assert result["account"] == "1105"


def test_ledger_by_exact_returns_none_when_missing():
    ledger = [{"account": "1105"}]
    assert _ledger_by_exact(ledger, "9999") is None


def test_credit_nature_balance():
    row = {"total_credit": "500000", "total_debit": "0"}
    assert _credit_nature_balance(row) == Decimal("500000")


def test_debit_nature_balance():
    row = {"total_debit": "300000", "total_credit": "0"}
    assert _debit_nature_balance(row) == Decimal("300000")


def test_safe_divide_normal():
    assert _safe_divide(10.0, 4.0) == 2.5


def test_safe_divide_zero_denominator():
    assert _safe_divide(10.0, 0.0) is None
