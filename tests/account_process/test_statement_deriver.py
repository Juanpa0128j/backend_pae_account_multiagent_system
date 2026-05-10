"""Pure unit tests for StatementDeriver.

No DB, no mocks — just input → output assertions.
"""

from __future__ import annotations

from decimal import Decimal

from app.account_process.statement_deriver import StatementDeriver


class TestDeriveBalanceGeneral:
    def test_empty_entries_returns_zeros(self) -> None:
        result = StatementDeriver.derive_balance_general([])

        assert result["total_activos"] == Decimal("0")
        assert result["total_pasivos"] == Decimal("0")
        assert result["total_patrimonio"] == Decimal("0")
        assert result["utilidad_neta"] == Decimal("0")
        assert result["patrimonio_sin_utilidad"] == Decimal("0")
        assert result["cuadre"] is True
        assert result["cuentas"] == []

    def test_mixed_asset_liability_equity(self) -> None:
        entries = [
            {
                "fecha": "2026-03-15",
                "cuenta": "110505",
                "descripcion": "Caja",
                "tercero_nit": "900123456",
                "detalle": "Efectivo",
                "debito": "1000000",
                "credito": "0",
            },
            {
                "fecha": "2026-03-15",
                "cuenta": "143505",
                "descripcion": "Inventarios",
                "tercero_nit": "900123456",
                "detalle": "Mercancia",
                "debito": "500000",
                "credito": "0",
            },
            {
                "fecha": "2026-03-15",
                "cuenta": "220505",
                "descripcion": "Proveedores",
                "tercero_nit": "900123456",
                "detalle": "CxP",
                "debito": "0",
                "credito": "800000",
            },
            {
                "fecha": "2026-03-15",
                "cuenta": "311505",
                "descripcion": "Capital social",
                "tercero_nit": "900123456",
                "detalle": "Aporte",
                "debito": "0",
                "credito": "700000",
            },
        ]

        result = StatementDeriver.derive_balance_general(entries)

        assert result["total_activos"] == Decimal("1500000")
        assert result["total_pasivos"] == Decimal("800000")
        assert result["total_patrimonio"] == Decimal("700000")
        assert result["utilidad_neta"] == Decimal("0")
        assert result["patrimonio_sin_utilidad"] == Decimal("700000")
        assert result["cuadre"] is True

        cuentas = {c["cuenta"]: c for c in result["cuentas"]}
        assert cuentas["110505"]["saldo"] == Decimal("1000000")
        assert cuentas["143505"]["saldo"] == Decimal("500000")
        assert cuentas["220505"]["saldo"] == Decimal("800000")
        assert cuentas["311505"]["saldo"] == Decimal("700000")

    def test_unbalanced_entries(self) -> None:
        entries = [
            {
                "fecha": "2026-03-15",
                "cuenta": "110505",
                "descripcion": "Caja",
                "tercero_nit": "900123456",
                "detalle": "Efectivo",
                "debito": "1000000",
                "credito": "0",
            },
            {
                "fecha": "2026-03-15",
                "cuenta": "220505",
                "descripcion": "Proveedores",
                "tercero_nit": "900123456",
                "detalle": "CxP",
                "debito": "0",
                "credito": "500000",
            },
        ]

        result = StatementDeriver.derive_balance_general(entries)

        assert result["total_activos"] == Decimal("1000000")
        assert result["total_pasivos"] == Decimal("500000")
        assert result["cuadre"] is False

    def test_utilidad_neta_affects_patrimonio(self) -> None:
        entries = [
            {
                "fecha": "2026-03-15",
                "cuenta": "110505",
                "descripcion": "Caja",
                "tercero_nit": "900123456",
                "detalle": "Efectivo",
                "debito": "2000000",
                "credito": "0",
            },
            {
                "fecha": "2026-03-15",
                "cuenta": "220505",
                "descripcion": "Proveedores",
                "tercero_nit": "900123456",
                "detalle": "CxP",
                "debito": "0",
                "credito": "500000",
            },
            {
                "fecha": "2026-03-15",
                "cuenta": "311505",
                "descripcion": "Capital",
                "tercero_nit": "900123456",
                "detalle": "Aporte",
                "debito": "0",
                "credito": "1000000",
            },
            {
                "fecha": "2026-03-15",
                "cuenta": "415505",
                "descripcion": "Ingresos",
                "tercero_nit": "900123456",
                "detalle": "Venta",
                "debito": "0",
                "credito": "1000000",
            },
            {
                "fecha": "2026-03-15",
                "cuenta": "513505",
                "descripcion": "Gastos",
                "tercero_nit": "900123456",
                "detalle": "Servicios",
                "debito": "200000",
                "credito": "0",
            },
            {
                "fecha": "2026-03-15",
                "cuenta": "613505",
                "descripcion": "Costos",
                "tercero_nit": "900123456",
                "detalle": "Mercancia",
                "debito": "300000",
                "credito": "0",
            },
        ]

        result = StatementDeriver.derive_balance_general(entries)

        # utilidad_neta = ingresos - gastos - costos = 1_000_000 - 200_000 - 300_000 = 500_000
        assert result["utilidad_neta"] == Decimal("500000")
        # patrimonio_sin_utilidad = capital = 1_000_000
        assert result["patrimonio_sin_utilidad"] == Decimal("1000000")
        # total_patrimonio = 1_000_000 + 500_000 = 1_500_000
        assert result["total_patrimonio"] == Decimal("1500000")
        # cuadre: activos (2_000_000) == pasivos (500_000) + total_patrimonio (1_500_000)
        assert result["cuadre"] is True

    def test_single_account_type_only(self) -> None:
        entries = [
            {
                "fecha": "2026-03-15",
                "cuenta": "110505",
                "descripcion": "Caja",
                "tercero_nit": "900123456",
                "detalle": "Efectivo",
                "debito": "500000",
                "credito": "0",
            },
        ]

        result = StatementDeriver.derive_balance_general(entries)

        assert result["total_activos"] == Decimal("500000")
        assert result["total_pasivos"] == Decimal("0")
        assert result["total_patrimonio"] == Decimal("0")
        assert result["utilidad_neta"] == Decimal("0")
        assert result["patrimonio_sin_utilidad"] == Decimal("0")
        assert result["cuadre"] is False
        assert len(result["cuentas"]) == 1
        assert result["cuentas"][0]["saldo"] == Decimal("500000")

    def test_aggregates_multiple_entries_same_account(self) -> None:
        entries = [
            {
                "fecha": "2026-03-15",
                "cuenta": "110505",
                "descripcion": "Caja",
                "tercero_nit": "900123456",
                "detalle": "Deposito 1",
                "debito": "300000",
                "credito": "0",
            },
            {
                "fecha": "2026-03-16",
                "cuenta": "110505",
                "descripcion": "Caja",
                "tercero_nit": "900123456",
                "detalle": "Deposito 2",
                "debito": "200000",
                "credito": "0",
            },
            {
                "fecha": "2026-03-17",
                "cuenta": "110505",
                "descripcion": "Caja",
                "tercero_nit": "900123456",
                "detalle": "Retiro",
                "debito": "0",
                "credito": "100000",
            },
        ]

        result = StatementDeriver.derive_balance_general(entries)

        assert result["total_activos"] == Decimal("400000")
        cuentas = {c["cuenta"]: c for c in result["cuentas"]}
        assert cuentas["110505"]["saldo"] == Decimal("400000")

    def test_skips_invalid_or_missing_cuenta(self) -> None:
        entries = [
            {
                "fecha": "2026-03-15",
                "cuenta": "",
                "descripcion": "Sin cuenta",
                "tercero_nit": "900123456",
                "detalle": "X",
                "debito": "100000",
                "credito": "0",
            },
            {
                "fecha": "2026-03-15",
                "descripcion": "Sin cuenta key",
                "tercero_nit": "900123456",
                "detalle": "X",
                "debito": "100000",
                "credito": "0",
            },
            {
                "fecha": "2026-03-15",
                "cuenta": "ABC123",
                "descripcion": "Cuenta no numerica",
                "tercero_nit": "900123456",
                "detalle": "X",
                "debito": "100000",
                "credito": "0",
            },
            {
                "fecha": "2026-03-15",
                "cuenta": "110505",
                "descripcion": "Caja valida",
                "tercero_nit": "900123456",
                "detalle": "Deposito",
                "debito": "500000",
                "credito": "0",
            },
        ]

        result = StatementDeriver.derive_balance_general(entries)

        assert result["total_activos"] == Decimal("500000")
        assert len(result["cuentas"]) == 1


