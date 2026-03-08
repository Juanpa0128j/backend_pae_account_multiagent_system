"""
Async processing jobs service.

Handles background execution of the accounting graph and updates ProcessJob
status/progress in the database.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.agents.graph import invoke_agent
from app.core.database import SessionLocal
from app.models.database import ProcessStatus
from app.services import db_service

logger = logging.getLogger(__name__)

MAX_PROCESS_SECONDS = 300  # 5 minutes


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
        if not ingest_job or not ingest_job.file_path:
            db_service.update_process_job(
                db,
                process_id=process_id,
                status=ProcessStatus.FAILED,
                current_stage="init",
                progress=0,
                error_message="Ingest job or file path not found",
                agent_log_entry={
                    "timestamp": _utc_iso(),
                    "agent": "supervisor",
                    "stage": "init",
                    "event": "failed",
                    "message": "Ingest job or file path not found",
                },
            )
            return

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
            current_stage="ingesta",
            current_agent="ingesta",
            progress=35,
            agent_log_entry={
                "timestamp": _utc_iso(),
                "agent": "ingesta",
                "stage": "ingesta",
                "event": "running",
                "message": "Ejecutando workflow de agentes",
            },
        )

        result = await asyncio.wait_for(
            asyncio.to_thread(
                invoke_agent,
                ingest_job.file_path,
                {"ingest_id": process_job.ingest_id},
            ),
            timeout=MAX_PROCESS_SECONDS,
        )

        status_value = str(result.get("status", "")).lower()
        if status_value in {"failed", "error", "rejected", "validation_error"}:
            db_service.update_process_job(
                db,
                process_id=process_id,
                status=ProcessStatus.FAILED,
                current_stage="auditor",
                current_agent="auditor",
                progress=100,
                error_message=result.get("error") or "El workflow finalizó con error",
                agent_log_entry={
                    "timestamp": _utc_iso(),
                    "agent": "auditor",
                    "stage": "auditor",
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
