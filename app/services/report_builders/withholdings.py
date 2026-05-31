"""Withholdings report builder."""

from decimal import Decimal

from ._base import (
    CUENTA_RETEICA,
    CUENTA_RETEFUENTE,
    _credit_nature_balance,
    _fetch_rag_referencias,
    _ledger_by_exact,
    _now_iso,
    _parse_date_param,
    _today_iso,
)

_CUENTA_RETEFUENTE = CUENTA_RETEFUENTE
_CUENTA_RETEICA = CUENTA_RETEICA


def build_withholdings(db, params: dict, svc) -> dict:
    start_date = _parse_date_param(params.get("start_date"))
    end_date = _parse_date_param(params.get("end_date"), end_of_day=True)
    company_nit = params.get("company_nit")
    ledger = svc.get_general_ledger(
        db, start_date=start_date, end_date=end_date, company_nit=company_nit
    )

    retefuente_row = _ledger_by_exact(ledger, _CUENTA_RETEFUENTE)
    reteica_row = _ledger_by_exact(ledger, _CUENTA_RETEICA)

    retefuente = (
        _credit_nature_balance(retefuente_row) if retefuente_row else Decimal("0")
    )
    reteica = _credit_nature_balance(reteica_row) if reteica_row else Decimal("0")
    total = retefuente + reteica

    retefuente_status = (
        "saldo_a_pagar"
        if retefuente > 0
        else "saldo_a_favor" if retefuente < 0 else "saldo_cero"
    )
    reteica_status = (
        "saldo_a_pagar"
        if reteica > 0
        else "saldo_a_favor" if reteica < 0 else "saldo_cero"
    )
    total_status = (
        "saldo_a_pagar" if total > 0 else "saldo_a_favor" if total < 0 else "saldo_cero"
    )

    rag_refs = _fetch_rag_referencias(
        "retención en la fuente servicios honorarios artículo 383 392 Decreto 2048 Estatuto Tributario"
    )
    referencias = rag_refs if rag_refs else ["Art. 383 ET", "Decreto 2048/1992"]

    return {
        "report_type": "withholdings_report",
        "period_start": params.get("start_date"),
        "period_end": params.get("end_date") or _today_iso(),
        "company_nit": company_nit,
        "generated_at": _now_iso(),
        "retencion_en_la_fuente": float(retefuente),
        "retencion_en_la_fuente_status": retefuente_status,
        "retencion_ica": float(reteica),
        "retencion_ica_status": reteica_status,
        "total_retenciones": float(total),
        "total_retenciones_status": total_status,
        "referencias": referencias,
    }
