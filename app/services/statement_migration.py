"""Migrate historical FinancialStatement.data shapes to canonical schemas."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.models.canonical_schemas import (
    AccountLine,
    BalanceGeneralCanonical,
    EstadoResultadosCanonical,
)


def _to_decimal(v: Any) -> Decimal:
    if isinstance(v, Decimal):
        return v
    if isinstance(v, (int, float, str)):
        return Decimal(str(v))
    return Decimal("0")


def _classify_tipo(codigo: str) -> str:
    if not codigo or not codigo[0].isdigit():
        return "activo"
    clase = int(codigo[0])
    mapping = {
        1: "activo",
        2: "pasivo",
        3: "patrimonio",
        4: "ingreso",
        5: "gasto",
        6: "costo",
    }
    return mapping.get(clase, "activo")


def is_canonical_balance(data: dict[str, Any]) -> bool:
    """Return True if data already follows the canonical balance shape."""
    return isinstance(data.get("activos"), list) and (
        len(data["activos"]) == 0
        or (isinstance(data["activos"][0], dict) and "codigo" in data["activos"][0])
    )


def is_canonical_pnl(data: dict[str, Any]) -> bool:
    """Return True if data already follows the canonical P&L shape."""
    return isinstance(data.get("ingresos"), list) and (
        len(data["ingresos"]) == 0
        or (isinstance(data["ingresos"][0], dict) and "codigo" in data["ingresos"][0])
    )


def migrate_balance_general(
    data: dict[str, Any],
    *,
    company_nit: str,
    period_start: str | None = None,
    period_end: str | None = None,
) -> BalanceGeneralCanonical:
    """Migrate any historical balance-general shape to canonical."""
    if is_canonical_balance(data):
        return BalanceGeneralCanonical.model_validate(data)

    ps = period_start or data.get("periodo_inicio") or data.get("period_start") or ""
    pe = period_end or data.get("periodo_fin") or data.get("period_end") or ""

    activos: list[AccountLine] = []
    pasivos: list[AccountLine] = []
    patrimonio: list[AccountLine] = []

    # Historical shape 1: cuentas list with cuenta/saldo
    cuentas = data.get("cuentas") or data.get("accounts") or []
    for c in cuentas:
        if not isinstance(c, dict):
            continue
        codigo = str(
            c.get("cuenta")
            or c.get("account")
            or c.get("cuenta_puc")
            or c.get("codigo")
            or ""
        )
        nombre = str(
            c.get("nombre") or c.get("name") or c.get("cuenta_nombre") or codigo
        )
        saldo = _to_decimal(
            c.get("saldo") or c.get("valor") or c.get("net_balance") or 0
        )
        tipo = _classify_tipo(codigo)
        line = AccountLine(codigo=codigo, nombre=nombre, saldo=saldo, tipo=tipo)
        if tipo == "activo":
            activos.append(line)
        elif tipo == "pasivo":
            pasivos.append(line)
        elif tipo == "patrimonio":
            patrimonio.append(line)

    # Historical shape 2: flat lines list
    lines = data.get("lines") or []
    for line in lines:
        if not isinstance(line, dict):
            continue
        codigo = str(
            line.get("cuenta_puc") or line.get("codigo") or line.get("cuenta") or ""
        )
        nombre = str(
            line.get("cuenta_nombre")
            or line.get("nombre")
            or line.get("name")
            or codigo
        )
        debito = _to_decimal(line.get("debito") or line.get("debit") or 0)
        credito = _to_decimal(line.get("credito") or line.get("credit") or 0)
        clase = int(codigo[0]) if codigo and codigo[0].isdigit() else 0
        if clase in (1, 5, 6):
            saldo = debito - credito
        else:
            saldo = credito - debito
        tipo = _classify_tipo(codigo)
        account_line = AccountLine(codigo=codigo, nombre=nombre, saldo=saldo, tipo=tipo)
        if tipo == "activo":
            activos.append(account_line)
        elif tipo == "pasivo":
            pasivos.append(account_line)
        elif tipo == "patrimonio":
            patrimonio.append(account_line)

    return BalanceGeneralCanonical(
        period_start=ps,
        period_end=pe,
        company_nit=company_nit,
        activos=activos,
        pasivos=pasivos,
        patrimonio=patrimonio,
        utilidad_neta=_to_decimal(
            data.get("utilidad_neta") or data.get("net_profit") or 0
        ),
        patrimonio_total=_to_decimal(
            data.get("total_patrimonio") or data.get("patrimonio_total") or 0
        ),
        cuadre=bool(data.get("cuadre", False)),
    )


def migrate_estado_resultados(
    data: dict[str, Any],
    *,
    company_nit: str,
    period_start: str | None = None,
    period_end: str | None = None,
) -> EstadoResultadosCanonical:
    """Migrate any historical P&L shape to canonical."""
    if is_canonical_pnl(data):
        return EstadoResultadosCanonical.model_validate(data)

    ps = period_start or data.get("periodo_inicio") or data.get("period_start") or ""
    pe = period_end or data.get("periodo_fin") or data.get("period_end") or ""

    def _build_lines(items: list[Any] | None, tipo: str) -> list[AccountLine]:
        out: list[AccountLine] = []
        for c in items or []:
            if not isinstance(c, dict):
                continue
            codigo = str(
                c.get("cuenta_puc") or c.get("codigo") or c.get("cuenta") or ""
            )
            nombre = str(
                c.get("nombre") or c.get("name") or c.get("cuenta_nombre") or codigo
            )
            saldo = _to_decimal(c.get("saldo") or c.get("valor") or 0)
            out.append(
                AccountLine(codigo=codigo, nombre=nombre, saldo=saldo, tipo=tipo)
            )
        return out

    return EstadoResultadosCanonical(
        period_start=ps,
        period_end=pe,
        company_nit=company_nit,
        ingresos=_build_lines(data.get("ingresos"), "ingreso"),
        costo_ventas=_build_lines(data.get("costo_ventas"), "costo"),
        gastos=_build_lines(data.get("gastos"), "gasto"),
        total_ingresos=_to_decimal(data.get("total_ingresos") or 0),
        total_costo_ventas=_to_decimal(data.get("total_costo_ventas") or 0),
        total_gastos=_to_decimal(data.get("total_gastos") or 0),
        utilidad_bruta=_to_decimal(data.get("utilidad_bruta") or 0),
        utilidad_operacional=_to_decimal(data.get("utilidad_operacional") or 0),
        utilidad_neta=_to_decimal(data.get("utilidad_neta") or 0),
    )
