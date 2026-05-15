from app.api.v1.process import _classify_process_error


class TestClassifyNoContadorAsientos:
    """Regression: NO_CONTADOR_ASIENTOS must surface with actionable remediation."""

    def test_classify_no_contador_asientos(self):
        category, code, remediation = _classify_process_error(
            "db_persist: No contador asientos to persist"
        )
        assert code == "NO_CONTADOR_ASIENTOS"
        assert category == "extraction_error"
        assert "imagen escaneada" in remediation

    def test_classify_no_contador_asientos_lowercase(self):
        category, code, remediation = _classify_process_error(
            "db_persist: no contador asientos to persist"
        )
        assert code == "NO_CONTADOR_ASIENTOS"
        assert category == "extraction_error"
        assert "imagen escaneada" in remediation

    def test_classify_no_asientos_fallback(self):
        category, code, remediation = _classify_process_error("no asientos found")
        assert code == "NO_CONTADOR_ASIENTOS"
        assert category == "extraction_error"
        assert "imagen escaneada" in remediation
