"""Tests for app.workflows.langsmith_bridge (AAA pattern)."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.workflows import langsmith_bridge as bridge


def _ctx(
    *, run_id: str = "r1", event_id: str = "e1", fn_id: str = "f1"
) -> SimpleNamespace:
    return SimpleNamespace(
        run_id=run_id,
        function_id=fn_id,
        event=SimpleNamespace(id=event_id),
    )


def test_no_langsmith_module_yields_silently() -> None:
    # Arrange
    ctx = _ctx()
    sentinel = []

    # Simulate ImportError by forcing the import inside the function to fail
    real_import = (
        __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__
    )

    def _fake_import(name, *a, **kw):
        if name == "langsmith":
            raise ImportError("simulated")
        return real_import(name, *a, **kw)

    # Act
    with patch("builtins.__import__", side_effect=_fake_import):
        with bridge.langsmith_inngest_span(ctx, name="test-span"):
            sentinel.append("inside")

    # Assert
    assert sentinel == ["inside"]


def test_metadata_includes_inngest_ids() -> None:
    # Arrange
    ctx = _ctx(run_id="r99", event_id="e99", fn_id="process-pipeline")
    mock_trace = MagicMock()
    mock_trace.return_value.__enter__ = MagicMock(return_value=None)
    mock_trace.return_value.__exit__ = MagicMock(return_value=False)

    fake_ls_module = SimpleNamespace(trace=mock_trace)

    with patch.dict(sys.modules, {"langsmith": fake_ls_module}):
        # Act
        with bridge.langsmith_inngest_span(ctx, name="my-span"):
            pass

    # Assert
    mock_trace.assert_called_once()
    kwargs = mock_trace.call_args.kwargs
    assert kwargs["name"] == "my-span"
    assert kwargs["metadata"] == {
        "inngest_run_id": "r99",
        "inngest_event_id": "e99",
        "inngest_fn_id": "process-pipeline",
    }


def test_trace_exception_is_swallowed() -> None:
    # Arrange
    ctx = _ctx()

    def _boom(*a, **kw):
        raise RuntimeError("ls broken")

    fake_ls_module = SimpleNamespace(trace=_boom)
    sentinel = []

    with patch.dict(sys.modules, {"langsmith": fake_ls_module}):
        # Act
        with bridge.langsmith_inngest_span(ctx, name="my-span"):
            sentinel.append("inside")

    # Assert — body still runs
    assert sentinel == ["inside"]
