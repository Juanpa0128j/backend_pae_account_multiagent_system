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
        """Create JournalEntryLine ORM objects from plain dicts.

        Uses ``safe_datetime`` (multi-format) to accept ISO datetimes, plain
        dates, and monthly YYYY-MM strings. Raises ``ValueError`` when the
        ``fecha`` is missing or unparseable — the pre-persist auditor must
        catch that condition earlier and route to HITL, so reaching this
        path with a bad date means we have a bug to surface.
        """
        from app.services.document_mappers import safe_datetime

        created: List[JournalEntryLine] = []
        for entry in entries:
            fecha_raw = entry.get("fecha")
            fecha = safe_datetime(fecha_raw)
            if fecha is None:
                raise ValueError(
                    f"JournalEntry missing or unparseable fecha (got {fecha_raw!r})"
                )

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
        bg_stmt = self._persist_financial_statement(
            ingest_id=ingest_id,
            statement_type="balance_general",
            company_nit=company_nit,
            period_start=period_start,
            period_end=period_end,
            data=_decimal_dict(bg_data),
        )
        created["balance_general"] = bg_stmt

        er_data = StatementDeriver.derive_estado_resultados(entries)
        er_stmt = self._persist_financial_statement(
            ingest_id=ingest_id,
            statement_type="estado_resultados",
            company_nit=company_nit,
            period_start=period_start,
            period_end=period_end,
            data=_decimal_dict(er_data),
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
