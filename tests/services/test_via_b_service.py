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
    period_start: datetime | None = None,
    source_mode: str = "direct",
    frequency: str | None = None,
) -> MagicMock:
    """Build a mock ``FinancialStatement`` row."""
    row = MagicMock()
    row.statement_type = statement_type
    row.data = data
    row.period_end = period_end or datetime(2026, 3, 31, tzinfo=timezone.utc)
    row.period_start = period_start or datetime(2026, 1, 1, tzinfo=timezone.utc)
    row.source_mode = source_mode
    row.frequency = frequency
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

        # Annual periods so the new annual-only derivation_ready gate fires.
        annual_start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        annual_end = datetime(2025, 12, 31, tzinfo=timezone.utc)
        bg = _stmt(
            statement_type="balance_general",
            data={"total_activos": 1_000_000, "total_pasivos": 300_000},
            period_start=annual_start,
            period_end=annual_end,
            frequency="annual",
        )
        er = _stmt(
            statement_type="estado_resultados",
            data={"utilidad_neta": 200_000},
            period_start=annual_start,
            period_end=annual_end,
            frequency="annual",
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
            period_start=annual_start,
            period_end=annual_end,
            frequency="annual",
        )
        db = _mock_db([bg, er, la])

        result = via_b_service.get_dashboard_overrides(db, "800999888")
        assert result["total_activos"] == 1_000_000
        assert result["total_pasivos"] == 300_000
        assert result["utilidad_neta"] == 200_000
        assert result["efectivo"] == 40_000  # 50000 debit - 10000 credit
        assert result["statements_count"] == 3
        assert result["derivation_ready"] is True
        # New: annual gate also exposes the matching period for the FE.
        assert result["derivation_annual_period"] == "2025-12-31"


class TestResolveUtilidadNeta:
    """Probing order: top-level → patrimonio.resultados_del_ejercicio → 3605* leaves."""

    def test_top_level_wins(self):
        from app.services import via_b_service

        data = {
            "utilidad_neta": 10_000_000,
            "patrimonio": {"resultados_del_ejercicio": 999_999},
            "accounts": [{"cuenta_puc": "3605", "saldo": 0}],
        }
        assert via_b_service.resolve_utilidad_neta(data) == 10_000_000.0

    def test_falls_back_to_patrimonio_nested(self):
        from app.services import via_b_service

        data = {
            "patrimonio": {"resultados_del_ejercicio": "8982254.4"},
            "accounts": [],
        }
        assert via_b_service.resolve_utilidad_neta(data) == pytest.approx(8_982_254.4)

    def test_falls_back_to_account_3605(self):
        from app.services import via_b_service

        data = {
            "accounts": [
                {"cuenta_puc": "360505", "saldo": 5_000_000},
                {"cuenta_puc": "3605", "saldo": 5_000_000},  # parent → dropped
            ]
        }
        assert via_b_service.resolve_utilidad_neta(data) == 5_000_000.0

    def test_returns_zero_when_no_source(self):
        from app.services import via_b_service

        assert via_b_service.resolve_utilidad_neta({}) == 0.0


class TestLatestCommonPeriod:
    """Patches the internal ``_statements`` so each statement_type returns its
    own list — the generic ``_mock_db`` can't distinguish filters."""

    @staticmethod
    def _patched_statements(rows_by_type):
        def _stub(_db, _nit, statement_type):
            return rows_by_type.get(statement_type, [])

        from unittest.mock import patch as _patch

        return _patch("app.services.via_b_service._statements", side_effect=_stub)

    def test_returns_shared_period_when_all_three_align(self):
        from datetime import date

        from app.services import via_b_service

        jan = datetime(2026, 1, 31, tzinfo=timezone.utc)
        rows_by_type = {
            "balance_general": [
                _stmt(statement_type="balance_general", data={}, period_end=jan)
            ],
            "estado_resultados": [
                _stmt(statement_type="estado_resultados", data={}, period_end=jan)
            ],
            "libro_auxiliar": [
                _stmt(statement_type="libro_auxiliar", data={}, period_end=jan)
            ],
        }
        with self._patched_statements(rows_by_type):
            assert via_b_service.latest_common_period(MagicMock(), "800999888") == date(
                2026, 1, 31
            )

    def test_returns_none_when_no_overlap(self):
        from app.services import via_b_service

        jan = datetime(2026, 1, 31, tzinfo=timezone.utc)
        dec = datetime(2025, 12, 31, tzinfo=timezone.utc)
        rows_by_type = {
            "balance_general": [
                _stmt(statement_type="balance_general", data={}, period_end=jan)
            ],
            "estado_resultados": [
                _stmt(statement_type="estado_resultados", data={}, period_end=dec)
            ],
            "libro_auxiliar": [
                _stmt(statement_type="libro_auxiliar", data={}, period_end=jan)
            ],
        }
        with self._patched_statements(rows_by_type):
            assert via_b_service.latest_common_period(MagicMock(), "800999888") is None

    def test_returns_none_when_one_type_missing(self):
        from app.services import via_b_service

        jan = datetime(2026, 1, 31, tzinfo=timezone.utc)
        rows_by_type = {
            "balance_general": [
                _stmt(statement_type="balance_general", data={}, period_end=jan)
            ],
            "estado_resultados": [
                _stmt(statement_type="estado_resultados", data={}, period_end=jan)
            ],
            "libro_auxiliar": [],  # missing
        }
        with self._patched_statements(rows_by_type):
            assert via_b_service.latest_common_period(MagicMock(), "800999888") is None


