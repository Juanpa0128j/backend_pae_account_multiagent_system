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
from app.core.database import SessionLocal
from app.models.database import ProcessStatus
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


async def start_process_job(process_id: str) -> None:
    """Schedule a process job in the current event loop."""
    asyncio.create_task(run_process_job(process_id))


async def run_process_job(process_id: str) -> None:
    """Execute the accounting flow for a process job with timeout handling."""
    db = SessionLocal()
    try:
        process_job = db_service.get_process_job(db, process_id)
        if not process_job:
            logger.error("Process job not found: %s", process_id)
            return

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

        staged = db_service.get_transactions_by_ingest(db, process_job.ingest_id)
        if not staged:
            db_service.update_process_job(
                db,
                process_id=process_id,
                status=ProcessStatus.FAILED,
                current_stage="init",
                progress=0,
                error_message="No staged transactions found for ingest job",
                agent_log_entry={
                    "timestamp": _utc_iso(),
                    "agent": "supervisor",
                    "stage": "init",
                    "event": "failed",
                    "message": "No staged transactions found for ingest job",
                },
            )
            return

        pending_id = str(staged[0].id)
        # Fallback: documents like CEs/nóminas/extractos have no nit_receptor
        # in their content. Use the company_nit captured at upload time so
        # downstream agents (tributario) can resolve company tax settings.
        fallback_nit = (
            getattr(ingest_job, "company_nit", None)
            or getattr(staged[0], "company_nit", None)
        )
        raw_transactions: list[dict] = []
        for tx in staged:
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
            # Tax ledger registers — already-processed data, no new journal entries needed
            "auxiliar_iva",
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

        # Use dedicated thread pool to avoid blocking the default executor
        loop = asyncio.get_event_loop()
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
                ),
            ),
            timeout=MAX_PROCESS_SECONDS,
        )

        if result.get("error"):
            db_service.update_process_job(
                db,
                process_id=process_id,
                status=ProcessStatus.FAILED,
                current_stage="failed",
                current_agent="supervisor",
                progress=100,
                error_message=result.get("error") or "El workflow finalizó con error",
                agent_log_entry={
                    "timestamp": _utc_iso(),
                    "agent": "supervisor",
                    "stage": "failed",
                    "event": "failed",
                    "message": result.get("error") or "El workflow finalizó con error",
                },
            )
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

    except asyncio.TimeoutError:
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
    finally:
        db.close()
