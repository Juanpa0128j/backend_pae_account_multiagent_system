from datetime import datetime, timezone
from types import SimpleNamespace

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


def _row(
    statement_id: str,
    statement_type: str,
    data: dict,
    *,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    frequency: str | None = None,
):
    return SimpleNamespace(
        id=statement_id,
        statement_type=statement_type,
        data=data,
        ingest_id="ing_1",
        period_start=period_start or datetime(2026, 1, 1, tzinfo=timezone.utc),
        period_end=period_end or datetime(2026, 1, 31, tzinfo=timezone.utc),
        entity_nit="900123456-7",
        source_mode="direct",
        frequency=frequency,
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
    monkeypatch.setattr(
        svc.db_service, "get_financial_statements", _get_financial_statements
    )

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
    monkeypatch.setattr(
        svc.db_service, "get_financial_statements", _get_financial_statements
    )

    with pytest.raises(BusinessRuleError):
        derive_financial_statements(
            company_nit="900123456-7",
            period_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            period_end=datetime(2026, 1, 31, tzinfo=timezone.utc),
        )


def test_derive_financial_statements_persists_outputs_and_lineage(monkeypatch):
    fake_db = _FakeDB()
    lineage_calls = []

    # Annual rows so we get past the new annual gate (paso 6 of the rework).
    annual_start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    annual_end = datetime(2025, 12, 31, tzinfo=timezone.utc)
    bg = _row(
        "fs_bg",
        "balance_general",
        {"total_activos": 1000, "total_pasivos": 400, "total_patrimonio": 600},
        period_start=annual_start,
        period_end=annual_end,
        frequency="annual",
    )
    er = _row(
        "fs_er",
        "estado_resultados",
        {"utilidad_neta": 120},
        period_start=annual_start,
        period_end=annual_end,
        frequency="annual",
    )
    la = _row(
        "fs_la",
        "libro_auxiliar",
        {"lines": [{"cuenta_puc": "110505", "saldo": 250}]},
        period_start=annual_start,
        period_end=annual_end,
        frequency="annual",
    )

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
    monkeypatch.setattr(
        svc.db_service, "get_financial_statements", _get_financial_statements
    )
    monkeypatch.setattr(svc.db_service, "create_ingest_job", _create_ingest_job)
    monkeypatch.setattr(
        svc.db_service, "create_financial_statement", _create_financial_statement
    )
    monkeypatch.setattr(
        svc.db_service,
        "create_financial_statement_lineage",
        _create_financial_statement_lineage,
    )
    # _load_prior_balance calls db.query() — patch it to return the BG fixture
    monkeypatch.setattr(svc, "_load_prior_balance", lambda _db, _nit, _start: bg)

    result = derive_financial_statements(
        company_nit="900123456-7",
        period_start=annual_start,
        period_end=annual_end,
    )

    assert result["derived_count"] == 3
    # Source rows are BG + ER + LA + prior_bg (4 sources × 3 targets = 12 links).
    # The earlier hard-coded ``9`` reflected the legacy 3-input shape; the new
    # derivation also records the prior-period source explicitly.
    assert result["lineage_links"] == 12
    assert len(lineage_calls) == 12
    assert fake_db.committed is True
