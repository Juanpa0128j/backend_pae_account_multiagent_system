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

from datetime import date
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
    utilidad_neta = safe_float(data.get("utilidad_neta"))
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

    Returns the same keys the Vía A flow computes from journal entries
    (``total_activos``, ``total_pasivos``, …) plus Vía B metadata
    (``statements_count``, ``latest_period``, ``derivation_ready``).
    """
    rows = (
        db.query(FinancialStatement)
        .filter(FinancialStatement.entity_nit == company_nit)
        .order_by(FinancialStatement.period_end.desc())
        .all()
    )
    bg = next((r for r in rows if r.statement_type == "balance_general"), None)
    er = next((r for r in rows if r.statement_type == "estado_resultados"), None)
    la = next((r for r in rows if r.statement_type == "libro_auxiliar"), None)

    bg_data = bg.data if bg and isinstance(bg.data, dict) else {}
    er_data = er.data if er and isinstance(er.data, dict) else {}

    total_activos = safe_float(bg_data.get("total_activos"))
    total_pasivos = safe_float(bg_data.get("total_pasivos"))
    utilidad_neta = safe_float(er_data.get("utilidad_neta"))

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
    required_types = ("balance_general", "estado_resultados", "libro_auxiliar")
    period_ends_by_type: Dict[str, set] = {t: set() for t in required_types}
    for r in direct:
        if r.statement_type in period_ends_by_type and r.period_end is not None:
            period_ends_by_type[r.statement_type].add(r.period_end)
    common_period_ends = (
        set.intersection(*period_ends_by_type.values())
        if all(period_ends_by_type[t] for t in required_types)
        else set()
    )
    latest = max((r.period_end for r in direct if r.period_end), default=None)

    return {
        "total_activos": total_activos,
        "total_pasivos": total_pasivos,
        "utilidad_neta": utilidad_neta,
        "efectivo": efectivo,
        "statements_count": len(direct),
        "latest_period": latest.isoformat() if latest else None,
        "derivation_ready": bool(common_period_ends),
    }


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
