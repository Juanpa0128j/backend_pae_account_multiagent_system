from fastapi import APIRouter, HTTPException, Query, Depends, Request
from typing import Optional, Any
from datetime import datetime
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser, get_current_user
from app.core.database import get_db
from app.core.limiter import limiter
from app.services import db_service, via_b_service
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


def _via_a_balance_per_account(
    db: Session,
    end_date,
    company_nit,
) -> list[dict]:
    """Return per-cuenta balance rows for tipo=balance Vía A.

    Uses the general ledger aggregation (one row per cuenta_puc) and keeps
    only classes 1/2/3 — assets, liabilities, equity — which is what a
    Balance General actually contains. Class 4/5/6 (income statement) is
    omitted to avoid duplicating Estado de Resultados data here.
    """
    rows: list[dict] = []
    try:
        ledger = db_service.get_general_ledger(db, None, end_date, company_nit)
    except Exception:
        return rows
    for r in ledger or []:
        code = str(r.get("account") or r.get("cuenta_puc") or "")
        if not code or code[0] not in ("1", "2", "3"):
            continue
        saldo_debit = safe_float(r.get("total_debit"))
        saldo_credit = safe_float(r.get("total_credit"))
        # Natural-side balance: assets are debit-natured, liab/equity credit-natured.
        if code.startswith("1"):
            saldo = saldo_debit - saldo_credit
        else:
            saldo = saldo_credit - saldo_debit
        rows.append(
            {
                "fecha": "",
                "cuenta": code,
                "descripcion": str(r.get("name") or r.get("cuenta_nombre") or ""),
                "debito": saldo_debit,
                "credito": saldo_credit,
                "saldo": saldo,
            }
        )
    rows.sort(key=lambda x: x["cuenta"])
    return rows


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
@limiter.limit("60/minute")
async def get_books(
    request: Request,
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
                return via_b_service.get_libro_auxiliar(db, normalized_company_nit)
            if tipo == "balance":
                return via_b_service.get_balance_rows(db, normalized_company_nit)
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
        # When the caller passes a specific cuenta_puc the response is the
        # classic auxiliary listing (movements of a single account ordered by
        # date). When they omit it we surface ALL journal lines so the
        # /books/auxiliar page is never empty even before the user picks a
        # code from the filter dropdown.
        if cuenta_puc:
            lines = db_service.get_subsidiary_journal(
                db, cuenta_puc, fi, ff, normalized_company_nit
            )
            return [
                {
                    "fecha": str(line.fecha) if line.fecha else "",
                    "comprobante": line.comprobante or "",
                    "cuenta": line.cuenta_puc,
                    "tercero_nit": line.tercero_nit or "",
                    "descripcion": line.descripcion or "",
                    "debito": float(line.debito),
                    "credito": float(line.credito),
                }
                for line in lines
            ]
        lines = db_service.get_daily_journal(db, fi, ff, normalized_company_nit)
        return [
            {
                "fecha": str(line.fecha) if line.fecha else "",
                "comprobante": line.comprobante or "",
                "cuenta": line.cuenta_puc,
                "tercero_nit": getattr(line, "tercero_nit", "") or "",
                "descripcion": line.descripcion or line.cuenta_nombre or "",
                "debito": float(line.debito or 0),
                "credito": float(line.credito or 0),
            }
            for line in lines
        ]

    elif tipo == "balance":
        # Return per-cuenta detail rows (Activos/Pasivos/Patrimonio) plus the
        # class-level summary at the bottom — preserves the legacy summary
        # the UI was already rendering while giving Vía A its account-level
        # detail back.
        per_account = _via_a_balance_per_account(db, ff, normalized_company_nit)
        balance = db_service.get_balance_sheet(db, ff, normalized_company_nit)
        summary = _via_a_balance_as_rows(balance)
        return per_account + summary
