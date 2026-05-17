"""Process pipeline workflow — single step wrapping the existing pipeline impl.

This is intentionally a thin wrapper: the LangGraph pipeline already manages
its own status updates via ProcessJob. Inngest provides retries, memoization,
and visibility around the existing impl as a black box.
"""

from __future__ import annotations

import logging

import inngest

from app.workflows.inngest_client import get_inngest_client

logger = logging.getLogger(__name__)

_client = get_inngest_client()


async def _process_pipeline_handler(ctx: inngest.Context) -> dict:
    """Run the accounting process pipeline for one ProcessJob."""
    process_id = ctx.event.data["process_id"]
    force_persist = ctx.event.data.get("force_persist", False)
    ctx.logger.info(
        "[Process %s] inngest fn start (force_persist=%s)",
        process_id,
        force_persist,
    )

    async def _run() -> dict:
        # Import inside the step to avoid a circular import at module load time.
        from app.services.jobs import _run_process_job_impl

        await _run_process_job_impl(process_id, force_persist=force_persist)
        return {"process_id": process_id, "ok": True}

    return await ctx.step.run("run-process-job", _run)


process_pipeline = _client.create_function(
    fn_id="process-pipeline",
    trigger=inngest.TriggerEvent(event="app/process.start"),
)(_process_pipeline_handler)
