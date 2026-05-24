"""
Strict Pydantic schemas for all agent outputs.

Every agent (Ingesta, Contador, Tributario, Auditor) MUST produce output
that validates against these schemas. Non-compliant outputs are rejected
by the Supervisor and re-sent for correction.

Colombian accounting standards (PUC codes, DIAN tax codes) are validated.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared enums & constants
# ---------------------------------------------------------------------------


class TipoDocumento(str, Enum):
    RECIBO = "recibo"
    FACTURA = "factura"
    EXTRACTO = "extracto"
    NOTA_CREDITO = "nota_credito"
    NOTA_DEBITO = "nota_debito"
    COMPROBANTE_EGRESO = "comprobante_egreso"
    OTRO = "otro"


class TipoMovimiento(str, Enum):
    DEBITO = "debito"
    CREDITO = "credito"


class TipoImpuesto(str, Enum):
    IVA = "IVA"
    RETEFUENTE = "retefuente"
    RETEIVA = "reteiva"
    RETEICA = "reteica"
    ICA = "ica"  # Impuesto de Industria y Comercio — Ley 14/1983
    RENTA = "renta"  # Provisión Impuesto de Renta — Art. 240 ET, Ley 2277/2022
    TIMBRE = "timbre"
    OTRO = "otro"


class NivelRiesgo(str, Enum):
    BAJO = "bajo"
    MEDIO = "medio"
    ALTO = "alto"
    CRITICO = "critico"


class SeveridadHallazgo(str, Enum):
    INFO = "info"
    ADVERTENCIA = "advertencia"
    ERROR = "error"
    CRITICO = "critico"


# PUC code regex: 1-12 digits. Decreto 2650 catalog uses 4-6 digits; codes of
# 7-12 digits are auxiliary subdivisions defined by the issuer's ERP (e.g.
# Bancolombia 11200501 = subcuenta de 1120 Banco moneda extranjera).
PUC_PATTERN = re.compile(r"^\d{1,12}$")


# ---------------------------------------------------------------------------
# Helper validators
# ---------------------------------------------------------------------------


def _validate_puc_code(v: str) -> str:
    """Validate a Colombian PUC (Plan Único de Cuentas) code."""
    if not PUC_PATTERN.match(v):
        raise ValueError(
            f"Código PUC inválido '{v}'. Debe ser numérico de 1 a 12 dígitos. "
            "El catálogo oficial (Decreto 2650) usa 4-6 dígitos; "
            "códigos de 7-12 dígitos son auxiliares del ERP del emisor."
        )
    return v


def _parse_date(v: str | date | None) -> date | None:
    """Accept ISO date strings and convert to date objects."""
    if v is None:
        return None
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return datetime.strptime(v, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError(f"Fecha inválida '{v}'. Se espera formato ISO YYYY-MM-DD.")
    raise ValueError(f"No se puede parsear la fecha desde el tipo {type(v)}.")


def _normalize_entity_text(v: Any) -> str | None:
    """Normalize legacy string fields that may arrive as structured entity dicts."""
    if v is None:
        return None
    if isinstance(v, str):
        text = v.strip()
        return text or None
    if isinstance(v, dict):
        for key in ("razon_social", "nombre", "nit", "cedula"):
            raw = v.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
        return None
    return str(v).strip() or None


# ---------------------------------------------------------------------------
# 1. INGESTA Agent Output
# ---------------------------------------------------------------------------


class RawTransactionItem(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    fecha: Optional[date] = Field(
        None, description="Document date in YYYY-MM-DD format"
    )
    nit_emisor: str = Field(..., description="NIT of the issuer")
    nit_receptor: str = Field(..., description="NIT of the receiver (empresa)")
    total: Decimal = Field(..., ge=0, description="Total amount of the transaction")
    descripcion: Optional[str] = Field(
        None, description="Description/concept of the transaction"
    )
    items: Optional[List[dict]] = Field(None, description="Line items")

    @field_validator("fecha", mode="before")
    @classmethod
    def parse_fecha(cls, v):  # noqa: N805
        return _parse_date(v)

    @field_validator("nit_emisor", "nit_receptor", mode="before")
    @classmethod
    def clean_nit(cls, v):  # noqa: N805
        """Normalize Colombian NIT: strip dots, spaces, and validate non-empty."""
        if not isinstance(v, str):
            v = str(v)
        cleaned = v.replace(".", "").replace(" ", "").strip()
        if not cleaned:
            raise ValueError("El NIT no puede estar vacío.")
        return cleaned


class IngestOutput(BaseModel):
    """
    Schema for the Ingesta (Ingest) agent output.

    Each document type now returns its own rich structured dict via a dedicated
    Gemini extraction method.  This wrapper validates only the common optional
    fields that may appear across all document types; unknown fields are allowed
    so that doc-type-specific fields pass through without errors.

    The `transactions` field is kept for legacy compatibility but is no longer
    required — new extraction methods return structured content objects instead
    of transaction lists.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="allow")

    transactions: List[RawTransactionItem] = Field(
        default_factory=list,
        description="Legacy transaction list (empty for new structured extraction methods)",
    )
    fecha: Optional[date] = Field(
        None, description="Document date in YYYY-MM-DD format"
    )
    monto: Optional[Decimal] = Field(
        None, ge=0, description="Total amount on the document"
    )
    concepto: Optional[str] = Field(
        None, max_length=500, description="Concept or description of the transaction"
    )
    beneficiario: Optional[str] = Field(
        None, max_length=200, description="Name of the beneficiary / recipient"
    )
    empresa: Optional[str] = Field(
        None, max_length=200, description="Name of the issuing company"
    )
    referencia: Optional[str] = Field(
        None, max_length=100, description="Optional document reference number"
    )
    tipo_documento: Optional[TipoDocumento] = Field(
        None, description="Type of source document"
    )

    @field_validator("fecha", mode="before")
    @classmethod
    def parse_fecha(cls, v):  # noqa: N805
        return _parse_date(v)

    @field_validator("beneficiario", "empresa", mode="before")
    @classmethod
    def normalize_entity_like_fields(cls, v):  # noqa: N805
        return _normalize_entity_text(v)


