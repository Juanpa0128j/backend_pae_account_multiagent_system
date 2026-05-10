"""Tests for canonical financial statement schemas."""

from decimal import Decimal

import pytest


class TestBalanceGeneralRoundTrip:
    def test_balance_general_round_trip(self):
        from app.models.canonical_schemas import AccountLine, BalanceGeneralCanonical

        original = BalanceGeneralCanonical(
            period_start="2026-01-01",
            period_end="2026-03-31",
            company_nit="900123456",
            activos=[
                AccountLine(
                    codigo="110505",
                    nombre="Caja",
                    saldo=Decimal("2000000"),
                    tipo="activo",
                ),
            ],
            pasivos=[
                AccountLine(
                    codigo="220505",
                    nombre="Proveedores",
                    saldo=Decimal("500000"),
                    tipo="pasivo",
                ),
            ],
            patrimonio=[
                AccountLine(
                    codigo="311505",
                    nombre="Capital",
                    saldo=Decimal("1000000"),
                    tipo="patrimonio",
                ),
            ],
            utilidad_neta=Decimal("500000"),
            patrimonio_total=Decimal("1500000"),
            cuadre=True,
        )
        dumped = original.model_dump()
        reconstructed = BalanceGeneralCanonical.model_validate(dumped)
        assert reconstructed == original


class TestEstadoResultadosRoundTrip:
    def test_estado_resultados_round_trip(self):
        from app.models.canonical_schemas import AccountLine, EstadoResultadosCanonical

        original = EstadoResultadosCanonical(
            period_start="2026-01-01",
            period_end="2026-03-31",
            company_nit="900123456",
            ingresos=[
                AccountLine(
                    codigo="415505",
                    nombre="Ingresos",
                    saldo=Decimal("1000000"),
                    tipo="ingreso",
                ),
            ],
            costo_ventas=[
                AccountLine(
                    codigo="613505",
                    nombre="Costos",
                    saldo=Decimal("300000"),
                    tipo="costo",
                ),
            ],
            gastos=[
                AccountLine(
                    codigo="513505",
                    nombre="Gastos",
                    saldo=Decimal("200000"),
                    tipo="gasto",
                ),
            ],
            total_ingresos=Decimal("1000000"),
            total_costo_ventas=Decimal("300000"),
            total_gastos=Decimal("200000"),
            utilidad_bruta=Decimal("700000"),
            utilidad_operacional=Decimal("500000"),
            utilidad_neta=Decimal("500000"),
        )
        dumped = original.model_dump()
        reconstructed = EstadoResultadosCanonical.model_validate(dumped)
        assert reconstructed == original


class TestFlujoCajaRoundTrip:
    def test_flujo_caja_round_trip(self):
        from app.models.canonical_schemas import AccountLine, FlujoCajaCanonical

        original = FlujoCajaCanonical(
            period_start="2026-01-01",
            period_end="2026-03-31",
            company_nit="900123456",
            operacionales=[
                AccountLine(
                    codigo="110505",
                    nombre="Caja",
                    saldo=Decimal("500000"),
                    tipo="activo",
                ),
            ],
            inversion=[
                AccountLine(
                    codigo="120505",
                    nombre="Inversiones",
                    saldo=Decimal("100000"),
                    tipo="activo",
                ),
            ],
            financiacion=[
                AccountLine(
                    codigo="210505",
                    nombre="Obligaciones",
                    saldo=Decimal("200000"),
                    tipo="pasivo",
                ),
            ],
            neto_operacionales=Decimal("500000"),
            neto_inversion=Decimal("100000"),
            neto_financiacion=Decimal("200000"),
            variacion_neta=Decimal("400000"),
            saldo_inicial=Decimal("1000000"),
            saldo_final=Decimal("1400000"),
        )
        dumped = original.model_dump()
        reconstructed = FlujoCajaCanonical.model_validate(dumped)
        assert reconstructed == original


class TestHistoricalConversion:
    def test_historical_shape_converts_to_canonical(self):
        from app.models.canonical_schemas import BalanceGeneralCanonical
        from app.services.statement_migration import migrate_balance_general

        old = {
            "total_activos": 2000000.0,
            "total_pasivos": 500000.0,
            "patrimonio_sin_utilidad": 1000000.0,
            "utilidad_neta": 500000.0,
            "total_patrimonio": 1500000.0,
            "cuadre": True,
            "cuentas": [
                {"cuenta": "110505", "saldo": 2000000.0},
                {"cuenta": "220505", "saldo": 500000.0},
                {"cuenta": "311505", "saldo": 1000000.0},
            ],
            "periodo_inicio": "2026-01-01",
            "periodo_fin": "2026-03-31",
        }
        canonical = migrate_balance_general(old, company_nit="900123456")
        assert isinstance(canonical, BalanceGeneralCanonical)
        assert canonical.cuadre is True
        assert canonical.utilidad_neta == Decimal("500000")


class TestValidation:
    def test_canonical_rejects_invalid_account_code(self):
        from app.models.canonical_schemas import AccountLine
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AccountLine(codigo="", nombre="Invalid", saldo=Decimal("0"), tipo="activo")
