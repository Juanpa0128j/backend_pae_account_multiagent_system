"""
Hatchet workflow for the ingest pipeline.

Replaces BackgroundTasks-based process_ingest_background with a durable
Hatchet workflow that supports retries, timeouts, and observability.

Payload only needs `ingest_id` — all other parameters are loaded from the
IngestJob row already persisted by the upload endpoint.
"""

import logging
from datetime import timedelta

from app.workers.hatchet_client import get_hatchet
from app.api.v1.ingest import _run_ingest_pipeline
from app.core.database import SessionLocal
from app.models.database import IngestStatus
from app.services import db_service

logger = logging.getLogger(__name__)

hatchet = get_hatchet()
ingest_workflow = hatchet.workflow(
    name="ingest-pipeline",
    on_events=["ingest:start"],
)


@ingest_workflow.task(
    name="run-ingest",
    execution_timeout=timedelta(seconds=600),
    retries=1,
)
def run_ingest(input, ctx) -> dict:  # noqa: A002
    """
    Execute the ingest pipeline for an already-created IngestJob.

    Input keys:
        ingest_id: str — ID of the IngestJob row created by the upload endpoint.
    """
    ingest_id: str = input["ingest_id"]

    logger.info("[Ingest %s] Hatchet ingest workflow starting", ingest_id)

    # Load job from DB
    with SessionLocal() as db:
        ingest_job = db_service.get_ingest_job(db, ingest_id=ingest_id)
        if ingest_job is None:
            raise ValueError(f"IngestJob not found: {ingest_id}")

        file_path: str = ingest_job.file_path
        company_nit: str | None = ingest_job.company_nit
        parser_mode: str | None = ingest_job.parser_mode
        multi_file_mode: str = ingest_job.multi_file_mode or "pages"

    # Run pipeline — catch exceptions, mark FAILED, re-raise
    try:
        _run_ingest_pipeline(
            temp_file_paths=[file_path],
            ingest_id=ingest_id,
            company_nit=company_nit,
            parser_mode=parser_mode,
            multi_file_mode=multi_file_mode,
        )
    except Exception as exc:
        logger.exception("[Ingest %s] Pipeline raised exception", ingest_id)
        with SessionLocal() as db:
            db_service.update_ingest_job(
                db,
                ingest_id,
                status=IngestStatus.FAILED,
                extraction_errors=[f"Hatchet ingest error: {exc}"],
            )
        raise

    logger.info("[Ingest %s] Hatchet ingest workflow completed", ingest_id)
    with SessionLocal() as db:
        db_service.update_ingest_job(
            db,
            ingest_id,
            status=IngestStatus.COMPLETED,
        )

    return {"status": "completed", "ingest_id": ingest_id}
