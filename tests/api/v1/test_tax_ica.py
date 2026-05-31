"""Verify /tax/ica endpoint SQL references the correct column."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from main import app


@pytest.fixture
def client():
    """TestClient with mocked DB that captures executed SQL."""
    executed_sql = {"text": ""}

    def _capture_execute(sql, params):
        executed_sql["text"] = str(sql)
        mock_row = MagicMock()
        mock_row.ingresos = 0
        return MagicMock(fetchone=lambda: mock_row)

    mock_db = MagicMock()
    mock_db.execute.side_effect = _capture_execute

    def _override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app, raise_server_exceptions=False), executed_sql
    app.dependency_overrides.clear()


def test_ica_declaration_sql_uses_company_nit(client):
    """The ICA declaration must join on tp.company_nit, not tp.nit_receptor."""
    test_client, executed_sql = client

    today = date.today()
    first_day = today.replace(day=1)
    with patch("app.api.v1.tax.db_service.get_company_settings", return_value=None):
        rsp = test_client.get(
            "/api/v1/tax/ica",
            params={
                "period_start": first_day.isoformat(),
                "period_end": today.isoformat(),
                "company_nit": "900123456",
            },
        )

    assert rsp.status_code == 200
    sql = executed_sql["text"]
    assert "tp.company_nit" in sql, f"SQL should join on tp.company_nit, got: {sql}"
    assert (
        "tp.nit_receptor" not in sql
    ), f"SQL should not reference tp.nit_receptor, got: {sql}"
