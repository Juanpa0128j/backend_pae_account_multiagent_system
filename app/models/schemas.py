from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class IngestResponse(BaseModel):
    message: str
    ingest_id: str
    status: str
    file_name: str
    ingest_ids: Optional[List[str]] = None
    extracted_transactions: int = 0
    raw_preview: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None
    per_file_results: Optional[List[Dict[str, Any]]] = None


class RawTransaction(BaseModel):
    fecha: str
    nit_emisor: str
    nit_receptor: str
    total: float
    descripcion: Optional[str] = None
    items: Optional[List[Dict[str, Any]]] = None
    source_file: Optional[str] = None


class ClassificationReviewOption(BaseModel):
    value: str
    label: str


class ClassificationReviewResponse(BaseModel):
    predicted_type: Optional[str] = None
    predicted_label: Optional[str] = None
    confidence: Optional[float] = None
    available_types: List[ClassificationReviewOption] = Field(default_factory=list)
    wrong_upload_area: bool = False


class PeriodReviewResponse(BaseModel):
    """HITL surface for the period & periodicity extracted from a Vía B upload.

    Same flavour as ``ClassificationReviewResponse`` but for the period range
    and frequency. Surfaced on ``IngestDetailResponse`` when the extraction
    confidence is low, the period collapsed to a single day, or the frequency
    had to be inferred from the span. The accountant can then PATCH the
    ``FinancialStatement`` row's period through ``PATCH /ingest/{id}/period``.
    """

    extracted_period_start: Optional[str] = None  # ISO YYYY-MM-DD
    extracted_period_end: Optional[str] = None
    extracted_periodicidad: Optional[str] = None  # 'mensual' | 'trimestral' | 'anual'
    extraction_confidence: Optional[float] = None  # 0..1 if available
    inferred_from_span: bool = False  # True when periodicidad came from fallback
    requires_review: bool = False
    review_reason: Optional[str] = (
        None  # 'low_confidence' | 'collapsed_range' | 'span_inferred' | 'annual_high_value'
    )


class IngestDetailResponse(BaseModel):
    ingest_id: str
    file_name: str
    status: str
    document_type: Optional[str] = None
    pathway: Optional[str] = None
    parser_mode: Optional[str] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    extraction_errors: Optional[List[str]] = None
    raw_transactions: List[RawTransaction] = Field(default_factory=list)
    # Audit metadata (mirrors ProcessStatusResponse for frontend parity)
    error_category: Optional[str] = None
    error_code: Optional[str] = None
    remediation: Optional[str] = None
    has_warnings: bool = False
    trace_url: Optional[str] = None
    classification_review: Optional[ClassificationReviewResponse] = None
    period_review: Optional[PeriodReviewResponse] = None
    file_names: Optional[List[str]] = None
    multi_file_mode: Optional[str] = None
    current_file_index: Optional[int] = None


class ClassificationReviewUpdateRequest(BaseModel):
    doc_type: str
    confirmed: bool = True
    parser_mode: Optional[str] = None


class PeriodReviewUpdateRequest(BaseModel):
    """Body for ``PATCH /api/v1/ingest/{id}/period`` — HITL override of the
    LLM-extracted period after human review (Ley 43/1990: el contador asume
    responsabilidad sobre la cifra final)."""

    period_start: str  # ISO YYYY-MM-DD
    period_end: str  # ISO YYYY-MM-DD
    periodicidad: Optional[str] = (
        None  # 'mensual' | 'trimestral' | 'anual' | 'personalizado'
    )


class MergeIngestRequest(BaseModel):
    source_ingest_id: str


class ProcessResponse(BaseModel):
    message: str
    process_id: str
    status: str


class ProcessCancelResponse(BaseModel):
    """Response for POST /process/{process_id}/cancel"""

    process_id: str
    status: str
    message: str


