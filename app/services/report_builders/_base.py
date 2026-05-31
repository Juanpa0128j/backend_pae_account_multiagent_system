"""Shared helpers for report builder functions."""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

logger = logging.getLogger(__name__)


def _fetch_rag_referencias(query: str, n_results: int = 3) -> list[str]:
    """Query the normativa RAG collection and return human-readable citation strings."""
    try:
        from app.services.rag_service import get_rag_service  # noqa: PLC0415

        rag_svc = get_rag_service()
        results = rag_svc.search_normativo(query, n_results=n_results)
        citations: list[str] = []
        for r in results:
            articulo = r.metadata.get("articulo")
            fuente = r.metadata.get("fuente", "")
            if articulo:
                citations.append(f"{articulo} ({fuente})" if fuente else articulo)
            else:
                citations.append(r.content[:80])
        logger.info(
            "reportero: RAG returned %d citations for query '%s'",
            len(citations),
            query[:50],
        )
        return citations
    except Exception as rag_err:  # noqa: BLE001
        logger.warning("reportero: RAG lookup failed (non-fatal): %s", rag_err)
        return []


def _fetch_rag_context_text(query: str, n_results: int = 5) -> str:
    """Return RAG results as a single text block for LLM context."""
    try:
        from app.services.rag_service import get_rag_service  # noqa: PLC0415

        rag_svc = get_rag_service()
        results = rag_svc.search_normativo(query, n_results=n_results)
        parts = []
        for r in results:
            articulo = r.metadata.get("articulo", "")
            fuente = r.metadata.get("fuente", "")
            header = f"[{articulo} - {fuente}]" if articulo else ""
            parts.append(f"{header}\n{r.content[:500]}")
        return "\n\n".join(parts)
    except Exception:  # noqa: BLE001
        return ""


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_date_param(
    value: Optional[str], end_of_day: bool = False
) -> Optional[datetime]:
    """Convert an ISO date string to UTC datetime."""
    if not value:
        return None
    try:
        dt = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if end_of_day:
            return dt.replace(hour=23, minute=59, second=59, microsecond=999999)
        return dt
    except ValueError:
        logger.warning("reportero: invalid date param '%s' — ignoring", value)
        return None


def _ledger_by_prefix(ledger: list[dict], prefix: str) -> list[dict]:
    """Filter general ledger rows whose account code starts with *prefix*."""
    return [row for row in ledger if row["account"].startswith(prefix)]


def _ledger_by_prefixes(ledger: list[dict], prefixes: tuple) -> list[dict]:
    """Filter ledger rows starting with any of the given prefixes."""
    return [
        row for row in ledger if any(row["account"].startswith(p) for p in prefixes)
    ]


def _ledger_by_exact(ledger: list[dict], code: str) -> Optional[dict]:
    """Return the single ledger row for *code*, or None if absent."""
    for row in ledger:
        if row["account"] == code:
            return row
    return None


def _credit_nature_balance(row: dict) -> Decimal:
    """Net balance for credit-nature accounts: credits - debits."""
    return Decimal(str(row["total_credit"])) - Decimal(str(row["total_debit"]))


def _debit_nature_balance(row: dict) -> Decimal:
    """Net balance for debit-nature accounts: debits - credits."""
    return Decimal(str(row["total_debit"])) - Decimal(str(row["total_credit"]))


def _safe_divide(numerator: float, denominator: float) -> Optional[float]:
    """Return numerator / denominator, or None if denominator is zero."""
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


# ---------------------------------------------------------------------------
# Shared PUC class/prefix constants used across multiple builders
# ---------------------------------------------------------------------------

CLASS_ACTIVOS = "1"
CLASS_PASIVOS = "2"
CLASS_PATRIMONIO = "3"
CLASS_INGRESOS = "4"
CLASS_GASTOS = "5"
CLASS_COSTO_VENTAS = "6"
PREFIX_EFECTIVO = "11"
PREFIX_IVA = "2408"
PREFIX_ACTIVOS_CORRIENTES = ("11", "12", "13")
PREFIX_PASIVOS_CORRIENTES = ("21", "22", "23")
PREFIX_INVENTARIOS = "14"
CUENTA_RETEFUENTE = "2365"
CUENTA_RETEICA = "2368"
