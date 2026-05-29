from unittest.mock import MagicMock

from app.services.report_builders.withholdings import build_withholdings


def test_build_withholdings_returns_dict():
    svc = MagicMock()
    svc.get_general_ledger.return_value = []
    result = build_withholdings(MagicMock(), {}, svc)
    assert isinstance(result, dict)