class ProcessStatusResponse(BaseModel):
    """Response for GET /process/status/{process_id}"""

    process_id: str
    status: str
    current_stage: Optional[str] = None
    current_agent: Optional[str] = None
    progress: Optional[int] = None
    error_message: Optional[str] = None
    error_category: Optional[str] = None
    error_code: Optional[str] = None
    remediation: Optional[str] = None
    agent_log: Optional[List[Dict[str, Any]]] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    has_warnings: bool = False
    trace_url: Optional[str] = None
    audit_review: Optional[Dict[str, Any]] = None


class ProcessResultResponse(BaseModel):
    """Response for GET /process/result/{process_id}"""

    process_id: str
    ingest_id: str
    status: str
    transactions: List[Dict[str, Any]] = Field(default_factory=list)
    error_message: Optional[str] = None
    error_category: Optional[str] = None
    error_code: Optional[str] = None
    remediation: Optional[str] = None


class CompanyProfileSetupRequest(BaseModel):
    """
    User-friendly input for automatic tax rate configuration.

    The user provides what they know (city, CIIU, régimen) and the agent
    determines the correct tax rates from the normative RAG.
    """

    nombre: Optional[str] = None
    ciudad: str = Field(
        ...,
        min_length=2,
        description="City where the company operates, e.g. 'Bogotá', 'Medellín', 'Cali'",
    )
    codigo_ciiu: str = Field(
        ...,
        min_length=1,
        description="CIIU economic activity code from the company's RUT",
    )
    iva_responsable: bool = Field(
        ...,
        description="True if régimen común (IVA applies), False if régimen simplificado",
    )


class CompanySettingsRequest(BaseModel):
    """Request body for creating or updating company tax settings."""

    nombre: Optional[str] = None
    ciudad: Optional[str] = None
    codigo_ciiu: Optional[str] = None
    iva_responsable: bool = True
    es_declarante: bool = True  # True=declarante de renta → lower retefuente rates
    tasa_retefuente_servicios: float = (
        0.04  # 4% servicios declarantes (Art. 401 ET, 2026)
    )
    tasa_retefuente_bienes: float = 0.025  # 2.5% compras declarantes
    tasa_retefuente_arrendamiento: float = 0.035  # 3.5% inmuebles declarantes
    tasa_reteica: float = 0.0069
    tasa_iva_general: float = 0.19
    tasa_ica: float = 0.00690  # ICA on gross income — Ley 14/1983
    tasa_renta: float = 0.35  # Renta societaria — Art. 240 ET, Ley 2277/2022


class CompanySettingsResponse(CompanySettingsRequest):
    """Response body for company tax settings endpoints."""

    nit: str
    locked_pathway: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ReportResponse(BaseModel):
    report: str
    data: Dict[str, Any]


class TaxResponse(BaseModel):
    report: str
    data: Dict[str, Any]


class EvaluationResponse(BaseModel):
    status: str
    metrics: Dict[str, float]


class TransactionResponse(BaseModel):
    id: str
    fecha: str
    concepto: str
    total: float
    status: str
    nit_emisor: str
    items: Optional[List[Dict[str, Any]]] = None
    raw_data: Optional[Dict[str, Any]] = None


class JournalEntryResponse(BaseModel):
    fecha: str
    comprobante: Optional[str] = None
    cuenta: str
    descripcion: str
    debito: float
    credito: float


class BalanceGeneralResponse(BaseModel):
    activos: float
    pasivos: float
    patrimonio: float
    ingresos: float
    gastos: float
    costos: float
    utilidad_neta: float
    patrimonio_total: float
    cuadre: bool


class CuentaPUCRequest(BaseModel):
    codigo: str
    nombre: str
    clase: int = Field(..., ge=1, le=6)
    naturaleza: Literal["debito", "credito"]
    grupo: Optional[str] = None
    cuenta: Optional[str] = None
    subcuenta: Optional[str] = None
    descripcion: Optional[str] = None
    activa: bool = True


