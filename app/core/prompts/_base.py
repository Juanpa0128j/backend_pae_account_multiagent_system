"""Base prompt building utilities.

Re-exports GENERAL_EXTRACTION_INSTRUCTIONS from the canonical location and
provides the _build_prompt helper used by every ingest prompt.
"""

from __future__ import annotations

from app.models.llm_schemas import GENERAL_EXTRACTION_INSTRUCTIONS

__all__ = ["GENERAL_EXTRACTION_INSTRUCTIONS", "_build_prompt"]


def _build_prompt(
    instructions: str,
    text: str,
    *,
    correction_feedback: str | None = None,
) -> str:
    """Assemble a full extraction prompt.

    Args:
        instructions: Document-type-specific instructions (Spanish).
        text: Raw document text to extract from.
        correction_feedback: Optional correction block appended when the user
            has flagged a previous extraction as wrong.
    """
    prompt = f"""{GENERAL_EXTRACTION_INSTRUCTIONS}

{instructions}

Documento:
---
{text}
---"""
    if correction_feedback:
        prompt += (
            f"\n\n=== CORRECCIÓN REQUERIDA ===\n{correction_feedback}\n"
            "Corrige los errores y vuelve a extraer."
        )
    return prompt
