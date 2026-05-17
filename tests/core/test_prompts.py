"""Tests for app.core.prompts modules."""


class TestBasePrompt:
    """Vertical slice tests for app.core.prompts._base."""

    def test_build_prompt_includes_general_instructions(self):
        from app.core.prompts._base import (
            GENERAL_EXTRACTION_INSTRUCTIONS,
            _build_prompt,
        )

        prompt = _build_prompt("Some specific instructions.", "doc text")
        assert GENERAL_EXTRACTION_INSTRUCTIONS in prompt

    def test_build_prompt_includes_document_text(self):
        from app.core.prompts._base import _build_prompt

        prompt = _build_prompt("Some specific instructions.", "the quick brown fox")
        assert "the quick brown fox" in prompt
        assert "Documento:" in prompt
        assert "---" in prompt

    def test_build_prompt_appends_correction_when_provided(self):
        from app.core.prompts._base import _build_prompt

        prompt = _build_prompt(
            "Instructions.", "text", correction_feedback="Fix the NIT"
        )
        assert "=== CORRECCIÓN REQUERIDA ===" in prompt
        assert "Fix the NIT" in prompt
        assert "Corrige los errores y vuelve a extraer." in prompt

    def test_build_prompt_omits_correction_when_none(self):
        from app.core.prompts._base import _build_prompt

        prompt = _build_prompt("Instructions.", "text")
        assert "=== CORRECCIÓN REQUERIDA ===" not in prompt


class TestIngestPrompts:
    """Vertical slice tests for app.core.prompts.ingest."""

    def test_factura_venta_prompt_contains_required_fields(self):
        from app.core.prompts.ingest import factura_venta

        prompt = factura_venta("sample text")
        assert "FACTURA DE VENTA" in prompt
        assert "CUFE" in prompt
        assert "qr_code" in prompt
        assert "sample text" in prompt

    def test_factura_compra_prompt_contains_required_fields(self):
        from app.core.prompts.ingest import factura_compra

        prompt = factura_compra("sample text")
        assert "FACTURA DE COMPRA" in prompt
        assert "cuentas por pagar" in prompt
        assert "sample text" in prompt

    def test_nota_credito_prompt_contains_required_fields(self):
        from app.core.prompts.ingest import nota_credito

        prompt = nota_credito("sample text")
        assert "NOTA CRÉDITO" in prompt
        assert "CUDE" in prompt
        assert "factura original" in prompt
        assert "sample text" in prompt

    def test_extract_transactions_prompt_routes_to_factura_venta(self):
        from app.core.prompts.ingest import extract_transactions, factura_venta

        prompt = extract_transactions("legacy text")
        assert prompt == factura_venta("legacy text")

    def test_bank_statement_prompt_contains_required_fields(self):
        from app.core.prompts.ingest import bank_statement

        prompt = bank_statement("sample text")
        assert "EXTRACTO BANCARIO" in prompt
        assert "entidad financiera" in prompt
        assert "total_debitos" in prompt
        assert "sample text" in prompt

    def test_factura_venta_prompt_mentions_all_pages_for_retentions(self):
        from app.core.prompts.ingest import factura_venta

        prompt = factura_venta("sample text")
        assert "TODAS las p\u00e1ginas" in prompt

    def test_factura_compra_prompt_mentions_all_pages_for_retentions(self):
        from app.core.prompts.ingest import factura_compra

        prompt = factura_compra("sample text")
        assert "TODAS las p\u00e1ginas" in prompt


class TestContadorPrompt:
    """Vertical slice tests for app.core.prompts.contador."""

    def test_contador_output_prompt_contains_transactions(self):
        from app.core.prompts.contador import contador_output

        raw_transactions = [
            {"fecha": "2024-01-01", "total": 1000, "descripcion": "Test tx"}
        ]
        prompt = contador_output(raw_transactions)
        assert "Transaccion 1:" in prompt
        assert "Test tx" in prompt
        assert "PUC colombiano" in prompt

    def test_contador_output_prompt_includes_doc_type_guidance(self):
        from app.core.prompts.contador import contador_output

        prompt = contador_output([], doc_type="factura_venta")
        assert "REGLA FACTURA VENTA" in prompt
        assert "130505" in prompt

    def test_contador_output_prompt_appends_correction(self):
        from app.core.prompts.contador import contador_output

        prompt = contador_output([], correction_feedback="Fix debit side")
        assert "=== CORRECCION REQUERIDA ===" in prompt
        assert "Fix debit side" in prompt


class TestAuditorPrompt:
    """Vertical slice tests for app.core.prompts.auditor."""

    def test_auditor_output_prompt_contains_transactions(self):
        from app.core.prompts.auditor import auditor_output

        contador_output_data = {
            "fecha_registro": "2024-01-01",
            "tipo_documento": "factura",
            "total_debitos": 1000,
            "total_creditos": 1000,
            "asientos": [],
        }
        raw_transactions = [{"fecha": "2024-01-01", "total": 1000}]
        prompt = auditor_output(
            contador_output=contador_output_data, raw_transactions=raw_transactions
        )
        assert "auditor" in prompt.lower()
        assert "2024-01-01" in prompt

    def test_auditor_output_prompt_appends_correction(self):
        from app.core.prompts.auditor import auditor_output

        prompt = auditor_output(
            contador_output={},
            raw_transactions=[],
            correction_feedback="Missing cuenta_puc",
        )
        assert "=== CORRECCION REQUERIDA ===" in prompt
        assert "Missing cuenta_puc" in prompt


class TestReporteroPrompt:
    """Vertical slice tests for app.core.prompts.reportero."""

    def test_reportero_brief_prompt_contains_report_type(self):
        from app.core.prompts.reportero import reportero_brief

        prompt = reportero_brief("balance", {"activos": 1000}, "some context")
        assert "balance" in prompt
        assert "activos" in prompt
        assert "some context" in prompt

    def test_reportero_analysis_prompt_contains_financial_data(self):
        from app.core.prompts.reportero import reportero_analysis

        prompt = reportero_analysis(
            {"ratios": {"razon_corriente": 1.5}},
            "RAG context",
            "System prompt here",
        )
        assert "System prompt here" in prompt
        assert "razon_corriente" in prompt
        assert "RAG context" in prompt
