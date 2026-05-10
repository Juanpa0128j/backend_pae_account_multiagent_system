"""Tests for statement migration from historical shapes to canonical schemas."""

from decimal import Decimal

from app.models.canonical_schemas import BalanceGeneralCanonical
from app.services.statement_migration import (
    is_canonical_balance,
    migrate_balance_general,
)


class TestMigrateV1Shape:
    """Old shape with total_activos, patrimonio_sin_utilidad, etc."""

    def test_migrate_v1_shape(self):
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
        assert canonical.patrimonio_total == Decimal("1500000")
        assert len(canonical.activos) == 1
        assert canonical.activos[0].codigo == "110505"
        assert canonical.pasivos[0].codigo == "220505"
        assert canonical.patrimonio[0].codigo == "311505"


class TestMigrateV2Shape:
    """Old shape with accounts list (libro_auxiliar style)."""

    def test_migrate_v2_shape(self):
        old = {
            "accounts": [
                {
                    "account": "110505",
                    "name": "Caja",
                    "total_debit": 2000000.0,
                    "total_credit": 0.0,
                    "saldo": 2000000.0,
                },
                {
                    "account": "220505",
                    "name": "Proveedores",
                    "total_debit": 0.0,
                    "total_credit": 500000.0,
                    "saldo": -500000.0,
                },
            ],
            "periodo_inicio": "2026-01-01",
            "periodo_fin": "2026-03-31",
        }
        canonical = migrate_balance_general(old, company_nit="900123456")
        assert isinstance(canonical, BalanceGeneralCanonical)
        assert canonical.activos[0].codigo == "110505"
        assert canonical.pasivos[0].codigo == "220505"


class TestMigrateV3Shape:
    """Old shape with lines list (flat journal lines)."""

    def test_migrate_v3_shape(self):
        old = {
            "lines": [
                {
                    "cuenta_puc": "110505",
                    "cuenta_nombre": "Caja",
                    "debito": 2000000.0,
                    "credito": 0.0,
                },
                {
                    "cuenta_puc": "220505",
                    "cuenta_nombre": "Proveedores",
                    "debito": 0.0,
                    "credito": 500000.0,
                },
            ],
            "periodo_inicio": "2026-01-01",
            "periodo_fin": "2026-03-31",
        }
        canonical = migrate_balance_general(old, company_nit="900123456")
        assert isinstance(canonical, BalanceGeneralCanonical)
        assert canonical.activos[0].codigo == "110505"
        assert canonical.pasivos[0].codigo == "220505"


class TestAlreadyCanonical:
    def test_already_canonical_passes_through(self):
        canonical_in = {
            "period_start": "2026-01-01",
            "period_end": "2026-03-31",
            "company_nit": "900123456",
            "activos": [
                {
                    "codigo": "110505",
                    "nombre": "Caja",
                    "saldo": 2000000.0,
                    "tipo": "activo",
                },
            ],
            "pasivos": [],
            "patrimonio": [],
            "utilidad_neta": 500000.0,
            "patrimonio_total": 1500000.0,
            "cuadre": True,
        }
        assert is_canonical_balance(canonical_in) is True
        canonical = migrate_balance_general(canonical_in, company_nit="900123456")
        assert isinstance(canonical, BalanceGeneralCanonical)
        assert canonical.activos[0].codigo == "110505"
