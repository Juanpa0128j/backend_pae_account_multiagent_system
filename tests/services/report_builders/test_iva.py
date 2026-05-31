from unittest.mock import MagicMock

from app.services.report_builders.iva import build_iva


def test_build_iva_returns_dict():
    svc = MagicMock()
    svc.get_general_ledger.return_value = []
    result = build_iva(MagicMock(), {}, svc)
    assert isinstance(result, dict)
