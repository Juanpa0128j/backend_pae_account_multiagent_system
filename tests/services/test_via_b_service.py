"""Unit tests for ``app.services.via_b_service``.

The service unwraps Vía B's ``FinancialStatement.data`` JSONB into the shapes
expected by the chat service, books API, and dashboard. Tests cover each
reader against in-memory mock statements; no DB roundtrip required.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest


def _stmt(
    *,
    statement_type: str,
    data: dict[str, Any],
    period_end: datetime | None = None,
    source_mode: str = "direct",
) -> MagicMock:
    """Build a mock ``FinancialStatement`` row."""
    row = MagicMock()
    row.statement_type = statement_type
    row.data = data
    row.period_end = period_end or datetime(2026, 3, 31, tzinfo=timezone.utc)
    row.period_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    row.source_mode = source_mode
    return row


def _mock_db(rows: list[MagicMock]) -> MagicMock:
    """Return a SQLAlchemy-like mock that surfaces ``rows`` for any query.

    ``via_b_service`` uses ``query().filter().order_by().first()`` for single
    fetches and ``...all()`` for multi-row reads — both chains return the same
    list filtered by ``statement_type`` when the caller filters by it.
    """
    db = MagicMock()
    query = db.query.return_value
    query.filter.return_value = query
    query.order_by.return_value = query
    query.limit.return_value = query
    query.first.side_effect = lambda: rows[0] if rows else None
    query.all.side_effect = lambda: list(rows)
    return db


class TestGetBalance:
    def test_returns_balance_dict_with_via_a_compatible_keys(self):
        from app.services import via_b_service

        bg = _stmt(
            statement_type="balance_general",
            data={
                "total_activos": 1_200_000,
                "total_pasivos": 500_000,
                "total_patrimonio": 700_000,
                "utilidad_neta": 150_000,
                "accounts": [
                    {"cuenta_puc": "1105", "nombre": "Caja", "saldo": 300_000},
                    {"cuenta_puc": "2205", "nombre": "Proveedores", "saldo": 500_000},
                    {"cuenta_puc": "3105", "nombre": "Capital", "saldo": 550_000},
                ],
            },
        )
        db = _mock_db([bg])

        result = via_b_service.get_balance(db, "800999888")

        assert result is not None
        assert result["source"] == "via_b"
        assert result["activos"] == 1_200_000
        assert result["pasivos"] == 500_000
        assert result["patrimonio_total"] == 700_000
        assert result["utilidad_neta"] == 150_000
        # patrimonio = patrimonio_total - utilidad_neta
        assert result["patrimonio"] == pytest.approx(550_000)
        assert len(result["activos_detalle"]) == 1
        assert result["activos_detalle"][0]["codigo"] == "1105"
        assert len(result["pasivos_detalle"]) == 1
        assert len(result["patrimonio_detalle"]) == 1
        assert result["cuadre"] is True

    def test_returns_none_when_no_balance_uploaded(self):
        from app.services import via_b_service

        db = _mock_db([])
        assert via_b_service.get_balance(db, "800999888") is None

    def test_flags_descuadre_when_totals_do_not_match(self):
        from app.services import via_b_service

        bg = _stmt(
            statement_type="balance_general",
            data={
                "total_activos": 1_000_000,
                "total_pasivos": 400_000,
                "total_patrimonio": 500_000,  # 400 + 500 != 1000
                "utilidad_neta": 0,
                "accounts": [],
            },
        )
        db = _mock_db([bg])

        result = via_b_service.get_balance(db, "800999888")
        assert result["cuadre"] is False
        assert "DESCUADRE" in result["mensaje_cuadre"]


class TestPeriodSelection:
    """``period_end`` selects a specific month; missing month → None."""

    def _two_balances(self):
        dec = _stmt(
            statement_type="balance_general",
            data={"total_activos": 500_000, "total_pasivos": 200_000, "accounts": []},
            period_end=datetime(2025, 12, 31, tzinfo=timezone.utc),
        )
        jan = _stmt(
            statement_type="balance_general",
            data={"total_activos": 660_000, "total_pasivos": 169_000, "accounts": []},
            period_end=datetime(2026, 1, 31, tzinfo=timezone.utc),
        )
        # _mock_db returns rows newest-first as the real desc query would.
        return _mock_db([jan, dec])

    def test_no_period_returns_latest(self):
        from app.services import via_b_service

        db = self._two_balances()
        result = via_b_service.get_balance(db, "800999888")
        assert result["activos"] == 660_000  # January (latest)

    def test_period_end_selects_matching_month(self):
        from datetime import date

        from app.services import via_b_service

        db = self._two_balances()
        result = via_b_service.get_balance(
            db, "800999888", period_end=date(2025, 12, 31)
        )
        assert result is not None
        assert result["activos"] == 500_000  # December, not the latest

    def test_period_end_with_no_match_returns_none(self):
        from datetime import date

        from app.services import via_b_service

        db = self._two_balances()
        # November isn't loaded → None (caller surfaces period_not_found).
        result = via_b_service.get_balance(
            db, "800999888", period_end=date(2025, 11, 30)
        )
        assert result is None

    def test_list_periods_returns_all_iso_dates(self):
        from app.services import via_b_service

        db = self._two_balances()
        periods = via_b_service.list_periods(db, "800999888", "balance_general")
        assert periods == ["2026-01-31", "2025-12-31"]


class TestGetPnl:
    def test_returns_pnl_with_aggregated_totals(self):
        from app.services import via_b_service

        er = _stmt(
            statement_type="estado_resultados",
            data={
                "total_ingresos": 900_000,
                "total_gastos": 400_000,
                "total_costo_ventas": 300_000,
                "utilidad_neta": 200_000,
                "accounts": [
                    {"cuenta_puc": "4135", "nombre": "Ventas", "saldo": 900_000},
                    {
                        "cuenta_puc": "5105",
                        "nombre": "Gastos personal",
                        "saldo": 400_000,
                    },
                    {
                        "cuenta_puc": "6135",
                        "nombre": "Costo mercancía",
                        "saldo": 300_000,
                    },
                ],
            },
        )
        db = _mock_db([er])

        result = via_b_service.get_pnl(db, "800999888")

        assert result is not None
        assert result["total_ingresos"] == 900_000
        assert result["total_gastos"] == 400_000
        assert result["total_costo_ventas"] == 300_000
        assert result["utilidad_neta"] == 200_000
        assert result["utilidad_bruta"] == 600_000
        assert len(result["ingresos"]) == 1
        assert len(result["gastos"]) == 1
        assert len(result["costo_ventas"]) == 1

    def test_falls_back_to_sum_when_totals_missing(self):
        """If the upload only has ``accounts`` and no rolled-up totals, sum them."""
        from app.services import via_b_service

        er = _stmt(
            statement_type="estado_resultados",
            data={
                "accounts": [
                    {"cuenta_puc": "4135", "nombre": "Ventas", "saldo": 800_000},
                    {"cuenta_puc": "5105", "nombre": "Gastos", "saldo": 200_000},
                ],
            },
        )
        db = _mock_db([er])

        result = via_b_service.get_pnl(db, "800999888")
        assert result["total_ingresos"] == 800_000
        assert result["total_gastos"] == 200_000

    def test_returns_none_when_no_pnl_uploaded(self):
        from app.services import via_b_service

        db = _mock_db([])
        assert via_b_service.get_pnl(db, "800999888") is None


class TestGetLibroAuxiliarAndBalanceRows:
    """Smoke tests for the books-shaped readers (parity with the previous
    private helpers ``_via_b_libro_auxiliar`` / ``_via_b_balance``)."""

    def test_get_libro_auxiliar_returns_book_rows(self):
        from app.services import via_b_service

        la = _stmt(
            statement_type="libro_auxiliar",
            data={
                "lines": [
                    {
                        "fecha": "2026-02-15",
                        "comprobante": "F-001",
                        "cuenta_puc": "1105",
                        "tercero_nit": "900111222",
                        "detalle": "Cobro factura",
                        "debito": 1000,
                        "credito": 0,
                        "saldo": 1000,
                    },
                ]
            },
        )
        db = _mock_db([la])

        rows = via_b_service.get_libro_auxiliar(db, "800999888")
        assert len(rows) == 1
        assert rows[0]["cuenta"] == "1105"
        assert rows[0]["debito"] == 1000.0
        assert rows[0]["tercero_nit"] == "900111222"

    def test_get_balance_rows_returns_flat_book_rows(self):
        from app.services import via_b_service

        bg = _stmt(
            statement_type="balance_general",
            data={
                "accounts": [
                    {"cuenta_puc": "1105", "nombre": "Caja", "saldo": 500},
                    {"cuenta_puc": "2205", "nombre": "Proveedores", "saldo": 200},
                ]
            },
            period_end=datetime(2026, 3, 31, tzinfo=timezone.utc),
        )
        db = _mock_db([bg])

        rows = via_b_service.get_balance_rows(db, "800999888")
        assert len(rows) == 2
        assert rows[0]["cuenta"] == "1105"
        assert rows[0]["saldo"] == 500.0
        assert rows[0]["fecha"] == "2026-03-31"


class TestGetDashboardOverrides:
    def test_aggregates_across_three_statement_types(self):
        from app.services import via_b_service

        bg = _stmt(
            statement_type="balance_general",
            data={"total_activos": 1_000_000, "total_pasivos": 300_000},
        )
        er = _stmt(
            statement_type="estado_resultados",
            data={"utilidad_neta": 200_000},
        )
        la = _stmt(
            statement_type="libro_auxiliar",
            data={
                "lines": [
                    {"cuenta_puc": "1105", "debito": 50_000, "credito": 0},
                    {"cuenta_puc": "1110", "debito": 0, "credito": 10_000},
                    {"cuenta_puc": "5105", "debito": 80_000, "credito": 0},  # not 11*
                ]
            },
        )
        db = _mock_db([bg, er, la])

        result = via_b_service.get_dashboard_overrides(db, "800999888")
        assert result["total_activos"] == 1_000_000
        assert result["total_pasivos"] == 300_000
        assert result["utilidad_neta"] == 200_000
        assert result["efectivo"] == 40_000  # 50000 debit - 10000 credit
        assert result["statements_count"] == 3
        assert result["derivation_ready"] is True


class TestGetMonthlyTrend:
    def test_returns_none_when_fewer_than_two_pnls(self):
        from app.services import via_b_service

        er = _stmt(
            statement_type="estado_resultados",
            data={"total_ingresos": 100_000, "total_gastos": 50_000},
        )
        db = _mock_db([er])
        assert via_b_service.get_monthly_trend(db, "800999888", months=6) is None

    def test_returns_chronological_points_for_multiple_pnls(self):
        from app.services import via_b_service

        # `all()` returns rows ordered period_end DESC (matches the production
        # query). via_b_service should flip them to chronological order.
        er_mar = _stmt(
            statement_type="estado_resultados",
            data={"total_ingresos": 900_000, "total_gastos": 400_000},
            period_end=datetime(2026, 3, 31, tzinfo=timezone.utc),
        )
        er_feb = _stmt(
            statement_type="estado_resultados",
            data={"total_ingresos": 700_000, "total_gastos": 350_000},
            period_end=datetime(2026, 2, 28, tzinfo=timezone.utc),
        )
        db = _mock_db([er_mar, er_feb])

        result = via_b_service.get_monthly_trend(db, "800999888", months=6)
        assert result is not None
        assert len(result["data"]) == 2
        assert result["data"][0]["month"] == "2026-02"
        assert result["data"][1]["month"] == "2026-03"
        assert result["data"][1]["ingresos"] == 900_000
