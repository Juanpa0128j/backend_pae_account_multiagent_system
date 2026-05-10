from fastapi import APIRouter, HTTPException, Query, Depends
from typing import Optional, Any
from datetime import datetime
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser, get_current_user
from app.core.database import get_db
from app.models.database import FinancialStatement
from app.services import db_service
from app.services.nit_utils import normalize_nit
from app.services.parse_utils import safe_float

router = APIRouter()


def _parse_date(date_str: Optional[str]) -> Optional[datetime]:
    """Parse a date string to datetime."""
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str)
    except ValueError:
        return None


def _via_b_libro_auxiliar(db: Session, company_nit: str) -> list[dict]:
    """Return libro_auxiliar lines as book rows for Vía B-locked companies."""
    stmt = (
        db.query(FinancialStatement)
        .filter(
            FinancialStatement.entity_nit == company_nit,
            FinancialStatement.statement_type == "libro_auxiliar",
        )
        .order_by(FinancialStatement.period_end.desc())
        .first()
    )
    if stmt is None or not isinstance(stmt.data, dict):
        return []
    lines = stmt.data.get("lines") or stmt.data.get("accounts") or []
    if not isinstance(lines, list):
        return []
    out = []
    for line in lines:
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


def _via_b_balance(db: Session, company_nit: str) -> list[dict]:
    """Return balance_general accounts as book rows for Vía B.

    Reads the most recent uploaded balance_general FinancialStatement and
    flattens its `accounts` array into the same row shape BookTable expects
    for tipo=balance (cuenta + nombre + saldo).
    """
    stmt = (
        db.query(FinancialStatement)
        .filter(
            FinancialStatement.entity_nit == company_nit,
            FinancialStatement.statement_type == "balance_general",
        )
        .order_by(FinancialStatement.period_end.desc())
        .first()
    )
    if stmt is None or not isinstance(stmt.data, dict):
        return []
    accounts = stmt.data.get("accounts") or []
    if not isinstance(accounts, list):
        return []
    period_end_str = stmt.period_end.date().isoformat() if stmt.period_end else ""
    out = []
    for acc in accounts:
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


def _via_a_balance_as_rows(balance: dict) -> list[dict]:
    """Convert get_balance_sheet's summary dict into book rows for tipo=balance.

    BookTable expects a list of rows. The legacy endpoint returned a single
    summary dict ({assets, liabilities, ...}) which made the UI render empty.
    """
    if not isinstance(balance, dict):
        return []
    rows = [
        ("1", "Activos", balance.get("assets")),
        ("2", "Pasivos", balance.get("liabilities")),
        ("3", "Patrimonio", balance.get("equity")),
        ("4", "Ingresos", balance.get("revenue")),
        ("5", "Gastos", balance.get("expenses")),
        ("6", "Costo de ventas", balance.get("cost_of_sales")),
        ("3", "Utilidad neta", balance.get("net_profit")),
        ("3", "Patrimonio total", balance.get("total_equity")),
    ]
    return [
        {
            "fecha": "",
            "cuenta": codigo,
            "descripcion": nombre,
            "debito": 0.0,
            "credito": 0.0,
            "saldo": float(saldo or 0),
        }
        for codigo, nombre, saldo in rows
    ]


@router.get("/")
async def get_books(
    tipo: str = Query(..., description="diario, mayor, auxiliar, or balance"),
    fecha_inicio: Optional[str] = None,
    fecha_fin: Optional[str] = None,
    cuenta_puc: Optional[str] = None,
    tercero_nit: Optional[str] = None,
    company_nit: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> Any:
    """
    Queries the accounting books (Diario, Mayor, Auxiliar, Balance General).
    Data is read from PostgreSQL journal_entry_lines table for Vía A.
    For Vía B-locked companies, auxiliar and balance read from FinancialStatement;
    diario and mayor return an empty result with a `not_available_for_via_b` flag.
    """
    fi = _parse_date(fecha_inicio)
    ff = _parse_date(fecha_fin)
    normalized_company_nit = None
    if company_nit:
        try:
            normalized_company_nit = normalize_nit(company_nit)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"Invalid company_nit: {e}")

    valid_tipos = {"diario", "mayor", "auxiliar", "balance"}
    if tipo not in valid_tipos:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Tipo inválido '{tipo}'. Valores válidos: "
                f"{', '.join(sorted(valid_tipos))}"
            ),
        )

    # Vía B branch: source data from FinancialStatement table for the relevant tipos.
    if normalized_company_nit:
        try:
            locked = db_service.get_company_locked_pathway(db, normalized_company_nit)
        except Exception:
            locked = None
        if locked == "work_with_existing":
            if tipo == "auxiliar":
                return _via_b_libro_auxiliar(db, normalized_company_nit)
            if tipo == "balance":
                return _via_b_balance(db, normalized_company_nit)
            # diario / mayor — only meaningful for Vía A.
            return {
                "available": False,
                "reason": "via_b",
                "message": (
                    f"El libro {tipo} solo está disponible para empresas en Vía A "
                    "(documentos fuente). Esta empresa está usando Vía B."
                ),
            }

    if tipo == "diario":
        lines = db_service.get_daily_journal(db, fi, ff, normalized_company_nit)
        return [
            {
                "fecha": str(line.fecha) if line.fecha else "",
                "comprobante": line.comprobante or "",
                "cuenta": line.cuenta_puc,
                "descripcion": line.descripcion or line.cuenta_nombre or "",
                "debito": float(line.debito),
                "credito": float(line.credito),
            }
            for line in lines
        ]

    elif tipo == "mayor":
        return db_service.get_general_ledger(db, fi, ff, normalized_company_nit)

    elif tipo == "auxiliar":
        if not cuenta_puc:
            return {"error": "cuenta_puc is required for auxiliar"}
        lines = db_service.get_subsidiary_journal(
            db, cuenta_puc, fi, ff, normalized_company_nit
        )
        return [
            {
                "fecha": str(line.fecha) if line.fecha else "",
                "comprobante": line.comprobante or "",
                "tercero_nit": line.tercero_nit or "",
                "descripcion": line.descripcion or "",
                "debito": float(line.debito),
                "credito": float(line.credito),
            }
            for line in lines
        ]

    elif tipo == "balance":
        # Return rows so BookTable (which expects list[BookEntry]) can render.
        # Vía A summary dict gets flattened into class-level rows.
        balance = db_service.get_balance_sheet(db, ff, normalized_company_nit)
        return _via_a_balance_as_rows(balance)
