"""
Tests for the cached compiled agent graph (perf fix A1).

Verifies:
1. get_compiled_agent_graph() returns the same singleton object across calls
   (build+compile happens once).
2. The cached graph is stateless / thread-safe: invoking it CONCURRENTLY with
   two different tenants (distinct company_nit) produces results that each
   correspond to their own input — no cross-tenant state bleed.

The graph topology is static; request state flows only via invoke(state).
We exercise the reporting path (supervisor -> reportero -> END) because it is
the shortest single-node pipeline, and stub reportero_node to echo the
tenant's company_nit back into the result so contamination is detectable.
"""

import threading

import pytest

pytest.importorskip("langgraph")

import app.agents.graph as graph_module
from app.agents.graph import create_agent_graph, get_compiled_agent_graph


def _fake_reportero_node(state):
    """Echo the per-invocation company_nit into result so bleed is detectable.

    Reads only from the passed-in `state` (no shared/module state), mirroring
    how every real node consumes request data exclusively via invoke().
    """
    nit = state.get("company_nit")
    # Touch a couple of fields to widen the window for any race to surface.
    state["result"] = {"echo_nit": nit, "status": "ok"}
    state["current_agent"] = "reportero"
    return state


class TestGraphCacheSingleton:
    def test_returns_same_object(self):
        a = get_compiled_agent_graph()
        b = get_compiled_agent_graph()
        assert a is b

    def test_cache_clear_rebuilds(self):
        a = get_compiled_agent_graph()
        get_compiled_agent_graph.cache_clear()
        b = get_compiled_agent_graph()
        assert a is not b


class TestGraphCacheNoTenantBleed:
    def test_concurrent_invoke_no_cross_tenant_bleed(self, monkeypatch):
        # Build a graph whose reportero node echoes the tenant nit. We patch the
        # node ref BEFORE compiling so the stub is baked into THIS test graph,
        # then point the cache at it. Restored via cache_clear in teardown.
        monkeypatch.setattr(graph_module, "reportero_node", _fake_reportero_node)
        get_compiled_agent_graph.cache_clear()
        # Rebuild the cached singleton with the patched node bound in.
        monkeypatch.setattr(
            graph_module, "create_agent_graph", lambda: create_agent_graph()
        )
        graph = get_compiled_agent_graph()
        assert graph is get_compiled_agent_graph()  # singleton confirmed

        tenant_a = "900111111"
        tenant_b = "800222222"
        n_iters = 200
        errors: list[str] = []
        lock = threading.Lock()

        def run(nit: str):
            for _ in range(n_iters):
                state = {
                    "mode": "reporting",
                    "company_nit": nit,
                    "report_type": "balance",
                    "report_params": {},
                    "result": {},
                    "agent_log": [],
                    "current_agent": "",
                }
                final = graph.invoke(state)
                got = final.get("result", {}).get("echo_nit")
                if got != nit:
                    with lock:
                        errors.append(f"sent {nit} got {got}")

        threads = [
            threading.Thread(target=run, args=(tenant_a,)),
            threading.Thread(target=run, args=(tenant_b,)),
            threading.Thread(target=run, args=(tenant_a,)),
            threading.Thread(target=run, args=(tenant_b,)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Drop the test graph so other tests get a clean real-node singleton.
        get_compiled_agent_graph.cache_clear()

        assert errors == [], f"cross-tenant bleed detected: {errors[:5]}"
