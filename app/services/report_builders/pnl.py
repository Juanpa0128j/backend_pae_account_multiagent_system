"""Profit & Loss report builder."""

from decimal import Decimal

from app.services.report_builders._base import (
    _CLASS_COSTO_VENTAS,
    _CLASS_GASTOS,
    _CLASS_INGRESOS,
    _credit_nature_balance,
    _debit_nature_balance,
    _fetch_rag_referencias,
    _ledger_by_prefix,
    _now_iso,
    _parse_date_param,
    _today_iso,
)


def build_pnl(db, params: dict, svc) -> dict:
    start_date = _parse_date_param(params.get("start_date"))
    end_date = _parse_date_param(params.get("end_date"), end_of_day=True)
    company_nit = params.get("company_nit")
    ledger = svc.get_general_ledger(
        db, start_date=start_date, end_date=end_date, company_nit=company_nit
    )

    ingresos_rows = _ledger_by_prefix(ledger, _CLASS_INGRESOS)
    gastos_rows = _ledger_by_prefix(ledger, _CLASS_GASTOS)
    costo_rows = _ledger_by_prefix(ledger, _CLASS_COSTO_VENTAS)

    def to_cuenta(row: dict, balance: Decimal) -> dict:
        return {
            "codigo": row["account"],
            "nombre": row["name"],
            "saldo": float(balance),
        }

    ingresos = [to_cuenta(r, _credit_nature_balance(r)) for r in ingresos_rows]
    gastos = [to_cuenta(r, _debit_nature_balance(r)) for r in gastos_rows]
    costo_ventas = [to_cuenta(r, _debit_nature_balance(r)) for r in costo_rows]

    total_ingresos = sum(Decimal(str(c["saldo"])) for c in ingresos)
    total_gastos = sum(Decimal(str(c["saldo"])) for c in gastos)
    total_costo = sum(Decimal(str(c["saldo"])) for c in costo_ventas)
    utilidad_bruta = total_ingresos - total_costo
    utilidad_neta = utilidad_bruta - total_gastos

    notas_normativas = _fetch_rag_referencias(
        "estado resultados ingresos gastos costo ventas principio realización NIIF PCGA",
        n_results=2,
    )

    return {
        "report_type": "profit_and_loss",
        "period_start": params.get("start_date"),
        "period_end": params.get("end_date") or _today_iso(),
        "company_nit": company_nit,
        "generated_at": _now_iso(),
        "ingresos": ingresos,
        "costo_ventas": costo_ventas,
        "gastos": gastos,
        "total_ingresos": float(total_ingresos),
        "total_costo_ventas": float(total_costo),
        "total_gastos": float(total_gastos),
        "utilidad_bruta": float(utilidad_bruta),
        "utilidad_neta": float(utilidad_neta),
        "notas_normativas": notas_normativas,
    }
