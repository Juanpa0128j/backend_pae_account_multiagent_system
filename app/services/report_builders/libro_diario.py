"""Libro Diario report builder."""

from app.services.report_builders._base import (
    _fetch_rag_referencias,
    _now_iso,
    _parse_date_param,
    _today_iso,
)


def build_libro_diario(db, params: dict, svc) -> dict:
    """Libro Diario: chronological journal of all transactions."""
    start_date = _parse_date_param(params.get("start_date"))
    end_date = _parse_date_param(params.get("end_date"), end_of_day=True)
    company_nit = params.get("company_nit")

    # get_daily_journal: company_nit optional, returns ORM objects
    rows = svc.get_daily_journal(
        db, start_date=start_date, end_date=end_date, company_nit=company_nit
    )
    lines = [
        {
            "fecha": r.fecha.isoformat() if r.fecha else None,
            "comprobante": r.comprobante or "",
            "cuenta_puc": r.cuenta_puc or "",
            "cuenta_nombre": r.cuenta_nombre or "",
            "tercero_nit": r.tercero_nit or "",
            "descripcion": r.descripcion or "",
            "debito": float(r.debito or 0),
            "credito": float(r.credito or 0),
        }
        for r in rows
    ]

    notas_normativas = _fetch_rag_referencias(
        "Libro Diario registro transacciones PUC débito crédito comprobante",
        n_results=2,
    )

    return {
        "report_type": "libro_diario",
        "period_start": params.get("start_date"),
        "period_end": params.get("end_date") or _today_iso(),
        "company_nit": company_nit,
        "generated_at": _now_iso(),
        "transacciones": lines,
        "total_transacciones": len(lines),
        "notas_normativas": notas_normativas,
    }
