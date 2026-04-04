from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("sqlalchemy")

from app.services.financial_statement_service import (
    BusinessRuleError,
    derive_financial_statements,
    list_financial_statements,
)


class _FakeDB:
    def __init__(self):
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def _row(statement_id: str, statement_type: str, data: dict):
    return SimpleNamespace(
        id=statement_id,
        statement_type=statement_type,
        data=data,
        ingest_id="ing_1",
        period_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        period_end=datetime(2026, 1, 31, tzinfo=timezone.utc),
        entity_nit="900123456-7",
        source_mode="direct",
        created_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )


def test_list_financial_statements_normalizes_nit(monkeypatch):
    fake_db = _FakeDB()
    captured = {}

    def _get_financial_statements(_db, **kwargs):
        captured.update(kwargs)
        return [_row("fs_bg", "balance_general", {"total_activos": 100})]

    import app.services.financial_statement_service as svc

    monkeypatch.setattr(svc, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(svc.db_service, "get_financial_statements", _get_financial_statements)

    out = list_financial_statements(company_nit="900.123.456-7")

    assert out
    assert captured["company_nit"] == "900123456-7"
    assert fake_db.closed is True


def test_derive_financial_statements_requires_bg_er_la(monkeypatch):
    fake_db = _FakeDB()

    def _get_financial_statements(_db, **kwargs):
        if kwargs["statement_type"] == "libro_auxiliar":
            return []
        return [_row(f"fs_{kwargs['statement_type']}", kwargs["statement_type"], {})]

    import app.services.financial_statement_service as svc

    monkeypatch.setattr(svc, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(svc.db_service, "get_financial_statements", _get_financial_statements)

    with pytest.raises(BusinessRuleError):
        derive_financial_statements(
            company_nit="900123456-7",
            period_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            period_end=datetime(2026, 1, 31, tzinfo=timezone.utc),
        )


def test_derive_financial_statements_persists_outputs_and_lineage(monkeypatch):
    fake_db = _FakeDB()
    lineage_calls = []

    bg = _row("fs_bg", "balance_general", {"total_activos": 1000, "total_pasivos": 400, "total_patrimonio": 600})
    er = _row("fs_er", "estado_resultados", {"utilidad_neta": 120})
    la = _row("fs_la", "libro_auxiliar", {"lines": [{"cuenta_puc": "110505", "saldo": 250}]})

    def _get_financial_statements(_db, **kwargs):
        t = kwargs["statement_type"]
        if t == "balance_general":
            return [bg]
        if t == "estado_resultados":
            return [er]
        if t == "libro_auxiliar":
            return [la]
        # Derived targets (flujo_de_caja, etc.) don't exist yet — return empty
        return []

    def _create_ingest_job(_db, **kwargs):
        return SimpleNamespace(id="ing_der")

    def _create_financial_statement(_db, **kwargs):
        return SimpleNamespace(id=f"fs_{kwargs['statement_type']}")

    def _create_financial_statement_lineage(_db, **kwargs):
        lineage_calls.append(kwargs)
        return SimpleNamespace(id="fsl_1")

    import app.services.financial_statement_service as svc

    monkeypatch.setattr(svc, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(svc.db_service, "get_financial_statements", _get_financial_statements)
    monkeypatch.setattr(svc.db_service, "create_ingest_job", _create_ingest_job)
    monkeypatch.setattr(svc.db_service, "create_financial_statement", _create_financial_statement)
    monkeypatch.setattr(
        svc.db_service,
        "create_financial_statement_lineage",
        _create_financial_statement_lineage,
    )

    result = derive_financial_statements(
        company_nit="900123456-7",
        period_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        period_end=datetime(2026, 1, 31, tzinfo=timezone.utc),
    )

    assert result["derived_count"] == 3
    assert result["lineage_links"] == 9
    assert len(lineage_calls) == 9
    assert fake_db.committed is True