class TestGetIvaReport:
    def _bg(self, accounts):
        return _stmt(statement_type="balance_general", data={"accounts": accounts})

    def test_leaf_subaccounts_split_into_generado_and_descontable(self):
        from app.services import via_b_service

        bg = self._bg(
            [
                {"cuenta_puc": "24080101", "saldo": 4_298_712},  # generado leaf
                {"cuenta_puc": "240801", "saldo": 4_298_712},  # parent → ignored
                {"cuenta_puc": "24080203", "saldo": -233_780},  # descontable leaf
                {"cuenta_puc": "240802", "saldo": -233_780},  # parent → ignored
                {"cuenta_puc": "2408", "saldo": 4_064_932},  # grandparent → ignored
            ]
        )
        result = via_b_service.get_iva_report(_mock_db([bg]), "800999888")

        assert result["iva_generado"] == 4_298_712.0
        assert result["iva_descontable"] == 233_780.0
        assert result["iva_a_pagar"] == 4_298_712.0 - 233_780.0
        assert result["iva_status"] == "saldo_a_pagar"
        assert result["source"] == "via_b"

    def test_only_parent_account_used_when_no_leaves(self):
        from app.services import via_b_service

        bg = self._bg([{"cuenta_puc": "2408", "saldo": 4_064_932}])
        result = via_b_service.get_iva_report(_mock_db([bg]), "800999888")

        assert result["iva_generado"] == 4_064_932.0
        assert result["iva_descontable"] == 0.0
        assert result["iva_status"] == "saldo_a_pagar"

    def test_saldo_a_favor_when_descontable_exceeds_generado(self):
        from app.services import via_b_service

        bg = self._bg(
            [
                {"cuenta_puc": "240801", "saldo": 100_000},
                {"cuenta_puc": "240802", "saldo": -300_000},
            ]
        )
        result = via_b_service.get_iva_report(_mock_db([bg]), "800999888")

        assert result["iva_a_pagar"] == -200_000.0
        assert result["iva_status"] == "saldo_a_favor"

    def test_returns_none_when_no_balance_uploaded(self):
        from app.services import via_b_service

        assert via_b_service.get_iva_report(_mock_db([]), "800999888") is None


class TestGetWithholdingsReport:
    def _bg(self, accounts):
        return _stmt(statement_type="balance_general", data={"accounts": accounts})

    def test_retefuente_and_reteica_summed_from_leaf_subaccounts(self):
        from app.services import via_b_service

        bg = self._bg(
            [
                {"cuenta_puc": "236505", "saldo": 500_000},  # retefuente leaf
                {"cuenta_puc": "236525", "saldo": 200_000},  # retefuente leaf
                {"cuenta_puc": "2365", "saldo": 700_000},  # parent → ignored
                {"cuenta_puc": "236805", "saldo": 80_000},  # reteica leaf
                {"cuenta_puc": "2368", "saldo": 80_000},  # parent → ignored
            ]
        )
        result = via_b_service.get_withholdings_report(_mock_db([bg]), "800999888")

        assert result["retencion_en_la_fuente"] == 700_000.0
        assert result["retencion_ica"] == 80_000.0
        assert result["total_retenciones"] == 780_000.0
        assert result["source"] == "via_b"

    def test_returns_zeros_when_no_retencion_accounts_in_balance(self):
        from app.services import via_b_service

        bg = self._bg([{"cuenta_puc": "1105", "saldo": 50_000}])
        result = via_b_service.get_withholdings_report(_mock_db([bg]), "800999888")

        assert result["retencion_en_la_fuente"] == 0.0
        assert result["retencion_ica"] == 0.0


