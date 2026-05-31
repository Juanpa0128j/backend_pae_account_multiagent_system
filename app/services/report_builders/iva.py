"""IVA report builder."""

from decimal import Decimal

from ._base import (
    PREFIX_IVA,
    _fetch_rag_referencias,
    _now_iso,
    _parse_date_param,
    _today_iso,
)

_PREFIX_IVA = PREFIX_IVA


def build_iva(db, params: dict, svc) -> dict:
    start_date = _parse_date_param(params.get("start_date"))
    end_date = _parse_date_param(params.get("end_date"), end_of_day=True)
    company_nit = params.get("company_nit")
    ledger = svc.get_general_ledger(
        db, start_date=start_date, end_date=end_date, company_nit=company_nit
    )

    iva_generado = Decimal("0")
    iva_descontable = Decimal("0")
    for row in ledger:
        code = str(row.get("account") or "").strip()
        if not code.startswith(_PREFIX_IVA):
            continue
        debit = Decimal(str(row.get("total_debit") or 0))
        credit = Decimal(str(row.get("total_credit") or 0))
        if code.startswith("240805"):
            iva_generado += credit
        elif code.startswith("240802") or code.startswith("240810"):
            iva_descontable += debit
        elif code == _PREFIX_IVA:
            saldo_neto = debit - credit
            if saldo_neto > 0:
                iva_descontable += saldo_neto
            else:
                iva_generado += abs(saldo_neto)
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