# ---------------------------------------------------------------------------
# 2. CONTADOR Agent Output
# ---------------------------------------------------------------------------


class AsientoContable(BaseModel):
    """Single accounting entry (journal line)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    cuenta_puc: str = Field(
        ...,
        description=(
            "PUC account code (1-12 digits). 4-6 digits = official Decreto 2650 catalog; "
            "7-12 digits = ERP auxiliary subdivision (e.g. bank subaccount)."
        ),
    )
    nombre_cuenta: str = Field(
        ..., min_length=2, max_length=200, description="Account name"
    )
    tipo_movimiento: TipoMovimiento = Field(..., description="Debit or Credit")
    valor: Decimal = Field(..., ge=0, description="Entry amount")
    descripcion: Optional[str] = Field(
        None, max_length=500, description="Optional line description"
    )

    @field_validator("cuenta_puc")
    @classmethod
    def validate_puc(cls, v):  # noqa: N805
        return _validate_puc_code(v)


class ContadorOutput(BaseModel):
    """
    Schema for the Contador (Accountant) agent output.
    Produces classified journal entries following Colombian PUC.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    fecha_registro: date = Field(..., description="Accounting registration date")
    tipo_documento: TipoDocumento = Field(..., description="Source document type")
    descripcion_general: str = Field(
        ...,
        min_length=5,
        max_length=500,
        description="General description of the accounting event",
    )
    asientos: List[AsientoContable] = Field(
        ..., min_length=1, description="Journal entries (at least one)"
    )
    total_debitos: Decimal = Field(..., ge=0, description="Sum of all debit entries")
    total_creditos: Decimal = Field(..., ge=0, description="Sum of all credit entries")

    @field_validator("fecha_registro", mode="before")
    @classmethod
    def parse_fecha(cls, v):  # noqa: N805
        return _parse_date(v)

    @model_validator(mode="after")
    def check_double_entry(self) -> "ContadorOutput":
        """Verify debits == credits (partida doble)."""
        debits = sum(
            a.valor for a in self.asientos if a.tipo_movimiento == TipoMovimiento.DEBITO
        )
        credits_ = sum(
            a.valor
            for a in self.asientos
            if a.tipo_movimiento == TipoMovimiento.CREDITO
        )
        if debits != credits_:
            logger.warning(
                "Violación de partida doble: los débitos (%s) no igualan "
                "los créditos (%s).",
                debits,
                credits_,
            )
        if self.total_debitos != debits:
            logger.warning(
                "El total de débitos (%s) no coincide con la suma "
                "de los débitos de los asientos (%s).",
                self.total_debitos,
                debits,
            )
        if self.total_creditos != credits_:
            logger.warning(
                "El total de créditos (%s) no coincide con la suma "
                "de los créditos de los asientos (%s).",
                self.total_creditos,
                credits_,
            )
        return self


