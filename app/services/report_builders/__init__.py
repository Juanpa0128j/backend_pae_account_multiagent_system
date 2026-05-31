"""Report builder registry."""

from app.services.report_builders.balance import build_balance
from app.services.report_builders.pnl import build_pnl
from app.services.report_builders.cashflow import build_cashflow
from app.services.report_builders.iva import build_iva
from app.services.report_builders.withholdings import build_withholdings
from app.services.report_builders.analysis import build_analysis
from app.services.report_builders.libro_diario import build_libro_diario
from app.services.report_builders.libro_auxiliar import build_libro_auxiliar
from app.services.report_builders.cambios_patrimonio import build_cambios_patrimonio
from app.services.report_builders.notas import build_notas_eeff

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
    "build_notas_eeff",
]
