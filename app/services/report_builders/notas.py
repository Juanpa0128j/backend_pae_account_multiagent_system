"""Notas a los Estados Financieros report builder."""

from ._base import (
    _fetch_rag_referencias,
    _now_iso,
    _parse_date_param,
    _today_iso,
)


def build_notas_eeff(db, params: dict, svc) -> dict:
    """Notas a los Estados Financieros: explanatory notes."""
    end_date = _parse_date_param(params.get("end_date"), end_of_day=True)
    company_nit = params.get("company_nit")

    balance_data = svc.get_balance_sheet(
        db, cutoff_date=end_date, company_nit=company_nit
    )

    notas_normativas = _fetch_rag_referencias(
        "Notas Estados Financieros NIIF PUC políticas contables estimaciones",
        n_results=5,
    )

    notas_contenido = [
        {
            "numero": i + 1,
            "titulo": f"Norma Contable {i + 1}",
            "contenido": nota,
        }
        for i, nota in enumerate(notas_normativas[:5])
    ]

    return {
        "report_type": "notas_eeff",
        "period_end": params.get("end_date") or _today_iso(),
        "company_nit": company_nit,
        "generated_at": _now_iso(),
        "notas": notas_contenido,
        "total_notas": len(notas_contenido),
        "resumen_financiero": {
            "activos": balance_data.get("assets", 0),
            "pasivos": balance_data.get("liabilities", 0),
            "patrimonio": balance_data.get("equity", 0),
        },
    }
