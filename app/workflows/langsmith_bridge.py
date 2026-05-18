"""Bridge Inngest run/event IDs into LangSmith traces.

Lets accountants and engineers correlate an Inngest run (durable orchestration)
with a LangSmith trace (LLM call graph). When ``LANGSMITH_TRACING=true`` is set
the helper opens a parent trace span tagged with Inngest identifiers; all
LangChain / LangGraph traces emitted inside the block inherit the metadata.

When LangSmith is not installed or not configured, the helper is a no-op.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any, Iterator

import inngest

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def langsmith_inngest_span(ctx: inngest.Context, *, name: str) -> Iterator[None]:
    """Open a LangSmith trace span carrying Inngest run / event / fn IDs.

    Silently no-ops if LangSmith is not importable.
    """
    try:
        from langsmith import trace as ls_trace
    except ImportError:
        yield
        return

    event = getattr(ctx, "event", None)
    metadata: dict[str, Any] = {
        "inngest_run_id": getattr(ctx, "run_id", None),
        "inngest_event_id": getattr(event, "id", None),
        "inngest_fn_id": getattr(ctx, "function_id", None),
    }
    try:
        with ls_trace(name=name, metadata=metadata):
            yield
    except Exception:  # noqa: BLE001
        # LangSmith problems must never break the workflow.
        logger.warning("langsmith trace span failed (non-fatal)", exc_info=True)
        yield
