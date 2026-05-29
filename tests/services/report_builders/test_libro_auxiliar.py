from unittest.mock import MagicMock

from app.services.report_builders.libro_auxiliar import build_libro_auxiliar


def test_build_libro_auxiliar_returns_dict():
    svc = MagicMock()
    svc.get_daily_journal.return_value = []
    result = build_libro_auxiliar(MagicMock(), {}, svc)
    assert isinstance(result, dict)
