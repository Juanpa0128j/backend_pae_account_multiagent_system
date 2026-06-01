"""
Async processing jobs service.

Handles background execution of the accounting graph and updates ProcessJob
status/progress in the database.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from app.agents.graph import invoke_accounting_pipeline
from app.core.database import SessionLocal, PROCESS_PIPELINE_SEMAPHORE
from app.models.database import ProcessStatus, TransactionStatus
from app.services import db_service

logger = logging.getLogger(__name__)

MAX_PROCESS_SECONDS = 300  # 5 minutes

# Dedicated thread pool for CPU-intensive graph operations.
# This prevents blocking the default event loop thread pool,
# ensuring health checks and other requests remain responsive.
# 20 workers allows ~20 concurrent pipeline invocations.
_GRAPH_EXECUTOR = ThreadPoolExecutor(max_workers=20, thread_name_prefix="graph_worker")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _translate_pipeline_error(raw: str) -> str:
    """Map known internal error strings to Spanish user-facing messages.

    Keep raw English text only as ``technical`` metadata in the log entry.
    """
    if not raw:
        return "El proceso finalizó con error."
    low = raw.lower()
    if "db persist error" in low or "puc code" in low and "not found" in low:
        return (
            "No fue posible registrar los asientos contables porque algún código "
            "PUC no existe en la base de datos. Revise el documento e intente nuevamente."
        )
    if "no contador asientos to persist" in low or "no asientos" in low:
        return (
            "El sistema no generó asientos contables a partir del documento. "
            "Verifique que el archivo contenga información contable válida."
        )
    if "schema validation" in low:
        return (
            "El agente no pudo generar una respuesta válida tras varios intentos. "
            "Revise el documento fuente y vuelva a intentarlo."
        )
    if "puc validation" in low or "missing codes" in low:
        return (
            "Algunos códigos PUC del documento no existen en la base de datos. "
            "Corrija el documento o use 'Continuar de todas formas' para registrarlos como Otros gastos."
        )
    return "Error en la ejecución del proceso contable. Revise el documento e intente nuevamente."


_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _on_task_done(task: asyncio.Task) -> None:
    _BACKGROUND_TASKS.discard(task)
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        logger.error(
            "process job background task raised an unhandled exception",
            exc_info=(type(exc), exc, exc.__traceback__),
        )


async def start_process_job(process_id: str, force_persist: bool = False) -> None:
    """Schedule a process job in the current event loop.

    The task reference is retained in a module-level set so it cannot be GC'd
    before completion, and unhandled exceptions are logged.
    """
    task = asyncio.create_task(run_process_job(process_id, force_persist=force_persist))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_on_task_done)


async def run_process_job(process_id: str, force_persist: bool = False) -> None:
    """Execute the accounting flow for a process job with timeout handling.

    Wraps the implementation in an outer timeout (semaphore wait + execution)
    and a top-level exception guard. If anything goes wrong before the inner
    impl can update the DB, mark the job FAILED here so the frontend doesn't
    poll RUNNING forever.
    """
    # Outer deadline = MAX_PROCESS_SECONDS plus a buffer for semaphore wait.
    OUTER_DEADLINE = MAX_PROCESS_SECONDS + 60
    loop = asyncio.get_event_loop()
    try:
        await asyncio.wait_for(
            loop.run_in_executor(None, PROCESS_PIPELINE_SEMAPHORE.acquire),
            timeout=OUTER_DEADLINE,
        )
    except asyncio.TimeoutError:
        logger.error(
            "process job %s timed out waiting for pipeline semaphore", process_id
        )
        _mark_job_failed_safe(
            process_id,
            "El sistema está saturado. Intente nuevamente en unos minutos.",
        )
        return

    try:
        await _run_process_job_impl(process_id, force_persist=force_persist)
    except Exception as e:
        logger.exception("process job %s failed with unhandled exception", process_id)
        _mark_job_failed_safe(
            process_id,
            "Error inesperado durante el procesamiento. Por favor reintente.",
            technical=str(e),
        )
    finally:
        PROCESS_PIPELINE_SEMAPHORE.release()


def _mark_job_failed_safe(
    process_id: str, user_message: str, technical: str = ""
) -> None:
    """Best-effort attempt to mark a process job FAILED. Never raises."""
    try:
        db = SessionLocal()
        try:
            db_service.update_process_job(
                db,
                process_id=process_id,
                status=ProcessStatus.FAILED,
                current_stage="failed",
                progress=100,
                error_message=user_message,
                agent_log_entry={
                    "timestamp": _utc_iso(),
                    "agent": "supervisor",
                    "stage": "failed",
                    "event": "failed",
                    "message": user_message,
                    "technical": technical,
                },
            )
        finally:
            db.close()
    except Exception:
        logger.exception(
            "_mark_job_failed_safe: failed to mark %s as FAILED", process_id
        )


def _mark_pending_failed_safe(pending_id: str) -> None:
    """Best-effort attempt to mark a pending transaction ERROR. Never raises."""
    if not pending_id:
        return
    try:
        db = SessionLocal()
        try:
            db_service.update_transaction_status(
                db, pending_id, TransactionStatus.ERROR
            )
        finally:
            db.close()
    except Exception:
        logger.exception(
            "_mark_pending_failed_safe: failed to mark %s as ERROR", pending_id
        )


def _mark_processing_transactions_failed_safe(ingest_id: str) -> None:
    """Mark all PROCESSING transactions for an ingest as ERROR. Never raises.

    Used when a multi-tx batch job fails mid-loop — ensures transactions that
    were moved to PROCESSING but never reached db_persist don't stay stuck.
    """
    if not ingest_id:
        return
    try:
        db = SessionLocal()
        try:
            txs = db_service.get_transactions_by_ingest(db, ingest_id)
            for tx in txs:
                if tx.status == TransactionStatus.PROCESSING:
                    db_service.update_transaction_status(
                        db, str(tx.id), TransactionStatus.ERROR, commit=False
                    )
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception(
            "_mark_processing_transactions_failed_safe: failed for ingest %s", ingest_id
        )


async def _run_process_job_impl(process_id: str, force_persist: bool = False) -> None:
    """Implementation of process job execution, protected by semaphore."""
    db = SessionLocal()
    pending_id = ""
    ingest_id = ""
    try:
        process_job = db_service.get_process_job(db, process_id)
        if not process_job:
            logger.error("Process job not found: %s", process_id)
            return

        ingest_id = str(process_job.ingest_id)
        ingest_job = db_service.get_ingest_job(db, process_job.ingest_id)
        if not ingest_job:
            db_service.update_process_job(
                db,
                process_id=process_id,
                status=ProcessStatus.FAILED,
                current_stage="init",
                progress=0,
                error_message="Ingest job not found",
                agent_log_entry={
                    "timestamp": _utc_iso(),
                    "agent": "supervisor",
                    "stage": "init",
                    "event": "failed",
                    "message": "Ingest job not found",
                },
            )
            return

        staged_all = db_service.get_transactions_by_ingest(db, process_job.ingest_id)
        # force_persist=True means the user confirmed a HITL review. Transactions
        # left in PROCESSING from the failed first run must be retried as well.
        if force_persist:
            staged_pending = [
                tx
                for tx in staged_all
                if tx.status
                in (TransactionStatus.PENDING, TransactionStatus.PROCESSING)
            ]
        else:
            staged_pending = [
                tx for tx in staged_all if tx.status == TransactionStatus.PENDING
            ]

        if not staged_pending:
            db_service.update_process_job(
                db,
                process_id=process_id,
                status=ProcessStatus.COMPLETED,
                current_stage="completed",
                current_agent="supervisor",
                progress=100,
                agent_log_entry={
                    "timestamp": _utc_iso(),
                    "agent": "supervisor",
                    "stage": "completed",
                    "event": "completed",
                    "message": "Todas las transacciones de este ingest ya fueron procesadas",
                },
            )
            return

        pending_id = str(staged_pending[0].id)

        # Mark staged transactions as PROCESSING so the UI can surface them
        for tx in staged_pending:
            db_service.update_transaction_status(
                db, str(tx.id), TransactionStatus.PROCESSING, commit=False
            )
        db.commit()
        # Fallback: documents like CEs/nóminas/extractos have no nit_receptor
        # in their content. Use the company_nit captured at upload time so
        # downstream agents (tributario) can resolve company tax settings.
        fallback_nit = getattr(ingest_job, "company_nit", None) or getattr(
            staged_pending[0], "company_nit", None
        )
        raw_transactions: list[dict] = []
        for tx in staged_pending:
            raw_transactions.append(
                {
                    "id": str(tx.id),
                    "fecha": tx.fecha.isoformat() if tx.fecha else None,
                    "nit_emisor": tx.nit_emisor,
                    "nit_receptor": tx.nit_receptor or fallback_nit,
                    "total": float(tx.total) if tx.total is not None else 0.0,
                    "descripcion": tx.descripcion,
                    "items": tx.items if isinstance(tx.items, list) else [],
                    "raw_data": tx.raw_data if isinstance(tx.raw_data, dict) else {},
                }
            )

        db_service.update_process_job(
            db,
            process_id=process_id,
            status=ProcessStatus.RUNNING,
            current_stage="supervisor",
            current_agent="supervisor",
            progress=10,
            agent_log_entry={
                "timestamp": _utc_iso(),
                "agent": "supervisor",
                "stage": "supervisor",
                "event": "started",
                "message": "Proceso asíncrono iniciado",
            },
        )

        db_service.update_process_job(
            db,
            process_id=process_id,
            current_stage="contador",
            current_agent="contador",
            progress=35,
            agent_log_entry={
                "timestamp": _utc_iso(),
                "agent": "contador",
                "stage": "contador",
                "event": "running",
                "message": "Ejecutando workflow de agentes",
            },
        )

        doc_type = ingest_job.document_type or ""
        source_document = (
            raw_transactions[0].get("raw_data", {}) if raw_transactions else {}
        )

        # Via B documents are already-processed financial statements.
        # They do not require journal entry generation — skip the accounting pipeline.
        _VIA_B_DOC_TYPES = {
            "balance_general",
            "estado_resultados",
            "libro_auxiliar",
            "flujo_de_caja",
            "cambios_patrimonio",
            "notas_estados_financieros",
            "libro_diario",
            # Non-accounting documents (regulatory decrees, legal texts)
            "otro",
        }
        if doc_type in _VIA_B_DOC_TYPES:
            logger.info(
                "Jobs: doc_type=%s is Via B — skipping accounting pipeline, marking COMPLETED",
                doc_type,
            )
            db_service.update_process_job(
                db,
                process_id=process_id,
                status=ProcessStatus.COMPLETED,
                current_stage="completed",
                current_agent="supervisor",
                progress=100,
                agent_log_entry={
                    "timestamp": _utc_iso(),
                    "agent": "supervisor",
                    "stage": "completed",
                    "event": "completed",
                    "message": f"Documento Via B ({doc_type}) no requiere procesamiento contable",
                },
            )
            return

        logger.info(f"Acquired process pipeline slot for: {process_id}")
        # Use dedicated thread pool to avoid blocking the default executor
        loop = asyncio.get_event_loop()
        _force_persist = force_persist
        result = await asyncio.wait_for(
            loop.run_in_executor(
                _GRAPH_EXECUTOR,
                lambda: invoke_accounting_pipeline(
                    ingest_id=process_job.ingest_id,
                    raw_transactions=raw_transactions,
                    pending_transaction_id=pending_id,
                    process_id=process_id,
                    doc_type=doc_type,
                    source_document=source_document,
                    force_persist=_force_persist,
                    company_nit=fallback_nit,
                ),
            ),
            timeout=MAX_PROCESS_SECONDS,
        )

        # Cooperative cancellation guard: the user may have hit the cancel
        # endpoint while this pipeline thread was running. Re-fetch the job
        # status and, if it was flipped to CANCELLED, do NOT overwrite it with
        # a terminal COMPLETED/FAILED/PENDING_AUDIT_REVIEW status.
        #
        # NOTE: journal-entry persistence happens INSIDE the pipeline thread
        # (app/agents/persist_node.db_persist_node, committed before this point),
        # so this guard can only prevent the status flip — it cannot roll back
        # rows already written. persist_node has its own status check that skips
        # the commit when the job is already CANCELLED to minimise that window.
        current = db_service.get_process_job(db, process_id)
        if current is not None and current.status == ProcessStatus.CANCELLED:
            logger.info(
                "Process %s was cancelled during execution; skipping result persist",
                process_id,
            )
            return

        result_status = result.get("status") or (result.get("result") or {}).get(
            "status"
        )
        if result_status == "pending_audit_review":
            db_service.update_process_job(
                db,
                process_id=process_id,
                status=ProcessStatus.PENDING_AUDIT_REVIEW,
                current_stage="pending_audit_review",
                current_agent="supervisor",
                progress=80,
                agent_log_entry={
                    "timestamp": _utc_iso(),
                    "agent": "supervisor",
                    "stage": "pending_audit_review",
                    "event": "audit_giveup",
                    "message": "Auditoría agotó reintentos. Se requiere confirmación manual para continuar.",
                    "details": result.get("giveup_record"),
                },
            )
            return

        if result.get("error"):
            raw_error = str(result.get("error") or "")
            user_message = _translate_pipeline_error(raw_error)
            db_service.update_process_job(
                db,
                process_id=process_id,
                status=ProcessStatus.FAILED,
                current_stage="failed",
                current_agent="supervisor",
                progress=100,
                error_message=user_message,
                agent_log_entry={
                    "timestamp": _utc_iso(),
                    "agent": "supervisor",
                    "stage": "failed",
                    "event": "failed",
                    "message": user_message,
                    "technical": raw_error,
                },
            )
            _mark_pending_failed_safe(pending_id)
            _mark_processing_transactions_failed_safe(ingest_id)
            return

        db_service.update_process_job(
            db,
            process_id=process_id,
            status=ProcessStatus.COMPLETED,
            current_stage="completed",
            current_agent="auditor",
            progress=100,
            agent_log_entry={
                "timestamp": _utc_iso(),
                "agent": "auditor",
                "stage": "completed",
                "event": "completed",
                "message": "Proceso completado correctamente",
            },
        )

        # ── Auto-chain: if more pending transactions exist for this ingest,
        # spawn a follow-up ProcessJob so multi-transaction docs (extractos,
        # nóminas, conciliaciones) don't leave orphans.
        db2 = SessionLocal()
        try:
            remaining = db_service.get_transactions_by_ingest(
                db2, process_job.ingest_id
            )
            remaining_pending = [
                tx for tx in remaining if tx.status == TransactionStatus.PENDING
            ]
            if remaining_pending:
                next_job = db_service.create_process_job(db2, process_job.ingest_id)
                await start_process_job(next_job.id)
                logger.info(
                    "Chained ProcessJob %s -> %s (%d remaining pending txs)",
                    process_id,
                    next_job.id,
                    len(remaining_pending),
                )
        except Exception as chain_err:
            logger.warning(
                "Failed to chain/cleanup ProcessJob for ingest %s: %s",
                process_job.ingest_id,
                chain_err,
            )
        finally:
            db2.close()

    except asyncio.TimeoutError:
        posted_count = 0
        try:
            txs = db_service.get_transactions_by_ingest(db, ingest_id)
            posted_count = sum(1 for tx in txs if tx.status == TransactionStatus.POSTED)
        except Exception:
            logger.exception(
                "Failed to count POSTED transactions during timeout handler"
            )

        if posted_count > 0:
            db_service.update_process_job(
                db,
                process_id=process_id,
                status=ProcessStatus.COMPLETED,
                current_stage="completed",
                progress=100,
                agent_log_entry={
                    "timestamp": _utc_iso(),
                    "agent": "supervisor",
                    "stage": "timeout",
                    "event": "non_fatal_error",
                    "message": (
                        f"Pipeline excedió {MAX_PROCESS_SECONDS}s pero "
                        f"{posted_count} transacciones quedaron persistidas. "
                        "Revise el detalle del proceso."
                    ),
                },
            )
            _mark_processing_transactions_failed_safe(ingest_id)
            # COMPLETED is "active" for get_active_process_job_for_ingest,
            # so no follow-up ProcessJob will be created. Mark remaining
            # PENDING txs as ERROR to prevent stranding.
            try:
                for tx in db_service.get_transactions_by_ingest(db, ingest_id):
                    if tx.status == TransactionStatus.PENDING:
                        _mark_pending_failed_safe(str(tx.id))
            except Exception:
                logger.exception(
                    "Failed to mark PENDING transactions as ERROR after timeout"
                )
        else:
            db_service.update_process_job(
                db,
                process_id=process_id,
                status=ProcessStatus.FAILED,
                current_stage="timeout",
                progress=100,
                error_message=f"Job timeout: exceeded {MAX_PROCESS_SECONDS} seconds",
                agent_log_entry={
                    "timestamp": _utc_iso(),
                    "agent": "supervisor",
                    "stage": "timeout",
                    "event": "failed",
                    "message": f"Timeout de procesamiento ({MAX_PROCESS_SECONDS}s)",
                },
            )
            _mark_pending_failed_safe(pending_id)
            _mark_processing_transactions_failed_safe(ingest_id)
    except Exception as exc:  # pragma: no cover - defensive logging branch
        logger.exception("Unhandled error running process job %s", process_id)
        db_service.update_process_job(
            db,
            process_id=process_id,
            status=ProcessStatus.FAILED,
            current_stage="error",
            progress=100,
            error_message=str(exc),
            agent_log_entry={
                "timestamp": _utc_iso(),
                "agent": "supervisor",
                "stage": "error",
                "event": "failed",
                "message": str(exc),
            },
        )
        _mark_pending_failed_safe(pending_id)
        _mark_processing_transactions_failed_safe(ingest_id)
    finally:
        db.close()
