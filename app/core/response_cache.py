"""In-process TTL response cache for the analysis report endpoint.

PERF FIX B1 — caches GET /api/v1/reports/analysis, the only report endpoint
that ALWAYS drives an LLM (the journal-based pipeline runs a narrated executive
summary every call; the stored-statement short-circuit in reports.py never
applies to report_type='analysis'). A 180s TTL collapses bursty refreshes from
the Reportes tab into a single pipeline run.

╔══════════════════════════════════════════════════════════════════════════╗
║  SINGLE-WORKER ASSUMPTION — READ BEFORE SCALING                           ║
║                                                                          ║
║  This cache lives in the process heap. It is CONSISTENT ONLY because     ║
║  production runs gunicorn/uvicorn with exactly ONE worker per instance   ║
║  (instance_count:1, no --workers / WEB_CONCURRENCY=1). Every request for ║
║  a given instance hits the same dict.                                    ║
║                                                                          ║
║  If WEB_CONCURRENCY>1, --workers is added, or the service is scaled to   ║
║  multiple instances, this becomes PER-WORKER inconsistent: a cache hit   ║
║  on worker A is a miss on worker B, TTLs drift, and ?refresh=true only   ║
║  busts the worker that happens to serve it. At that point MOVE THIS TO   ║
║  REDIS (shared key space + atomic TTL). Do not paper over it with        ║
║  sticky sessions.                                                        ║
╚══════════════════════════════════════════════════════════════════════════╝

Design (locked — Option C): in-process TTL cache, no external deps, no explicit
write-invalidation. Staleness is bounded by TTL; ?refresh=true forces a fresh
run for users who just uploaded documents.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Optional, Tuple

from app.core.logger import get_logger

logger = get_logger(__name__)

# TTL for cached analysis responses. 180s balances LLM cost against staleness:
# long enough to absorb a tab's burst of identical refreshes, short enough that
# a freshly ingested document surfaces within a few minutes without ?refresh.
ANALYSIS_CACHE_TTL_SECONDS: float = 180.0

# key -> (expires_at_monotonic, value)
_store: dict[Any, Tuple[float, Any]] = {}
_lock = threading.Lock()


def get(key: Any) -> Optional[Any]:
    """Return the cached value for ``key`` if present and not expired, else None.

    Uses ``time.monotonic()`` so wall-clock jumps (NTP, DST) can't extend or
    truncate a TTL. Expired entries are evicted lazily on read.
    """
    now = time.monotonic()
    with _lock:
        entry = _store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if now >= expires_at:
            # Lazy eviction — don't let dead keys accumulate.
            _store.pop(key, None)
            return None
        return value


def set(key: Any, value: Any, ttl: Optional[float] = None) -> None:
    """Store ``value`` under ``key`` with a TTL (seconds).

    ``ttl`` defaults to the module ``ANALYSIS_CACHE_TTL_SECONDS`` read at call
    time (not bound as a default arg) so tests can monkeypatch the constant.
    """
    effective_ttl = ANALYSIS_CACHE_TTL_SECONDS if ttl is None else ttl
    expires_at = time.monotonic() + effective_ttl
    with _lock:
        _store[key] = (expires_at, value)


def clear() -> None:
    """Drop every cached entry. Used by tests for isolation between cases."""
    with _lock:
        _store.clear()


def warn_if_multi_worker() -> None:
    """Log a loud WARNING at startup if env hints at >1 worker.

    The in-process cache is only consistent under a single worker. This is a
    best-effort heuristic over the usual env vars; it does not block startup.
    """
    raw = os.getenv("WEB_CONCURRENCY") or os.getenv("WORKERS")
    try:
        workers = int(raw) if raw is not None else 1
    except ValueError:
        workers = 1
    if workers > 1:
        logger.warning(
            "response_cache: detected %s workers (WEB_CONCURRENCY/WORKERS) but the "
            "in-process analysis cache is single-worker only. Cache will be "
            "per-worker inconsistent (hits/misses/TTL/?refresh diverge across "
            "workers). Move it to Redis before running >1 worker.",
            workers,
        )