class TestGetIcaReport:
    def test_ica_uses_estado_resultados_ingresos_times_tasa(self):
        from decimal import Decimal

        from app.services import via_b_service

        er = _stmt(
            statement_type="estado_resultados",
            data={"total_ingresos": 22_625_936, "accounts": []},
        )
        result = via_b_service.get_ica_report(
            _mock_db([er]), "800999888", tasa_ica=Decimal("0.00690")
        )

        assert result["ingresos_brutos"] == 22_625_936.0
        assert result["tasa_ica"] == 0.0069
        # _calc_ica rounds to integer (per Colombian DIAN); accept either int or
        # exact float depending on the implementation.
        assert abs(result["ica_a_pagar"] - 156_118.96) < 1.0
        assert result["source"] == "via_b"

    def test_returns_none_when_no_estado_resultados(self):
        from app.services import via_b_service

        assert via_b_service.get_ica_report(_mock_db([]), "800999888") is None


class TestGetRentaProvisionReport:
    def test_renta_uses_utilidad_neta_times_tasa(self):
        from decimal import Decimal

        from app.services import via_b_service

        er = _stmt(
            statement_type="estado_resultados",
            data={"utilidad_neta": 8_983_445.4, "accounts": []},
        )
        result = via_b_service.get_renta_provision_report(
            _mock_db([er]), "800999888", tasa_renta=Decimal("0.35")
        )

        assert result["utilidad_antes_impuestos"] == 8_983_445.4
        assert result["tasa_renta"] == 0.35
        assert abs(result["provision_renta"] - 3_144_205.89) < 0.01
        assert result["source"] == "via_b"

    def test_provision_is_zero_when_there_is_a_loss(self):
        from decimal import Decimal

        from app.services import via_b_service

        er = _stmt(
            statement_type="estado_resultados",
            data={"utilidad_neta": -5_000_000, "accounts": []},
        )
        result = via_b_service.get_renta_provision_report(
            _mock_db([er]), "800999888", tasa_renta=Decimal("0.35")
        )

        assert result["utilidad_antes_impuestos"] == -5_000_000.0
        assert result["provision_renta"] == 0.0  # clamped


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


class TestFrequencyHelpers:
    """``infer_frequency`` + ``is_annual`` underpin the derivation gate."""

    def test_infer_frequency_thresholds(self):
        from app.services.financial_statement_service import infer_frequency

        assert (
            infer_frequency(
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 1, 31, tzinfo=timezone.utc),
            )
            == "monthly"
        )
        assert (
            infer_frequency(
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 3, 31, tzinfo=timezone.utc),
            )
            == "quarterly"
        )
        assert (
            infer_frequency(
                datetime(2025, 1, 1, tzinfo=timezone.utc),
                datetime(2025, 12, 31, tzinfo=timezone.utc),
            )
            == "annual"
        )
        assert (
            infer_frequency(
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 1, 5, tzinfo=timezone.utc),
            )
            == "custom"
        )
        assert infer_frequency(None, None) is None

    def test_normalize_periodicidad(self):
        from app.services.financial_statement_service import normalize_periodicidad

        assert normalize_periodicidad("anual") == "annual"
        assert normalize_periodicidad("MENSUAL") == "monthly"
        assert normalize_periodicidad("foo") is None
        assert normalize_periodicidad(None) is None

    def test_is_annual_uses_column_then_span_fallback(self):
        from app.services.financial_statement_service import is_annual

        annual = _stmt(
            statement_type="balance_general",
            data={},
            period_start=datetime(2025, 1, 1, tzinfo=timezone.utc),
            period_end=datetime(2025, 12, 31, tzinfo=timezone.utc),
            frequency=None,  # forces span inference
        )
        monthly = _stmt(
            statement_type="balance_general",
            data={},
            period_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            period_end=datetime(2026, 1, 31, tzinfo=timezone.utc),
            frequency="monthly",
        )
        assert is_annual(annual) is True
        assert is_annual(monthly) is False


class TestSynthesizeBalanceFromLibroAuxiliar:
    """LA → BG conversion: sum class 1/2/3 by natural-side balance."""

    def test_balanced_sample(self):
        from app.services.financial_statement_service import (
            synthesize_balance_from_libro_auxiliar,
        )

        la = {
            "lines": [
                {
                    "cuenta_puc": "1105",
                    "debito": 1000,
                    "credito": 0,
                    "cuenta_nombre": "Caja",
                },
                {
                    "cuenta_puc": "2205",
                    "debito": 0,
                    "credito": 400,
                    "cuenta_nombre": "Proveedores",
                },
                {
                    "cuenta_puc": "3105",
                    "debito": 0,
                    "credito": 600,
                    "cuenta_nombre": "Capital",
                },
            ]
        }
        bg = synthesize_balance_from_libro_auxiliar(la)
        assert bg["total_activos"] == 1000
        assert bg["total_pasivos"] == 400
        assert bg["total_patrimonio"] == 600
        assert bg["source"] == "synthesized_from_libro_auxiliar"
        assert len(bg["accounts"]) == 3

    def test_pnl_classes_ignored_in_balance(self):
        from app.services.financial_statement_service import (
            synthesize_balance_from_libro_auxiliar,
        )

        la = {
            "lines": [
                {"cuenta_puc": "1105", "debito": 100, "credito": 0},
                {"cuenta_puc": "5105", "debito": 50, "credito": 0},  # gasto → ignore
            ]
        }
        bg = synthesize_balance_from_libro_auxiliar(la)
        # Only class 1 counted on the balance side.
        assert len(bg["accounts"]) == 1
        assert bg["accounts"][0]["cuenta_puc"] == "1105"