# ---------------------------------------------------------------------------
# 3. TRIBUTARIO Agent Output
# ---------------------------------------------------------------------------


class DetalleImpuesto(BaseModel):
    """Detail of a single tax applied to the transaction."""

    model_config = ConfigDict(str_strip_whitespace=True)

    tipo_impuesto: TipoImpuesto = Field(..., description="Tax type")
    base_gravable: Decimal = Field(..., ge=0, description="Taxable base amount")
    tarifa_porcentaje: Decimal = Field(
        ..., ge=0, le=100, description="Tax rate as percentage (0-100)"
    )
    valor_impuesto: Decimal = Field(..., ge=0, description="Calculated tax amount")
    cuenta_puc: Optional[str] = Field(
        None, description="PUC account for this tax entry"
    )

    @field_validator("cuenta_puc")
    @classmethod
    def validate_puc(cls, v):  # noqa: N805
        if v is not None:
            return _validate_puc_code(v)
        return v


class TributarioOutput(BaseModel):
    """
    Schema for the Tributario (Tax) agent output.
    Analyses tax implications per Colombian DIAN regulations.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    fecha_analisis: date = Field(..., description="Date the tax analysis was performed")
    documento_referencia: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Reference to the source document/transaction",
    )
    aplica_impuestos: bool = Field(
        ..., description="Whether taxes apply to this transaction"
    )
    impuestos: List[DetalleImpuesto] = Field(
        default_factory=list, description="List of applicable taxes"
    )
    total_impuestos: Decimal = Field(..., ge=0, description="Sum of all tax amounts")
    observaciones: Optional[str] = Field(
        None, max_length=1000, description="Additional tax observations or notes"
    )
    referencias_legales: List[str] = Field(
        default_factory=list,
        description="Legal references cited (e.g. 'Art. 383 ET', 'Decreto 2048/1992')",
    )
    asientos_enriquecidos: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Tax-enriched journal entries including retention/IVA lines",
    )

    @field_validator("fecha_analisis", mode="before")
    @classmethod
    def parse_fecha(cls, v):  # noqa: N805
        return _parse_date(v)

    @model_validator(mode="after")
    def check_tax_consistency(self) -> "TributarioOutput":
        """Validate internal tax consistency."""
        if self.aplica_impuestos and len(self.impuestos) == 0:
            raise ValueError(
                "aplica_impuestos es True pero no se proporcionaron impuestos."
            )
        if not self.aplica_impuestos and len(self.impuestos) > 0:
            raise ValueError(
                "aplica_impuestos es False pero se proporcionaron impuestos."
            )
        calculated_total = sum(i.valor_impuesto for i in self.impuestos)
        if self.total_impuestos != calculated_total:
            logger.warning(
                "El total de impuestos (%s) no coincide con la "
                "suma de los impuestos individuales (%s).",
                self.total_impuestos,
                calculated_total,
            )
        return self


# ---------------------------------------------------------------------------
# 4. AUDITOR Agent Output
# ---------------------------------------------------------------------------


class HallazgoAuditoria(BaseModel):
    """Single audit finding/observation."""

    model_config = ConfigDict(str_strip_whitespace=True)

    codigo: str = Field(
        ..., pattern=r"^AUD-\d{3,6}$", description="Finding code (e.g. AUD-001)"
    )
    severidad: SeveridadHallazgo = Field(..., description="Finding severity level")
    descripcion: str = Field(
        ...,
        min_length=10,
        max_length=1000,
        description="Detailed description of the finding",
    )
    campo_afectado: Optional[str] = Field(
        None, max_length=200, description="Field or account affected"
    )
    recomendacion: str = Field(
        ..., min_length=10, max_length=1000, description="Recommended corrective action"
    )


class AuditorOutput(BaseModel):
    """
    Schema for the Auditor agent output.
    Produces structured audit results with findings and risk assessment.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    fecha_auditoria: date = Field(..., description="Date the audit was performed")
    documento_referencia: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Reference to the audited document",
    )
    aprobado: bool = Field(..., description="Whether the document passes audit")
    nivel_riesgo: NivelRiesgo = Field(..., description="Overall risk level assessment")
    hallazgos: List[HallazgoAuditoria] = Field(
        default_factory=list, description="List of audit findings"
    )
    puntaje_calidad: Decimal = Field(
        ..., ge=0, le=100, description="Quality score 0-100"
    )
    resumen: str = Field(
        ...,
        min_length=10,
        max_length=2000,
        description="Executive summary of the audit",
    )

    @field_validator("fecha_auditoria", mode="before")
    @classmethod
    def parse_fecha(cls, v):  # noqa: N805
        return _parse_date(v)

    @model_validator(mode="after")
    def check_audit_consistency(self) -> "AuditorOutput":
        """Validate audit logic consistency."""
        if self.aprobado and self.nivel_riesgo in (
            NivelRiesgo.ALTO,
            NivelRiesgo.CRITICO,
        ):
            raise ValueError(
                "No se puede aprobar un documento con nivel de riesgo alto/crítico."
            )
        critical_findings = [
            h for h in self.hallazgos if h.severidad == SeveridadHallazgo.CRITICO
        ]
        if self.aprobado and len(critical_findings) > 0:
            raise ValueError("No se puede aprobar un documento con hallazgos críticos.")
        return self


