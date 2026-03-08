"""
DB Persist node for the LangGraph pipeline.

Persists ingest/process outputs to PostgreSQL:
IngestJob -> TransactionPending -> TransactionPosted -> JournalEntryLines.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from sqlalchemy.exc import OperationalError as SAOperationalError

from app.agents.agent_utils import append_log
from app.agents.state import AgentState
from app.core.database import SessionLocal
from app.core.logger import get_logger
from app.models.database import IngestStatus, ProcessStatus, TransactionPending
from app.services import db_service

logger = get_logger("app.agents.persist")

MAX_NODE_RETRIES = 3


def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _safe_decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _safe_datetime(value: Any) -> Optional[datetime]:
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
    """Persist current state output to DB for ingest/process mode."""
    if state.get("error"):
        logger.warning(f"db_persist: Skipping due to upstream error: {state['error']}")
        return state

    append_log(state, "db_persist", "node_start", {"mode": state.get("mode", "ingest")})

    for _attempt in range(1, MAX_NODE_RETRIES + 1):
        try:
            _db_persist_inner(state)
            if state.get("error"):
                append_log(state, "db_persist", "node_error", {"error": state["error"]})
                return state
            append_log(state, "db_persist", "node_complete", {
                "ingest_id": state.get("ingest_id"),
            })
            return state
        except SAOperationalError as e:
            logger.warning(
                f"db_persist: transient DB error attempt {_attempt}/{MAX_NODE_RETRIES}: {e}"
            )
            if _attempt == MAX_NODE_RETRIES:
                state["error"] = f"DB persist failed after {MAX_NODE_RETRIES} attempts: {e}"
                append_log(state, "db_persist", "node_error", {"error": str(e)})
                return state
        except Exception:
            # Non-transient — fall through to original error handling below
            break

    return _db_persist_inner_with_cleanup(state)


def _db_persist_inner(state: AgentState) -> None:
    """Run the core DB persistence; raises on any error (called inside retry loop)."""
    _run_persist(state)


def _db_persist_inner_with_cleanup(state: AgentState) -> AgentState:
    """Run persistence with full error cleanup; used when retry loop is exhausted/skipped."""
    _run_persist(state)
    return state


def _run_persist(state: AgentState) -> None:
    """Core persistence logic. Raises on failure; called by the retry wrappers."""
    mode = state.get("mode", "ingest")
    interpreted = state.get("interpreted_data", {}) or {}

    if mode == "process":
        contador_output = state.get("contador_output") or interpreted
        asientos = contador_output.get("asientos", []) if isinstance(contador_output, dict) else []
        if not asientos:
            msg = "db_persist: No contador asientos to persist"
            logger.error(msg)
            state["error"] = msg
            raise RuntimeError(msg)

        raw_txs = state.get("raw_transactions") or []
        base_tx = raw_txs[0] if raw_txs and isinstance(raw_txs[0], dict) else {}

        total = base_tx.get("total")
        if total is None:
            total = contador_output.get("total_debitos") or contador_output.get("total_creditos") or 0

        fecha = base_tx.get("fecha") or contador_output.get("fecha_registro")
        nit_emisor = base_tx.get("nit_emisor", "")
        nit_receptor = base_tx.get("nit_receptor", "")
        descripcion = base_tx.get("descripcion") or contador_output.get("descripcion_general", "")
        items = base_tx.get("items", [])

        debit_line = next(
            (a for a in asientos if str(a.get("tipo_movimiento", "")).lower() == "debito"),
            None,
        )

        tx_data = {
            "fecha": fecha,
            "nit_emisor": nit_emisor,
            "nit_receptor": nit_receptor,
            "total": total,
            "concepto": descripcion,
            "descripcion": descripcion,
            "items": items,
            "cuenta_puc": (debit_line or {}).get("cuenta_puc", ""),
            "cuenta_nombre": (debit_line or {}).get("nombre_cuenta", ""),
            "_contador_asientos": asientos,
        }
        transactions = [tx_data]
    else:
        transactions = interpreted.get("transactions", []) if isinstance(interpreted, dict) else []
        if not transactions:
            logger.warning("db_persist: No transactions to persist")
            return state

    db = SessionLocal()
    ingest_id = _as_str(state.get("ingest_id"), "")

    try:
        if ingest_id:
            ingest_job = db_service.get_ingest_job(db, ingest_id)
            if ingest_job:
                db_service.update_ingest_job(
                    db,
                    ingest_id,
                    IngestStatus.PROCESSING,
                    raw_preview=_build_preview(transactions[0]),
                )
        else:
            file_name = state.get("file_path", "unknown.pdf").split("/")[-1]
            ingest_job = db_service.create_ingest_job(db, file_name, state.get("file_path"))
            ingest_id = _as_str(getattr(ingest_job, "id", ""), "")
            state["ingest_id"] = ingest_id

        total_lines = 0
        total_duplicates = 0
        posted_ids: list[str] = []
        pending_ids: list[str] = []

        if mode == "process":
            process_id = _as_str(state.get("process_id"), "")
            if process_id:
                db_service.update_process_job(
                    db,
                    process_id,
                    status=ProcessStatus.RUNNING,
                    current_stage="persisting",
                    current_agent="db_persist",
                    progress=85,
                    agent_log_entry={"agent": "db_persist", "stage": "persisting", "status": "running"},
                )

        for tx_data in transactions:
            fecha = _safe_datetime(tx_data.get("fecha")) or datetime.now(timezone.utc)
            total = _safe_decimal(tx_data.get("total") or tx_data.get("valor_total")) or Decimal("0")
            nit_emisor = _as_str(tx_data.get("nit_emisor"), "").strip()
            nit_receptor = _as_str(tx_data.get("nit_receptor"), "").strip()
            descripcion = _as_str(tx_data.get("concepto") or tx_data.get("descripcion"), "")
            items = tx_data.get("items") or tx_data.get("detalle_items") or []

            if mode == "process" and state.get("pending_transaction_id"):
                pending_id = _as_str(state.get("pending_transaction_id"), "")
                txn_pending = db.query(TransactionPending).filter(TransactionPending.id == pending_id).first()
                if not txn_pending:
                    state["error"] = "DB persist error: pending transaction not found for process mode"
                    return state
            else:
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

            pending_ids.append(_as_str(getattr(txn_pending, "id", ""), ""))

            duplicates = []
            if nit_emisor and total and fecha:
                duplicates = db_service.check_duplicates(db, nit_emisor, total, fecha)
                txn_pending_id = _as_str(getattr(txn_pending, "id", ""), "")
                duplicates = [d for d in duplicates if _as_str(getattr(d, "id", ""), "") != txn_pending_id]
                if duplicates:
                    total_duplicates += len(duplicates)
                    logger.warning(
                        f"db_persist: Found {len(duplicates)} potential duplicates for "
                        f"NIT {nit_emisor}, total={total}"
                    )

            if mode == "process":
                asientos = tx_data.get("_contador_asientos", [])
                debit_line = next(
                    (a for a in asientos if str(a.get("tipo_movimiento", "")).lower() == "debito"),
                    None,
                )
                cuenta_puc = _as_str((debit_line or {}).get("cuenta_puc"), "")
                puc_descripcion = _as_str((debit_line or {}).get("nombre_cuenta"), "")
                if not cuenta_puc:
                    state["error"] = "DB persist error: contador output missing debit cuenta_puc"
                    return state
            else:
                cuenta_puc = _as_str(tx_data.get("cuenta_puc"), "") or "519595"
                puc_descripcion = _as_str(tx_data.get("cuenta_nombre"), "")

            puc_record = db_service.validate_puc_exists(db, cuenta_puc)
            if puc_record:
                puc_descripcion = _as_str(getattr(puc_record, "nombre", ""), "")
            elif mode == "process":
                state["error"] = f"DB persist error: PUC code {cuenta_puc} not found"
                return state
            else:
                logger.warning(f"db_persist: PUC code {cuenta_puc} not found")

            retefuente = _safe_decimal(tx_data.get("retefuente")) or Decimal("0")
            reteica = _safe_decimal(tx_data.get("reteica")) or Decimal("0")
            iva = _safe_decimal(tx_data.get("iva") or tx_data.get("iva_valor")) or Decimal("0")
            neto = _safe_decimal(tx_data.get("neto_a_pagar")) or total

            if mode == "process":
                journal_json = _journal_entries_from_contador(
                    fecha=fecha,
                    asientos=tx_data.get("_contador_asientos", []),
                    nit=nit_emisor,
                    descripcion=descripcion,
                )
                neto = total
            else:
                journal_json = _build_journal_entries(
                    fecha=fecha,
                    cuenta_puc=cuenta_puc,
                    puc_descripcion=puc_descripcion,
                    total=total,
                    iva=iva,
                    retefuente=retefuente,
                    reteica=reteica,
                    nit=nit_emisor,
                    descripcion=descripcion,
                )

            txn_posted = db_service.create_transaction_posted(
                db,
                transaction_pending_id=_as_str(getattr(txn_pending, "id", ""), ""),
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
            posted_ids.append(_as_str(getattr(txn_posted, "id", ""), ""))

            lines = db_service.create_journal_entry_lines(
                db,
                _as_str(getattr(txn_posted, "id", ""), ""),
                journal_json,
            )
            total_lines += len(lines)

        if mode == "ingest":
            db_service.update_ingest_job(db, ingest_id, IngestStatus.COMPLETED)
        else:
            process_id = _as_str(state.get("process_id"), "")
            if process_id:
                db_service.update_process_job(
                    db,
                    process_id,
                    status=ProcessStatus.COMPLETED,
                    current_stage="completed",
                    current_agent="db_persist",
                    progress=100,
                    agent_log_entry={"agent": "db_persist", "stage": "completed", "status": "completed"},
                )

        state["db_result"] = {
            "ingest_id": ingest_id,
            "processed_transactions": len(transactions),
            "journal_lines_count": total_lines,
            "duplicates_found": total_duplicates,
            "transaction_pending_id": pending_ids[0] if pending_ids else "",
            "transaction_posted_id": posted_ids[0] if posted_ids else "",
        }

        if state.get("result") is not None:
            state["result"]["db_persisted"] = True
            state["result"]["ingest_id"] = ingest_id
            state["result"]["transaction_ids"] = posted_ids
            if posted_ids:
                state["result"]["transaction_id"] = posted_ids[0]

        logger.info(f"db_persist: Successfully persisted {len(transactions)} txs for ingest {ingest_id}")

    except SAOperationalError:
        # Re-raise so the retry loop in db_persist_node can catch and retry
        raise
    except Exception as e:
        logger.error(f"db_persist: Error persisting data: {e}", exc_info=True)
        state["error"] = f"DB persist error: {str(e)}"
        append_log(state, "db_persist", "node_error", {"error": str(e)})

        if mode == "ingest" and ingest_id:
            try:
                db_service.update_ingest_job(
                    db,
                    ingest_id,
                    IngestStatus.FAILED,
                    extraction_errors=[str(e)],
                )
            except Exception:
                pass

        if mode == "process":
            process_id = _as_str(state.get("process_id"), "")
            if process_id:
                try:
                    db_service.update_process_job(
                        db,
                        process_id,
                        status=ProcessStatus.FAILED,
                        current_stage="failed",
                        current_agent="db_persist",
                        error_message=str(e),
                        progress=100,
                        agent_log_entry={"agent": "db_persist", "stage": "failed", "status": "failed"},
                    )
                except Exception:
                    pass
    finally:
        db.close()

    return state


def _journal_entries_from_contador(*, fecha: datetime, asientos: list, nit: str, descripcion: str) -> list:
    fecha_iso = fecha.isoformat() if isinstance(fecha, datetime) else str(fecha)
    entries = []
    for asiento in asientos:
        tipo = str(asiento.get("tipo_movimiento", "")).lower()
        valor = _safe_decimal(asiento.get("valor")) or Decimal("0")
        entries.append(
            {
                "fecha": fecha_iso,
                "cuenta": str(asiento.get("cuenta_puc", "")),
                "descripcion": asiento.get("nombre_cuenta") or descripcion,
                "tercero_nit": nit,
                "detalle": asiento.get("descripcion") or descripcion,
                "debito": str(valor if tipo == "debito" else Decimal("0")),
                "credito": str(valor if tipo == "credito" else Decimal("0")),
            }
        )
    return entries


def _build_preview(interpreted: dict) -> dict:
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
    entries = []
    base = total - iva
    fecha_iso = fecha.isoformat() if isinstance(fecha, datetime) else str(fecha)

    if base > 0:
        entries.append(
            {
                "fecha": fecha_iso,
                "cuenta": cuenta_puc,
                "descripcion": puc_descripcion or descripcion,
                "tercero_nit": nit,
                "detalle": descripcion,
                "debito": str(base),
                "credito": "0",
            }
        )

    if iva > 0:
        entries.append(
            {
                "fecha": fecha_iso,
                "cuenta": "240802",
                "descripcion": "IVA Descontable",
                "tercero_nit": nit,
                "detalle": f"IVA por {descripcion}",
                "debito": str(iva),
                "credito": "0",
            }
        )

    total_credito_proveedor = total - retefuente - reteica
    if total_credito_proveedor > 0:
        entries.append(
            {
                "fecha": fecha_iso,
                "cuenta": "220505",
                "descripcion": "Proveedores Nacionales",
                "tercero_nit": nit,
                "detalle": f"CxP {descripcion}",
                "debito": "0",
                "credito": str(total_credito_proveedor),
            }
        )

    if retefuente > 0:
        entries.append(
            {
                "fecha": fecha_iso,
                "cuenta": "240815",
                "descripcion": "Retencion en la Fuente - Servicios",
                "tercero_nit": nit,
                "detalle": f"Retefuente {descripcion}",
                "debito": "0",
                "credito": str(retefuente),
            }
        )

    if reteica > 0:
        entries.append(
            {
                "fecha": fecha_iso,
                "cuenta": "236540",
                "descripcion": "ReteICA por pagar",
                "tercero_nit": nit,
                "detalle": f"ReteICA {descripcion}",
                "debito": "0",
                "credito": str(reteica),
            }
        )

    return entries
