"""Tests for the in-process TTL response cache on GET /api/v1/reports/analysis.

PERF FIX B1. The analysis endpoint always drives an LLM via the reporting
pipeline; these lock the caching contract:
  (a) two identical requests within TTL -> pipeline invoked ONCE.
  (b) ?refresh=true -> bypasses cache, re-invokes the pipeline.
  (c) different company_nit / period -> different key -> separate invocation
      (proves no cross-tenant / cross-period bleed).
  (d) TTL expiry -> miss again (clock injected via the TTL constant, no sleep).

Harness mirrors tests/api/v1/test_handlers_characterization.py: auth + rate
limits are neutralised by autouse fixtures in tests/conftest.py, the reporting
pipeline is mocked at ``app.api.v1.reports.invoke_reporting_pipeline``, and the
DB is an empty in-memory SQLite via a ``get_db`` override.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import response_cache
from app.core.database import Base, get_db
from main import app

_START = "2026-01-01"
_END = "2026-01-31"


def _mock_report(tag: str = "analysis") -> dict:
    return {
        "report_type": tag,
        "period_start": _START,
        "period_end": _END,
        "generated_at": "2026-01-31T00:00:00+00:00",
    }


def _pipeline_ok(report: dict) -> dict:
    return {"status": "ok", "report": report, "agent_log": []}


@pytest.fixture(autouse=True)
def _isolate_cache():
    """Clear the shared in-process cache around every test for isolation."""
    response_cache.clear()
    yield
    response_cache.clear()


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    def _override_get_db():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)
    session.close()


def test_two_identical_requests_invoke_pipeline_once(client):
    # (a) Cache hit within TTL: pipeline runs once, both responses equal.
    with patch(
        "app.api.v1.reports.invoke_reporting_pipeline",
        return_value=_pipeline_ok(_mock_report()),
    ) as mock_pipeline:
        first = client.get(
            "/api/v1/reports/analysis",
            params={"start_date": _START, "end_date": _END, "company_nit": "900123"},
        )
        second = client.get(
            "/api/v1/reports/analysis",
            params={"start_date": _START, "end_date": _END, "company_nit": "900123"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert mock_pipeline.call_count == 1


def test_refresh_true_bypasses_cache(client):
    # (b) ?refresh=true forces a re-run even with a warm cache entry.
    with patch(
        "app.api.v1.reports.invoke_reporting_pipeline",
        return_value=_pipeline_ok(_mock_report()),
    ) as mock_pipeline:
        warm = client.get(
            "/api/v1/reports/analysis",
            params={"start_date": _START, "end_date": _END, "company_nit": "900123"},
        )
        assert warm.status_code == 200
        assert mock_pipeline.call_count == 1

        fresh = client.get(
            "/api/v1/reports/analysis",
            params={
                "start_date": _START,
                "end_date": _END,
                "company_nit": "900123",
                "refresh": "true",
            },
        )

    assert fresh.status_code == 200
    assert fresh.json() == warm.json()
    assert mock_pipeline.call_count == 2


def test_different_key_is_separate_invocation(client):
    # (c) Different NIT and different period -> different keys -> two runs.
    with patch(
        "app.api.v1.reports.invoke_reporting_pipeline",
        return_value=_pipeline_ok(_mock_report()),
    ) as mock_pipeline:
        a = client.get(
            "/api/v1/reports/analysis",
            params={"start_date": _START, "end_date": _END, "company_nit": "900111"},
        )
        # different tenant
        b = client.get(
            "/api/v1/reports/analysis",
            params={"start_date": _START, "end_date": _END, "company_nit": "900222"},
        )
        # same tenant, different period
        c = client.get(
            "/api/v1/reports/analysis",
            params={
                "start_date": "2026-02-01",
                "end_date": "2026-02-28",
                "company_nit": "900111",
            },
        )

    assert a.status_code == b.status_code == c.status_code == 200
    assert mock_pipeline.call_count == 3


def test_ttl_expiry_causes_miss(client, monkeypatch):
    # (d) After the TTL elapses the entry is gone -> pipeline runs again.
    # Deterministic: set a tiny TTL so the set() entry is already expired by the
    # time the second request reads it. No sleep.
    monkeypatch.setattr(response_cache, "ANALYSIS_CACHE_TTL_SECONDS", -1.0)
    with patch(
        "app.api.v1.reports.invoke_reporting_pipeline",
        return_value=_pipeline_ok(_mock_report()),
    ) as mock_pipeline:
        first = client.get(
            "/api/v1/reports/analysis",
            params={"start_date": _START, "end_date": _END, "company_nit": "900123"},
        )
        second = client.get(
            "/api/v1/reports/analysis",
            params={"start_date": _START, "end_date": _END, "company_nit": "900123"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert mock_pipeline.call_count == 2
