from fastapi import APIRouter, Query
from typing import List, Optional
from pydantic import BaseModel

router = APIRouter()

class TransactionListItem(BaseModel):
    id: str
    fecha: str
    concepto: str
    total: float
    status: str
    nit_emisor: str

MOCK_TRANSACTIONS = [
    {"id": "1042", "fecha": "2026-02-15", "concepto": "Factura Proveedor XYZ", "total": 1500000, "status": "POSTED", "nit_emisor": "900.123.456-1"},
    {"id": "1041", "fecha": "2026-02-14", "concepto": "Servicio de consultoría", "total": 3200000, "status": "POSTED", "nit_emisor": "800.234.567-2"},
    {"id": "1040", "fecha": "2026-02-13", "concepto": "Compra insumos", "total": 750000, "status": "PENDING", "nit_emisor": "700.345.678-3"},
    {"id": "1039", "fecha": "2026-02-12", "concepto": "Factura de arriendo", "total": 2100000, "status": "REJECTED", "nit_emisor": "600.456.789-4"},
    {"id": "1038", "fecha": "2026-02-11", "concepto": "Servicios públicos", "total": 180000, "status": "PROCESSING", "nit_emisor": "500.567.890-5"},
]

@router.get("/", response_model=List[TransactionListItem])
async def list_transactions(status: Optional[str] = Query(None)):
    """
    Returns a list of transactions, optionally filtered by status.
    """
    if status is not None:
        return [t for t in MOCK_TRANSACTIONS if t["status"] == status]
    return MOCK_TRANSACTIONS

@router.get("/{id}", response_model=TransactionListItem)
async def get_transaction(id: str):
    """
    Returns a single transaction by ID.
    """
    for t in MOCK_TRANSACTIONS:
        if t["id"] == id:
            return t
    return MOCK_TRANSACTIONS[0] # Fallback mock response
