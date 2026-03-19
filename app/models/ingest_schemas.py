"""
Polymorphic content schemas for document ingestion.

Each document type produces data with a different structure. These Pydantic
models define the expected output from Gemini for each document type.
"""

from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# 1. TransactionListContent — facturas, notas crédito/débito
# ---------------------------------------------------------------------------

class TransactionItem(BaseModel):
    """Single transaction extracted from an invoice or similar document."""

    fecha: Optional[str] = Field(None, description="Date YYYY-MM-DD")
    nit_emisor: str = Field(description="NIT of the issuer")
    nit_receptor: str = Field(description="NIT of the receiver")
    total: Decimal = Field(ge=0, description="Total amount")
    descripcion: Optional[str] = Field(None, description="Description/concept")
    items: Optional[List[Dict[str, Any]]] = Field(None, description="Line items")
    tipo_persona: Optional[Literal["natural", "juridica"]] = Field(
        None, description="Person type for withholding tax classification"
    )

    @field_validator("total", mode="before")
    @classmethod
    def parse_total(cls, v):
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        return v


class TransactionListContent(BaseModel):
    """Content schema for invoice-like documents (facturas, notas)."""

    transactions: List[TransactionItem] = Field(
        ..., min_length=1, description="Extracted transactions"
    )


# ---------------------------------------------------------------------------
# 2. BankStatementContent — extractos bancarios
# ---------------------------------------------------------------------------

class BankMovement(BaseModel):
    """Single bank account movement."""

    fecha: str = Field(description="Date YYYY-MM-DD")
    descripcion: str = Field(description="Movement description")
    referencia: Optional[str] = Field(None, description="Reference number")
    debito: Optional[Decimal] = Field(None, ge=0, description="Debit amount")
    credito: Optional[Decimal] = Field(None, ge=0, description="Credit amount")
    saldo: Optional[Decimal] = Field(None, description="Running balance")

    @field_validator("debito", "credito", "saldo", mode="before")
    @classmethod
    def parse_amounts(cls, v):
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        return v


class BankStatementContent(BaseModel):
    """Content schema for bank statements."""

    cuenta_bancaria: str = Field(description="Bank account number")
    entidad_bancaria: str = Field(description="Bank name")
    saldo_inicial: Decimal = Field(description="Opening balance")
    saldo_final: Decimal = Field(description="Closing balance")
    movements: List[BankMovement] = Field(
        default_factory=list, description="List of movements"
    )

    @field_validator("saldo_inicial", "saldo_final", mode="before")
    @classmethod
    def parse_balances(cls, v):
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        return v


# ---------------------------------------------------------------------------
# 3. TaxDeclarationContent — declaraciones IVA, ReteICA
# ---------------------------------------------------------------------------

class TaxDeclarationContent(BaseModel):
    """Content schema for DIAN tax declarations."""

    formulario: str = Field(description="DIAN form number (e.g. '300' for IVA)")
    periodo: str = Field(description="Tax period (e.g. '2026-01' bimestral)")
    nit_declarante: str = Field(description="NIT of the declaring entity")
    renglones: Dict[str, Decimal] = Field(
        description="DIAN form row values keyed by row number"
    )
    total_a_pagar: Optional[Decimal] = Field(
        None, description="Total amount payable"
    )

    @field_validator("total_a_pagar", mode="before")
    @classmethod
    def parse_total(cls, v):
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        return v


# ---------------------------------------------------------------------------
# 4. TaxAnnexContent — anexos tributarios
# ---------------------------------------------------------------------------

class AnnexRow(BaseModel):
    """Single row from a tax annex table."""

    nit: str = Field(description="Third-party NIT")
    razon_social: str = Field(description="Third-party name")
    base_gravable: Decimal = Field(ge=0, description="Taxable base")
    tarifa: Decimal = Field(ge=0, description="Rate applied")
    retencion: Decimal = Field(ge=0, description="Retention amount")

    @field_validator("base_gravable", "tarifa", "retencion", mode="before")
    @classmethod
    def parse_amounts(cls, v):
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        return v


class TaxAnnexContent(BaseModel):
    """Content schema for tax declaration annexes."""

    tipo_anexo: str = Field(description="Annex type: 'iva', 'reteica', etc.")
    periodo: str = Field(description="Tax period")
    rows: List[AnnexRow] = Field(description="Annex detail rows")
    total_base: Decimal = Field(ge=0, description="Total taxable base")
    total_retencion: Decimal = Field(ge=0, description="Total retention")

    @field_validator("total_base", "total_retencion", mode="before")
    @classmethod
    def parse_totals(cls, v):
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        return v


