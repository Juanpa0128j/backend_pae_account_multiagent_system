"""Cambios en Patrimonio report builder."""

from app.services.report_builders._base import (
    _CLASS_PATRIMONIO,
    _credit_nature_balance,
    _fetch_rag_referencias,
    _ledger_by_prefix,
    _now_iso,
    _parse_date_param,
    _today_iso,
)


def build_cambios_patrimonio(db, params: dict, svc) -> dict:
    """Cambios en Patrimonio: changes to equity accounts (class 3)."""
    start_date = _parse_date_param(params.get("start_date"))
    end_date = _parse_date_param(params.get("end_date"), end_of_day=True)
    company_nit = params.get("company_nit")

    ledger = svc.get_general_ledger(
        db, start_date=start_date, end_date=end_date, company_nit=company_nit
    )

    patrimonio_rows = _ledger_by_prefix(ledger, _CLASS_PATRIMONIO)

    cambios = [
        {
            "codigo": r["account"],
            "nombre": r["name"],
            "movimiento_debito": float(r["total_debit"]),
            "movimiento_credito": float(r["total_credit"]),
            "saldo_final": float(_credit_nature_balance(r)),
        }
        for r in patrimonio_rows
    ]

    notas_normativas = _fetch_rag_referencias(
        "Cambios en Patrimonio capital reservas resultados revaluacion",
        n_results=2,
    )

    return {
        "report_type": "cambios_patrimonio",
        "period_start": params.get("start_date"),
        "period_end": params.get("end_date") or _today_iso(),
        "company_nit": company_nit,
        "generated_at": _now_iso(),
        "cambios": cambios,
        "total_cambios": len(cambios),
        "notas_normativas": notas_normativas,
    }
