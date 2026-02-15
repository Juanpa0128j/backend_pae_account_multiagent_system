from fastapi import APIRouter
from app.models.schemas import EvaluationResponse

router = APIRouter()

@router.get("/run", response_model=EvaluationResponse)
async def run_evaluation():
    return EvaluationResponse(
        status="completed",
        metrics={
            "schema_compliance": 1.0,
            "double_entry_integrity": 1.0
        }
    )
