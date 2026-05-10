"""Report builders — pure functions that turn ledger data into structured reports.

All builders accept the same signature:

    build_xxx(db, params: dict, svc) -> dict

Where *svc* is the `db_service` module (or any object implementing the same
`get_general_ledger`, `get_balance_sheet`, … interface).
"""

from app.services.report_builders.analysis import build_analysis
from app.services.report_builders.balance import build_balance
from app.services.report_builders.cambios_patrimonio import build_cambios_patrimonio
from app.services.report_builders.cashflow import build_cashflow
from app.services.report_builders.iva import build_iva
from app.services.report_builders.libro_auxiliar import build_libro_auxiliar
from app.services.report_builders.libro_diario import build_libro_diario
from app.services.report_builders.notas import build_notas
from app.services.report_builders.pnl import build_pnl
from app.services.report_builders.withholdings import build_withholdings

_BUILDERS = {
    "balance": build_balance,
    "pnl": build_pnl,
    "cashflow": build_cashflow,
    "iva": build_iva,
    "withholdings": build_withholdings,
    "analysis": build_analysis,
    "libro_diario": build_libro_diario,
    "libro_auxiliar": build_libro_auxiliar,
    "cambios_patrimonio": build_cambios_patrimonio,
    "notas_eeff": build_notas,
}

__all__ = [
    "build_balance",
    "build_pnl",
    "build_cashflow",
    "build_iva",
    "build_withholdings",
    "build_analysis",
    "build_libro_diario",
    "build_libro_auxiliar",
    "build_cambios_patrimonio",
    "build_notas",
    "get_builder",
]


def get_builder(report_type: str):
    """Return the builder function for *report_type* or raise KeyError."""
    if report_type not in _BUILDERS:
        raise KeyError(f"Unknown report_type '{report_type}'")
    return _BUILDERS[report_type]
