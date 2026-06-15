"""Concurrency benchmark: does converting a FastAPI handler from ``async def`` to
plain ``def`` reduce event-loop starvation?

Hypothesis: a handler that does sync blocking work (graph.invoke / LLM) with no
``await`` will, under ``async def``, hog the single event loop while it runs and
starve other concurrent requests. Under plain ``def``, FastAPI offloads the
handler to a threadpool, so the loop stays free and concurrent requests stay
fast.

This script is NOT a pytest. It:
  1. Imports the real FastAPI ``app`` from main.
  2. Monkeypatches ``invoke_reporting_pipeline`` as imported in
     app/api/v1/reports.py to a deterministically slow stub (``time.sleep``)
     returning a minimal valid pipeline result — no real LLM/DB.
  3. Overrides get_current_user + get_db so /reports/analysis returns 200 offline.
  4. Adds a loop-bound async victim route /__bench_ping to the app object.
  5. Runs uvicorn in a daemon thread on a free port.
  6. Measures victim ping latency baseline vs under one in-flight slow request.

Run twice — once on the current ``def`` code, once with the handler change
stashed back to ``async def`` — and compare the victim ping p95/max.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from uuid import UUID

import httpx
import uvicorn

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SLOW_S = 2.0  # how long the slow /reports/analysis request blocks
N_PINGS = 20  # victim pings per scenario
WARMUP = 3

# ---------------------------------------------------------------------------
# 1. Import the real app
# ---------------------------------------------------------------------------
from main import app  # noqa: E402
import app.api.v1.reports as reports_mod  # noqa: E402
from app.core.auth import CurrentUser, get_current_user  # noqa: E402
from app.core.database import Base, get_db  # noqa: E402

# ---------------------------------------------------------------------------
# 2. Monkeypatch the heavy call — deterministically slow, offline.
#    Stub shape mirrors tests/api/v1/test_handlers_characterization.py:
#    _pipeline_ok(dict(_MOCK_GENERIC_REPORT))
# ---------------------------------------------------------------------------
_MOCK_GENERIC_REPORT = {
    "report_type": "generic",
    "period_start": "2026-01-01",
    "period_end": "2026-01-31",
    "generated_at": "2026-01-31T00:00:00+00:00",
}


def _slow_pipeline(*args, **kwargs):
    time.sleep(SLOW_S)  # blocking sync work — no await, like graph.invoke/LLM
    return {"status": "ok", "report": dict(_MOCK_GENERIC_REPORT), "agent_log": []}


reports_mod.invoke_reporting_pipeline = _slow_pipeline

# ---------------------------------------------------------------------------
# 3. Dependency overrides (auth + empty in-memory SQLite db), mirroring
#    tests/conftest.py + tests/api/v1/test_handlers_characterization.py
# ---------------------------------------------------------------------------
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(bind=_engine)
_SessionLocal = sessionmaker(bind=_engine)
_session = _SessionLocal()


def _override_get_db():
    try:
        yield _session
    finally:
        pass


app.dependency_overrides[get_current_user] = lambda: CurrentUser(
    id=UUID("00000000-0000-0000-0000-000000000000"),
    email="test@test.com",
)
app.dependency_overrides[get_db] = _override_get_db

# Disable rate limits (autouse fixture equivalent).
try:
    from app.core.limiter import limiter

    limiter.enabled = False
except Exception:
    pass

# ---------------------------------------------------------------------------
# 4. Runtime victim route — loop-bound async endpoint (added to app object only).
# ---------------------------------------------------------------------------


@app.get("/__bench_ping")
async def __bench_ping():
    return {"ok": True}


# ---------------------------------------------------------------------------
# 5. Launch uvicorn in a daemon thread on a free port.
# ---------------------------------------------------------------------------
def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


PORT = _free_port()
BASE = f"http://127.0.0.1:{PORT}"


def _run_server(server: uvicorn.Server):
    server.run()


def _start_server() -> uvicorn.Server:
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=PORT,
        log_level="error",
        lifespan="off",  # skip app startup hooks (DB/RAG); we run fully offline
    )
    server = uvicorn.Server(config)
    t = threading.Thread(target=_run_server, args=(server,), daemon=True)
    t.start()
    return server


def _wait_ready(timeout_s: float = 30.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = httpx.get(f"{BASE}/__bench_ping", timeout=1.0)
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.1)
    raise RuntimeError("server did not become ready")


# ---------------------------------------------------------------------------
# 6. Measurement
# ---------------------------------------------------------------------------
def _pct(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


async def _ping(client: httpx.AsyncClient) -> float:
    t0 = time.perf_counter()
    r = await client.get("/__bench_ping", timeout=30.0)
    assert r.status_code == 200, r.status_code
    return (time.perf_counter() - t0) * 1000.0  # ms


async def _baseline(client: httpx.AsyncClient) -> list[float]:
    # 20 sequential pings, NO slow request in flight.
    out = []
    for _ in range(N_PINGS):
        out.append(await _ping(client))
    return out


async def _under_load(client: httpx.AsyncClient) -> tuple[list[float], float]:
    # Fire ONE slow /reports/analysis as a concurrent task; while it's in
    # flight, fire 20 pings concurrently.
    slow_t0 = time.perf_counter()
    slow_task = asyncio.create_task(
        client.get("/api/v1/reports/analysis", timeout=60.0)
    )
    # Give the slow request a beat to actually arrive at the server and start
    # blocking before we issue the pings (it sleeps SLOW_S, plenty of runway).
    await asyncio.sleep(0.05)
    ping_tasks = [asyncio.create_task(_ping(client)) for _ in range(N_PINGS)]
    pings = await asyncio.gather(*ping_tasks)
    slow_resp = await slow_task
    slow_dur = (time.perf_counter() - slow_t0) * 1000.0
    assert slow_resp.status_code == 200, slow_resp.status_code
    return list(pings), slow_dur


async def _measure() -> None:
    async with httpx.AsyncClient(base_url=BASE) as client:
        # WARMUP
        for _ in range(WARMUP):
            await _ping(client)

        base = await _baseline(client)
        load, slow_dur = await _under_load(client)

    def row(name, pings, slow=None):
        return (
            name,
            f"{_pct(pings, 50):.1f}",
            f"{_pct(pings, 95):.1f}",
            f"{max(pings):.1f}",
            f"{slow:.0f}" if slow is not None else "-",
        )

    rows = [
        row("BASELINE (no load)", base),
        row("UNDER LOAD (1 slow req)", load, slow_dur),
    ]

    mode = "async def" if _detect_async() else "def (threadpool)"
    print()
    print(f"=== handler mode: {mode}  | SLOW_S={SLOW_S}  N_PINGS={N_PINGS} ===")
    hdr = ("scenario", "ping p50 ms", "ping p95 ms", "ping max ms", "slow ms")
    widths = [26, 12, 12, 12, 9]

    def fmt(cols):
        return " | ".join(str(c).ljust(w) for c, w in zip(cols, widths))

    print(fmt(hdr))
    print("-" * (sum(widths) + 3 * (len(widths) - 1)))
    for r in rows:
        print(fmt(r))
    print()


def _detect_async() -> bool:
    """True if the live get_analysis_report handler is a coroutine function."""
    import inspect

    for route in app.routes:
        if getattr(route, "path", None) == "/api/v1/reports/analysis":
            return inspect.iscoroutinefunction(route.endpoint)
    return False


def main() -> None:
    server = _start_server()
    try:
        _wait_ready()
        asyncio.run(_measure())
    finally:
        server.should_exit = True
        time.sleep(0.3)


if __name__ == "__main__":
    main()
