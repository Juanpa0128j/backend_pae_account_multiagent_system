"""Balance sheet report builder."""

from decimal import Decimal

from app.services.report_builders._base import (
    _CLASS_ACTIVOS,
    _CLASS_PASIVOS,
    _CLASS_PATRIMONIO,
    _credit_nature_balance,
    _debit_nature_balance,
    _fetch_rag_referencias,
    _ledger_by_prefix,
    _now_iso,
    _parse_date_param,
    _today_iso,
)


def build_balance(db, params: dict, svc) -> dict:
    end_date = _parse_date_param(params.get("end_date"), end_of_day=True)
    company_nit = params.get("company_nit")
    data = svc.get_balance_sheet(db, cutoff_date=end_date, company_nit=company_nit)
    ledger = svc.get_general_ledger(
        db,
        start_date=None,
        end_date=end_date,
        company_nit=company_nit,
    )

    def _to_cuenta(row: dict, balance: Decimal) -> dict:
        return {
            "codigo": row["account"],
            "nombre": row["name"],
            "saldo": float(balance),
        }

    activos_detalle = [
        _to_cuenta(r, _debit_nature_balance(r))
        for r in _ledger_by_prefix(ledger, _CLASS_ACTIVOS)
    ]
    pasivos_detalle = [
        _to_cuenta(r, _credit_nature_balance(r))
        for r in _ledger_by_prefix(ledger, _CLASS_PASIVOS)
    ]
    patrimonio_detalle = [
        _to_cuenta(r, _credit_nature_balance(r))
        for r in _ledger_by_prefix(ledger, _CLASS_PATRIMONIO)
    ]

    activos = Decimal(str(data["assets"]))
    pasivos = Decimal(str(data["liabilities"]))
    patrimonio = Decimal(str(data["equity"]))
    utilidad_neta = Decimal(str(data["net_profit"]))
    patrimonio_total = Decimal(str(data["total_equity"]))
    cuadre = bool(data["is_balanced"])

    if cuadre:
        mensaje = (
            f"ACTIVOS ({activos:,.0f}) == "
            f"PASIVOS ({pasivos:,.0f}) + PATRIMONIO TOTAL ({patrimonio_total:,.0f}) ✓"
        )
    else:
        diferencia = activos - (pasivos + patrimonio_total)
        mensaje = (
            f"DESCUADRE: ACTIVOS ({activos:,.0f}) - "
            f"(PASIVOS + PATRIMONIO TOTAL) = {diferencia:,.0f}"
        )

    notas_normativas = _fetch_rag_referencias(
        "NIIF balance general activos pasivos patrimonio principios contabilidad PCGA",
        n_results=2,
    )

    return {
        "report_type": "balance_sheet",
        "period_start": params.get("start_date"),
        "period_end": params.get("end_date") or _today_iso(),
        "company_nit": company_nit,
        "generated_at": _now_iso(),
        "activos": float(activos),
        "pasivos": float(pasivos),
        "patrimonio": float(patrimonio),
        "activos_detalle": activos_detalle,
        "pasivos_detalle": pasivos_detalle,
        "patrimonio_detalle": patrimonio_detalle,
        "utilidad_neta": float(utilidad_neta),
        "patrimonio_total": float(patrimonio_total),
        "cuadre": cuadre,
        "mensaje_cuadre": mensaje,
        "notas_normativas": notas_normativas,
    }
