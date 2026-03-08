from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
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
