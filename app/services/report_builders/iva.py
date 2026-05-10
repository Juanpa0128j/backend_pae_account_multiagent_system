"""IVA report builder."""

from decimal import Decimal

from app.services.report_builders._base import (
    _CUENTA_IVA_DESCONTABLE,
    _CUENTA_IVA_GENERADO,
    _credit_nature_balance,
    _debit_nature_balance,
    _fetch_rag_referencias,
    _ledger_by_exact,
    _now_iso,
    _parse_date_param,
    _today_iso,
)


def build_iva(db, params: dict, svc) -> dict:
    start_date = _parse_date_param(params.get("start_date"))
    end_date = _parse_date_param(params.get("end_date"), end_of_day=True)
    company_nit = params.get("company_nit")
    ledger = svc.get_general_ledger(
        db, start_date=start_date, end_date=end_date, company_nit=company_nit
    )

    generado_row = _ledger_by_exact(ledger, _CUENTA_IVA_GENERADO)
    descontable_row = _ledger_by_exact(ledger, _CUENTA_IVA_DESCONTABLE)

    iva_generado = (
        _credit_nature_balance(generado_row) if generado_row else Decimal("0")
    )
    iva_descontable = (
        _debit_nature_balance(descontable_row) if descontable_row else Decimal("0")
    )
    iva_a_pagar = iva_generado - iva_descontable
    iva_status = (
        "saldo_a_pagar"
        if iva_a_pagar > 0
        else "saldo_a_favor"
        if iva_a_pagar < 0
        else "saldo_cero"
    )

    rag_refs = _fetch_rag_referencias(
        "IVA impuesto ventas tarifa general artículo 468 477 Estatuto Tributario"
    )
    referencias = rag_refs if rag_refs else ["Art. 477 ET", "Art. 24 ET"]

    return {
        "report_type": "iva_report",
        "period_start": params.get("start_date"),
        "period_end": params.get("end_date") or _today_iso(),
        "company_nit": company_nit,
        "generated_at": _now_iso(),
        "iva_generado": float(iva_generado),
        "iva_descontable": float(iva_descontable),
        "iva_a_pagar": float(iva_a_pagar),
        "iva_status": iva_status,
        "referencias": referencias,
    }
