"""Thin adapter that coordinates DB persistence for the accounting pipeline.

Receives a SQLAlchemy Session and persists journal entries + derived financial
statements. All business logic lives in JournalBuilder and StatementDeriver;
this module only handles the DB mapping.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models.canonical_schemas import (
    AccountLine,
    BalanceGeneralCanonical,
    EstadoResultadosCanonical,
)
from app.models.database import FinancialStatement, JournalEntryLine


class PersistOrchestrator:
    """Coordinates persistence of journal entries and financial statements."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _persist_journal_entry_lines(
        self,
        entries: List[Dict[str, Any]],
        *,
        transaction_posted_id: str,
        company_nit: str,
    ) -> List[JournalEntryLine]:
        """Create JournalEntryLine ORM objects from plain dicts."""
        created: List[JournalEntryLine] = []
        for entry in entries:
            fecha_raw = entry.get("fecha")
            if isinstance(fecha_raw, str):
                fecha = datetime.fromisoformat(fecha_raw)
            else:
                fecha = fecha_raw or datetime.now()

            line = JournalEntryLine(
                transaction_posted_id=transaction_posted_id,
                company_nit=company_nit,
                fecha=fecha,
                cuenta_puc=entry.get("cuenta", ""),
                cuenta_nombre=entry.get("descripcion", ""),
                tercero_nit=entry.get("tercero_nit", ""),
                descripcion=entry.get("detalle", ""),
                debito=Decimal(entry.get("debito", "0") or "0"),
                credito=Decimal(entry.get("credito", "0") or "0"),
            )
            self.db.add(line)
            created.append(line)
        return created

    def _persist_financial_statement(
        self,
        *,
        ingest_id: str,
        statement_type: str,
        company_nit: str,
        period_start: datetime,
        period_end: datetime,
        data: Dict[str, Any],
        source_mode: str = "derived_from_journal",
    ) -> FinancialStatement:
        """Create a FinancialStatement record."""
        stmt = FinancialStatement(
            id=str(uuid4()),
            ingest_id=ingest_id,
            statement_type=statement_type,
            entity_nit=company_nit,
            period_start=period_start,
            period_end=period_end,
            source_mode=source_mode,
            data=data,
        )
        self.db.add(stmt)
        return stmt

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def persist_journal_entries(
        self,
        entries: List[Dict[str, Any]],
        *,
        transaction_posted_id: str,
        company_nit: str,
    ) -> List[JournalEntryLine]:
        """Persist a list of journal entries and return the created lines."""
        if not entries:
            return []
        lines = self._persist_journal_entry_lines(
            entries,
            transaction_posted_id=transaction_posted_id,
            company_nit=company_nit,
        )
        self.db.commit()
        return lines

    def derive_and_persist_statements(
        self,
        entries: List[Dict[str, Any]],
        *,
        ingest_id: str,
        company_nit: str,
        period_start: datetime,
        period_end: datetime,
    ) -> Dict[str, FinancialStatement]:
        """Derive financial statements from entries and persist them.

        Returns a dict mapping statement_type -> FinancialStatement.
        """
        from app.account_process.statement_deriver import StatementDeriver

        created: Dict[str, FinancialStatement] = {}

        bg_data = StatementDeriver.derive_balance_general(entries)
        bg_canonical = _build_balance_general_canonical(
            bg_data,
            company_nit=company_nit,
            period_start=period_start.date().isoformat(),
            period_end=period_end.date().isoformat(),
        )
        bg_stmt = self._persist_financial_statement(
            ingest_id=ingest_id,
            statement_type="balance_general",
            company_nit=company_nit,
            period_start=period_start,
            period_end=period_end,
            data=_decimal_dict(bg_canonical.model_dump()),
        )
        created["balance_general"] = bg_stmt

        er_data = StatementDeriver.derive_estado_resultados(entries)
        er_canonical = _build_estado_resultados_canonical(
            er_data,
            company_nit=company_nit,
            period_start=period_start.date().isoformat(),
            period_end=period_end.date().isoformat(),
        )
        er_stmt = self._persist_financial_statement(
            ingest_id=ingest_id,
            statement_type="estado_resultados",
            company_nit=company_nit,
            period_start=period_start,
            period_end=period_end,
            data=_decimal_dict(er_canonical.model_dump()),
        )
        created["estado_resultados"] = er_stmt

        self.db.commit()
        return created


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------


