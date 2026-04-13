"""
Pydantic structured-output schemas for LLM responses.

These models are used by LLMClient to parse structured JSON from the LLM.
They are separate from ingest_schemas (raw document extraction) and database
models (ORM). Import from here, not from app.core.gemini_client.
"""

from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Ingest / transaction extraction
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


# ---------------------------------------------------------------------------
# Contador (journal entry) schemas
# ---------------------------------------------------------------------------


class AsientoContable(BaseModel):
    """Simplified journal entry schema for structured output."""

    cuenta_puc: str = Field(description="PUC account code (1-6 digits)")
    descripcion: Optional[str] = Field(
        default=None, description="Description of the entry"
    )
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


class ContadorOutput(BaseModel):
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
    asientos: List[AsientoContable] = Field(
        description="Journal entries (at least one debit and one credit)"
    )
    total_debitos: Decimal = Field(
        default=Decimal("0"), description="Sum of all debit entries"
    )
    total_creditos: Decimal = Field(
        default=Decimal("0"), description="Sum of all credit entries"
    )

    @field_validator("total_debitos", "total_creditos", mode="before")
    @classmethod
    def parse_totals(cls, v):  # noqa: N805
        if v is None:
            return Decimal("0")
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        return v

    @model_validator(mode="after")
    def ensure_totals(self) -> "ContadorOutput":
        """Backfill totals when LLM omits them but asientos are present."""
        if not self.asientos:
            return self

        calc_debitos = Decimal("0")
        calc_creditos = Decimal("0")
        for asiento in self.asientos:
            valor = Decimal(str(asiento.valor or 0))
            if asiento.tipo_movimiento == "debito":
                calc_debitos += valor
            else:
                calc_creditos += valor

        if self.total_debitos == Decimal("0") and calc_debitos > Decimal("0"):
            self.total_debitos = calc_debitos
        if self.total_creditos == Decimal("0") and calc_creditos > Decimal("0"):
            self.total_creditos = calc_creditos

        return self


# ---------------------------------------------------------------------------
# Auditor schemas
# ---------------------------------------------------------------------------


class AuditorHallazgo(BaseModel):
    """Single audit finding."""

    codigo: str = Field(default="AUD-000", description="Finding code in format AUD-XXX")
    severidad: Literal["info", "advertencia", "error", "critico"] = Field(
        default="advertencia", description="Finding severity level"
    )
    descripcion: str = Field(default="", description="Clear description of the finding")
    campo_afectado: Optional[str] = Field(
        None, description="Campo contable afectado (opcional)"
    )
    recomendacion: str = Field(
        default="", description="Recomendacion para corregir el hallazgo"
    )


class AuditorOutput(BaseModel):
    """AuditorOutput-compatible schema for structured output."""

    fecha_auditoria: str = Field(
        default="1970-01-01", description="Fecha de auditoria en formato YYYY-MM-DD"
    )
    documento_referencia: str = Field(
        default="sin referencia", description="Referencia del documento auditado"
    )
    aprobado: bool = Field(
        default=False, description="True cuando el audit pasa sin bloqueadores"
    )
    nivel_riesgo: Literal["bajo", "medio", "alto", "critico"] = Field(
        default="medio", description="Nivel de riesgo global de la transaccion"
    )
    hallazgos: List[AuditorHallazgo] = Field(
        default_factory=list,
        description="Lista estructurada de hallazgos detectados",
    )
    puntaje_calidad: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        le=100,
        description="Puntaje de calidad contable entre 0 y 100",
    )
    resumen: str = Field(default="", description="Resumen ejecutivo de la auditoria")


# ---------------------------------------------------------------------------
# Tributario (tax) schemas
# ---------------------------------------------------------------------------


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
# Reportero analysis schemas
# ---------------------------------------------------------------------------


class ExplicacionResultado(BaseModel):
    """Detailed explanation of a financial metric."""

    metrica: str = Field(
        description="Metric name, e.g. 'activos_totales', 'razon_corriente'"
    )
    valor: float = Field(description="The metric's numeric value")
    explicacion: str = Field(
        description="WHY this value — root causes, contributing accounts, business implications"
    )
    nivel: Literal["positivo", "neutral", "negativo"] = Field(
        description="Traffic light assessment"
    )


