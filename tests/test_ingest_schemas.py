"""
Tests for polymorphic ingest content schemas.
"""

import pytest
from decimal import Decimal
from pydantic import ValidationError

from app.models.ingest_schemas import (
    FacturaVentaContent,
    FacturaCompraContent,
    NotaCreditoContent,
    NotaDebitoContent,
    BankMovement,
    BankStatementContent,
    TaxDeclarationContent,
    AnexoIVAContent,
    LedgerLine,
    AuxiliaryLedgerContent,
    AccountBalance,
    FinancialStatementContent,
    BalanceGeneralContent,
    EstadoResultadosContent,
    INGEST_CONTENT_SCHEMAS,
)


# ---------------------------------------------------------------------------
# FacturaVentaContent (replaces TransactionListContent tests)
# ---------------------------------------------------------------------------

class TestFacturaVentaContent:
    def test_valid_minimal(self):
        data = {
            "consecutivo": "FV-0192",
            "fecha_emision": "2026-01-15",
            "emisor": {"razon_social": "Empresa ABC", "nit": "900123456-7"},
            "receptor": {"razon_social": "Cliente XYZ", "nit": "800999888-1"},
            "totales": {"total_a_pagar": 1500000},
        }
        result = FacturaVentaContent.model_validate(data)
        assert result.consecutivo == "FV-0192"
        assert result.totales.total_a_pagar == Decimal("1500000")

    def test_all_optional_fields_null(self):
        result = FacturaVentaContent.model_validate({})
        assert result.consecutivo is None
        assert result.emisor is None

    def test_informacion_adicional_captured(self):
        data = {
            "consecutivo": "FV-0193",
            "informacion_adicional": {
                "regimen_emisor": "responsable_iva",
                "retefuente_aplicable": True,
            },
        }
        result = FacturaVentaContent.model_validate(data)
        assert result.informacion_adicional["regimen_emisor"] == "responsable_iva"


class TestFacturaCompraContent:
    def test_valid_minimal(self):
        data = {
            "consecutivo": "FC-001",
            "proveedor": {"razon_social": "Proveedor SAS", "nit": "901234567-1"},
        }
        result = FacturaCompraContent.model_validate(data)
        assert result.consecutivo == "FC-001"

    def test_documento_soporte_flag(self):
        data = {"documento_soporte": True}
        result = FacturaCompraContent.model_validate(data)
        assert result.documento_soporte is True


# ---------------------------------------------------------------------------
# BankStatementContent
# ---------------------------------------------------------------------------

class TestBankStatementContent:
    def test_valid_statement(self):
        data = {
            "entidad_financiera": "Bancolombia",
            "numero_cuenta": "1234567890",
            "tipo_cuenta": "corriente",
            "saldo_inicial": 5000000,
            "saldo_final": 4200000,
            "movements": [{
                "fecha": "2026-01-10",
                "descripcion": "Transferencia salida",
                "tipo": "debito",
                "debito": 800000,
                "credito": None,
                "saldo": 4200000,
            }],
        }
        result = BankStatementContent.model_validate(data)
        assert result.saldo_inicial == Decimal("5000000")
        assert len(result.movements) == 1

    def test_empty_movements_allowed(self):
        data = {
            "entidad_financiera": "Davivienda",
            "saldo_inicial": 1000000,
            "saldo_final": 1000000,
            "movements": [],
        }
        result = BankStatementContent.model_validate(data)
        assert result.movements == []

    def test_legacy_property_aliases(self):
        data = {
            "entidad_financiera": "Banco Bogotá",
            "numero_cuenta": "9876543210",
            "saldo_inicial": 0,
            "saldo_final": 0,
            "movements": [],
        }
        result = BankStatementContent.model_validate(data)
        assert result.entidad_bancaria == "Banco Bogotá"
        assert result.cuenta_bancaria == "9876543210"


# ---------------------------------------------------------------------------
# TaxDeclarationContent
# ---------------------------------------------------------------------------

class TestTaxDeclarationContent:
    def test_valid_iva_declaration(self):
        data = {
            "formulario": "300",
            "periodo": "2026-01",
            "nit_declarante": "900123456",
            "renglones": {
                "29": 15000000,
                "32": 2850000,
                "59": 1200000,
                "91": 1650000,
            },
            "total_a_pagar": 1650000,
        }
        result = TaxDeclarationContent.model_validate(data)
        assert result.formulario == "300"
        assert result.renglones["32"] == Decimal("2850000")

    def test_total_a_pagar_optional(self):
        data = {
            "formulario": "350",
            "periodo": "2026-01",
            "nit_declarante": "800999888",
            "renglones": {"10": 500000},
        }
        result = TaxDeclarationContent.model_validate(data)
        assert result.total_a_pagar is None


# ---------------------------------------------------------------------------
# AnexoIVAContent (replaces TaxAnnexContent tests)
# ---------------------------------------------------------------------------

class TestAnexoIVAContent:
    def test_valid_annex(self):
        data = {
            "nit_declarante": "900111222",
            "periodo_numero": 1,
            "iva_generado": [
                {"tarifa": 0.19, "base_gravable": 10000000, "iva_generado": 1900000},
            ],
            "total_iva_generado": 1900000,
            "total_iva_descontable": 500000,
            "saldo_a_pagar": 1400000,
        }
        result = AnexoIVAContent.model_validate(data)
        assert result.total_iva_generado == Decimal("1900000")
        assert len(result.iva_generado) == 1

    def test_all_optional(self):
        result = AnexoIVAContent.model_validate({})
        assert result.total_iva_generado is None


