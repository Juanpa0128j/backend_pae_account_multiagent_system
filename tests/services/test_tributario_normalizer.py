"""Tests for tributario normalizer."""

from __future__ import annotations

from datetime import date

import pytest

from app.services.tributario_normalizer import normalize_tributario_output


@pytest.fixture
def base_state():
    return {
        "file_path": "",
        "raw_text": "",
        "interpreted_data": {},
        "result": {},
        "error": None,
        "validation_history": [],
        "current_agent": "",
        "correction_feedback": None,
        "retry_count": 0,
        "ingest_id": None,
        "db_result": None,
        "mode": "process",
        "raw_transactions": [],
        "contador_output": {},
        "tributario_output": {},
        "company_config": None,
        "process_id": None,
        "pending_transaction_id": None,
        "current_stage": None,
        "agent_log": [],
        "auditor_output": {},
        "audit_approved": None,
        "audit_rejection_reason": None,
        "audit_decision": None,
        "audit_feedback": None,
        "audit_rejection_count": 0,
        "report_type": None,
        "report_params": None,
        "document_classification": None,
        "pathway": None,
        "parsed_content": None,
        "company_nit": None,
        "source_document": {},
        "pipeline_warnings": [],
        "unfixable_findings": [],
        "audit_reports": [],
        "retry_budget": {},
        "giveup_record": None,
        "force_persist": False,
        "needs_hitl_review": False,
    }


def test_normalize_empty_dict(base_state):
    result = normalize_tributario_output(base_state, {})
    assert result["impuestos"] == []
    assert result["aplica_impuestos"] is False
    assert result["total_impuestos"] == "0"
    assert result["documento_referencia"] == "sin referencia"
    assert result["referencias_legales"] == []
    assert result["asientos_enriquecidos"] == []
    assert result["observaciones"] is None


def test_normalize_with_impuestos(base_state):
    tributario_output = {
        "impuestos": [
            {"nombre": "IVA", "valor_impuesto": 19000},
            {"nombre": "Retefuente", "valor_impuesto": 5000},
        ]
    }
    result = normalize_tributario_output(base_state, tributario_output)
    assert result["impuestos"] == tributario_output["impuestos"]
    assert result["aplica_impuestos"] is True
    assert result["total_impuestos"] == "24000"


def test_normalize_uses_contador_reference(base_state):
    base_state["contador_output"] = {"descripcion_general": "Factura 123"}
    result = normalize_tributario_output(base_state, {})
    assert result["documento_referencia"] == "Factura 123"


def test_normalize_uses_raw_tx_reference(base_state):
    base_state["raw_transactions"] = [{"referencia": "Tx-001", "descripcion": "Compra"}]
    result = normalize_tributario_output(base_state, {})
    assert result["documento_referencia"] == "Tx-001"


def test_normalize_sets_fecha_analisis(base_state):
    result = normalize_tributario_output(base_state, {})
    assert result["fecha_analisis"] == date.today().isoformat()
