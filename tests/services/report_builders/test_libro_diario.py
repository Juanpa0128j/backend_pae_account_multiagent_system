from unittest.mock import MagicMock

from app.services.report_builders.libro_diario import build_libro_diario


def test_build_libro_diario_returns_dict():
    svc = MagicMock()
    svc.get_daily_journal.return_value = []
    result = build_libro_diario(MagicMock(), {}, svc)
    assert isinstance(result, dict)
