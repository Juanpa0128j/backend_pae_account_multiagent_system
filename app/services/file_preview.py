"""
Service for extracting a text preview from file content.

The supervisor truncates all file types at 3000 characters for document
classification. This service centralises that logic.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_MAX_PREVIEW_CHARS = 3000


def extract_file_preview(file_content: Optional[str], filename: str) -> str:
    """Return up to _MAX_PREVIEW_CHARS characters of file_content.

    Args:
        file_content: Raw text extracted from the file. May be None or empty.
        filename: Used for logging context only.

    Returns:
        A string of at most _MAX_PREVIEW_CHARS characters.
    """
    if not file_content:
        return ""
    return str(file_content)[:_MAX_PREVIEW_CHARS]
