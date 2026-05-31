from unittest.mock import MagicMock, patch

from app.services.report_builders.analysis import build_analysis


def _svc():
    svc = MagicMock()
    svc.get_general_ledger.return_value = []
    svc.get_balance_sheet.return_value = {
        "assets": 0,
        "liabilities": 0,
        "equity": 0,
        "net_profit": 0,
        "total_equity": 0,
        "is_balanced": True,
        "revenue": 0,
    }
    svc.get_monthly_totals_by_class.return_value = {}
    svc.get_top_accounts.return_value = []
    svc.get_top_terceros.return_value = []
    return svc


def test_build_analysis_returns_dict():
    with patch(
        "app.services.report_builders.analysis._fetch_rag_context_text", return_value=""
    ):
        result = build_analysis(MagicMock(), {}, _svc())
    assert isinstance(result, dict)
