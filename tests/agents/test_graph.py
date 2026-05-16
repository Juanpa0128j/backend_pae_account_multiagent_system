"""Tests for graph _base_state and invoke_ingest_pipeline."""

from unittest.mock import MagicMock

from app.agents.graph import _base_state, invoke_ingest_pipeline


class TestBaseState:
    def test_base_state_includes_file_paths(self):
        state = _base_state()
        assert "file_paths" in state
        assert state["file_paths"] == []


class TestInvokeIngestPipeline:
    def test_invoke_ingest_pipeline_sets_file_paths(self, monkeypatch):
        captured_state = {}

        def _capture_invoke(state):
            captured_state.update(state)
            return {**state, "result": {}}

        monkeypatch.setattr(
            "app.agents.graph.create_agent_graph",
            lambda: MagicMock(invoke=_capture_invoke),
        )
        invoke_ingest_pipeline(
            "/tmp/fake.pdf",
            initial_state={"ingest_id": "test-id"},
            file_paths=["/tmp/fake.pdf", "/tmp/fake2.pdf"],
        )
        assert captured_state.get("file_paths") == ["/tmp/fake.pdf", "/tmp/fake2.pdf"]
