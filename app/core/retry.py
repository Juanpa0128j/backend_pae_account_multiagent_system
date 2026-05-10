from typing import Any, Callable, TypeVar

from sqlalchemy.exc import OperationalError as SAOperationalError

T = TypeVar("T")


def with_db_retry(
    fn: Callable[[], T],
    *,
    max_retries: int = 3,
    logger: Any | None = None,
    on_non_transient: Callable[[Exception], None] | None = None,
) -> T:
    """Execute fn with retry on SAOperationalError.

    Retries up to max_retries on OperationalError (transient DB errors).
    Calls on_non_transient immediately for non-retryable errors without retry.
    Logs each retry attempt.
    """
    last_exception: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except SAOperationalError as e:
            last_exception = e
            if logger is not None:
                logger.warning(
                    "db_retry: transient DB error attempt %s/%s: %s",
                    attempt,
                    max_retries,
                    e,
                )
            if attempt == max_retries:
                break
        except Exception as e:
            if on_non_transient is not None:
                on_non_transient(e)
            raise
    if last_exception is not None:
        raise last_exception
    # This line is unreachable, but satisfies the type checker
    raise RuntimeError("Retry loop exited without result or exception")
