"""Process pipeline workflow — single step wrapping the existing pipeline impl.

This is intentionally a thin wrapper: the LangGraph pipeline already manages
its own status updates via ProcessJob. Inngest provides retries, memoization,
and visibility around the existing impl as a black box.

Concurrency primitives:
- ``Concurrency(key="event.data.company_nit")``: per-tenant fairness so a big
  empresa cannot starve smaller ones.
- ``Throttle(key='"openai"')``: cluster-wide OpenAI rate budget to keep 429s
  out of the pipeline.
- ``Singleton(key="event.data.process_id")``: defence-in-depth against
  double-dispatch of the same process job.
"""

from __future__ import annotations

import datetime
import logging
import re

import inngest

from app.core.config import get_settings
from app.workflows.inngest_client import get_inngest_client
from app.workflows.langsmith_bridge import langsmith_inngest_span

logger = logging.getLogger(__name__)

_client = get_inngest_client()
_settings = get_settings()


async def _handle_audit_review(ctx: inngest.Context, process_id: str) -> dict:
    """Durable HITL gate. Waits up to 1h for app/process.audit-confirmed.

    Returns the final handler result dict: ``{ok: True}`` after force-persist,
    or ``{timeout: True}`` if no confirmation arrives within the window.
    """
    # Validate for CEL injection safety — only allow alphanumeric, underscores, hyphens.
    if not isinstance(process_id, str) or not re.match(r"^[A-Za-z0-9_-]+$", process_id):
        raise ValueError(
            f"process_id contains unsafe characters for CEL filter, got: {process_id!r}"
        )

    confirmed = await ctx.step.wait_for_event(
        "await-audit-confirm",
        event="app/process.audit-confirmed",
        timeout=datetime.timedelta(hours=1),
        if_=f"async.data.process_id == '{process_id}'",
    )

    if confirmed is None:

        async def _mark_timeout() -> dict:
            from app.services.jobs import _mark_job_failed_safe

            _mark_job_failed_safe(
                process_id,
                "Confirmación de auditoría no recibida en 1 hora. Reintenta el proceso.",
            )
            return {"process_id": process_id, "timeout": True}

        return await ctx.step.run("mark-timeout", _mark_timeout)

    async def _run_force() -> dict:
        from app.services.jobs import _run_process_job_impl

        await _run_process_job_impl(process_id, force_persist=True)
        return {"process_id": process_id, "ok": True}

    return await ctx.step.run("run-process-job-force", _run_force)


async def _process_pipeline_handler(ctx: inngest.Context) -> dict:
    """Run the accounting process pipeline for one ProcessJob."""
    process_id = ctx.event.data["process_id"]
    force_persist = ctx.event.data.get("force_persist", False)
    ctx.logger.info(
        "[Process %s] inngest fn start (force_persist=%s)",
        process_id,
        force_persist,
    )

    with langsmith_inngest_span(ctx, name="process-pipeline"):

        async def _run_first() -> dict:
            # Import inside the step to avoid a circular import at module load time.
            from app.core.database import SessionLocal
            from app.services import db_service
            from app.services.jobs import _run_process_job_impl

            await _run_process_job_impl(process_id, force_persist=force_persist)
            db = SessionLocal()
            try:
                pj = db_service.get_process_job(db, process_id)
                status_val = pj.status.value if pj and pj.status else None
                return {"process_id": process_id, "status": status_val}
            finally:
                db.close()

        first_result = await ctx.step.run("run-process-job", _run_first)

        if first_result.get("status") != "pending_audit_review":
            return {"process_id": process_id, "ok": True}

        return await _handle_audit_review(ctx, process_id)


process_pipeline = _client.create_function(
    fn_id="process-pipeline",
    trigger=inngest.TriggerEvent(event="app/process.start"),
    concurrency=[
        inngest.Concurrency(
            limit=_settings.inngest_concurrency_per_nit,
            key="event.data.company_nit",
        ),
    ],
    throttle=inngest.Throttle(
        limit=_settings.inngest_openai_throttle_rpm,
        period=datetime.timedelta(seconds=60),
        key='"openai"',
    ),
    singleton=inngest.Singleton(
        key="event.data.process_id",
        mode="skip",
    ),
)(_process_pipeline_handler)
