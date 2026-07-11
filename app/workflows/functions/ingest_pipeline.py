"""Ingest pipeline workflow — wraps existing _run_ingest_pipeline as single step.

Mirrors `process_pipeline.py`. The LangGraph ingest pipeline already manages
its own status updates via IngestJob; Inngest provides retries, memoization,
and per-tenant concurrency around the existing sync impl.
"""

from __future__ import annotations

import asyncio
import logging

import inngest

from app.core.config import get_settings
from app.workflows.inngest_client import get_inngest_client
from app.workflows.langsmith_bridge import langsmith_inngest_span

logger = logging.getLogger(__name__)

_client = get_inngest_client()
_settings = get_settings()


async def _ingest_pipeline_handler(ctx: inngest.Context) -> dict:
    """Run the ingest pipeline for one IngestJob."""
    ingest_id = ctx.event.data["ingest_id"]
    temp_file_paths = ctx.event.data["temp_file_paths"]
    company_nit = ctx.event.data.get("company_nit")
    parser_mode = ctx.event.data.get("parser_mode")
    multi_file_mode = ctx.event.data.get("multi_file_mode", "pages")
    ctx.logger.info(
        "[Ingest %s] inngest fn start (company_nit=%s)",
        ingest_id,
        company_nit,
    )

    with langsmith_inngest_span(ctx, name="ingest-pipeline"):

        async def _run() -> dict:
            # Import inside the step to avoid circular import at module load time.
            from app.api.v1.ingest import _run_ingest_pipeline
            from app.core.database import SessionLocal
            from app.models.database import IngestStatus
            from app.services import db_service, ingest_file_service

            # This instance may not be the one that received the upload
            # (restart / horizontal scaling) — rehydrate from shared storage
            # rather than trusting the dispatch-time scratch paths.
            paths = temp_file_paths
            db = SessionLocal()
            try:
                job = db_service.get_ingest_job(db, ingest_id)
                if job is not None:
                    try:
                        paths = ingest_file_service.ensure_local_files(db, job)
                    except (
                        ingest_file_service.IngestFilesUnavailableError
                    ) as missing_err:
                        db_service.update_ingest_job(
                            db,
                            ingest_id,
                            IngestStatus.FAILED,
                            extraction_errors=[missing_err.detail],
                        )
                        return {"ingest_id": ingest_id, "error": missing_err.detail}
            finally:
                db.close()

            await asyncio.to_thread(
                _run_ingest_pipeline,
                paths,
                ingest_id,
                company_nit,
                parser_mode,
                multi_file_mode,
            )
            return {"ingest_id": ingest_id, "ok": True}

        return await ctx.step.run("run-ingest-job", _run)


ingest_pipeline = _client.create_function(
    fn_id="ingest-pipeline",
    trigger=inngest.TriggerEvent(event="app/ingest.start"),
    concurrency=[
        inngest.Concurrency(
            limit=_settings.inngest_concurrency_per_nit,
            key="event.data.company_nit",
        ),
    ],
)(_ingest_pipeline_handler)
