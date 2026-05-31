from unittest.mock import MagicMock

from app.services.report_builders.cambios_patrimonio import build_cambios_patrimonio


def test_build_cambios_patrimonio_returns_dict():
    svc = MagicMock()
    svc.get_general_ledger.return_value = []
    result = build_cambios_patrimonio(MagicMock(), {}, svc)
    assert isinstance(result, dict)
