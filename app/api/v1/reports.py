from fastapi import APIRouter
from app.models.schemas import ReportResponse

router = APIRouter()

@router.get("/balance", response_model=ReportResponse)
async def get_balance_report():
    """
    FUTURE EVOLUTIONS:
    - REQUEST MODELS: Add query parameters for filtering (e.g., `start_date`, 
      `end_date`, `cost_center`).
    - AUTHENTICATION: Ensure proper JWT checks for data access control.
    """
    return ReportResponse(report="Balance Sheet", data={})

@router.get("/pnl", response_model=ReportResponse)
async def get_pnl_report():
    return ReportResponse(report="Profit & Loss", data={})

@router.get("/cashflow", response_model=ReportResponse)
async def get_cashflow_report():
    return ReportResponse(report="Cash Flow", data={})
