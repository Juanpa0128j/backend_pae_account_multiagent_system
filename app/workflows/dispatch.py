"""Workflow dispatch — flag-aware routing between inline asyncio and Inngest."""

from __future__ import annotations

import logging

import inngest

from app.core.config import get_settings
from app.services import jobs
from app.workflows.inngest_client import get_inngest_client

logger = logging.getLogger(__name__)


async def dispatch_process_start(process_id: str, force_persist: bool = False) -> None:
    """Dispatch a process job. Routes to inline or Inngest per settings.workflow_engine."""
    engine = get_settings().workflow_engine
    if engine == "inngest":
        client = get_inngest_client()
        await client.send(
            inngest.Event(
                name="app/process.start",
                data={"process_id": process_id, "force_persist": force_persist},
            )
        )
        logger.info(
            "[Process %s] dispatched to Inngest (force_persist=%s)",
            process_id,
            force_persist,
        )
        return
    if engine == "inline":
        await jobs.start_process_job(process_id, force_persist=force_persist)
        return
    raise ValueError(f"Unknown workflow_engine: {engine!r}")
