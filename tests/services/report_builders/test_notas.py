from unittest.mock import MagicMock, patch

from app.services.report_builders.notas import build_notas_eeff


def test_build_notas_returns_dict():
    svc = MagicMock()
    svc.get_balance_sheet.return_value = {"assets": 0, "liabilities": 0, "equity": 0}
    with patch(
        "app.services.report_builders.notas._fetch_rag_referencias", return_value=[]
    ):
        result = build_notas_eeff(MagicMock(), {}, svc)
    assert isinstance(result, dict)
