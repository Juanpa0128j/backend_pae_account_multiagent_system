"""Workflow dispatch — flag-aware routing between inline asyncio and Inngest."""

from __future__ import annotations

import logging

import inngest

from app.core.config import get_settings
from app.services import jobs
from app.workflows.inngest_client import get_inngest_client

logger = logging.getLogger(__name__)


async def dispatch_process_start(
    process_id: str,
    force_persist: bool = False,
    *,
    company_nit: str | None = None,
) -> None:
    """Dispatch a process job. Routes to inline or Inngest per settings.workflow_engine.

    ``company_nit`` is included in the Inngest event payload so the
    ``process-pipeline`` function can apply per-tenant concurrency
    (``Concurrency(key="event.data.company_nit")``). Pass ``None`` for
    legacy paths — the concurrency bucket will collapse into a shared
    ``unknown`` group, which is acceptable but loses tenant fairness.
    """
    engine = get_settings().workflow_engine
    if engine == "inngest":
        client = get_inngest_client()
        await client.send(
            inngest.Event(
                name="app/process.start",
                data={
                    "process_id": process_id,
                    "force_persist": force_persist,
                    "company_nit": company_nit or "unknown",
                },
            )
        )
        logger.info(
            "[Process %s] dispatched to Inngest (force_persist=%s, company_nit=%s)",
            process_id,
            force_persist,
            company_nit,
        )
        return
    if engine == "inline":
        await jobs.start_process_job(process_id, force_persist=force_persist)
        return
    raise ValueError(f"Unknown workflow_engine: {engine!r}")


async def dispatch_ingest_start(
    *,
    ingest_id: str,
    temp_file_paths: list[str],
    company_nit: str | None,
    parser_mode: str | None,
    multi_file_mode: str = "pages",
) -> None:
    """Dispatch an ingest job to Inngest. Inline path is a no-op.

    Inline mode is intentionally not handled here: FastAPI ``BackgroundTasks``
    must be added inside the request handler, so the inline scheduling lives
    in ``app/api/v1/ingest.py``. This dispatcher only handles the Inngest
    path; callers must still ``add_task`` for inline mode themselves.
    """
    engine = get_settings().workflow_engine
    if engine == "inngest":
        client = get_inngest_client()
        await client.send(
            inngest.Event(
                name="app/ingest.start",
                data={
                    "ingest_id": ingest_id,
                    "temp_file_paths": temp_file_paths,
                    "company_nit": company_nit or "unknown",
                    "parser_mode": parser_mode,
                    "multi_file_mode": multi_file_mode,
                },
            )
        )
        logger.info(
            "[Ingest %s] dispatched to Inngest (company_nit=%s)",
            ingest_id,
            company_nit,
        )
        return
    if engine == "inline":
        # Inline dispatch is owned by the caller (FastAPI BackgroundTasks).
        return
    raise ValueError(f"Unknown workflow_engine: {engine!r}")
