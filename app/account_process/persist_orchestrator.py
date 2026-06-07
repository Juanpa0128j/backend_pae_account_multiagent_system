"""Thin adapter that coordinates DB persistence for the accounting pipeline.

Receives a SQLAlchemy Session and persists journal entries. All business logic
lives in JournalBuilder; this module only handles the DB mapping.

Financial-statement derivation from journal entries lives in
``app.services.financial_statement_service.build_first_level_from_journal_entries``
(the single journal→statements path) and is triggered manually via the Vía A
derivation endpoints.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.models.database import JournalEntryLine


class PersistOrchestrator:
    """Coordinates persistence of journal entries."""

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
