from fastapi import APIRouter, Query
from typing import List, Optional, Dict, Any

router = APIRouter()

@router.get("/")
async def get_books(
    tipo: str = Query(..., description="diario, mayor, or auxiliar"),
    fecha_inicio: Optional[str] = None,
    fecha_fin: Optional[str] = None,
    cuenta_puc: Optional[str] = None,
    tercero_nit: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Queries the accounting books (Diario, Mayor, Auxiliar).
    Returns mock ledger entries based on the requested book type.
    """
    
    # Mock data for demonstration
    if tipo == "diario":
        return [
            {"fecha": "2026-02-15", "comprobante": "CE-001", "cuenta": "110505", "descripcion": "Pago proveedor", "debito": 0, "credito": 1500000},
            {"fecha": "2026-02-15", "comprobante": "CE-001", "cuenta": "220505", "descripcion": "Pago proveedor", "debito": 1500000, "credito": 0},
        ]
    elif tipo == "mayor":
        return [
            {"cuenta": "110505", "nombre": "Caja General", "saldo_anterior": 5000000, "mov_debito": 1000000, "mov_credito": 1500000, "nuevo_saldo": 4500000},
        ]
    else:
        return [
            {"fecha": "2026-02-10", "documento": "FC-123", "tercero": "Proveedor XYZ", "detalle": "Compra papelería", "valor": 350000}
        ]