# ---------------------------------------------------------------------------
# AuxiliaryLedgerContent
# ---------------------------------------------------------------------------

class TestAuxiliaryLedgerContent:
    def test_valid_ledger(self):
        data = {
            "cuenta_principal": "240802",
            "periodo": "2026-01",
            "lines": [
                {
                    "fecha": "2026-01-15",
                    "cuenta_puc": "240802",
                    "cuenta_nombre": "IVA Descontable",
                    "detalle": "Factura compra #123",
                    "debito": 285000,
                    "credito": 0,
                    "saldo": 285000,
                },
            ],
        }
        result = AuxiliaryLedgerContent.model_validate(data)
        assert len(result.lines) == 1
        assert result.lines[0].debito == Decimal("285000")

    def test_optional_fields(self):
        data = {
            "lines": [{
                "fecha": "2026-01-15",
                "cuenta_puc": "1110",
                "detalle": "Depósito",
                "debito": 1000000,
                "credito": 0,
            }],
        }
        result = AuxiliaryLedgerContent.model_validate(data)
        assert result.cuenta_principal is None
        assert result.lines[0].saldo is None


# ---------------------------------------------------------------------------
# FinancialStatementContent
# ---------------------------------------------------------------------------

class TestFinancialStatementContent:
    def test_valid_balance_general(self):
        data = {
            "tipo": "balance_general",
            "periodo_fin": "2026-01-31",
            "accounts": [
                {"cuenta_puc": "1110", "nombre": "Bancos", "saldo": 5000000},
                {"cuenta_puc": "2205", "nombre": "Proveedores", "saldo": 2000000},
                {"cuenta_puc": "3105", "nombre": "Capital", "saldo": 3000000},
            ],
            "total_activos": 5000000,
            "total_pasivos": 2000000,
            "total_patrimonio": 3000000,
        }
        result = FinancialStatementContent.model_validate(data)
        assert result.tipo == "balance_general"
        assert len(result.accounts) == 3
        assert result.total_activos == Decimal("5000000")

    def test_valid_estado_resultados(self):
        data = {
            "tipo": "estado_resultados",
            "periodo_inicio": "2026-01-01",
            "periodo_fin": "2026-01-31",
            "entity_nit": "900123456",
            "accounts": [
                {"cuenta_puc": "4135", "nombre": "Ingresos servicios", "saldo": 10000000},
                {"cuenta_puc": "5110", "nombre": "Honorarios", "saldo": 3000000},
            ],
            "utilidad_neta": 7000000,
        }
        result = FinancialStatementContent.model_validate(data)
        assert result.tipo == "estado_resultados"
        assert result.utilidad_neta == Decimal("7000000")

    def test_invalid_tipo_rejected(self):
        with pytest.raises(ValidationError):
            FinancialStatementContent.model_validate({
                "tipo": "invalid_type",
                "periodo_fin": "2026-01-31",
                "accounts": [],
            })


# ---------------------------------------------------------------------------
# Schema registry
# ---------------------------------------------------------------------------

class TestSchemaRegistry:
    def test_registry_has_expected_entries(self):
        # Registry grew from 12 to 28 entries
        assert len(INGEST_CONTENT_SCHEMAS) >= 24

    def test_factura_types_use_specific_schemas(self):
        assert INGEST_CONTENT_SCHEMAS["factura_venta"] is FacturaVentaContent
        assert INGEST_CONTENT_SCHEMAS["factura_compra"] is FacturaCompraContent
        assert INGEST_CONTENT_SCHEMAS["nota_credito"] is NotaCreditoContent
        assert INGEST_CONTENT_SCHEMAS["nota_debito"] is NotaDebitoContent

    def test_declarations_use_tax_declaration(self):
        assert INGEST_CONTENT_SCHEMAS["declaracion_iva"] is TaxDeclarationContent
        assert INGEST_CONTENT_SCHEMAS["declaracion_reteica"] is TaxDeclarationContent

    def test_via_b_types_use_correct_schemas(self):
        assert INGEST_CONTENT_SCHEMAS["balance_general"] is BalanceGeneralContent
        assert INGEST_CONTENT_SCHEMAS["estado_resultados"] is EstadoResultadosContent
        assert INGEST_CONTENT_SCHEMAS["libro_auxiliar"] is AuxiliaryLedgerContent

    def test_new_types_registered(self):
        assert "declaracion_ica" in INGEST_CONTENT_SCHEMAS
        assert "autorretencion_ica" in INGEST_CONTENT_SCHEMAS
        assert "comprobante_egreso" in INGEST_CONTENT_SCHEMAS
        assert "nomina" in INGEST_CONTENT_SCHEMAS
        assert "conciliacion_bancaria" in INGEST_CONTENT_SCHEMAS
        assert "flujo_de_caja" in INGEST_CONTENT_SCHEMAS
        assert "notas_estados_financieros" in INGEST_CONTENT_SCHEMAS


# ---------------------------------------------------------------------------
# qr_code and condiciones_pago fields
# ---------------------------------------------------------------------------

def test_factura_venta_has_qr_code_field():
    schema = FacturaVentaContent.model_fields
    assert "qr_code" in schema


def test_factura_compra_has_qr_code_field():
    schema = FacturaCompraContent.model_fields
    assert "qr_code" in schema


def test_factura_compra_has_condiciones_pago_field():
    schema = FacturaCompraContent.model_fields
    assert "condiciones_pago" in schema


def test_factura_venta_qr_code_optional():
    obj = FacturaVentaContent()
    assert obj.qr_code is None


def test_factura_compra_condiciones_pago_optional():
    obj = FacturaCompraContent()
    assert obj.condiciones_pago is None