class TestLibroAuxiliarComprehensive:
    """`la_is_comprehensive` decides whether LA can replace BG+ER."""

    def test_partial_la_not_comprehensive(self):
        from app.services.financial_statement_service import (
            _libro_auxiliar_is_comprehensive,
        )

        only_caja = {"lines": [{"cuenta_puc": "1105", "debito": 100, "credito": 0}]}
        assert _libro_auxiliar_is_comprehensive(only_caja) is False

    def test_only_balance_classes_not_comprehensive(self):
        from app.services.financial_statement_service import (
            _libro_auxiliar_is_comprehensive,
        )

        only_balance = {
            "lines": [
                {"cuenta_puc": "1105", "debito": 100, "credito": 0},
                {"cuenta_puc": "2205", "debito": 0, "credito": 50},
                {"cuenta_puc": "3105", "debito": 0, "credito": 50},
            ]
        }
        assert _libro_auxiliar_is_comprehensive(only_balance) is False

    def test_full_la_comprehensive(self):
        from app.services.financial_statement_service import (
            _libro_auxiliar_is_comprehensive,
        )

        full = {
            "lines": [
                {"cuenta_puc": "1105", "debito": 100, "credito": 0},
                {"cuenta_puc": "2205", "debito": 0, "credito": 50},
                {"cuenta_puc": "3105", "debito": 0, "credito": 50},
                {"cuenta_puc": "4135", "debito": 0, "credito": 200},
                {"cuenta_puc": "5105", "debito": 80, "credito": 0},
            ]
        }
        assert _libro_auxiliar_is_comprehensive(full) is True


class TestLatestCommonPeriodAnnualOnly:
    """``annual_only=True`` filters out monthly closings — the derivation
    gate must not mistake a coincident monthly period for an annual one."""

    def test_only_monthly_uploads_returns_none(self):
        from datetime import date as _date
        from unittest.mock import patch as _patch

        from app.services import via_b_service

        jan = datetime(2026, 1, 31, tzinfo=timezone.utc)
        rows_by_type = {
            "balance_general": [
                _stmt(
                    statement_type="balance_general",
                    data={},
                    period_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    period_end=jan,
                    frequency="monthly",
                )
            ],
            "estado_resultados": [
                _stmt(
                    statement_type="estado_resultados",
                    data={},
                    period_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    period_end=jan,
                    frequency="monthly",
                )
            ],
            "libro_auxiliar": [
                _stmt(
                    statement_type="libro_auxiliar",
                    data={},
                    period_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    period_end=jan,
                    frequency="monthly",
                )
            ],
        }
        with _patch(
            "app.services.via_b_service._statements",
            side_effect=lambda _db, _nit, stype: rows_by_type.get(stype, []),
        ):
            # Without annual_only, the common period is recognised.
            assert via_b_service.latest_common_period(
                MagicMock(), "800999888"
            ) == _date(2026, 1, 31)
            # With annual_only=True, the monthly rows are skipped → None.
            assert (
                via_b_service.latest_common_period(
                    MagicMock(), "800999888", annual_only=True
                )
                is None
            )

    def test_annual_uploads_return_common_date(self):
        from datetime import date as _date
        from unittest.mock import patch as _patch

        from app.services import via_b_service

        dec = datetime(2025, 12, 31, tzinfo=timezone.utc)
        rows_by_type = {
            "balance_general": [
                _stmt(
                    statement_type="balance_general",
                    data={},
                    period_start=datetime(2025, 1, 1, tzinfo=timezone.utc),
                    period_end=dec,
                    frequency="annual",
                )
            ],
            "estado_resultados": [
                _stmt(
                    statement_type="estado_resultados",
                    data={},
                    period_start=datetime(2025, 1, 1, tzinfo=timezone.utc),
                    period_end=dec,
                    frequency="annual",
                )
            ],
            "libro_auxiliar": [
                _stmt(
                    statement_type="libro_auxiliar",
                    data={},
                    period_start=datetime(2025, 1, 1, tzinfo=timezone.utc),
                    period_end=dec,
                    frequency="annual",
                )
            ],
        }
        with _patch(
            "app.services.via_b_service._statements",
            side_effect=lambda _db, _nit, stype: rows_by_type.get(stype, []),
        ):
            assert via_b_service.latest_common_period(
                MagicMock(), "800999888", annual_only=True
            ) == _date(2025, 12, 31)
