"""Unit tests for app/services/file_preview.py."""

from unittest.mock import MagicMock, patch


from app.services.file_preview import extract_preview


def test_extract_preview_xlsx():
    with patch("app.services.excel_parser.parse_excel") as mock_parse:
        mock_parse.return_value = ("md text", [{"col": 1}])
        text_preview, parsed_content = extract_preview("/path/to/file.xlsx")
        assert text_preview == "md text"
        assert parsed_content == [{"col": 1}]
        mock_parse.assert_called_once_with("/path/to/file.xlsx")


def test_extract_preview_xml():
    with patch("app.services.xml_parser.parse_xml") as mock_parse:
        mock_parse.return_value = "xml text"
        text_preview, parsed_content = extract_preview("/path/to/file.xml")
        assert text_preview == "xml text"
        assert parsed_content is None
        mock_parse.assert_called_once_with("/path/to/file.xml")


def test_extract_preview_pdf():
    with patch("app.services.pdf_processor.extract_text_from_pdf") as mock_extract:
        mock_extract.return_value = "pdf text"
        text_preview, parsed_content = extract_preview("/path/to/file.pdf")
        assert text_preview == "pdf text"
        assert parsed_content is None
        mock_extract.assert_called_once_with("/path/to/file.pdf")


def test_extract_preview_image():
    with patch("llama_parse.LlamaParse") as MockParser:
        mock_instance = MagicMock()
        mock_doc = MagicMock()
        mock_doc.text = "image text"
        mock_instance.load_data.return_value = [mock_doc]
        MockParser.return_value = mock_instance

        text_preview, parsed_content = extract_preview("/path/to/file.jpg")
        assert text_preview == "image text"
        assert parsed_content is None


def test_extract_preview_unsupported():
    text_preview, parsed_content = extract_preview("/path/to/file.txt")
    assert text_preview == ""
    assert parsed_content is None


def test_extract_preview_error():
    with patch("app.services.pdf_processor.extract_text_from_pdf") as mock_extract:
        mock_extract.side_effect = Exception("pdf broke")
        text_preview, parsed_content = extract_preview("/path/to/file.pdf")
        assert text_preview == ""
        assert parsed_content is None