class CuentaPUCResponse(BaseModel):
    id: int
    codigo: str
    nombre: str
    clase: int
    naturaleza: str
    grupo: Optional[str] = None
    cuenta: Optional[str] = None
    subcuenta: Optional[str] = None
    descripcion: Optional[str] = None
    activa: bool
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ICADeclaracionOutput(BaseModel):
    """Response for GET /api/v1/tax/ica — período ICA declaration."""

    report_type: Literal["ica_declaracion"] = "ica_declaracion"
    period_start: Optional[str] = None
    period_end: str
    generated_at: str
    ingresos_brutos: float
    tasa_ica: float
    ica_a_pagar: float
    cuenta_gasto_puc: str = "540101"
    cuenta_pasivo_puc: str = "2368"
    referencias: List[str]
    source: Optional[str] = None  # 'via_a' | 'via_b' — used by FE for context badge


class RentaProvisionOutput(BaseModel):
    """Response for GET /api/v1/tax/renta-provision — periodic income tax provision."""

    report_type: Literal["renta_provision"] = "renta_provision"
    period_start: Optional[str] = None
    period_end: str
    generated_at: str
    utilidad_antes_impuestos: float
    tasa_renta: float
    provision_renta: float
    cuenta_gasto_puc: str = "540502"
    cuenta_pasivo_puc: str = "240405"
    referencias: List[str]
    source: Optional[str] = None  # 'via_a' | 'via_b' — used by FE for context badge


# ─── Tax constants (UVT + base mínima) admin schemas ─────────────

VALID_CONCEPTO_VALUES = frozenset(
    {
        "retefuente_servicios",
        "retefuente_bienes",
        "retefuente_arrendamiento",
        "reteica",
    }
)


class UvtUpsertRequest(BaseModel):
    """Request body for PUT /api/v1/tax/constants/uvt."""

    year: int = Field(..., ge=2000, le=2100)
    value: float = Field(..., gt=0, description="UVT value in COP pesos")
    referencia_normativa: Optional[str] = Field(None, max_length=64)


class BaseMinimaUpsertRequest(BaseModel):
    """Request body for PUT /api/v1/tax/constants/base-minima."""

    concepto: str = Field(
        ...,
        description=(
            "One of: retefuente_servicios, retefuente_bienes, "
            "retefuente_arrendamiento, reteica"
        ),
    )
    uvt_units: float = Field(..., gt=0, description="Threshold in UVT units")
    year: int = Field(..., ge=2000, le=2100)


class UvtResponse(BaseModel):
    """Single UVT row."""

    year: int
    value: str
    referencia_normativa: Optional[str] = None

    model_config = {"from_attributes": True}


class BaseMinimaItem(BaseModel):
    """Single base mínima row."""

    concepto: str
    uvt_units: str
    year: int

    model_config = {"from_attributes": True}