class PrediccionPeriodo(BaseModel):
    """Single month financial prediction."""

    periodo: str = Field(description="Target month as YYYY-MM, e.g. '2026-04'")
    ingresos_estimados: float = Field(description="Projected revenue for the month")
    gastos_estimados: float = Field(description="Projected expenses for the month")
    utilidad_estimada: float = Field(description="Projected net profit for the month")
    flujo_caja_estimado: float = Field(
        description="Projected net cash flow for the month (based on historical cash movements)"
    )
    confianza: Literal["alta", "media", "baja"] = Field(
        description="Confidence level based on data volume and trend consistency"
    )


class InterpretacionRatio(BaseModel):
    """Interpretation of a single financial ratio."""

    ratio: str = Field(description="Ratio name in Spanish")
    valor: Optional[float] = Field(None, description="Numeric value")
    interpretacion: str = Field(
        default="",
        description="What this ratio means for the business",
    )
    que_significa: str = Field(
        default="",
        description="Plain-language explanation for non-accountants",
    )


class ReporteroAnalysis(BaseModel):
    """Full structured analysis output from the Reportero LLM call."""

    resumen_ejecutivo: str = Field(
        description="2-3 paragraph executive summary of financial health"
    )
    explicaciones: List[ExplicacionResultado] = Field(
        description="Detailed explanation of EACH major financial result"
    )
    interpretacion_ratios: List[InterpretacionRatio] = Field(
        description="Interpretation of each financial ratio"
    )
    tendencias: str = Field(
        description="Narrative of how revenue, expenses, profit evolved over recent months"
    )
    predicciones: List[PrediccionPeriodo] = Field(
        default_factory=list,
        description="3-month financial projections",
    )

    @field_validator("predicciones", mode="before")
    @classmethod
    def _coerce_predicciones(cls, v):  # noqa: N805
        if isinstance(v, str):
            return []
        return v

    predicciones_narrativa: str = Field(
        description="Plain-language interpretation of predictions: where the company is headed, risks, inflection points"
    )
    alertas: List[str] = Field(description="Risk alerts and early warning signals")
    recomendaciones: List[str] = Field(description="3-5 actionable recommendations")
    nivel_salud_financiera: str = Field(
        description="Overall financial health assessment: bueno, aceptable, preocupante, or critico"
    )

    @field_validator("nivel_salud_financiera", mode="before")
    @classmethod
    def _normalize_salud(cls, v):  # noqa: N805
        import unicodedata

        if isinstance(v, str):
            v = unicodedata.normalize("NFD", v)
            v = "".join(c for c in v if unicodedata.category(c) != "Mn")
            v = v.lower().strip()
        return v


class ReporteroBriefAnalysis(BaseModel):
    """Brief analysis for individual report types (balance, pnl, etc.)."""

    resumen: str = Field(description="1-2 paragraph summary of this specific report")
    puntos_clave: List[str] = Field(description="3-5 key takeaways")
    alertas: List[str] = Field(default_factory=list, description="Risk alerts if any")
    recomendaciones: List[str] = Field(
        default_factory=list, description="1-3 recommendations"
    )


# ---------------------------------------------------------------------------
# Chatbot schemas
# ---------------------------------------------------------------------------


class ChatIntentClassification(BaseModel):
    """LLM structured output for classifying a user's financial question."""

    intent: Literal[
        "balance",
        "pnl",
        "cashflow",
        "iva",
        "withholdings",
        "analysis",
        "top_accounts",
        "ratios",
        "general_question",
        "explanation",
        "dashboard",
    ] = Field(description="Classified intent of the user's question")
    needs_data: bool = Field(
        description="Whether financial data from DB is required to answer"
    )
    rag_query: Optional[str] = Field(
        None,
        description="If RAG normative search would help, the Spanish query to use",
    )
    explanation: str = Field(description="Brief reason for this classification")


class ChatbotResponse(BaseModel):
    """LLM structured output for the non-streaming chat response."""

    respuesta: str = Field(
        description="Conversational response in Spanish, Markdown allowed"
    )
    puntos_clave: List[str] = Field(
        default_factory=list, description="Key points highlighted"
    )
    referencias_normativas: List[str] = Field(
        default_factory=list,
        description="Legal/normative references cited (e.g. Art. 383 ET)",
    )


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


__all__ = [
    "RawTransaction",
    "RawTransactionsList",
    "AsientoContable",
    "ContadorOutput",
    "AuditorHallazgo",
    "AuditorOutput",
    "TaxJustification",
    "TaxRateLookup",
    "ExplicacionResultado",
    "PrediccionPeriodo",
    "InterpretacionRatio",
    "ReporteroAnalysis",
    "ReporteroBriefAnalysis",
    "ChatIntentClassification",
    "ChatbotResponse",
    "GENERAL_EXTRACTION_INSTRUCTIONS",
]
