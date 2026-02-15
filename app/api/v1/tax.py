from fastapi import APIRouter
from app.models.schemas import TaxResponse

router = APIRouter()

@router.get("/iva", response_model=TaxResponse)
async def get_iva_report():
    return TaxResponse(report="IVA", data={})

@router.get("/withholdings", response_model=TaxResponse)
async def get_withholdings_report():
    return TaxResponse(report="Withholdings", data={})
