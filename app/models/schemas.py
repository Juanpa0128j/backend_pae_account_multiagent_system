from pydantic import BaseModel, Field
from typing import List, Literal, Optional, Dict, Any
from datetime import datetime


class IngestResponse(BaseModel):
    message: str
    ingest_id: str
    status: str
    file_name: str
    extracted_transactions: int = 0
    raw_preview: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None


class RawTransaction(BaseModel):
    fecha: str
    nit_emisor: str
    nit_receptor: str
    total: float
    descripcion: Optional[str] = None
    items: Optional[List[Dict[str, Any]]] = None


class IngestDetailResponse(BaseModel):
    ingest_id: str
    file_name: str
    status: str
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    extraction_errors: Optional[List[str]] = None
    raw_transactions: List[RawTransaction] = Field(default_factory=list)


class ProcessResponse(BaseModel):
    message: str
    process_id: str
    status: str


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
    tasa_retefuente_servicios: float = 0.11
    tasa_retefuente_bienes: float = 0.03
    tasa_retefuente_arrendamiento: float = 0.10
    tasa_reteica: float = 0.0069
    tasa_iva_general: float = 0.19
    tasa_ica: float = 0.00690  # ICA on gross income — Ley 14/1983
    tasa_renta: float = 0.35  # Renta societaria — Art. 240 ET, Ley 2277/2022


class CompanySettingsResponse(CompanySettingsRequest):
    """Response body for company tax settings endpoints."""

    nit: str
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


class CuentaPUCResponse(BaseModel):
    codigo: str
    nombre: str
    clase: int
    naturaleza: str
    descripcion: Optional[str] = None


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
    cuenta_pasivo_puc: str = "240808"
    referencias: List[str]


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