def _decimal_dict(obj: Any) -> Any:
    """Recursively convert Decimal values to float for JSONB storage."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _decimal_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decimal_dict(v) for v in obj]
    return obj


def _classify_account_tipo(codigo: str) -> str:
    if not codigo or not codigo[0].isdigit():
        return "activo"
    clase = int(codigo[0])
    mapping = {
        1: "activo",
        2: "pasivo",
        3: "patrimonio",
        4: "ingreso",
        5: "gasto",
        6: "costo",
    }
    return mapping.get(clase, "activo")


def _build_balance_general_canonical(
    bg_data: Dict[str, Any],
    *,
    company_nit: str,
    period_start: str,
    period_end: str,
) -> BalanceGeneralCanonical:
    activos: list[AccountLine] = []
    pasivos: list[AccountLine] = []
    patrimonio: list[AccountLine] = []

    for c in bg_data.get("cuentas", []):
        codigo = str(c.get("cuenta", ""))
        saldo = Decimal(str(c.get("saldo", "0") or "0"))
        tipo = _classify_account_tipo(codigo)
        line = AccountLine(codigo=codigo, nombre=codigo, saldo=saldo, tipo=tipo)
        if tipo == "activo":
            activos.append(line)
        elif tipo == "pasivo":
            pasivos.append(line)
        elif tipo == "patrimonio":
            patrimonio.append(line)

    return BalanceGeneralCanonical(
        period_start=period_start,
        period_end=period_end,
        company_nit=company_nit,
        activos=activos,
        pasivos=pasivos,
        patrimonio=patrimonio,
        utilidad_neta=Decimal(str(bg_data.get("utilidad_neta", "0") or "0")),
        patrimonio_total=Decimal(str(bg_data.get("total_patrimonio", "0") or "0")),
        cuadre=bool(bg_data.get("cuadre", False)),
    )


def _build_estado_resultados_canonical(
    er_data: Dict[str, Any],
    *,
    company_nit: str,
    period_start: str,
    period_end: str,
) -> EstadoResultadosCanonical:
    def _to_account_lines(items: list[Any], tipo: str) -> list[AccountLine]:
        out: list[AccountLine] = []
        for c in items:
            codigo = str(c.get("cuenta", ""))
            saldo = Decimal(str(c.get("saldo", "0") or "0"))
            out.append(
                AccountLine(codigo=codigo, nombre=codigo, saldo=saldo, tipo=tipo)
            )
        return out

    ingresos = _to_account_lines(er_data.get("ingresos", []), "ingreso")
    gastos = _to_account_lines(er_data.get("gastos", []), "gasto")
    costo_ventas = _to_account_lines(er_data.get("costo_ventas", []), "costo")

    total_ingresos = sum((i.saldo for i in ingresos), Decimal("0"))
    total_gastos = sum((g.saldo for g in gastos), Decimal("0"))
    total_costo_ventas = sum((c.saldo for c in costo_ventas), Decimal("0"))
    utilidad_bruta = Decimal(str(er_data.get("utilidad_bruta", "0") or "0"))
    utilidad_neta = Decimal(str(er_data.get("utilidad_neta", "0") or "0"))
    utilidad_operacional = utilidad_bruta - total_gastos

    return EstadoResultadosCanonical(
        period_start=period_start,
        period_end=period_end,
        company_nit=company_nit,
        ingresos=ingresos,
        costo_ventas=costo_ventas,
        gastos=gastos,
        total_ingresos=total_ingresos,
        total_costo_ventas=total_costo_ventas,
        total_gastos=total_gastos,
        utilidad_bruta=utilidad_bruta,
        utilidad_operacional=utilidad_operacional,
        utilidad_neta=utilidad_neta,
    )
