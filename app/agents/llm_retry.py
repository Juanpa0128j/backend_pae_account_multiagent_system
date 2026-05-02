"""
Shared LLM retry helper for agent nodes.

Centralizes the retry-on-parse-error pattern: when a structured-output LLM call
fails Pydantic validation (raised by LangChain as `OutputParserException`),
the parse error is fed back as `correction_feedback` on the next attempt so
the model can self-correct rather than repeating identically (temperature=0
calls are deterministic without new context).

Also catches transient network errors (TimeoutError, ConnectionError, OSError)
with the same retry budget.

Used by ingest, contador, and auditor agent nodes.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

try:
    from langchain_core.exceptions import (
        OutputParserException as _OutputParserException,
    )
except ImportError:
    _OutputParserException = None  # type: ignore[assignment,misc]

DEFAULT_MAX_RETRIES = 3
_TRANSIENT_EXC: tuple[type[BaseException], ...] = (
    TimeoutError,
    ConnectionError,
    OSError,
) + ((_OutputParserException,) if _OutputParserException is not None else ())


def is_parse_error(exc: BaseException) -> bool:
    """Return True if the exception is a LangChain output-parser exception."""
    return _OutputParserException is not None and isinstance(
        exc, _OutputParserException
    )


def is_double_entry_violation(exc: BaseException) -> bool:
    """Return True if the parse error message describes a double-entry violation."""
    return is_parse_error(exc) and "Double-entry violation" in str(exc)


def is_invalid_puc(exc: BaseException) -> bool:
    """Return True if the parse error message describes an invalid PUC code."""
    return is_parse_error(exc) and "Invalid PUC code" in str(exc)


def llm_with_parse_retry(
    method: Callable[..., Any],
    *args: Any,
    correction_feedback: str | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    agent_label: str = "llm",
    **kwargs: Any,
) -> Any:
    """
    Invoke an LLM extraction method with parse-error and transient-error retry.

    On `OutputParserException`, the error message is forwarded as
    `correction_feedback` on the next attempt so the model can self-correct.
    On transient network errors, retries with the original feedback unchanged.

    The wrapped method must accept `correction_feedback=` as a keyword argument.

    Raises the last exception captured if all attempts are exhausted.
    """
    last_exc: BaseException | None = None
    active_feedback = correction_feedback
    for attempt in range(1, max_retries + 1):
        try:
            return method(*args, correction_feedback=active_feedback, **kwargs)
        except _TRANSIENT_EXC as e:
            last_exc = e
            logger.warning(
                "%s: LLM transient/parse error attempt %d/%d: %s",
                agent_label,
                attempt,
                max_retries,
                e,
            )
            if is_parse_error(e):
                active_feedback = (
                    "El intento anterior falló por un error de validación de esquema "
                    f"o JSON. Corrige y reintenta. Error: {e}"
                )
        except Exception:
            raise
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"{agent_label}: LLM call failed without a captured exception")
