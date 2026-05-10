from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import OperationalError as SAOperationalError

from app.core.retry import with_db_retry


def test_success_on_first_attempt():
    def fn():
        return "ok"

    result = with_db_retry(fn)
    assert result == "ok"


def test_success_after_one_transient_retry():
    calls = []

    def fn():
        calls.append(1)
        if len(calls) == 1:
            raise SAOperationalError("transient", None, None)
        return "ok"

    result = with_db_retry(fn)
    assert result == "ok"
    assert len(calls) == 2


def test_raises_after_max_retries_exhausted():
    calls = []

    def fn():
        calls.append(1)
        raise SAOperationalError("transient", None, None)

    with pytest.raises(SAOperationalError):
        with_db_retry(fn, max_retries=3)

    assert len(calls) == 3


def test_does_not_retry_on_non_transient():
    calls = []

    def fn():
        calls.append(1)
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        with_db_retry(fn, max_retries=3)

    assert len(calls) == 1


def test_calls_on_non_transient_for_non_transient_error():
    received = []

    def callback(exc: Exception) -> None:
        received.append(exc)

    def fn():
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        with_db_retry(fn, on_non_transient=callback)

    assert len(received) == 1
    assert isinstance(received[0], ValueError)
    assert str(received[0]) == "boom"


def test_logs_retry_attempts():
    logger = MagicMock()
    calls = []

    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise SAOperationalError("transient", None, None)
        return "ok"

    result = with_db_retry(fn, max_retries=3, logger=logger)
    assert result == "ok"
    assert logger.warning.call_count == 2
    # Verify the warning was called with the expected format
    first_call_args = logger.warning.call_args_list[0][0]
    assert "transient DB error attempt" in first_call_args[0]
