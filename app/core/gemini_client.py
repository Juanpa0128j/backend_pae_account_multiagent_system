"""
Gemini client — backward-compatibility shim.

All extraction logic has moved to `app.core.llm_client.LLMClient`.
This module keeps:
  - Pydantic structured-output models (imported by agents and tests)
  - Ingest schema re-exports (imported by llm_client methods)
  - GENERAL_EXTRACTION_INSTRUCTIONS constant
  - GeminiClient / get_gemini_client aliases pointing to LLMClient
"""
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional, TypeVar

from pydantic import BaseModel, Field, field_validator

ModelT = TypeVar("ModelT", bound=BaseModel)

# ---------------------------------------------------------------------------
# Pydantic structured-output models (used by agents & tests)
# ---------------------------------------------------------------------------

class RawTransaction(BaseModel):
    """Structured schema for extracted receipt/invoice data."""

    fecha: Optional[str] = Field(None, description="Date in YYYY-MM-DD format")
    nit_emisor: str = Field(description="NIT of the issuer")
    nit_receptor: str = Field(description="NIT of the receiver (empresa)")
    total: Decimal = Field(description="Total amount of the transaction")
    descripcion: Optional[str] = Field(
        None, description="Description/concept of the transaction"
    )
    items: Optional[List[Dict[str, Any]]] = Field(None, description="Line items")

    @field_validator("total", mode="before")
    @classmethod
    def parse_total(cls, v):  # noqa: N805
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        return v


class RawTransactionsList(BaseModel):
    transactions: List[RawTransaction] = Field(
        default_factory=list,
        description="Extracted list of transactions from the document",
    )


class AsientoContableGemini(BaseModel):
    """Simplified journal entry schema for structured output."""

    cuenta_puc: str = Field(description="PUC account code (1-6 digits)")
    descripcion: str = Field(description="Description of the entry")
    tipo_movimiento: Literal["debito", "credito"] = Field(
        description="Movement type: 'debito' or 'credito' (lowercase)"
    )
    valor: Decimal = Field(description="Amount of the entry")

    @field_validator("valor", mode="before")
    @classmethod
    def parse_valor(cls, v):  # noqa: N805
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        return v


class ContadorOutputGemini(BaseModel):
    """ContadorOutput-compatible schema for structured output."""

    fecha_registro: str = Field(description="Accounting registration date YYYY-MM-DD")
    tipo_documento: str = Field(
        description=(
            "Document type: recibo, factura, extracto, nota_credito, "
            "nota_debito, comprobante_egreso, otro"
        )
    )
    descripcion_general: str = Field(
        description="General description of the accounting event"
    )
    asientos: List[AsientoContableGemini] = Field(
        description="Journal entries (at least one debit and one credit)"
    )
    total_debitos: Decimal = Field(description="Sum of all debit entries")
    total_creditos: Decimal = Field(description="Sum of all credit entries")

    @field_validator("total_debitos", "total_creditos", mode="before")
    @classmethod
    def parse_totals(cls, v):  # noqa: N805
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        return v


class AuditorHallazgoGemini(BaseModel):
    """Single audit finding."""

    codigo: str = Field(description="Finding code in format AUD-XXX")
    severidad: Literal["info", "advertencia", "error", "critico"] = Field(
        description="Finding severity level"
    )
    descripcion: str = Field(description="Clear description of the finding")
    campo_afectado: Optional[str] = Field(
        None, description="Campo contable afectado (opcional)"
    )
    recomendacion: str = Field(description="Recomendacion para corregir el hallazgo")


class AuditorOutputGemini(BaseModel):
    """AuditorOutput-compatible schema for structured output."""

    fecha_auditoria: str = Field(description="Fecha de auditoria en formato YYYY-MM-DD")
    documento_referencia: str = Field(description="Referencia del documento auditado")
    aprobado: bool = Field(description="True cuando el audit pasa sin bloqueadores")
    nivel_riesgo: Literal["bajo", "medio", "alto", "critico"] = Field(
        description="Nivel de riesgo global de la transaccion"
    )
    hallazgos: List[AuditorHallazgoGemini] = Field(
        default_factory=list,
        description="Lista estructurada de hallazgos detectados",
    )
    puntaje_calidad: Decimal = Field(
        ge=0,
        le=100,
        description="Puntaje de calidad contable entre 0 y 100",
    )
    resumen: str = Field(description="Resumen ejecutivo de la auditoria")


class TaxJustification(BaseModel):
    """Structured output for tax justification calls."""

    referencias: List[str] = Field(
        description="Legal articles cited, e.g. ['Art. 383 ET', 'Decreto 2048/1992']"
    )
    justificacion: str = Field(
        description="Spanish explanation of why these rates apply to the transaction"
    )
    confirma_tasas: bool = Field(
        description="True if the normative context confirms the calculated rates"
    )