# ---------------------------------------------------------------------------
# 5. AuxiliaryLedgerContent — libro auxiliar de impuestos o general
# ---------------------------------------------------------------------------

class LedgerLine(BaseModel):
    """Single line from an auxiliary ledger."""

    fecha: str = Field(description="Date YYYY-MM-DD")
    cuenta_puc: str = Field(description="PUC account code")
    cuenta_nombre: Optional[str] = Field(None, description="Account name")
    tercero_nit: Optional[str] = Field(None, description="Third-party NIT")
    detalle: str = Field(description="Line detail/description")
    debito: Decimal = Field(ge=0, description="Debit amount")
    credito: Decimal = Field(ge=0, description="Credit amount")
    saldo: Optional[Decimal] = Field(None, description="Running balance")

    @field_validator("debito", "credito", "saldo", mode="before")
    @classmethod
    def parse_amounts(cls, v):
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        return v


class AuxiliaryLedgerContent(BaseModel):
    """Content schema for auxiliary ledgers."""

    cuenta_principal: Optional[str] = Field(
        None, description="Main account code if specific to one account"
    )
    periodo: Optional[str] = Field(None, description="Period covered")
    lines: List[LedgerLine] = Field(description="Ledger lines")


# ---------------------------------------------------------------------------
# 6. FinancialStatementContent — balance general, estado de resultados (Vía B)
# ---------------------------------------------------------------------------

class AccountBalance(BaseModel):
    """Single account balance from a financial statement."""

    cuenta_puc: str = Field(description="PUC account code")
    nombre: str = Field(description="Account name")
    saldo: Decimal = Field(description="Account balance")

    @field_validator("saldo", mode="before")
    @classmethod
    def parse_saldo(cls, v):
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        return v


class FinancialStatementContent(BaseModel):
    """Content schema for existing financial statements (Vía B)."""

    tipo: Literal["balance_general", "estado_resultados"] = Field(
        description="Statement type"
    )
    periodo_inicio: Optional[str] = Field(None, description="Period start YYYY-MM-DD")
    periodo_fin: str = Field(description="Period end YYYY-MM-DD")
    entity_nit: Optional[str] = Field(None, description="Entity NIT")
    accounts: List[AccountBalance] = Field(
        description="Account balances from the statement"
    )
    total_activos: Optional[Decimal] = Field(None, description="Total assets (balance only)")
    total_pasivos: Optional[Decimal] = Field(None, description="Total liabilities (balance only)")
    total_patrimonio: Optional[Decimal] = Field(None, description="Total equity (balance only)")
    utilidad_neta: Optional[Decimal] = Field(None, description="Net income")

    @field_validator(
        "total_activos", "total_pasivos", "total_patrimonio", "utilidad_neta",
        mode="before",
    )
    @classmethod
    def parse_totals(cls, v):
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        return v


# ---------------------------------------------------------------------------
# Schema registry: maps DocumentType values to content schemas
# ---------------------------------------------------------------------------

from app.models.document_types import DocumentType  # noqa: E402

INGEST_CONTENT_SCHEMAS: dict[str, type[BaseModel]] = {
    DocumentType.FACTURA_VENTA.value: TransactionListContent,
    DocumentType.FACTURA_COMPRA.value: TransactionListContent,
    DocumentType.NOTA_CREDITO.value: TransactionListContent,
    DocumentType.NOTA_DEBITO.value: TransactionListContent,
    DocumentType.EXTRACTO_BANCARIO.value: BankStatementContent,
    DocumentType.DECLARACION_IVA.value: TaxDeclarationContent,
    DocumentType.DECLARACION_RETEICA.value: TaxDeclarationContent,
    DocumentType.ANEXO_TRIBUTARIO.value: TaxAnnexContent,
    DocumentType.AUXILIAR_IMPUESTO.value: AuxiliaryLedgerContent,
    DocumentType.BALANCE_GENERAL.value: FinancialStatementContent,
    DocumentType.ESTADO_RESULTADOS.value: FinancialStatementContent,
    DocumentType.LIBRO_AUXILIAR.value: AuxiliaryLedgerContent,
}
