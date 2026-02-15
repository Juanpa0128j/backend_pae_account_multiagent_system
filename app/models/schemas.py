from pydantic import BaseModel
from typing import Optional, Dict, Any

class IngestResponse(BaseModel):
    message: str
    ingest_id: str
    status: str

class ProcessResponse(BaseModel):
    message: str
    process_id: str
    status: str

class ReportResponse(BaseModel):
    report: str
    data: Dict[str, Any]

class TaxResponse(BaseModel):
    report: str
    data: Dict[str, Any]

class EvaluationResponse(BaseModel):
    status: str
    metrics: Dict[str, float]