class TaxRateLookup(BaseModel):
    """Structured output for tax profile setup."""

    tasa_retefuente_servicios: Decimal = Field(
        description="Retefuente rate for services as decimal fraction, e.g. 0.11"
    )
    tasa_retefuente_bienes: Decimal = Field(
        description="Retefuente rate for goods purchases as decimal fraction"
    )
    tasa_retefuente_arrendamiento: Decimal = Field(
        description="Retefuente rate for lease/rent as decimal fraction"
    )
    tasa_reteica: Decimal = Field(
        description="ReteICA rate for city/CIIU as decimal fraction"
    )
    tasa_iva_general: Decimal = Field(
        description="IVA tariff as decimal fraction (0.19 or 0.0)"
    )
    fuentes: List[str] = Field(
        description="Legal articles and municipal agreements supporting rates"
    )

    @field_validator(
        "tasa_retefuente_servicios",
        "tasa_retefuente_bienes",
        "tasa_retefuente_arrendamiento",
        "tasa_reteica",
        "tasa_iva_general",
        mode="before",
    )
    @classmethod
    def parse_rates(cls, v):  # noqa: N805
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        return v


# ---------------------------------------------------------------------------
# Ingest schema re-exports (used by llm_client extraction methods)
# ---------------------------------------------------------------------------

from app.models.ingest_schemas import (  # noqa: E402
    FacturaVentaContent,
    FacturaCompraContent,
    NotaCreditoContent,
    NotaDebitoContent,
    BankStatementContent,
    TaxDeclarationContent,
    DeclaracionICAContent,
    AutoretencionICAContent,
    AnexoIVAContent,
    AuxiliarIVAContent,
    AuxiliaryLedgerContent,
    FinancialStatementContent,
    LibroDiarioContent,
    FlujoDeCajaContent,
    CambiosPatrimonioContent,
    NotasEstadosFinancierosContent,
    ComprobanteEgresoContent,
    DocumentoSoporteContent,
    ReciboCajaContent,
    NominaContent,
    ConciliacionBancariaContent,
    CuentaCobroContent,
    PlanillaSegSocialContent,
    ReciboPagoImpuestoContent,
)

__all__ = [
    "RawTransaction",
    "RawTransactionsList",
    "AsientoContableGemini",
    "ContadorOutputGemini",
    "AuditorHallazgoGemini",
    "AuditorOutputGemini",
    "TaxJustification",
    "TaxRateLookup",
    "GENERAL_EXTRACTION_INSTRUCTIONS",
    "GeminiClient",
    "get_gemini_client",
    # ingest schemas
    "FacturaVentaContent",
    "FacturaCompraContent",
    "NotaCreditoContent",
    "NotaDebitoContent",
    "BankStatementContent",
    "TaxDeclarationContent",
    "DeclaracionICAContent",
    "AutoretencionICAContent",
    "AnexoIVAContent",
    "AuxiliarIVAContent",
    "AuxiliaryLedgerContent",
    "FinancialStatementContent",
    "LibroDiarioContent",
    "FlujoDeCajaContent",
    "CambiosPatrimonioContent",
    "NotasEstadosFinancierosContent",
    "ComprobanteEgresoContent",
    "DocumentoSoporteContent",
    "ReciboCajaContent",
    "NominaContent",
    "ConciliacionBancariaContent",
    "CuentaCobroContent",
    "PlanillaSegSocialContent",
    "ReciboPagoImpuestoContent",
]

# ---------------------------------------------------------------------------
# Shared extraction instructions constant
# ---------------------------------------------------------------------------

GENERAL_EXTRACTION_INSTRUCTIONS = """
INSTRUCCIONES GENERALES DE EXTRACCIÓN:
1. Extrae SOLO los campos que estén presentes en el documento. Si un campo no existe, usa null.
2. Fechas: formato ISO 8601 (YYYY-MM-DD). Si solo hay mes/año, usa el último día del mes.
3. NIT: incluir dígito de verificación separado por guion (ej: 900123456-7). Si el DV no aparece, déjalo como el NIT sin DV.
4. Moneda: todos los valores monetarios son numéricos, sin separadores de miles, usando punto como decimal. Moneda por defecto: COP.
5. Tarifas de impuestos: como decimal (ej: 19% → 0.19, 4.14‰ → 0.00414).
6. En el campo `informacion_adicional`, captura TODO lo que pueda ser útil para el procesamiento contable posterior:
   - Conceptos de retención mencionados (retefuente, reteICA, reteIVA)
   - Referencias a resoluciones DIAN, acuerdos municipales, decretos
   - Actividades económicas (códigos CIIU)
   - Régimen tributario del emisor/receptor
   - Centros de costo, proyectos o contratos referenciados
   - Sellos, firmas y autorizaciones presentes
   - Números de contrato, órdenes de compra, referencias cruzadas
   - Cualquier anomalía, dato inusual o información ambigua
   - Observaciones que el contador, tributarista o auditor necesitarían conocer
"""

# ---------------------------------------------------------------------------
# Backward-compatibility aliases
# ---------------------------------------------------------------------------

from app.core.llm_client import LLMClient as GeminiClient, get_llm_client  # noqa: E402


def get_gemini_client() -> GeminiClient:
    """Backward-compatible alias for get_llm_client()."""
    return get_llm_client()
