from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

class DashboardStatsResponse(BaseModel):
    documentos_pendientes: int
    transacciones_procesadas_mes: int
    alertas_activas: int
    total_activos_cop: float

@router.get("/stats", response_model=DashboardStatsResponse)
async def get_dashboard_stats():
    """
    Returns aggregated top-level metrics for the Dashboard view.
    Currently returns mock data.
    """
    return DashboardStatsResponse(
        documentos_pendientes=12,
        transacciones_procesadas_mes=148,
        alertas_activas=3,
        total_activos_cop=125000000.00
    )
