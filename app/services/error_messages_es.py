"""
Spanish user-facing error classifier for ingest pipeline exceptions.

Maps internal Python exceptions to accountant-readable Spanish strings.
Raw exception messages are NEVER forwarded to the HTTP client — they are
logged at DEBUG level only.

Public API
----------
classify_error(exc)                -> bucket key str
get_extraction_error_message(exc)  -> Spanish user-facing string
classify_exception(exc, context)   -> Spanish user-facing string  (alias)
classify_empty_result(context)     -> Spanish user-facing string
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Error bucket taxonomy — accountant-facing Spanish copy
BUCKETS: dict[str, str] = {
    "documento_ilegible": (
        "El documento no pudo ser leído. "
        "Verifica que no esté protegido con contraseña o dañado."
    ),
    "sin_transacciones": (
        "El documento fue procesado pero no se encontraron transacciones contables."
    ),
    "formato_no_soportado": (
        "El formato del archivo no es compatible. Usa PDF, XML, Excel o imagen."
    ),
    "nit_no_encontrado": ("No se encontró el NIT del emisor en el documento."),
    "fecha_invalida": (
        "La fecha del documento no pudo ser interpretada. Verifica el formato."
    ),
    "error_interno": (
        "Ocurrió un problema al procesar el documento. "
        "Contacta soporte si la situación persiste."
    ),
}


def classify_error(exc: Exception | None) -> str:
    """Return the bucket key for a given exception (or None for empty result).

    Returns one of: documento_ilegible, sin_transacciones,
    formato_no_soportado, nit_no_encontrado, fecha_invalida, error_interno.
    """
    if exc is None:
        return "sin_transacciones"

    logger.debug("Pipeline exception [%s]: %s", type(exc).__name__, exc, exc_info=exc)

    exc_str = str(exc).lower()
    exc_type = type(exc).__name__

    if exc_type == "KeyError":
        return "documento_ilegible"

    if "password" in exc_str or "encrypted" in exc_str or "legible" in exc_str:
        return "documento_ilegible"

    if (
        "no transaction" in exc_str
        or "sin transacciones" in exc_str
        or ("empty" in exc_str and "transaction" in exc_str)
    ):
        return "sin_transacciones"

    if exc_type == "ValueError" and any(
        kw in exc_str
        for kw in ("format", "extension", "content", "type", "mime", "unsupported")
    ):
        return "formato_no_soportado"

    if "fecha" in exc_str or ("date" in exc_str and "format" in exc_str):
        return "fecha_invalida"

    if "nit" in exc_str or "emisor" in exc_str:
        return "nit_no_encontrado"

    return "error_interno"


def get_extraction_error_message(exc: Exception | None) -> str:
    """Return a Spanish user-facing error string for a given exception or None.

    None signals an empty-result / no-transactions scenario.
    """
    bucket = classify_error(exc)
    return BUCKETS[bucket]


def classify_exception(exc: Exception, context: dict[str, Any] | None = None) -> str:
    """Alias of get_extraction_error_message for pipeline integration sites."""
    return get_extraction_error_message(exc)


def classify_empty_result(context: dict[str, Any] | None = None) -> str:
    """Return Spanish error for pipelines that produce zero transactions."""
    return BUCKETS["sin_transacciones"]
