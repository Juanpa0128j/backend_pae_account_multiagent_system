from app.services.file_preview import extract_file_preview


def test_returns_string():
    assert isinstance(extract_file_preview("hello content", "test.pdf"), str)


def test_empty_content_returns_string():
    assert isinstance(extract_file_preview("", "doc.pdf"), str)


def test_none_content_returns_string():
    assert isinstance(extract_file_preview(None, "doc.pdf"), str)


def test_truncates_long_content():
    long = "x" * 10000
    result = extract_file_preview(long, "doc.pdf")
    assert len(result) <= 5000