class TestDeriveEstadoResultados:
    def test_empty_entries_returns_zeros(self) -> None:
        result = StatementDeriver.derive_estado_resultados([])

        assert result["ingresos"] == []
        assert result["gastos"] == []
        assert result["costo_ventas"] == []
        assert result["utilidad_bruta"] == Decimal("0")
        assert result["utilidad_neta"] == Decimal("0")

    def test_mixed_revenue_expenses_cogs(self) -> None:
        entries = [
            {
                "fecha": "2026-03-15",
                "cuenta": "415505",
                "descripcion": "Ingresos operacionales",
                "tercero_nit": "900123456",
                "detalle": "Venta producto A",
                "debito": "0",
                "credito": "2000000",
            },
            {
                "fecha": "2026-03-15",
                "cuenta": "417505",
                "descripcion": "Ingresos no operacionales",
                "tercero_nit": "900123456",
                "detalle": "Intereses",
                "debito": "0",
                "credito": "500000",
            },
            {
                "fecha": "2026-03-15",
                "cuenta": "513505",
                "descripcion": "Gastos operacionales",
                "tercero_nit": "900123456",
                "detalle": "Servicios",
                "debito": "300000",
                "credito": "0",
            },
            {
                "fecha": "2026-03-15",
                "cuenta": "519505",
                "descripcion": "Gastos varios",
                "tercero_nit": "900123456",
                "detalle": "Otros",
                "debito": "200000",
                "credito": "0",
            },
            {
                "fecha": "2026-03-15",
                "cuenta": "613505",
                "descripcion": "Costo de ventas",
                "tercero_nit": "900123456",
                "detalle": "Mercancia",
                "debito": "800000",
                "credito": "0",
            },
        ]

        result = StatementDeriver.derive_estado_resultados(entries)

        # ingresos = 2_000_000 + 500_000 = 2_500_000
        # gastos = 300_000 + 200_000 = 500_000
        # costo_ventas = 800_000
        # utilidad_bruta = ingresos - costo_ventas = 2_500_000 - 800_000 = 1_700_000
        # utilidad_neta = utilidad_bruta - gastos = 1_700_000 - 500_000 = 1_200_000
        assert result["utilidad_bruta"] == Decimal("1700000")
        assert result["utilidad_neta"] == Decimal("1200000")

        ingresos = {i["cuenta"]: i for i in result["ingresos"]}
        assert ingresos["415505"]["saldo"] == Decimal("2000000")
        assert ingresos["417505"]["saldo"] == Decimal("500000")

        gastos = {g["cuenta"]: g for g in result["gastos"]}
        assert gastos["513505"]["saldo"] == Decimal("300000")
        assert gastos["519505"]["saldo"] == Decimal("200000")

        costos = {c["cuenta"]: c for c in result["costo_ventas"]}
        assert costos["613505"]["saldo"] == Decimal("800000")

    def test_single_account_type_only(self) -> None:
        entries = [
            {
                "fecha": "2026-03-15",
                "cuenta": "415505",
                "descripcion": "Ingresos",
                "tercero_nit": "900123456",
                "detalle": "Venta",
                "debito": "0",
                "credito": "1000000",
            },
        ]

        result = StatementDeriver.derive_estado_resultados(entries)

        assert len(result["ingresos"]) == 1
        assert result["gastos"] == []
        assert result["costo_ventas"] == []
        assert result["utilidad_bruta"] == Decimal("1000000")
        assert result["utilidad_neta"] == Decimal("1000000")
