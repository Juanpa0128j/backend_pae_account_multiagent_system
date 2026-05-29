"""Cash flow report builder."""

from decimal import Decimal

from ._base import (
    PREFIX_EFECTIVO,
    _debit_nature_balance,
    _fetch_rag_referencias,
    _ledger_by_prefix,
    _now_iso,
    _parse_date_param,
    _today_iso,
)

_PREFIX_EFECTIVO = PREFIX_EFECTIVO


def build_cashflow(db, params: dict, svc) -> dict:
    start_date = _parse_date_param(params.get("start_date"))
    end_date = _parse_date_param(params.get("end_date"), end_of_day=True)
    company_nit = params.get("company_nit")
    ledger = svc.get_general_ledger(
        db, start_date=start_date, end_date=end_date, company_nit=company_nit
    )

    efectivo_rows = _ledger_by_prefix(ledger, _PREFIX_EFECTIVO)
    cuentas_efectivo = [
        {
            "codigo": r["account"],
            "nombre": r["name"],
            "saldo": float(_debit_nature_balance(r)),
        }
        for r in efectivo_rows
    ]
    total_efectivo = sum(Decimal(str(c["saldo"])) for c in cuentas_efectivo)

    notas_normativas = _fetch_rag_referencias(
        "flujo caja efectivo bancos método directo NIIF NIC 7 PCGA",
        n_results=2,
    )

    return {
        "report_type": "cash_flow",
        "period_start": params.get("start_date"),
        "period_end": params.get("end_date") or _today_iso(),
        "company_nit": company_nit,
        "generated_at": _now_iso(),
        "cuentas_efectivo": cuentas_efectivo,
        "total_efectivo": float(total_efectivo),
        "nota": (
            "Flujo de caja directo — saldo neto de cuentas de efectivo y "
            "bancos (clase 11)."
        ),
        "notas_normativas": notas_normativas,
    }
