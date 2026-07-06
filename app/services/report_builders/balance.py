"""Balance sheet report builder."""

import logging
from decimal import Decimal

from ._base import (
    CLASS_ACTIVOS,
    CLASS_PASIVOS,
    CLASS_PATRIMONIO,
    _credit_nature_balance,
    _debit_nature_balance,
    _fetch_rag_referencias,
    _ledger_by_prefix,
    _now_iso,
    _parse_date_param,
    _today_iso,
)

logger = logging.getLogger(__name__)

_CLASS_ACTIVOS = CLASS_ACTIVOS
_CLASS_PASIVOS = CLASS_PASIVOS
_CLASS_PATRIMONIO = CLASS_PATRIMONIO


def build_balance(db, params: dict, svc) -> dict:
    end_date = _parse_date_param(params.get("end_date"), end_of_day=True)
    company_nit = params.get("company_nit")
    data = svc.get_balance_sheet(db, cutoff_date=end_date, company_nit=company_nit)
    # Balance sheet totals are cumulative up to `end_date`, so detalle must use
    # the same cutoff-based basis to remain reconcilable. Intentionally ignore
    # any provided `start_date` for this report.
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

    # Reclasificación contable: cualquier cuenta clase 2 (pasivos) con saldo
    # DEUDOR indica un anticipo / saldo a favor / IVA recuperable contra el
    # acreedor — debe presentarse como activo, no como pasivo negativo.
    # Ejemplos comunes: 2408* IVA descontable (saldo a favor DIAN), 237005
    # Aportes EPS pagados en exceso al fondo, 238030 anticipo pensión.
    # Riesgo: si la cuenta tiene saldo deudor por ERROR contable (mal
    # asiento), la reclasificación lo enmascara. Mitigamos con warning.
    activos_detalle: list[dict] = []
    pasivos_detalle: list[dict] = []
    for r in _ledger_by_prefix(ledger, _CLASS_ACTIVOS):
        saldo_debito = _debit_nature_balance(r)  # debit - credit
        if saldo_debito < 0:
            # Mirror case: cuenta clase 1 con saldo ACREEDOR (p.ej. cuentas
            # por cobrar 130505 cuando un cobro se contabilizó antes que su
            # factura de origen) es un anticipo de cliente — pasivo, no un
            # activo negativo.
            logger.warning(
                "_build_balance: cuenta clase 1 con saldo acreedor reclasificada "
                "a pasivos (anticipo) — PUC=%s saldo=%s. Verifique si es "
                "anticipo de cliente (normal) o error contable.",
                r.get("account"),
                saldo_debito,
            )
            pasivos_detalle.append(_to_cuenta(r, abs(saldo_debito)))
        else:
            activos_detalle.append(_to_cuenta(r, saldo_debito))
    for r in _ledger_by_prefix(ledger, _CLASS_PASIVOS):
        saldo_credito = _credit_nature_balance(r)  # credit - debit
        if saldo_credito < 0:
            logger.warning(
                "_build_balance: cuenta clase 2 con saldo deudor reclasificada "
                "a activos — PUC=%s saldo=%s. Verifique si es anticipo/saldo "
                "a favor (normal) o error contable.",
                r.get("account"),
                saldo_credito,
            )
            activos_detalle.append(_to_cuenta(r, abs(saldo_credito)))
        else:
            pasivos_detalle.append(_to_cuenta(r, saldo_credito))
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
