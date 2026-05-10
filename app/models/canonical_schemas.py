"""Canonical Pydantic schemas for stored financial statements.

All new FinancialStatement.data is written in one of these shapes.
Old historical shapes are migrated on read.
"""

from __future__ import annotations

from decimal import Decimal
from pydantic import BaseModel, Field


class AccountLine(BaseModel):
    codigo: str = Field(..., min_length=1)
    nombre: str
    saldo: Decimal
    tipo: str  # "activo", "pasivo", "patrimonio", "ingreso", "gasto", "costo"


class BalanceGeneralCanonical(BaseModel):
    period_start: str
    period_end: str
    company_nit: str
    activos: list[AccountLine]
    pasivos: list[AccountLine]
    patrimonio: list[AccountLine]
    utilidad_neta: Decimal
    patrimonio_total: Decimal
    cuadre: bool


class EstadoResultadosCanonical(BaseModel):
    period_start: str
    period_end: str
    company_nit: str
    ingresos: list[AccountLine]
    costo_ventas: list[AccountLine]
    gastos: list[AccountLine]
    total_ingresos: Decimal
    total_costo_ventas: Decimal
    total_gastos: Decimal
    utilidad_bruta: Decimal
    utilidad_operacional: Decimal
    utilidad_neta: Decimal


class FlujoCajaCanonical(BaseModel):
    period_start: str
    period_end: str
    company_nit: str
    operacionales: list[AccountLine]
    inversion: list[AccountLine]
    financiacion: list[AccountLine]
    neto_operacionales: Decimal
    neto_inversion: Decimal
    neto_financiacion: Decimal
    variacion_neta: Decimal
    saldo_inicial: Decimal
    saldo_final: Decimal
