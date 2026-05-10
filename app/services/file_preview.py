"""File preview extraction adapter.

Wraps format-specific parsers (LlamaParse, openpyxl, xml) into a single
uniform interface used by the ingest router.
"""

from pathlib import Path

from app.core.config import get_settings
from app.core.logger import get_logger

logger = get_logger("app.services.file_preview")


def extract_preview(file_path: str) -> tuple[str, list | None]:
    """Extract text preview and parsed content from a file.

    Returns (text_preview, parsed_content) where parsed_content is only
    populated for Excel files (tabular data).
    """
    ext = Path(file_path).suffix.lower()
    text_preview = ""
    parsed_content = None

    try:
        if ext == ".xlsx":
            from app.services.excel_parser import parse_excel

            markdown_text, tabular_data = parse_excel(file_path)
            text_preview = markdown_text
            parsed_content = tabular_data
        elif ext == ".xml":
            from app.services.xml_parser import parse_xml

            xml_text = parse_xml(file_path)
            text_preview = xml_text
        elif ext == ".pdf":
            from app.services.pdf_processor import extract_text_from_pdf

            text_preview = extract_text_from_pdf(file_path)
        elif ext in (".jpg", ".jpeg", ".png"):
            from llama_parse import LlamaParse  # type: ignore[import-untyped]

            settings = get_settings()
            parser = LlamaParse(
                api_key=settings.llama_cloud_api_key,
                result_type="markdown",
            )
            documents = parser.load_data(file_path)
            image_text = "\n\n".join([doc.text for doc in documents])

            if not image_text.strip():
                logger.warning(
                    "file_preview: empty image preview in markdown mode; retrying with text mode"
                )
                parser = LlamaParse(
                    api_key=settings.llama_cloud_api_key,
                    result_type="text",
                )
                documents = parser.load_data(file_path)
                image_text = "\n\n".join([doc.text for doc in documents])

            text_preview = image_text
    except Exception as preview_err:
        logger.warning("file_preview: extraction failed: %s", preview_err)

    return text_preview, parsed_content
