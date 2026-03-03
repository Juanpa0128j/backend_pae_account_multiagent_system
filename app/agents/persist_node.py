"""
DB Persist node for the LangGraph pipeline (pilot phase).

This is a transitional node that will be replaced by the auditor agent
once the full 5-node architecture (supervisor, ingest, contador,
tributario, auditor) is implemented.

It receives interpreted_data from the ingest agent and persists it
to PostgreSQL:  IngestJob → TransactionPending → TransactionPosted → JournalEntryLines.

It also runs duplicate detection and PUC validation before posting.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from app.agents.state import AgentState
from app.core.database import SessionLocal
from app.services import db_service
from app.models.database import (
    IngestStatus,
    TransactionStatus,
    ProcessStatus,
)
from app.services.rag_service import get_rag_service

logger = logging.getLogger(__name__)


def _safe_decimal(value) -> Optional[Decimal]:
    """Safely convert a value to Decimal."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _safe_datetime(value) -> Optional[datetime]:
    """Safely parse a datetime string."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(str(value), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def db_persist_node(state: AgentState) -> AgentState:
    """
    Persist interpreted data to PostgreSQL.

    Flow:
    1. Create/update IngestJob
    2. Loop through each transaction in raw_transactions
    3. Create TransactionPending from extracted data
    4. Run duplicate detection and PUC validation
    5. Create TransactionPosted with PUC classification
    6. Generate JournalEntryLines (partida doble)
    7. Mark IngestJob as completed
    """
    # Skip if upstream error
    if state.get("error"):
        logger.warning(f"db_persist: Skipping due to upstream error: {state['error']}")
        return state

    interpreted = state.get("interpreted_data", {})
    transactions = interpreted.get("transactions", [])
    if not transactions:
        logger.warning("db_persist: No transactions to persist")
        return state

    db = SessionLocal()
    try:
        # ── 1. Create or update IngestJob ──
        ingest_id = state.get("ingest_id")
        if ingest_id:
            ingest_job = db_service.get_ingest_job(db, ingest_id)
            if ingest_job:
                # Update with preview of first transaction
                db_service.update_ingest_job(
                    db, ingest_id, IngestStatus.PROCESSING,
                    raw_preview=_build_preview(transactions[0]) if transactions else {},
                )
        else:
            file_name = state.get("file_path", "unknown.pdf").split("/")[-1]
            ingest_job = db_service.create_ingest_job(
                db, file_name, state.get("file_path")
            )
            ingest_id = ingest_job.id
            state["ingest_id"] = ingest_id

        total_lines = 0
        total_duplicates = 0
        posted_ids = []

        # ── Loop through each transaction ──
        for idx, tx_data in enumerate(transactions):
            # ── 2. Create TransactionPending ──
            fecha = _safe_datetime(tx_data.get("fecha"))
            total = _safe_decimal(tx_data.get("total") or tx_data.get("valor_total"))
            nit_emisor = str(tx_data.get("nit_emisor", "") or "").strip()
            nit_receptor = str(tx_data.get("nit_receptor", "") or "").strip()
            descripcion = tx_data.get("concepto") or tx_data.get("descripcion", "")
            items = tx_data.get("items") or tx_data.get("detalle_items", [])

            txn_pending = db_service.create_transaction_pending(
                db,
                ingest_id=ingest_id,
                fecha=fecha,
                nit_emisor=nit_emisor or None,
                nit_receptor=nit_receptor or None,
                total=total,
                descripcion=descripcion,
                items=items if isinstance(items, list) else [],
                raw_data=tx_data,
            )
            logger.info(f"db_persist: Created TransactionPending {txn_pending.id}")

            # ── 3. Duplicate detection ──
            duplicates = []
            if nit_emisor and total and fecha:
                duplicates = db_service.check_duplicates(db, nit_emisor, total, fecha)
                # Exclude the one we just created
                duplicates = [d for d in duplicates if d.id != txn_pending.id]
                if duplicates:
                    total_duplicates += len(duplicates)
                    logger.warning(
                        f"db_persist: Found {len(duplicates)} potential duplicates for "
                        f"NIT {nit_emisor}, total={total}"
                    )

            # ── 4. Classify PUC and create TransactionPosted ──
            cuenta_puc = tx_data.get("cuenta_puc", "519595")  # Fallback to Gastos Diversos
            puc_descripcion = tx_data.get("cuenta_nombre", "")

            # Validate PUC exists
            puc_record = db_service.validate_puc_exists(db, cuenta_puc)
            if puc_record:
                puc_descripcion = puc_record.nombre
            else:
                logger.warning(f"db_persist: PUC code {cuenta_puc} not found")

            retefuente = _safe_decimal(tx_data.get("retefuente")) or Decimal("0")
            reteica = _safe_decimal(tx_data.get("reteica")) or Decimal("0")
            iva = _safe_decimal(tx_data.get("iva") or tx_data.get("iva_valor")) or Decimal("0")
            neto = _safe_decimal(tx_data.get("neto_a_pagar")) or (total or Decimal("0"))

            # Build journal entries JSON
            journal_json = _build_journal_entries(
                fecha=fecha or datetime.now(timezone.utc),
                cuenta_puc=cuenta_puc,
                puc_descripcion=puc_descripcion,
                total=total or Decimal("0"),
                iva=iva,
                retefuente=retefuente,
                reteica=reteica,
                nit=nit_emisor,
                descripcion=descripcion,
            )

            txn_posted = db_service.create_transaction_posted(
                db,
                transaction_pending_id=txn_pending.id,
                cuenta_puc=cuenta_puc,
                puc_descripcion=puc_descripcion,
                retefuente=retefuente,
                reteica=reteica,
                iva=iva,
                neto_a_pagar=neto,
                journal_entries_json=journal_json,
                tax_references=tx_data.get("referencias_legales", []),
                agent_reasoning=tx_data.get("agent_reasoning"),
            )
            logger.info(f"db_persist: Created TransactionPosted {txn_posted.id}")
            posted_ids.append(txn_posted.id)

            # ── 5. Create normalized JournalEntryLines ──
            lines = db_service.create_journal_entry_lines(
                db, txn_posted.id, journal_json
            )
            total_lines += len(lines)
            logger.info(f"db_persist: Created {len(lines)} journal entry lines")

            # ── 5.5. Auto-Vectorize into ChromaDB ──
            try:
                rag = get_rag_service()
                doc_text = state.get("raw_text", "")
                nit_para_rag = nit_emisor or "unknown_nit"
                
                if doc_text:
                    rag.add_empresa_doc(
                        nit=nit_para_rag,
                        text=doc_text,
                        metadata={
                            "fecha": str(fecha),
                            "total": str(total),
                            "descripcion": descripcion,
                            "ingest_id": ingest_id,
                            "transaction_id": txn_posted.id
                        }
                    )
                    logger.info(f"db_persist: Vectorized transaction to ChromaDB for NIT {nit_para_rag}")
            except Exception as e:
                # Do not hard fail the entire API if vectorization fails
                logger.error(f"db_persist: Failed to vectorize to ChromaDB: {e}", exc_info=True)

        # ── 6. Mark IngestJob as completed ──
        db_service.update_ingest_job(db, ingest_id, IngestStatus.COMPLETED)

        # ── 7. Enrich state result ──
        state["db_result"] = {
            "ingest_id": ingest_id,
            "processed_transactions": len(transactions),
            "journal_lines_count": total_lines,
            "duplicates_found": total_duplicates,
        }

        # Update the main result
        if state.get("result"):
            state["result"]["db_persisted"] = True
            state["result"]["ingest_id"] = ingest_id
            state["result"]["transaction_ids"] = posted_ids

        logger.info(f"db_persist: Successfully persisted {len(transactions)} txs for ingest {ingest_id}")

    except Exception as e:
        logger.error(f"db_persist: Error persisting data: {e}", exc_info=True)
        state["error"] = f"DB persist error: {str(e)}"
        if ingest_id:
            try:
                db_service.update_ingest_job(
                    db, ingest_id, IngestStatus.FAILED,
                    extraction_errors=[str(e)],
                )
            except Exception:
                pass
    finally:
        db.close()

    return state


def _build_preview(interpreted: dict) -> dict:
    """Build a quick preview dict from interpreted data."""
    return {
        "nit_emisor": interpreted.get("nit_emisor"),
        "total": str(interpreted.get("total", "")),
        "fecha": str(interpreted.get("fecha", "")),
        "concepto": str(interpreted.get("concepto") or "")[:100],
    }


def _build_journal_entries(
    fecha: datetime,
    cuenta_puc: str,
    puc_descripcion: str,
    total: Decimal,
    iva: Decimal,
    retefuente: Decimal,
    reteica: Decimal,
    nit: str,
    descripcion: str,
) -> list:
    """
    Build double-entry (partida doble) journal entries.

    For a typical purchase/expense:
    - DEBIT the expense account (PUC) for total
    - DEBIT IVA descontable (240802) if IVA > 0
    - CREDIT the vendor payable (220505) for base + IVA
    - CREDIT retefuente (240815) if retention > 0
    - CREDIT reteICA (236540) if reteica > 0

    Returns a list of dicts with JSON-serialisable values (fecha as ISO string)
    ready to be stored in the ``journal_entries_json`` JSONB column.
    """
    entries = []
    base = total - iva  # Base gravable (before IVA)

    # Serialize fecha to ISO string for JSONB storage
    fecha_iso = fecha.isoformat() if isinstance(fecha, datetime) else str(fecha)

    # Debit: Expense/Cost account
    if base > 0:
        entries.append({
            "fecha": fecha_iso,
            "cuenta": cuenta_puc,
            "descripcion": puc_descripcion or descripcion,
            "tercero_nit": nit,
            "detalle": descripcion,
            "debito": str(base),
            "credito": "0",
        })

    # Debit: IVA descontable
    if iva > 0:
        entries.append({
            "fecha": fecha_iso,
            "cuenta": "240802",
            "descripcion": "IVA Descontable",
            "tercero_nit": nit,
            "detalle": f"IVA por {descripcion}",
            "debito": str(iva),
            "credito": "0",
        })

    # Credit: Proveedor / Cuentas por pagar
    total_credito_proveedor = total - retefuente - reteica
    if total_credito_proveedor > 0:
        entries.append({
            "fecha": fecha_iso,
            "cuenta": "220505",
            "descripcion": "Proveedores Nacionales",
            "tercero_nit": nit,
            "detalle": f"CxP {descripcion}",
            "debito": "0",
            "credito": str(total_credito_proveedor),
        })

    # Credit: Retención en la fuente
    if retefuente > 0:
        entries.append({
            "fecha": fecha_iso,
            "cuenta": "240815",
            "descripcion": "Retención en la Fuente - Servicios",
            "tercero_nit": nit,
            "detalle": f"Retefuente {descripcion}",
            "debito": "0",
            "credito": str(retefuente),
        })

    # Credit: ReteICA
    if reteica > 0:
        entries.append({
            "fecha": fecha_iso,
            "cuenta": "236540",
            "descripcion": "ReteICA por pagar",
            "tercero_nit": nit,
            "detalle": f"ReteICA {descripcion}",
            "debito": "0",
            "credito": str(reteica),
        })

    return entries
