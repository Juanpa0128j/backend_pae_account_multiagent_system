from app.agents.routing.edge_functions import (
    should_retry_agent,
    should_retry_auditor,
    should_retry_contador,
)


def _state(**kwargs):
    base = {
        "error": None,
        "correction_feedback": None,
        "retry_count": 0,
        "mode": "ingest",
        "current_agent": "",
        "agent_log": [],
    }
    return {**base, **kwargs}


def test_should_retry_agent_error():
    assert should_retry_agent(_state(error="fail")) == "error"


def test_should_retry_agent_feedback():
    assert should_retry_agent(_state(correction_feedback="fix")) == "retry"


def test_should_retry_agent_end():
    assert should_retry_agent(_state()) == "end"


def test_should_retry_contador_under_limit():
    assert (
        should_retry_contador(_state(correction_feedback="fix", retry_count=1))
        == "retry"
    )


def test_should_retry_contador_at_limit():
    from app.agents.routing.edge_functions import MAX_CONTADOR_RETRIES

    assert (
        should_retry_contador(
            _state(correction_feedback="fix", retry_count=MAX_CONTADOR_RETRIES)
        )
        == "end"
    )


def test_should_retry_auditor_under_limit():
    assert (
        should_retry_auditor(_state(correction_feedback="fix", retry_count=1))
        == "retry"
    )