# ---------------------------------------------------------------------------
# 5. REPORTERO Agent Output — one schema per report type
# ---------------------------------------------------------------------------


class CuentaResumen(BaseModel):
    """Summary of a single PUC account for report display."""

    model_config = ConfigDict(str_strip_whitespace=True)

    codigo: str = Field(..., description="PUC account code")
    nombre: str = Field(..., description="Account name")
    saldo: float = Field(..., description="Net balance for the period")


class BalanceSheetOutput(BaseModel):
    """
    Schema for the Balance Sheet (Balance General) report.
    Assets (class 1) == Liabilities (class 2) + Equity (class 3) + Net Profit
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    report_type: str = Field(default="balance_sheet")
    period_start: Optional[str] = Field(
        None, description="Start date ISO YYYY-MM-DD, or null for all-time"
    )
    period_end: str = Field(..., description="Cutoff date ISO YYYY-MM-DD")
    company_nit: Optional[str] = Field(
        None, description="Optional company NIT filter applied"
    )
    generated_at: str = Field(..., description="ISO UTC timestamp of report generation")
    activos: float = Field(..., description="Total assets (class 1 accounts)")
    pasivos: float = Field(..., description="Total liabilities (class 2 accounts)")
    patrimonio: float = Field(..., description="Equity (class 3 accounts)")
    activos_detalle: List[CuentaResumen] = Field(
        default_factory=list,
        description="Detailed class-1 asset accounts used in report exports",
    )
    pasivos_detalle: List[CuentaResumen] = Field(
        default_factory=list,
        description="Detailed class-2 liability accounts used in report exports",
    )
    patrimonio_detalle: List[CuentaResumen] = Field(
        default_factory=list,
        description="Detailed class-3 equity accounts used in report exports",
    )
    utilidad_neta: float = Field(
        ..., description="Net profit: revenue - expenses - COGS"
    )
    patrimonio_total: float = Field(..., description="Equity + Net Profit")
    cuadre: bool = Field(
        ..., description="True if assets == liabilities + total equity"
    )
    mensaje_cuadre: str = Field(
        ..., description="Human-readable balance validation message"
    )
    notas_normativas: List[str] = Field(
        default_factory=list,
        description="NIIF/PCGA normative notes retrieved from RAG (empty if RAG unavailable)",
    )
    analysis: Optional[Dict[str, Any]] = Field(
        None,
        description="LLM narrative analysis (present when include_analysis=true)",
    )


class PnLOutput(BaseModel):
    """Schema for the Profit and Loss (Estado de Resultados) report."""

    model_config = ConfigDict(str_strip_whitespace=True)

    report_type: str = Field(default="profit_and_loss")
    period_start: Optional[str] = Field(
        None, description="Start date ISO YYYY-MM-DD, or null for all-time"
    )
    period_end: str = Field(..., description="End date ISO YYYY-MM-DD")
    company_nit: Optional[str] = Field(
        None, description="Optional company NIT filter applied"
    )
    generated_at: str = Field(..., description="ISO UTC timestamp of report generation")
    ingresos: List[CuentaResumen] = Field(
        default_factory=list, description="Revenue accounts (class 4)"
    )
    costo_ventas: List[CuentaResumen] = Field(
        default_factory=list, description="COGS accounts (class 6)"
    )
    gastos: List[CuentaResumen] = Field(
        default_factory=list, description="Expense accounts (class 5)"
    )
    total_ingresos: float = Field(..., description="Total revenue")
    total_costo_ventas: float = Field(..., description="Total COGS")
    total_gastos: float = Field(..., description="Total operating expenses")
    utilidad_bruta: float = Field(..., description="Gross profit: revenue - COGS")
    utilidad_neta: float = Field(..., description="Net profit: gross - expenses")
    notas_normativas: List[str] = Field(
        default_factory=list,
        description="NIIF/PCGA normative notes retrieved from RAG (empty if RAG unavailable)",
    )
    analysis: Optional[Dict[str, Any]] = Field(
        None,
        description="LLM narrative analysis (present when include_analysis=true)",
    )


class CashFlowOutput(BaseModel):
    """Schema for the Cash Flow (Flujo de Caja) report — simplified direct method."""

    model_config = ConfigDict(str_strip_whitespace=True)

    report_type: str = Field(default="cash_flow")
    period_start: Optional[str] = Field(
        None, description="Start date ISO YYYY-MM-DD, or null for all-time"
    )
    period_end: str = Field(..., description="End date ISO YYYY-MM-DD")
    company_nit: Optional[str] = Field(
        None, description="Optional company NIT filter applied"
    )
    generated_at: str = Field(..., description="ISO UTC timestamp of report generation")
    cuentas_efectivo: List[CuentaResumen] = Field(
        default_factory=list,
        description="Cash and bank accounts (class 11XX)",
    )
    total_efectivo: float = Field(
        ..., description="Net balance across all cash accounts"
    )
    nota: str = Field(
        default="Flujo de caja directo — saldo neto de cuentas de efectivo y bancos (clase 11).",
        description="Methodology note",
    )
    notas_normativas: List[str] = Field(
        default_factory=list,
        description="NIIF/PCGA normative notes retrieved from RAG (empty if RAG unavailable)",
    )
    analysis: Optional[Dict[str, Any]] = Field(
        None,
        description="LLM narrative analysis (present when include_analysis=true)",
    )


class IVAOutput(BaseModel):
    """Schema for the IVA (Value Added Tax) report."""

    model_config = ConfigDict(str_strip_whitespace=True)

    report_type: str = Field(default="iva_report")
    period_start: Optional[str] = Field(
        None, description="Start date ISO YYYY-MM-DD, or null for all-time"
    )
    period_end: str = Field(..., description="End date ISO YYYY-MM-DD")
    company_nit: Optional[str] = Field(
        None, description="Optional company NIT filter applied"
    )
    generated_at: str = Field(..., description="ISO UTC timestamp of report generation")
    iva_generado: float = Field(
        ..., ge=0, description="IVA generated on sales (account 240805)"
    )
    iva_descontable: float = Field(
        ..., ge=0, description="IVA deductible on purchases (account 240802)"
    )
    iva_a_pagar: float = Field(
        ..., description="Net IVA payable: generated - deductible"
    )
    referencias: List[str] = Field(
        default_factory=lambda: ["Art. 477 ET", "Art. 24 ET"],
        description="Applicable legal references",
    )
    analysis: Optional[Dict[str, Any]] = Field(
        None,
        description="LLM narrative analysis (present when include_analysis=true)",
    )


class WithholdingsOutput(BaseModel):
    """Schema for the Withholdings (Retenciones) report."""

    model_config = ConfigDict(str_strip_whitespace=True)

    report_type: str = Field(default="withholdings_report")
    period_start: Optional[str] = Field(
        None, description="Start date ISO YYYY-MM-DD, or null for all-time"
    )
    period_end: str = Field(..., description="End date ISO YYYY-MM-DD")
    company_nit: Optional[str] = Field(
        None, description="Optional company NIT filter applied"
    )
    generated_at: str = Field(..., description="ISO UTC timestamp of report generation")
    retencion_en_la_fuente: float = Field(
        ..., ge=0, description="Retefuente balance (account 2365)"
    )
    retencion_ica: float = Field(
        ..., ge=0, description="ReteICA balance (account 2368)"
    )
    total_retenciones: float = Field(..., ge=0, description="Total withholdings")
    referencias: List[str] = Field(
        default_factory=lambda: ["Art. 383 ET", "Decreto 2048/1992"],
        description="Applicable legal references",
    )
    analysis: Optional[Dict[str, Any]] = Field(
        None,
        description="LLM narrative analysis (present when include_analysis=true)",
    )


# ---------------------------------------------------------------------------
# 6. REPORTERO Analysis Output — financial analysis with predictions
# ---------------------------------------------------------------------------


class FinancialRatios(BaseModel):
    """Key financial ratios computed deterministically."""

    model_config = ConfigDict(str_strip_whitespace=True)

    razon_corriente: Optional[float] = Field(
        None, description="Current ratio: current assets / current liabilities"
    )
    prueba_acida: Optional[float] = Field(
        None,
        description="Acid test: (current assets - inventories) / current liabilities",
    )
    margen_neto: Optional[float] = Field(
        None, description="Net margin %: net profit / revenue"
    )
    roa: Optional[float] = Field(
        None, description="Return on assets %: net profit / total assets"
    )
    razon_endeudamiento: Optional[float] = Field(
        None, description="Debt ratio: liabilities / assets"
    )
    deuda_patrimonio: Optional[float] = Field(
        None, description="Debt-to-equity: liabilities / equity"
    )
    rotacion_activos: Optional[float] = Field(
        None, description="Asset turnover: revenue / assets"
    )


class PrediccionNumerica(BaseModel):
    """Single month prediction from linear regression."""

    periodo: str = Field(..., description="Month as YYYY-MM")
    ingresos_estimados: float = Field(0, description="Projected revenue")
    gastos_estimados: float = Field(0, description="Projected expenses")
    utilidad_estimada: float = Field(0, description="Projected net profit")


class PeriodDelta(BaseModel):
    """Change in a single account between two periods."""

    account: str = Field(..., description="PUC account code")
    name: str = Field("", description="Account name")
    period1_value: float = Field(0)
    period2_value: float = Field(0)
    absolute_change: float = Field(0)
    percentage_change: Optional[float] = Field(
        None, description="% change, null if base is zero"
    )


class ComparativeReportOutput(BaseModel):
    """Side-by-side comparison of two periods."""

    model_config = ConfigDict(str_strip_whitespace=True)

    report_type: str = Field(default="comparative")
    period1: Dict[str, str] = Field(..., description="{start, end}")
    period2: Dict[str, str] = Field(..., description="{start, end}")
    deltas: List[PeriodDelta] = Field(default_factory=list)
    generated_at: str = Field(..., description="ISO UTC timestamp")


class FinancialAnalysisOutput(BaseModel):
    """Full financial analysis report with deterministic data + LLM narrative."""

    model_config = ConfigDict(str_strip_whitespace=True)

    report_type: str = Field(default="financial_analysis")
    period_start: Optional[str] = Field(None)
    period_end: str = Field(...)
    generated_at: str = Field(...)
    # Deterministic section
    balance_summary: Dict[str, Any] = Field(
        default_factory=dict, description="Balance sheet summary"
    )
    pnl_summary: Dict[str, Any] = Field(default_factory=dict, description="P&L summary")
    ratios: FinancialRatios = Field(default_factory=FinancialRatios)
    top_accounts_debit: List[CuentaResumen] = Field(default_factory=list)
    top_accounts_credit: List[CuentaResumen] = Field(default_factory=list)
    top_terceros: List[Dict[str, Any]] = Field(default_factory=list)
    anomalies: List[Dict[str, Any]] = Field(default_factory=list)
    monthly_trends: Dict[str, Any] = Field(
        default_factory=dict, description="Monthly trend data by class"
    )
    predicciones_numericas: List[PrediccionNumerica] = Field(
        default_factory=list, description="3-month linear regression projections"
    )
    # LLM-generated section (non-fatal: may be absent if Gemini fails)
    analysis: Optional[Dict[str, Any]] = Field(
        None,
        description="LLM narrative: resumen, explicaciones, predicciones, recomendaciones",
    )
    notas_normativas: List[str] = Field(default_factory=list)


class DashboardFinancialSummary(BaseModel):
    """Complete financial summary for dashboard view."""

    model_config = ConfigDict(str_strip_whitespace=True)

    total_activos: float = Field(0)
    total_pasivos: float = Field(0)
    patrimonio: float = Field(0)
    utilidad_neta: float = Field(0)
    efectivo_disponible: float = Field(0, description="Cash position (class 11)")
    iva_por_pagar: float = Field(0)
    total_retenciones: float = Field(0)
    ingresos_periodo: float = Field(0)
    gastos_periodo: float = Field(0)
    transacciones_por_estado: Dict[str, int] = Field(default_factory=dict)
    actividad_reciente: List[Dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Registry: maps agent names to their output schema
# ---------------------------------------------------------------------------

AGENT_OUTPUT_SCHEMAS: dict[str, type[BaseModel]] = {
    "ingesta": IngestOutput,
    "contador": ContadorOutput,
    "tributario": TributarioOutput,
    "auditor": AuditorOutput,
    # Reportero schemas are not used in the retry-validation loop (no LLM),
    # but are exported here for external callers and documentation.
    "reportero_balance": BalanceSheetOutput,
    "reportero_pnl": PnLOutput,
    "reportero_cashflow": CashFlowOutput,
    "reportero_iva": IVAOutput,
    "reportero_withholdings": WithholdingsOutput,
}
