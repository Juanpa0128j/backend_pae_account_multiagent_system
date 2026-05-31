"""Unit tests for db_service TarifaRenta helpers.

Covers:
- get_tarifa_renta: exact match, overlap (most specific year_from wins),
  NULL actividad fallback, year_to=NULL open-ended, returns None when no match
- list_tarifas_renta: no filter, year filter
- upsert_tarifa_renta: insert then update
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.services.db_service import (
    get_tarifa_renta,
    list_tarifas_renta,
    upsert_tarifa_renta,
)


def _make_row(
    *,
    id: int = 1,
    regimen: str = "ordinario",
    actividad: str | None = "general",
    tarifa_base: str = "0.3500",
    sobretasa: str = "0.0000",
    year_from: int = 2023,
    year_to: int | None = None,
    base_legal: str | None = "Art. 240 ET",
    notas: str | None = None,
):
    row = MagicMock()
    row.id = id
    row.regimen = regimen
    row.actividad = actividad
    row.tarifa_base = Decimal(tarifa_base)
    row.sobretasa = Decimal(sobretasa)
    row.year_from = year_from
    row.year_to = year_to
    row.base_legal = base_legal
    row.notas = notas
    return row


# ---------------------------------------------------------------------------
# get_tarifa_renta
# ---------------------------------------------------------------------------


class TestGetTarifaRenta:
    """Tests for get_tarifa_renta lookup precedence."""

    def _build_db(self, first_return):
        """
        Build a mock DB that returns `first_return` from the chained query call
        used inside get_tarifa_renta (exact actividad path).
        """
        mock_db = MagicMock()
        chain = mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value
        chain.first.return_value = first_return
        return mock_db

    def test_exact_match_returns_dict(self):
        row = _make_row(tarifa_base="0.3500", sobretasa="0.0000")
        mock_db = self._build_db(row)

        result = get_tarifa_renta(mock_db, "ordinario", "general", 2024)

        assert result is not None
        assert result["tarifa_base"] == pytest.approx(0.35)
        assert result["sobretasa"] == pytest.approx(0.0)
        assert result["tarifa_efectiva"] == pytest.approx(0.35)
        assert result["base_legal"] == "Art. 240 ET"

    def test_exact_match_with_sobretasa(self):
        row = _make_row(
            regimen="ordinario",
            actividad="financiero",
            tarifa_base="0.3500",
            sobretasa="0.0500",
            year_from=2023,
            year_to=2027,
            base_legal="Art. 240 par. 4 ET",
        )
        mock_db = self._build_db(row)

        result = get_tarifa_renta(mock_db, "ordinario", "financiero", 2025)

        assert result is not None
        assert result["tarifa_efectiva"] == pytest.approx(0.40)

    def test_emergency_sobretasa_2026(self):
        """When overlap exists, highest year_from (most specific) wins."""
        # The DB mock returns the 2026 emergency row (Decreto 0150) because
        # get_tarifa_renta orders by year_from DESC and takes first().
        row = _make_row(
            regimen="ordinario",
            actividad="financiero",
            tarifa_base="0.3500",
            sobretasa="0.2000",
            year_from=2026,
            year_to=2026,
            base_legal="Decreto 0150/2026 emergencia económica",
        )
        mock_db = self._build_db(row)

        result = get_tarifa_renta(mock_db, "ordinario", "financiero", 2026)

        assert result is not None
        assert result["tarifa_efectiva"] == pytest.approx(0.55)
        assert "Decreto 0150" in result["base_legal"]

    def test_esal_20_percent(self):
        row = _make_row(
            regimen="esal",
            actividad="general",
            tarifa_base="0.2000",
            sobretasa="0.0000",
            year_from=2017,
            year_to=None,
            base_legal="Art. 19 ET",
        )
        mock_db = self._build_db(row)

        result = get_tarifa_renta(mock_db, "esal", "general", 2026)

        assert result is not None
        assert result["tarifa_efectiva"] == pytest.approx(0.20)

    def test_hidroelectrico_38_percent(self):
        row = _make_row(
            regimen="ordinario",
            actividad="hidroelectrico",
            tarifa_base="0.3500",
            sobretasa="0.0300",
            year_from=2023,
            year_to=2026,
            base_legal="Art. 240 par. 5 ET",
        )
        mock_db = self._build_db(row)

        result = get_tarifa_renta(mock_db, "ordinario", "hidroelectrico", 2025)

        assert result is not None
        assert result["tarifa_efectiva"] == pytest.approx(0.38)

    def test_null_actividad_fallback(self):
        """When exact actividad match fails, should fall back to actividad=NULL row."""
        mock_db = MagicMock()

        # First query (exact actividad) → None
        # Second query (actividad=NULL fallback) → row
        null_row = _make_row(
            regimen="ordinario",
            actividad=None,
            tarifa_base="0.3500",
            sobretasa="0.0000",
            year_from=2023,
            base_legal="Art. 240 ET fallback",
        )

        call_count = [0]

        def fake_filter(*args, **kwargs):
            chain = MagicMock()
            chain.filter.return_value = chain
            chain.order_by.return_value = chain
            call_count[0] += 1
            if call_count[0] == 1:
                chain.first.return_value = None  # exact match fails
            else:
                chain.first.return_value = null_row  # null fallback succeeds
            return chain

        mock_db.query.return_value.filter.side_effect = fake_filter

        result = get_tarifa_renta(mock_db, "ordinario", "otro_sector", 2024)

        assert result is not None
        assert result["base_legal"] == "Art. 240 ET fallback"

    def test_returns_none_when_no_match(self):
        mock_db = MagicMock()
        chain = MagicMock()
        chain.filter.return_value = chain
        chain.order_by.return_value = chain
        chain.first.return_value = None
        mock_db.query.return_value.filter.return_value = chain

        result = get_tarifa_renta(mock_db, "ordinario", "inexistente", 2020)

        assert result is None

    def test_year_to_null_open_ended(self):
        """year_to=NULL rows remain valid for any future year."""
        row = _make_row(
            tarifa_base="0.3500",
            sobretasa="0.0000",
            year_from=2023,
            year_to=None,
        )
        mock_db = self._build_db(row)

        result = get_tarifa_renta(mock_db, "ordinario", "general", 2099)

        assert result is not None
        assert result["tarifa_efectiva"] == pytest.approx(0.35)


# ---------------------------------------------------------------------------
# list_tarifas_renta
# ---------------------------------------------------------------------------


class TestListTarifasRenta:
    def test_no_year_filter(self):
        row = _make_row(id=1, tarifa_base="0.3500", sobretasa="0.0000")
        mock_db = MagicMock()
        mock_db.query.return_value.order_by.return_value.all.return_value = [row]

        result = list_tarifas_renta(mock_db)

        assert len(result) == 1
        assert result[0]["id"] == 1
        assert result[0]["tarifa_efectiva"] == pytest.approx(0.35)

    def test_year_filter_applied(self):
        """When year is given, query must apply year_from/year_to filter."""
        row = _make_row(id=2, year_from=2023, year_to=None)
        mock_db = MagicMock()
        # With year filter, list_tarifas_renta builds: query().filter().order_by().all()
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            row
        ]

        result = list_tarifas_renta(mock_db, year=2026)

        assert len(result) == 1
        assert result[0]["year_from"] == 2023


# ---------------------------------------------------------------------------
# upsert_tarifa_renta
# ---------------------------------------------------------------------------


class TestUpsertTarifaRenta:
    def _build_db(self, first_return):
        """Build a mock DB where query().filter()...first() returns first_return."""
        mock_db = MagicMock()
        chain = MagicMock()
        chain.filter.return_value = chain
        chain.first.return_value = first_return
        mock_db.query.return_value.filter.return_value = chain
        return mock_db

    def test_insert_new_row(self):
        mock_db = self._build_db(None)

        def _refresh(r):
            r.id = 10

        mock_db.refresh.side_effect = _refresh

        upsert_tarifa_renta(
            mock_db,
            regimen="zona_franca",
            actividad="general",
            tarifa_base=Decimal("0.2000"),
            year_from=2017,
        )
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_update_existing_row(self):
        existing = _make_row(id=5, tarifa_base="0.3500", sobretasa="0.0000")
        mock_db = self._build_db(existing)
        mock_db.refresh.side_effect = lambda r: None

        upsert_tarifa_renta(
            mock_db,
            regimen="ordinario",
            actividad="general",
            tarifa_base=Decimal("0.3500"),
            sobretasa=Decimal("0.0500"),
            year_from=2023,
        )

        assert existing.sobretasa == Decimal("0.0500")
        mock_db.commit.assert_called_once()
        mock_db.add.assert_not_called()
