"""
Hatchet workflow for the accounting pipeline.

Replaces the asyncio.create_task approach in app/services/jobs.py with a
durable Hatchet workflow that supports retries, timeouts, and observability.
"""

import logging
from datetime import timedelta

from app.workers.hatchet_client import get_hatchet
from app.agents.graph import invoke_accounting_pipeline
from app.core.database import SessionLocal
from app.services import db_service
from app.models.database import ProcessStatus

logger = logging.getLogger(__name__)

hatchet = get_hatchet()
accounting_workflow = hatchet.workflow(
    name="accounting-pipeline",
    on_events=["accounting:start"],
)


@accounting_workflow.task(
    name="run-pipeline",
    execution_timeout=timedelta(seconds=600),
    retries=2,
)
def run_pipeline(input, ctx) -> dict:  # noqa: A002
    """
    Execute the accounting pipeline for a single process job.

    Input keys:
        process_id, ingest_id, raw_transactions, pending_transaction_id,
        doc_type, force_persist, source_document (optional), company_nit (optional)
    """
    process_id = input["process_id"]
    force_persist = input.get("force_persist", False)

    logger.info("[Process %s] Hatchet accounting workflow starting", process_id)

    # Load jobs + mark RUNNING
    with SessionLocal() as db:
        process_job = db_service.get_process_job(db, process_id=process_id)
        _ingest_job = db_service.get_ingest_job(db, ingest_id=process_job.ingest_id)
        db_service.update_process_job(
            db,
            process_id=process_id,
            status=ProcessStatus.RUNNING,
            progress=10,
        )

    # Run pipeline — catch exceptions to mark FAILED, then re-raise
    try:
        result = invoke_accounting_pipeline(
            ingest_id=input["ingest_id"],
            raw_transactions=input.get("raw_transactions", []),
            pending_transaction_id=input.get("pending_transaction_id", ""),
            process_id=process_id,
            doc_type=input.get("doc_type", ""),
            source_document=input.get("source_document"),
            force_persist=force_persist,
            company_nit=input.get("company_nit"),
        )
    except Exception as exc:
        logger.exception("[Process %s] Pipeline raised exception", process_id)
        with SessionLocal() as db:
            db_service.update_process_job(
                db,
                process_id=process_id,
                status=ProcessStatus.FAILED,
                error_message=str(exc),
            )
        raise

    result_status = result.get("status") or (result.get("result") or {}).get("status")

    with SessionLocal() as db:
        if result_status == "pending_audit_review":
            db_service.update_process_job(
                db,
                process_id=process_id,
                status=ProcessStatus.PENDING_AUDIT_REVIEW,
                progress=80,
            )
            return {"status": "pending_audit_review", "process_id": process_id}

        if result_status in ("completed", None):
            db_service.update_process_job(
                db,
                process_id=process_id,
                status=ProcessStatus.COMPLETED,
                progress=100,
            )
            return {"status": "completed", "process_id": process_id}

        # Unknown / error status
        error_msg = str(result.get("error") or "Pipeline returned unknown status")
        db_service.update_process_job(
            db,
            process_id=process_id,
            status=ProcessStatus.FAILED,
            error_message=error_msg,
        )
        raise Exception(f"[Process {process_id}] Pipeline failed: {error_msg}")
