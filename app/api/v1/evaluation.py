"""
Evaluation API – exposes Schema Compliance Rate, validation metrics,
and RAG collection status.
"""

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import Any, Dict

from app.services.validation_engine import get_validator

logger = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# RAG status
# ---------------------------------------------------------------------------

class CollectionStatus(BaseModel):
    name: str
    document_count: int


class RAGStatusResponse(BaseModel):
    status: str = Field(description="'ready' if normativa is populated, 'empty' otherwise")
    normativa_collection: CollectionStatus
    empresa_collections: list[CollectionStatus] = Field(default_factory=list)
    total_collections: int


@router.get("/rag-status", response_model=RAGStatusResponse)
async def rag_status():
    """
    Return the status of all ChromaDB vector collections.

    Use this endpoint to verify that the normativa collection has been
    seeded before processing transactions. An empty normativa collection
    means `python scripts/populate_rag.py` has not been run yet.
    """
    try:
        # Lazy import: avoid startup failure if ChromaDB is not installed
        from app.core.vectordb import get_vectordb, NORMATIVA_COLLECTION
        db = get_vectordb()

        normativa_count = db.collection_count(NORMATIVA_COLLECTION)
        normativa_col = CollectionStatus(
            name=NORMATIVA_COLLECTION,
            document_count=normativa_count,
        )

        all_collections = db.list_collections()
        empresa_cols = [
            CollectionStatus(
                name=name,
                document_count=db.collection_count(name),
            )
            for name in all_collections
            if name != NORMATIVA_COLLECTION
        ]

        status = "ready" if normativa_count > 0 else "empty"
        return RAGStatusResponse(
            status=status,
            normativa_collection=normativa_col,
            empresa_collections=empresa_cols,
            total_collections=len(all_collections),
        )
    except Exception as exc:
        logger.warning("Could not connect to ChromaDB: %s", exc)
        return RAGStatusResponse(
            status="unavailable",
            normativa_collection=CollectionStatus(
                name=NORMATIVA_COLLECTION, document_count=0
            ),
            empresa_collections=[],
            total_collections=0,
        )
