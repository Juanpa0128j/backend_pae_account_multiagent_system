"""
Vía B (work_with_existing) data access layer.

Vía B companies upload pre-existing financial statements (balance general,
estado de resultados, libro auxiliar) instead of source documents. Their data
lives in ``financial_statements`` keyed by ``entity_nit`` — they never write to
``journal_entry_lines`` / ``transactions_posted``.

This module centralises every read against ``FinancialStatement`` so the API
endpoints, dashboard, and chat service all reach the same canonical shape and
no caller needs to duplicate JSONB unwrap logic.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.database import FinancialStatement
from app.services.parse_utils import safe_float

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _statements(
    db: Session, company_nit: str, statement_type: str
) -> List[FinancialStatement]:
    """All statements of a type for a company, newest period_end first."""
    return (
        db.query(FinancialStatement)
        .filter(
            FinancialStatement.entity_nit == company_nit,
            FinancialStatement.statement_type == statement_type,
        )
        .order_by(FinancialStatement.period_end.desc())
        .all()
    )


def _latest_statement(
    db: Session,
    company_nit: str,
    statement_type: str,
    period_end: Optional[date] = None,
) -> Optional[FinancialStatement]:
    """Pick one statement of ``statement_type``.

    With ``period_end=None`` returns the most recent one. With a target date,
    returns the statement whose ``period_end`` falls in the same year-month;
    if no upload matches that period, returns ``None`` (callers surface that as
    "ese período no está cargado" instead of silently using another period).
    """
    rows = _statements(db, company_nit, statement_type)
    if not rows:
        return None
    if period_end is None:
        return rows[0]
    for r in rows:
        if (
            r.period_end is not None
            and r.period_end.year == period_end.year
            and r.period_end.month == period_end.month
        ):
            return r
    return None


def list_periods(db: Session, company_nit: str, statement_type: str) -> List[str]:
    """Return the available period_end dates (ISO) for a statement type."""
    return [
        r.period_end.date().isoformat()
        for r in _statements(db, company_nit, statement_type)
        if r.period_end is not None
    ]


def _data(stmt: Optional[FinancialStatement]) -> Dict[str, Any]:
    if stmt is None or not isinstance(stmt.data, dict):
        return {}
    return stmt.data


def _accounts(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    accounts = data.get("accounts") or []
    return accounts if isinstance(accounts, list) else []


def _lines(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    lines = data.get("lines") or data.get("accounts") or []
    return lines if isinstance(lines, list) else []


def _drop_redundant_codes(
    candidates: List[tuple[str, float, str]],
) -> List[tuple[str, float, str]]:
    """Remove any candidate whose code is a strict prefix of another candidate.

    PUC charts often list both an intermediate parent (e.g. ``240801``) and
    its children (e.g. ``24080101``). Counting both double-charges the saldo;
    keeping only the most-specific code preserves the right total whatever
    depth the upload happens to use.
    """
    codes = [c[0] for c in candidates]
    return [
        (code, saldo, group)
        for code, saldo, group in candidates
        if not any(other != code and other.startswith(code) for other in codes)
    ]


_CORE_STATEMENT_TYPES: tuple[str, ...] = (
    "balance_general",
    "estado_resultados",
    "libro_auxiliar",
)


def latest_common_period(db: Session, company_nit: str) -> Optional[date]:
    """Return the latest period_end shared by balance + E.R. + libro auxiliar.

    Used to anchor dashboard / reports KPIs to a single coherent period so
    figures from different statement types don't get mixed (e.g. assets from
    January with utilidad from December). Returns ``None`` when at least one
    of the three core types has no upload that shares any period_end with the
    others.
    """
    period_sets: list[set[date]] = []
    for stype in _CORE_STATEMENT_TYPES:
        rows = _statements(db, company_nit, stype)
        period_sets.append(
            {r.period_end.date() for r in rows if r.period_end is not None}
        )
    if not all(period_sets):
        return None
    common = set.intersection(*period_sets)
    return max(common) if common else None


def resolve_utilidad_neta(balance_data: Dict[str, Any]) -> float:
    """Find the period's net result inside a balance_general JSONB.

    LLM-extracted balances put the utilidad in one of three places depending
    on the source PDF:

    1. As a top-level ``utilidad_neta`` field (the canonical shape).
    2. Nested under ``patrimonio.resultados_del_ejercicio`` (newer extractor).
    3. Only as a row in ``accounts`` with PUC ``3605*`` (some chart-heavy
       uploads).

    Probing in that order keeps backwards compatibility while making the
    common "result wasn't extracted to the top-level" case work without
    affecting balances that already populate the field.
    """
    direct = safe_float(balance_data.get("utilidad_neta"))
    if direct:
        return direct
    patrimonio_obj = balance_data.get("patrimonio")
    if isinstance(patrimonio_obj, dict):
        nested = safe_float(patrimonio_obj.get("resultados_del_ejercicio"))
        if nested:
            return nested
    # Walk the accounts list as a last resort. Same dedup rule as IVA so
    # parent (``3605``) + leaves (``360505``) don't double-count.
    candidates: List[tuple[str, float, str]] = []
    for acc in _accounts(balance_data):
        if not isinstance(acc, dict):
            continue
        code = str(acc.get("cuenta_puc") or "")
        if not code.startswith("3605"):
            continue
        candidates.append((code, safe_float(acc.get("saldo")), "u"))
    non_redundant = _drop_redundant_codes(candidates)
    if non_redundant:
        return sum(s for _, s, _ in non_redundant)
    return 0.0


# ---------------------------------------------------------------------------
# Books-shaped readers (consumed by /api/v1/books)
# ---------------------------------------------------------------------------


def get_libro_auxiliar(db: Session, company_nit: str) -> List[Dict[str, Any]]:
    """Return libro_auxiliar lines as BookTable rows for a Vía B company."""
    stmt = _latest_statement(db, company_nit, "libro_auxiliar")
    out: List[Dict[str, Any]] = []
    for line in _lines(_data(stmt)):
        if not isinstance(line, dict):
            continue
        out.append(
            {
                "fecha": str(line.get("fecha") or ""),
                "comprobante": str(line.get("comprobante") or ""),
                "cuenta": str(line.get("cuenta_puc") or ""),
                "tercero_nit": str(line.get("tercero_nit") or ""),
                "descripcion": str(
                    line.get("detalle") or line.get("cuenta_nombre") or ""
                ),
                "debito": safe_float(line.get("debito")),
                "credito": safe_float(line.get("credito")),
                "saldo": safe_float(line.get("saldo")),
            }
        )
    return out


def get_balance_rows(db: Session, company_nit: str) -> List[Dict[str, Any]]:
    """Return balance_general accounts as BookTable rows for a Vía B company."""
    stmt = _latest_statement(db, company_nit, "balance_general")
    if stmt is None:
        return []
    period_end_str = stmt.period_end.date().isoformat() if stmt.period_end else ""
    out: List[Dict[str, Any]] = []
    for acc in _accounts(_data(stmt)):
        if not isinstance(acc, dict):
            continue
        out.append(
            {
                "fecha": period_end_str,
                "cuenta": str(acc.get("cuenta_puc") or ""),
                "descripcion": str(acc.get("nombre") or ""),
                "debito": 0.0,
                "credito": 0.0,
                "saldo": safe_float(acc.get("saldo")),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Chat / dashboard readers (consumed by chat_service and /api/v1/dashboard)
# ---------------------------------------------------------------------------


def get_balance(
    db: Session, company_nit: str, period_end: Optional[date] = None
) -> Optional[Dict[str, Any]]:
    """Return a Vía B balance sheet card payload, or None if no statement.

    Shape mirrors the Vía A ``_build_balance`` keys the chat LLM is already
    primed for (``activos``, ``pasivos``, ``patrimonio``, ``utilidad_neta``,
    ``activos_detalle`` / ``pasivos_detalle`` / ``patrimonio_detalle``) so the
    same prompt works for both pathways without branching the response copy.

    ``period_end`` selects the snapshot for a specific month; ``None`` uses the
    most recent uploaded balance.
    """
    stmt = _latest_statement(db, company_nit, "balance_general", period_end)
    if stmt is None:
        return None
    data = _data(stmt)
    accounts = _accounts(data)

    activos_detalle: List[Dict[str, Any]] = []
    pasivos_detalle: List[Dict[str, Any]] = []
    patrimonio_detalle: List[Dict[str, Any]] = []
    for acc in accounts:
        if not isinstance(acc, dict):
            continue
        codigo = str(acc.get("cuenta_puc") or "")
        if not codigo:
            continue
        cuenta = {
            "codigo": codigo,
            "nombre": str(acc.get("nombre") or ""),
            "saldo": safe_float(acc.get("saldo")),
        }
        if codigo.startswith("1"):
            activos_detalle.append(cuenta)
        elif codigo.startswith("2"):
            pasivos_detalle.append(cuenta)
        elif codigo.startswith("3"):
            patrimonio_detalle.append(cuenta)

    activos = safe_float(data.get("total_activos"))
    pasivos = safe_float(data.get("total_pasivos"))
    patrimonio_total = safe_float(data.get("total_patrimonio"))
    utilidad_neta = resolve_utilidad_neta(data)
    patrimonio = patrimonio_total - utilidad_neta

    diferencia = activos - (pasivos + patrimonio_total)
    cuadre = abs(diferencia) < 1.0
    mensaje = (
        f"ACTIVOS ({activos:,.0f}) == PASIVOS ({pasivos:,.0f}) + PATRIMONIO ({patrimonio_total:,.0f}) ✓"
        if cuadre
        else f"DESCUADRE: ACTIVOS - (PASIVOS + PATRIMONIO) = {diferencia:,.0f}"
    )

    return {
        "report_type": "balance_sheet",
        "source": "via_b",
        "period_end": stmt.period_end.isoformat() if stmt.period_end else None,
        "company_nit": company_nit,
        "activos": activos,
        "pasivos": pasivos,
        "patrimonio": patrimonio,
        "patrimonio_total": patrimonio_total,
        "utilidad_neta": utilidad_neta,
        "activos_detalle": activos_detalle,
        "pasivos_detalle": pasivos_detalle,
        "patrimonio_detalle": patrimonio_detalle,
        "cuadre": cuadre,
        "mensaje_cuadre": mensaje,
    }


def get_pnl(
    db: Session, company_nit: str, period_end: Optional[date] = None
) -> Optional[Dict[str, Any]]:
    """Return a Vía B estado de resultados card payload, or None.

    ``period_end`` selects a specific month; ``None`` uses the most recent.
    """
    stmt = _latest_statement(db, company_nit, "estado_resultados", period_end)
    if stmt is None:
        return None
    data = _data(stmt)
    accounts = _accounts(data)

    ingresos: List[Dict[str, Any]] = []
    gastos: List[Dict[str, Any]] = []
    costo_ventas: List[Dict[str, Any]] = []
    for acc in accounts:
        if not isinstance(acc, dict):
            continue
        codigo = str(acc.get("cuenta_puc") or "")
        if not codigo:
            continue
        cuenta = {
            "codigo": codigo,
            "nombre": str(acc.get("nombre") or ""),
            "saldo": safe_float(acc.get("saldo")),
        }
        if codigo.startswith("4"):
            ingresos.append(cuenta)
        elif codigo.startswith("5"):
            gastos.append(cuenta)
        elif codigo.startswith("6"):
            costo_ventas.append(cuenta)

    total_ingresos = safe_float(data.get("total_ingresos")) or sum(
        c["saldo"] for c in ingresos
    )
    total_gastos = safe_float(data.get("total_gastos")) or sum(
        c["saldo"] for c in gastos
    )
    total_costo = safe_float(data.get("total_costo_ventas")) or sum(
        c["saldo"] for c in costo_ventas
    )
    utilidad_neta = safe_float(data.get("utilidad_neta")) or (
        total_ingresos - total_costo - total_gastos
    )

    return {
        "report_type": "profit_and_loss",
        "source": "via_b",
        "period_start": stmt.period_start.isoformat() if stmt.period_start else None,
        "period_end": stmt.period_end.isoformat() if stmt.period_end else None,
        "company_nit": company_nit,
        "ingresos": ingresos,
        "costo_ventas": costo_ventas,
        "gastos": gastos,
        "total_ingresos": total_ingresos,
        "total_costo_ventas": total_costo,
        "total_gastos": total_gastos,
        "utilidad_bruta": total_ingresos - total_costo,
        "utilidad_neta": utilidad_neta,
    }


def get_cashflow(
    db: Session, company_nit: str, period_end: Optional[date] = None
) -> Optional[Dict[str, Any]]:
    """Best-effort cash position derived from the libro_auxiliar.

    Returns None when there is no libro_auxiliar to read from — the caller
    should surface that as "no aplica" so the user understands Vía B has no
    direct flujo de caja unless they upload one. ``period_end`` selects a
    specific month; ``None`` uses the most recent.
    """
    stmt = _latest_statement(db, company_nit, "libro_auxiliar", period_end)
    if stmt is None:
        return None
    cuentas: List[Dict[str, Any]] = []
    for line in _lines(_data(stmt)):
        if not isinstance(line, dict):
            continue
        code = str(line.get("cuenta_puc") or line.get("codigo") or "")
        if not code.startswith("11"):
            continue
        cuentas.append(
            {
                "codigo": code,
                "nombre": str(line.get("cuenta_nombre") or line.get("detalle") or ""),
                "saldo": safe_float(line.get("debito"))
                - safe_float(line.get("credito")),
            }
        )
    total = sum(c["saldo"] for c in cuentas)
    return {
        "report_type": "cash_flow",
        "source": "via_b",
        "period_end": stmt.period_end.isoformat() if stmt.period_end else None,
        "company_nit": company_nit,
        "cuentas_efectivo": cuentas,
        "total_efectivo": total,
        "nota": (
            "Flujo derivado del libro auxiliar cargado — no se calcula por "
            "actividad operativa/inversión/financiación porque Vía B no tiene "
            "asientos individuales."
        ),
    }


def get_top_accounts(
    db: Session, company_nit: str, limit: int = 5
) -> Optional[Dict[str, Any]]:
    """Top movimientos derivados del libro auxiliar (debe/haber)."""
    stmt = _latest_statement(db, company_nit, "libro_auxiliar")
    if stmt is None:
        return None
    totals: Dict[str, Dict[str, Any]] = {}
    for line in _lines(_data(stmt)):
        if not isinstance(line, dict):
            continue
        code = str(line.get("cuenta_puc") or "")
        if not code:
            continue
        entry = totals.setdefault(
            code,
            {
                "account": code,
                "name": str(line.get("cuenta_nombre") or ""),
                "total_debit": 0.0,
                "total_credit": 0.0,
            },
        )
        entry["total_debit"] += safe_float(line.get("debito"))
        entry["total_credit"] += safe_float(line.get("credito"))
    rows = list(totals.values())
    top_debit = sorted(rows, key=lambda r: r["total_debit"], reverse=True)[:limit]
    top_credit = sorted(rows, key=lambda r: r["total_credit"], reverse=True)[:limit]
    return {"top_debit": top_debit, "top_credit": top_credit}


def get_dashboard_overrides(db: Session, company_nit: str) -> Dict[str, Any]:
    """Compute Vía B financial totals from FinancialStatement rows.

    Anchors every KPI to a single coherent period — the most recent date
    shared by balance_general + estado_resultados + libro_auxiliar — so
    figures don't mix periods. When no single shared period exists, falls
    back to the latest of each type independently and flags the response
    as ``period_resolution: "partial"`` so the frontend can warn the user.

    Returns the same keys the Vía A flow computes from journal entries
    (``total_activos``, ``total_pasivos``, …) plus Vía B metadata
    (``statements_count``, ``latest_period``, ``derivation_ready``,
    ``period_end``, ``period_resolution``).
    """
    rows = (
        db.query(FinancialStatement)
        .filter(FinancialStatement.entity_nit == company_nit)
        .order_by(FinancialStatement.period_end.desc())
        .all()
    )

    common_period = latest_common_period(db, company_nit)
    period_resolution = "common" if common_period is not None else "partial"

    def _pick(stype: str) -> Optional[FinancialStatement]:
        if common_period is not None:
            for r in rows:
                if (
                    r.statement_type == stype
                    and r.period_end is not None
                    and r.period_end.date() == common_period
                ):
                    return r
        return next((r for r in rows if r.statement_type == stype), None)

    bg = _pick("balance_general")
    er = _pick("estado_resultados")
    la = _pick("libro_auxiliar")

    bg_data = bg.data if bg and isinstance(bg.data, dict) else {}
    er_data = er.data if er and isinstance(er.data, dict) else {}

    total_activos = safe_float(bg_data.get("total_activos"))
    total_pasivos = safe_float(bg_data.get("total_pasivos"))
    # Prefer the E.R. utilidad (canonical source of profit). Fall back to the
    # balance — same multi-shape resolver as the chat/reports use — when the
    # E.R. for the chosen period is missing or its top-level field is empty.
    utilidad_neta = safe_float(er_data.get("utilidad_neta")) or resolve_utilidad_neta(
        bg_data
    )

    efectivo = 0.0
    if la and isinstance(la.data, dict):
        for line in _lines(la.data):
            if not isinstance(line, dict):
                continue
            code = str(line.get("cuenta_puc") or line.get("codigo") or "")
            if code.startswith("11"):
                efectivo += safe_float(line.get("debito")) - safe_float(
                    line.get("credito")
                )

    direct = [r for r in rows if r.source_mode == "direct"]
    latest = max((r.period_end for r in direct if r.period_end), default=None)
    period_end = (
        common_period.isoformat()
        if common_period
        else (latest.date().isoformat() if latest else None)
    )

    return {
        "total_activos": total_activos,
        "total_pasivos": total_pasivos,
        "utilidad_neta": utilidad_neta,
        "efectivo": efectivo,
        "statements_count": len(direct),
        "latest_period": latest.isoformat() if latest else None,
        "period_end": period_end,
        "period_resolution": period_resolution,
        "derivation_ready": common_period is not None,
    }


# ---------------------------------------------------------------------------
# Tributario readers (consumed by /api/v1/tax)
# ---------------------------------------------------------------------------


def _iva_a_pagar_status(iva_a_pagar: float) -> str:
    if iva_a_pagar > 0:
        return "saldo_a_pagar"
    if iva_a_pagar < 0:
        return "saldo_a_favor"
    return "saldo_cero"


def _sum_account_group(
    accounts: List[Dict[str, Any]],
    *,
    generado_prefixes: tuple[str, ...],
    descontable_prefixes: tuple[str, ...],
    parent_code: str,
) -> tuple[float, float]:
    """Sum IVA-style accounts split by generado vs descontable.

    Walks every account starting with the configured prefixes, drops those
    that are duplicated at a more specific level (see
    :func:`_drop_redundant_codes`), and falls back to ``parent_code``'s
    saldo when no group accounts are present at all.
    """
    candidates: List[tuple[str, float, str]] = []
    parent_saldo: Optional[float] = None
    for acc in accounts:
        if not isinstance(acc, dict):
            continue
        code = str(acc.get("cuenta_puc") or "")
        if not code:
            continue
        saldo = safe_float(acc.get("saldo"))
        if code == parent_code:
            parent_saldo = saldo
            continue
        if any(code.startswith(p) for p in generado_prefixes):
            candidates.append((code, saldo, "g"))
        elif any(code.startswith(p) for p in descontable_prefixes):
            candidates.append((code, saldo, "d"))

    non_redundant = _drop_redundant_codes(candidates)
    generado = sum(abs(s) for _, s, g in non_redundant if g == "g")
    descontable = sum(abs(s) for _, s, g in non_redundant if g == "d")

    if not non_redundant and parent_saldo is not None:
        if parent_saldo >= 0:
            generado = parent_saldo
        else:
            descontable = abs(parent_saldo)
    return generado, descontable


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_iva_report(
    db: Session, company_nit: str, period_end: Optional[date] = None
) -> Optional[Dict[str, Any]]:
    """Derive an ``IVAOutput``-shaped payload from a Vía B balance_general.

    IVA generated lives in subaccounts of 240801/240805 (credit-natured);
    IVA deductible lives in 240802/240810/240811 (debit-natured against the
    liability, often stored as negative). Falls back to the parent ``2408``
    when only the aggregate is present. Returns ``None`` when no balance
    is loaded so the endpoint can render an empty state.
    """
    stmt = _latest_statement(db, company_nit, "balance_general", period_end)
    if stmt is None:
        return None
    accounts = _accounts(_data(stmt))
    iva_generado, iva_descontable = _sum_account_group(
        accounts,
        generado_prefixes=("240801", "240805"),
        descontable_prefixes=("240802", "240810", "240811"),
        parent_code="2408",
    )
    iva_a_pagar = iva_generado - iva_descontable
    return {
        "report_type": "iva_report",
        "source": "via_b",
        "period_start": (
            stmt.period_start.date().isoformat() if stmt.period_start else None
        ),
        "period_end": (
            stmt.period_end.date().isoformat()
            if stmt.period_end
            else date.today().isoformat()
        ),
        "company_nit": company_nit,
        "generated_at": _now_iso(),
        "iva_generado": iva_generado,
        "iva_descontable": iva_descontable,
        "iva_a_pagar": iva_a_pagar,
        "iva_status": _iva_a_pagar_status(iva_a_pagar),
        "referencias": [
            "Art. 437 ET — Responsables del impuesto sobre las ventas",
            "Art. 484 ET — IVA descontable",
            "Cifras derivadas del balance general cargado (Vía B); no hay detalle por factura.",
        ],
    }


def get_withholdings_report(
    db: Session, company_nit: str, period_end: Optional[date] = None
) -> Optional[Dict[str, Any]]:
    """Derive a ``WithholdingsOutput``-shaped payload from balance saldos.

    Retefuente lives in 2365 (and subaccounts); ReteICA in 2368. Both are
    credit-natured liabilities — their saldo at the cutoff is the pending
    amount to remit. Returns ``None`` when no balance is loaded.
    """
    stmt = _latest_statement(db, company_nit, "balance_general", period_end)
    if stmt is None:
        return None
    accounts = _accounts(_data(stmt))

    def _saldo_for(prefix: str) -> float:
        """Sum non-redundant saldos under ``prefix``; fall back to the parent.

        Same de-duplication rule as IVA: drop any code that is a strict prefix
        of another collected code so we don't double-count parent+child.
        """
        parent_saldo: Optional[float] = None
        candidates: List[tuple[str, float, str]] = []
        for acc in accounts:
            if not isinstance(acc, dict):
                continue
            code = str(acc.get("cuenta_puc") or "")
            if not code.startswith(prefix):
                continue
            saldo = safe_float(acc.get("saldo"))
            if code == prefix:
                parent_saldo = saldo
            else:
                candidates.append((code, saldo, "x"))
        non_redundant = _drop_redundant_codes(candidates)
        if non_redundant:
            return sum(abs(s) for _, s, _ in non_redundant)
        return abs(parent_saldo) if parent_saldo is not None else 0.0

    retefuente = _saldo_for("2365")
    reteica = _saldo_for("2368")
    total = retefuente + reteica
    return {
        "report_type": "withholdings_report",
        "source": "via_b",
        "period_start": (
            stmt.period_start.date().isoformat() if stmt.period_start else None
        ),
        "period_end": (
            stmt.period_end.date().isoformat()
            if stmt.period_end
            else date.today().isoformat()
        ),
        "company_nit": company_nit,
        "generated_at": _now_iso(),
        "retencion_en_la_fuente": retefuente,
        "retencion_ica": reteica,
        "total_retenciones": total,
        "referencias": [
            "Art. 365 ET — Agentes de retención en la fuente",
            "Ley 14 de 1983 — ReteICA",
            "Cifras derivadas del balance general cargado (Vía B); no hay detalle por tercero.",
        ],
    }


def get_ica_report(
    db: Session,
    company_nit: str,
    period_end: Optional[date] = None,
    tasa_ica: Optional[Decimal] = None,
) -> Optional[Dict[str, Any]]:
    """Derive an ``ICADeclaracionOutput`` from the uploaded estado_resultados.

    ``ingresos_brutos`` is taken from the period's E.R. (``total_ingresos`` or
    the sum of class-4 saldos when the total isn't present). The configured
    ``tasa_ica`` is applied per :func:`_calc_ica`. Returns ``None`` when no
    E.R. is loaded.
    """
    from app.agents.tributario_agent import TASA_ICA_DEFAULT, _calc_ica

    stmt = _latest_statement(db, company_nit, "estado_resultados", period_end)
    if stmt is None:
        return None
    data = _data(stmt)
    accounts = _accounts(data)
    ingresos = safe_float(data.get("total_ingresos"))
    if ingresos == 0:
        # Fall back to class-4 saldos when the rolled-up total is missing.
        ingresos = sum(
            safe_float(a.get("saldo"))
            for a in accounts
            if isinstance(a, dict) and str(a.get("cuenta_puc") or "").startswith("4")
        )
    rate = tasa_ica if tasa_ica is not None else TASA_ICA_DEFAULT
    ica_a_pagar = _calc_ica(Decimal(str(ingresos)), rate)
    return {
        "report_type": "ica_declaracion",
        "source": "via_b",
        "period_start": (
            stmt.period_start.date().isoformat() if stmt.period_start else None
        ),
        "period_end": (
            stmt.period_end.date().isoformat()
            if stmt.period_end
            else date.today().isoformat()
        ),
        "generated_at": _now_iso(),
        "ingresos_brutos": float(ingresos),
        "tasa_ica": float(rate),
        "ica_a_pagar": float(ica_a_pagar),
        "cuenta_gasto_puc": "540101",
        "cuenta_pasivo_puc": "2368",
        "referencias": [
            "Ley 14 de 1983 — Impuesto de Industria y Comercio",
            "Decreto 1333 de 1986 — Código de Régimen Municipal",
            "Cifras derivadas del estado de resultados cargado (Vía B).",
        ],
    }


def get_renta_provision_report(
    db: Session,
    company_nit: str,
    period_end: Optional[date] = None,
    tasa_renta: Optional[Decimal] = None,
) -> Optional[Dict[str, Any]]:
    """Derive a ``RentaProvisionOutput`` from the uploaded estado_resultados.

    Uses ``utilidad_neta`` from the E.R. as ``utilidad_antes_impuestos`` (Vía B
    statements typically report the audited pre-tax line as ``utilidad_neta``
    of the operating cycle; provisions live elsewhere). Provision = utilidad ×
    tasa, clamped at zero on losses. Returns ``None`` when no E.R. is loaded.
    """
    from app.agents.tributario_agent import TASA_RENTA

    stmt = _latest_statement(db, company_nit, "estado_resultados", period_end)
    if stmt is None:
        return None
    data = _data(stmt)
    utilidad = safe_float(data.get("utilidad_neta"))
    if utilidad == 0:
        # Try utilidad_bruta - gastos as a fallback if the LLM didn't extract
        # the net line cleanly.
        bruta = safe_float(data.get("utilidad_bruta"))
        gastos = safe_float(data.get("total_gastos"))
        utilidad = bruta - gastos if (bruta or gastos) else 0.0
    rate = tasa_renta if tasa_renta is not None else TASA_RENTA
    provision = max(Decimal(str(utilidad)) * rate, Decimal("0"))
    return {
        "report_type": "renta_provision",
        "source": "via_b",
        "period_start": (
            stmt.period_start.date().isoformat() if stmt.period_start else None
        ),
        "period_end": (
            stmt.period_end.date().isoformat()
            if stmt.period_end
            else date.today().isoformat()
        ),
        "generated_at": _now_iso(),
        "utilidad_antes_impuestos": float(utilidad),
        "tasa_renta": float(rate),
        "provision_renta": float(provision),
        "cuenta_gasto_puc": "540502",
        "cuenta_pasivo_puc": "240405",
        "referencias": [
            "Art. 240 ET — Tarifa general sociedades nacionales",
            "Ley 2277 de 2022 — Reforma tributaria",
            "Cifras derivadas del estado de resultados cargado (Vía B); "
            "verificar si utilidad_neta está antes o después de impuestos.",
        ],
    }


# ---------------------------------------------------------------------------
# Monthly trend (consumed by /api/v1/dashboard/monthly-trend)
# ---------------------------------------------------------------------------


def get_monthly_trend(
    db: Session, company_nit: str, months: int = 6
) -> Optional[Dict[str, Any]]:
    """Return monthly ingresos vs gastos for a Vía B company.

    Aggregates over uploaded ``estado_resultados`` rows (one point per period
    ending in the requested window). Returns ``None`` when the company has
    only one P&L upload (or none) — the dashboard surfaces that as
    ``available: false`` because a "trend" needs at least two points.
    """
    rows = (
        db.query(FinancialStatement)
        .filter(
            FinancialStatement.entity_nit == company_nit,
            FinancialStatement.statement_type == "estado_resultados",
            FinancialStatement.source_mode == "direct",
        )
        .order_by(FinancialStatement.period_end.desc())
        .limit(months)
        .all()
    )
    if len(rows) < 2:
        return None
    rows_chrono = list(reversed(rows))
    points: List[Dict[str, Any]] = []
    for r in rows_chrono:
        data = r.data if isinstance(r.data, dict) else {}
        period = r.period_end
        ym = period.strftime("%Y-%m") if period else ""
        points.append(
            {
                "month": ym,
                "ingresos": safe_float(data.get("total_ingresos")),
                "gastos": safe_float(data.get("total_gastos")),
            }
        )
    return {"data": points}
