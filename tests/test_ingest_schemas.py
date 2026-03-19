"""
Tests for polymorphic ingest content schemas.
"""

import pytest
from decimal import Decimal
from pydantic import ValidationError

from app.models.ingest_schemas import (
    TransactionItem,
    TransactionListContent,
    BankMovement,
    BankStatementContent,
    TaxDeclarationContent,
    AnnexRow,
    TaxAnnexContent,
    LedgerLine,
    AuxiliaryLedgerContent,
    AccountBalance,
    FinancialStatementContent,
    INGEST_CONTENT_SCHEMAS,
)


# ---------------------------------------------------------------------------
# TransactionListContent
# ---------------------------------------------------------------------------

class TestTransactionListContent:
    def test_valid_transaction(self):
        data = {
            "transactions": [{
                "fecha": "2026-01-15",
                "nit_emisor": "900123456",
                "nit_receptor": "800999888",
                "total": 1500000,
                "descripcion": "Consultoría",
            }]
        }
        result = TransactionListContent.model_validate(data)
        assert len(result.transactions) == 1
        assert result.transactions[0].total == Decimal("1500000")

    def test_empty_transactions_rejected(self):
        with pytest.raises(ValidationError, match="at least 1"):
            TransactionListContent.model_validate({"transactions": []})

    def test_tipo_persona_optional(self):
        data = {
            "transactions": [{
                "fecha": "2026-01-15",
                "nit_emisor": "900123456",
                "nit_receptor": "800999888",
                "total": 100000,
                "tipo_persona": "juridica",
            }]
        }
        result = TransactionListContent.model_validate(data)
        assert result.transactions[0].tipo_persona == "juridica"


# ---------------------------------------------------------------------------
# BankStatementContent
# ---------------------------------------------------------------------------

class TestBankStatementContent:
    def test_valid_statement(self):
        data = {
            "cuenta_bancaria": "1234567890",
            "entidad_bancaria": "Bancolombia",
            "saldo_inicial": 5000000,
            "saldo_final": 4200000,
            "movements": [{
                "fecha": "2026-01-10",
                "descripcion": "Transferencia salida",
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
            "cuenta_bancaria": "1234567890",
            "entidad_bancaria": "Davivienda",
            "saldo_inicial": 1000000,
            "saldo_final": 1000000,
            "movements": [],
        }
        result = BankStatementContent.model_validate(data)
        assert result.movements == []


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
# TaxAnnexContent
# ---------------------------------------------------------------------------

class TestTaxAnnexContent:
    def test_valid_annex(self):
        data = {
            "tipo_anexo": "reteica",
            "periodo": "2026-01",
            "rows": [
                {
                    "nit": "900111222",
                    "razon_social": "Proveedor ABC",
                    "base_gravable": 10000000,
                    "tarifa": 0.0069,
                    "retencion": 69000,
                },
                {
                    "nit": "800333444",
                    "razon_social": "Servicios XYZ",
                    "base_gravable": 5000000,
                    "tarifa": 0.0069,
                    "retencion": 34500,
                },
            ],
            "total_base": 15000000,
            "total_retencion": 103500,
        }
        result = TaxAnnexContent.model_validate(data)
        assert len(result.rows) == 2
        assert result.total_retencion == Decimal("103500")


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
    def test_registry_has_12_entries(self):
        assert len(INGEST_CONTENT_SCHEMAS) == 12

    def test_all_factura_types_use_transaction_list(self):
        assert INGEST_CONTENT_SCHEMAS["factura_venta"] is TransactionListContent
        assert INGEST_CONTENT_SCHEMAS["factura_compra"] is TransactionListContent
        assert INGEST_CONTENT_SCHEMAS["nota_credito"] is TransactionListContent
        assert INGEST_CONTENT_SCHEMAS["nota_debito"] is TransactionListContent

    def test_declarations_use_tax_declaration(self):
        assert INGEST_CONTENT_SCHEMAS["declaracion_iva"] is TaxDeclarationContent
        assert INGEST_CONTENT_SCHEMAS["declaracion_reteica"] is TaxDeclarationContent

    def test_via_b_types_use_correct_schemas(self):
        assert INGEST_CONTENT_SCHEMAS["balance_general"] is FinancialStatementContent
        assert INGEST_CONTENT_SCHEMAS["estado_resultados"] is FinancialStatementContent
        assert INGEST_CONTENT_SCHEMAS["libro_auxiliar"] is AuxiliaryLedgerContent
