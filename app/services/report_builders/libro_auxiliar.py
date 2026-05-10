"""Libro Auxiliar report builder."""

from app.services.report_builders._base import (
    _fetch_rag_referencias,
    _now_iso,
    _parse_date_param,
    _today_iso,
)


def build_libro_auxiliar(db, params: dict, svc) -> dict:
    """Libro Auxiliar: ledger per account (detail by account)."""
    start_date = _parse_date_param(params.get("start_date"))
    end_date = _parse_date_param(params.get("end_date"), end_of_day=True)
    company_nit = params.get("company_nit")

    # get_daily_journal: company_nit optional, returns ORM objects
    rows = svc.get_daily_journal(
        db, start_date=start_date, end_date=end_date, company_nit=company_nit
    )

    # Group by account code
    cuentas_detalle: dict = {}
    for r in rows:
        cuenta = r.cuenta_puc or "SIN_CUENTA"
        nombre = r.cuenta_nombre or r.descripcion or ""
        if cuenta not in cuentas_detalle:
            cuentas_detalle[cuenta] = {
                "cuenta": cuenta,
                "nombre": nombre,
                "movimientos": [],
                "total_debito": 0.0,
                "total_credito": 0.0,
                "saldo": 0.0,
            }
        elif not cuentas_detalle[cuenta]["nombre"] and nombre:
            cuentas_detalle[cuenta]["nombre"] = nombre

        debito = float(r.debito or 0)
        credito = float(r.credito or 0)
        cuentas_detalle[cuenta]["movimientos"].append(
            {
                "fecha": r.fecha.isoformat() if r.fecha else None,
                "comprobante": r.comprobante or "",
                "descripcion": r.descripcion or "",
                "debito": debito,
                "credito": credito,
            }
        )
        cuentas_detalle[cuenta]["total_debito"] += debito
        cuentas_detalle[cuenta]["total_credito"] += credito
        cuentas_detalle[cuenta]["saldo"] = (
            cuentas_detalle[cuenta]["total_debito"]
            - cuentas_detalle[cuenta]["total_credito"]
        )

    notas_normativas = _fetch_rag_referencias(
        "Libro Auxiliar saldo cuenta detalle transacciones por cuenta",
        n_results=2,
    )

    return {
        "report_type": "libro_auxiliar",
        "period_start": params.get("start_date"),
        "period_end": params.get("end_date") or _today_iso(),
        "company_nit": company_nit,
        "generated_at": _now_iso(),
        "cuentas": list(cuentas_detalle.values()),
        "total_cuentas": len(cuentas_detalle),
        "notas_normativas": notas_normativas,
    }
