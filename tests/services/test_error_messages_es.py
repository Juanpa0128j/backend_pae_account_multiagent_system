"""
TDD tests for app/services/error_messages_es.py (RED phase — module does not exist yet).

Error bucket taxonomy:
  documento_ilegible   — KeyError (unreadable / password-protected doc)
  sin_transacciones    — empty result / no transactions found
  formato_no_soportado — ValueError (unsupported format)
  error_interno        — generic / unexpected exceptions

All public-facing strings must be Spanish. Raw exceptions must never reach
the API response.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Import under test — will raise ImportError in RED phase (module not yet created)
# ---------------------------------------------------------------------------
from app.services.error_messages_es import classify_error, get_extraction_error_message

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ENGLISH_WORDS = {
    "error",
    "failed",
    "invalid",
    "exception",
    "key",
    "value",
    "type",
    "not",
    "found",
    "unsupported",
    "internal",
    "unexpected",
}


def _contains_english(text: str) -> bool:
    """Return True if any known English sentinel word appears in the text."""
    words = set(text.lower().split())
    return bool(words & _ENGLISH_WORDS)


# ---------------------------------------------------------------------------
# Tests: classify_error()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestClassifyError:
    def test_keyerror_maps_to_documento_ilegible(self):
        bucket = classify_error(KeyError("campo_faltante"))
        assert bucket == "documento_ilegible"

    def test_valueerror_maps_to_formato_no_soportado(self):
        bucket = classify_error(ValueError("unsupported mime type"))
        assert bucket == "formato_no_soportado"

    def test_generic_exception_maps_to_error_interno(self):
        bucket = classify_error(RuntimeError("something went wrong"))
        assert bucket == "error_interno"

    def test_typeerror_maps_to_error_interno(self):
        bucket = classify_error(TypeError("bad type"))
        assert bucket == "error_interno"

    def test_attribute_error_maps_to_error_interno(self):
        bucket = classify_error(AttributeError("NoneType has no attr x"))
        assert bucket == "error_interno"

    def test_returns_string(self):
        result = classify_error(Exception("anything"))
        assert isinstance(result, str)

    def test_known_buckets_only(self):
        valid_buckets = {
            "documento_ilegible",
            "sin_transacciones",
            "formato_no_soportado",
            "error_interno",
        }
        for exc in (
            KeyError("k"),
            ValueError("v"),
            RuntimeError("r"),
            TypeError("t"),
            Exception("e"),
        ):
            assert classify_error(exc) in valid_buckets


# ---------------------------------------------------------------------------
# Tests: get_extraction_error_message()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetExtractionErrorMessage:
    def test_keyerror_returns_spanish_string(self):
        msg = get_extraction_error_message(KeyError("campo"))
        assert isinstance(msg, str)
        assert len(msg) > 10

    def test_keyerror_maps_to_documento_ilegible_message(self):
        msg = get_extraction_error_message(KeyError("campo"))
        assert "El documento no pudo ser leído" in msg
        assert "contraseña" in msg

    def test_valueerror_returns_formato_no_soportado_message(self):
        msg = get_extraction_error_message(ValueError("bad format"))
        assert isinstance(msg, str)
        assert len(msg) > 10

    def test_empty_result_maps_to_sin_transacciones(self):
        """Sentinel value None / empty list signals 'no transactions found'."""
        msg = get_extraction_error_message(None)
        assert isinstance(msg, str)
        assert len(msg) > 10
        # Should mention transactions or movements in Spanish
        lower = msg.lower()
        assert any(
            word in lower
            for word in ("transacción", "transacciones", "movimiento", "movimientos")
        )

    def test_generic_exception_maps_to_error_interno(self):
        msg = get_extraction_error_message(RuntimeError("boom"))
        assert isinstance(msg, str)
        assert len(msg) > 10

    def test_classify_returns_spanish_string(self):
        """Output must NOT start with or consist of bare English exception repr."""
        for exc in (
            KeyError("k"),
            ValueError("v"),
            RuntimeError("r"),
            None,
        ):
            msg = get_extraction_error_message(exc)
            # Must not contain raw Python exception class names in message body
            assert "KeyError" not in msg
            assert "ValueError" not in msg
            assert "RuntimeError" not in msg
            assert "Exception" not in msg
            assert "Traceback" not in msg

    def test_extraction_errors_always_spanish(self):
        """No English sentinel words should appear in any user-facing message."""
        for exc in (
            KeyError("k"),
            ValueError("v"),
            RuntimeError("r"),
            TypeError("t"),
            AttributeError("a"),
            None,
        ):
            msg = get_extraction_error_message(exc)
            assert not _contains_english(msg), (
                f"English word detected in message for {type(exc).__name__!r}: {msg!r}"
            )


# ---------------------------------------------------------------------------
# Parametrized: extraction_errors always Spanish
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "exc",
    [
        KeyError("missing_field"),
        ValueError("unsupported format"),
        RuntimeError("internal pipeline failure"),
        TypeError("unexpected type"),
        AttributeError("NoneType has no attr"),
        OSError("file not found"),
        MemoryError("out of memory"),
        None,
    ],
    ids=[
        "KeyError",
        "ValueError",
        "RuntimeError",
        "TypeError",
        "AttributeError",
        "OSError",
        "MemoryError",
        "empty_result",
    ],
)
def test_extraction_errors_always_spanish_parametrized(exc):
    """Every exception type and None must produce a Spanish user message."""
    msg = get_extraction_error_message(exc)
    assert isinstance(msg, str)
    assert len(msg) > 5
    # Raw exception class names must not leak
    assert "Error" not in msg or any(
        spanish in msg for spanish in ("Error", "el", "de", "no", "se")
    ), f"Suspicious English class name leak: {msg!r}"
    # No Python traceback
    assert "Traceback" not in msg
    assert "File " not in msg


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEdgeCases:
    def test_nested_keyerror_still_classifies(self):
        inner = KeyError("nested")
        outer = RuntimeError("wrap")
        outer.__cause__ = inner
        # classify_error looks at the exception itself, not cause
        bucket = classify_error(outer)
        assert bucket in {
            "error_interno",
            "documento_ilegible",
            "formato_no_soportado",
            "sin_transacciones",
        }

    def test_none_input_returns_sin_transacciones_bucket(self):
        """None sentinel = no transactions extracted = sin_transacciones."""
        assert len(get_extraction_error_message(None)) > 0
        bucket = classify_error(None)  # classify_error may also accept None
        assert bucket == "sin_transacciones"

    def test_message_is_not_just_whitespace(self):
        for exc in (KeyError("k"), ValueError("v"), RuntimeError("r"), None):
            msg = get_extraction_error_message(exc)
            assert msg.strip() != ""