class TaxConstantsResponse(BaseModel):
    """Response for GET /api/v1/tax/constants?year=YYYY."""

    uvt: Optional[UvtResponse] = None
    base_minima: List[BaseMinimaItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Pérdidas fiscales acumuladas schemas
# ---------------------------------------------------------------------------


class PerdidaFiscalResponse(BaseModel):
    """Single fiscal loss row returned by the API."""

    id: int
    company_nit: str
    year: int
    monto_perdida: str
    monto_compensado: str
    monto_pendiente: str
    decreto: Optional[str] = None
    notas: Optional[str] = None

    model_config = {"from_attributes": True}


class PerdidaFiscalUpsertRequest(BaseModel):
    """Request body for POST /api/v1/tax/perdidas-acumuladas."""

    company_nit: str = Field(..., min_length=1, max_length=20)
    year: int = Field(..., ge=1990, le=2100)
    monto_perdida: float = Field(
        ..., gt=0, description="Total fiscal loss in COP pesos"
    )
    decreto: Optional[str] = Field(None, max_length=100)
    notas: Optional[str] = None


# ---------------------------------------------------------------------------
# ReteicaTarifa schemas
# ---------------------------------------------------------------------------

_VALID_CIIU_SECCION = frozenset(
    {chr(c) for c in range(ord("A"), ord("U") + 1)} | {"general"}
)


class ReteicaTarifaResponse(BaseModel):
    """Single reteica_tarifas row returned by the API."""

    id: int
    municipio: str
    ciiu_seccion: str
    tasa: float
    fuente: Optional[str] = None
    base_minima_uvt: Optional[float] = None

    model_config = {"from_attributes": True}


class ReteicaTarifaUpsertRequest(BaseModel):
    """Request body for PUT /api/v1/tax/reteica-tarifas."""

    municipio: str = Field(..., min_length=1, max_length=100)
    ciiu_seccion: str = Field(..., description="CIIU section letter A-U or 'general'")
    tasa: float = Field(
        ..., gt=0, le=0.1, description="Rate as decimal fraction, e.g. 0.00966"
    )
    fuente: Optional[str] = Field(None, max_length=255)
    base_minima_uvt: Optional[float] = Field(None, ge=0)

    @field_validator("ciiu_seccion")
    @classmethod
    def _check_ciiu(cls, v: str) -> str:
        if v not in _VALID_CIIU_SECCION:
            raise ValueError(
                f"ciiu_seccion must be a letter A-U or 'general', got {v!r}"
            )
        return v


# ---------------------------------------------------------------------------
# TarifaRenta schemas
# ---------------------------------------------------------------------------

_VALID_REGIMEN = frozenset({"ordinario", "esal", "zona_franca", "rst"})
_VALID_ACTIVIDAD = frozenset({"general", "financiero", "hidroelectrico", "otro"})


class TarifaRentaResponse(BaseModel):
    """Single tarifa_renta row returned by the API."""

    id: int
    regimen: str
    actividad: Optional[str] = None
    tarifa_base: float
    sobretasa: float
    tarifa_efectiva: float
    year_from: int
    year_to: Optional[int] = None
    base_legal: Optional[str] = None
    notas: Optional[str] = None


# ---------------------------------------------------------------------------
# Tax declaration workflow schemas
# ---------------------------------------------------------------------------


class TaxDeclarationDraftResponse(BaseModel):
    """Full draft response including workflow audit fields."""

    draft_id: str
    company_nit: str
    form_type: str
    period_start: str
    period_end: str
    year: int
    status: str
    fields: List[Any]
    warnings: List[Any]
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    # Workflow fields
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    filed_by: Optional[str] = None
    filed_at: Optional[datetime] = None
    dian_acknowledgment: Optional[str] = None
    reopened_by: Optional[str] = None
    reopened_at: Optional[datetime] = None
    reopen_reason: Optional[str] = None


class FileDraftRequest(BaseModel):
    """Request body for POST /declarations/{draft_id}/file."""

    dian_acknowledgment: Optional[str] = Field(None, max_length=64)


class ReopenDraftRequest(BaseModel):
    """Request body for POST /declarations/{draft_id}/reopen."""

    reason: str = Field(..., min_length=5)


class TarifaRentaUpsertRequest(BaseModel):
    """Request body for POST /api/v1/tax/tarifas-renta."""

    regimen: str = Field(..., description="ordinario | esal | zona_franca | rst")
    actividad: Optional[str] = Field(
        None,
        description="general | financiero | hidroelectrico | otro | null (any)",
    )
    tarifa_base: float = Field(
        ..., gt=0, le=1, description="Base rate as decimal fraction, e.g. 0.35"
    )
    sobretasa: float = Field(
        0.0, ge=0, le=1, description="Surcharge decimal fraction, e.g. 0.05"
    )
    year_from: int = Field(..., ge=2000, le=2100)
    year_to: Optional[int] = Field(None, ge=2000, le=2100)
    base_legal: Optional[str] = Field(None, max_length=128)
    notas: Optional[str] = None

    @field_validator("regimen")
    @classmethod
    def validate_regimen(cls, v: str) -> str:
        if v not in _VALID_REGIMEN:
            raise ValueError(f"regimen must be one of {sorted(_VALID_REGIMEN)}")
        return v

    @field_validator("actividad")
    @classmethod
    def validate_actividad(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in _VALID_ACTIVIDAD:
            raise ValueError(
                f"actividad must be one of {sorted(_VALID_ACTIVIDAD)} or null"
            )
        return v


# ---------------------------------------------------------------------------
# Preflight validation schemas
# ---------------------------------------------------------------------------


class PreflightCheck(BaseModel):
    """Single preflight check result."""

    code: str
    severity: Literal["blocker", "warning", "info"]
    passed: bool
    message: str
    cta_path: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class PreflightResponse(BaseModel):
    """Aggregated preflight validation report."""

    ready: bool
    form_type: str
    period_start: str
    period_end: str
    checks: List[PreflightCheck]
    blockers: int
    warnings: int


# ---------------------------------------------------------------------------
# TaxConcept schemas (F350 — Res. DIAN 000031/2024)
# ---------------------------------------------------------------------------

_VALID_APLICA_A = frozenset({"PJ", "PN", "AMB"})
_VALID_CONCEPT_CATEGORIA = frozenset(
    {
        "compras",
        "servicios",
        "honorarios",
        "arrendamiento",
        "hidrocarburos",
        "minerales",
        "pes",
        "salarios",
        "ica",
        "iva",
        "otros",
    }
)


class TaxConceptResponse(BaseModel):
    """Single tax_concepts row returned by the API."""

    code: str
    label: str
    renglon_350: str
    aplica_a: str
    tarifa_default: Optional[float] = None
    base_minima_uvt: Optional[float] = None
    categoria: str
    art_referencia: Optional[str] = None
    activo: bool = True

    model_config = {"from_attributes": True}


class TaxConceptUpsertRequest(BaseModel):
    """Request body for PUT /api/v1/tax/concepts."""

    code: str = Field(..., min_length=1, max_length=16)
    label: str = Field(..., min_length=1, max_length=255)
    renglon_350: str = Field(..., min_length=1, max_length=8)
    aplica_a: str = Field(..., description="PJ | PN | AMB")
    categoria: str = Field(..., description="compras | servicios | ...")
    tarifa_default: Optional[float] = Field(default=None, ge=0, le=1)
    base_minima_uvt: Optional[float] = Field(default=None, ge=0)
    art_referencia: Optional[str] = Field(default=None, max_length=64)
    activo: bool = True

    @field_validator("aplica_a")
    @classmethod
    def _check_aplica_a(cls, v: str) -> str:
        if v not in _VALID_APLICA_A:
            raise ValueError(f"aplica_a must be one of {sorted(_VALID_APLICA_A)}")
        return v

    @field_validator("categoria")
    @classmethod
    def _check_categoria(cls, v: str) -> str:
        if v not in _VALID_CONCEPT_CATEGORIA:
            raise ValueError(
                f"categoria must be one of {sorted(_VALID_CONCEPT_CATEGORIA)}"
            )
        return v


# ---------------------------------------------------------------------------
# AjusteFiscal schemas (F2516 reconciliation rows)
# ---------------------------------------------------------------------------

_VALID_AJUSTE_SECCIONES = frozenset(
    {
        "ESF_ACTIVO",
        "ESF_PASIVO",
        "ESF_PATRIMONIO",
        "ERI_INGRESO",
        "ERI_COSTO",
        "ERI_GASTO",
    }
)

_VALID_AJUSTE_TIPO_DIFERENCIA = frozenset(
    {"permanente", "temporaria_imponible", "temporaria_deducible"}
)


class AjusteFiscalResponse(BaseModel):
    """Single ajuste_fiscal row returned by the API."""

    id: str
    company_nit: str
    year: int
    seccion: str
    concepto: str
    valor_contable: float
    valor_fiscal: float
    tipo_diferencia: str
    descripcion: Optional[str] = None


class AjusteFiscalUpsertRequest(BaseModel):
    """Request body for PUT /api/v1/tax/ajustes-fiscales."""

    company_nit: str = Field(..., min_length=1, max_length=20)
    year: int = Field(..., ge=1990, le=2100)
    seccion: str = Field(..., description="ESF_* | ERI_*")
    concepto: str = Field(..., min_length=1, max_length=64)
    valor_contable: float = Field(default=0.0)
    valor_fiscal: float = Field(default=0.0)
    tipo_diferencia: str = Field(
        ...,
        description="permanente | temporaria_imponible | temporaria_deducible",
    )
    descripcion: Optional[str] = None

    @field_validator("seccion")
    @classmethod
    def _check_seccion(cls, v: str) -> str:
        if v not in _VALID_AJUSTE_SECCIONES:
            raise ValueError(
                f"seccion must be one of {sorted(_VALID_AJUSTE_SECCIONES)}"
            )
        return v

    @field_validator("tipo_diferencia")
    @classmethod
    def _check_tipo_diferencia(cls, v: str) -> str:
        if v not in _VALID_AJUSTE_TIPO_DIFERENCIA:
            raise ValueError(
                f"tipo_diferencia must be one of {sorted(_VALID_AJUSTE_TIPO_DIFERENCIA)}"
            )
        return v


# ── NationalRate schemas ──────────────────────────────────────────────────────


class NationalRateResponse(BaseModel):
    """Single national_rates row returned by the API."""

    code: str
    value: float
    descripcion: str
    norma_referencia: str
    vigente_desde: str  # ISO date string e.g. "2023-01-01"

    model_config = {"from_attributes": True}


class NationalRateUpdateRequest(BaseModel):
    """Request body for PUT /api/v1/settings/national-rates/{code}."""

    value: float = Field(
        ...,
        gt=0,
        le=1.0,
        description="Rate as decimal fraction, e.g. 0.04 for 4%",
    )
    descripcion: str = Field(..., min_length=1, max_length=255)
    norma_referencia: str = Field(..., min_length=1, max_length=128)
    vigente_desde: str = Field(
        ...,
        description="Effective date ISO format YYYY-MM-DD",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    )


# ── Company-scoped PUC and Rate Override schemas ────────────────────────────────


class CompanyPucEntryResponse(BaseModel):
    """Single PUC account with per-company activation status."""

    codigo: str
    nombre: str
    clase: int
    naturaleza: str
    activa: bool  # Global activa flag
    is_active_for_company: bool  # Effective for this company
    custom_nombre: Optional[str] = None

    model_config = {"from_attributes": True}


class CompanyPucToggleRequest(BaseModel):
    """Request body for PUT /api/v1/settings/company/{nit}/puc/{codigo}."""

    is_active: bool
    custom_nombre: Optional[str] = None


class EffectiveRateResponse(BaseModel):
    """Single effective rate (national + company override)."""

    code: str
    value: float
    descripcion: str
    norma_referencia: str
    vigente_desde: str  # ISO date string
    overridden: bool = False  # True if company has an override

    model_config = {"from_attributes": True}


class CompanyRateOverrideRequest(BaseModel):
    """Request body for PUT /api/v1/settings/company/{nit}/rates/{code}."""

    value: float = Field(
        ...,
        gt=0,
        le=1.0,
        description="Rate as decimal fraction, e.g. 0.04 for 4%",
    )
    norma_referencia: Optional[str] = Field(None, max_length=128)
    vigente_desde: str = Field(
        ...,
        description="Effective date ISO format YYYY-MM-DD",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    )
