"""
Evaluation API – exposes Schema Compliance Rate and other validation metrics.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import Any, Dict

from app.services.validation_engine import get_validator


router = APIRouter()


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class AgentDetail(BaseModel):
    passed: int = 0
    failed: int = 0
    total: int = 0
    rate: float = 1.0


class SchemaComplianceMetrics(BaseModel):
    """Full metrics report for schema validation."""
    overall_compliance_rate: float = Field(
        ..., ge=0, le=1,
        description="Overall Schema Compliance Rate (0.0 – 1.0)"
    )
    per_agent_compliance_rate: Dict[str, float] = Field(
        default_factory=dict,
        description="Compliance rate per agent"
    )
    total_validations: int = 0
    total_passed: int = 0
    total_failed: int = 0
    per_agent_detail: Dict[str, AgentDetail] = Field(
        default_factory=dict,
        description="Detailed pass/fail per agent"
    )


class EvaluationResponse(BaseModel):
    status: str
    metrics: Dict[str, Any]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/run", response_model=EvaluationResponse)
async def run_evaluation():
    """Return live validation metrics from the OutputValidator."""
    validator = get_validator()
    metrics = validator.get_metrics()
    return EvaluationResponse(status="completed", metrics=metrics)


@router.get("/schema-compliance", response_model=SchemaComplianceMetrics)
async def schema_compliance():
    """Detailed Schema Compliance Rate report."""
    validator = get_validator()
    raw = validator.get_metrics()
    return SchemaComplianceMetrics(
        overall_compliance_rate=raw["overall_compliance_rate"],
        per_agent_compliance_rate=raw["per_agent_compliance_rate"],
        total_validations=raw["total_validations"],
        total_passed=raw["total_passed"],
        total_failed=raw["total_failed"],
        per_agent_detail={
            k: AgentDetail(**v)
            for k, v in raw["per_agent_detail"].items()
        },
    )


@router.post("/reset-metrics")
async def reset_metrics():
    """Reset all validation metrics (for testing)."""
    validator = get_validator()
    validator.reset_metrics()
    return {"status": "metrics_reset"}
