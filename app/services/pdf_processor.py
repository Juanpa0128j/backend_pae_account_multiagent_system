"""
PDF processing utilities.
Handles PDF text extraction using PyPDF.
"""

import logging
from pathlib import Path
from pypdf import PdfReader

logger = logging.getLogger(__name__)


def extract_text_from_pdf(file_path: str) -> str:
    """
    Extract all text from a PDF file.

    Args:
        file_path: Path to the PDF file

    Returns:
        Concatenated text from all pages

    Raises:
        FileNotFoundError: If PDF file doesn't exist
        ValueError: If PDF is corrupted or unreadable
    """
    file_path = Path(file_path)

    if not file_path.exists():
        logger.error(f"PDF file not found: {file_path}")
        raise FileNotFoundError(f"PDF file not found: {file_path}")

    try:
        reader = PdfReader(file_path)

        if len(reader.pages) == 0:
            logger.warning(f"PDF has no pages: {file_path}")
            return ""

        full_text = []
        for page_num, page in enumerate(reader.pages):
            try:
                text = page.extract_text()
                full_text.append(f"--- PAGE {page_num + 1} ---\n{text}")
            except Exception as e:
                logger.warning(f"Error extracting page {page_num + 1}: {str(e)}")
                continue

        result = "\n\n".join(full_text)
        logger.info(f"Extracted {len(full_text)} pages from PDF: {file_path}")
        return result

    except Exception as e:
        logger.error(f"Error reading PDF {file_path}: {str(e)}")
        raise ValueError(f"Failed to read PDF: {str(e)}")


def save_uploaded_file(file_content: bytes, destination: str) -> str:
    """
    Save uploaded file to the destination path.

    Args:
        file_content: Binary content of the file
        destination: Path where to save the file

    Returns:
        The destination path
    """
    dest_path = Path(destination)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    with open(dest_path, "wb") as f:
        f.write(file_content)

    logger.info(f"Saved uploaded file to: {dest_path}")
    return str(dest_path)
