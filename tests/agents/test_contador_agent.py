"""
Example-driven tests for Contador behavior.

Goal: validate that realistic input transactions produce sensible accounting
outputs and that retry/validation/prompt behavior is coherent.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from app.agents.contador_agent import contador_node
from app.agents.state import AgentState
from app.agents.supervisor import validate_contador_output_node
from app.core.llm_client import LLMClient


def _base_state(**overrides) -> AgentState:
    state: AgentState = {
        "file_path": "",
        "raw_text": "",
        "interpreted_data": {},
        "result": {},
        "error": None,
        "validation_history": [],
        "current_agent": "",
        "correction_feedback": None,
        "retry_count": 0,
        "ingest_id": "ing-test",
        "db_result": None,
        "mode": "process",
        "raw_transactions": [
            {
                "fecha": "2026-03-01",
                "nit_emisor": "900123456",
                "nit_receptor": "800999888",
                "total": 1190000,
                "descripcion": "Servicio de consultoria tributaria marzo",
                "items": [
                    {"descripcion": "Consultoria", "cantidad": 1, "valor": 1190000}
                ],
            }
        ],
        "contador_output": {},
        "process_id": "proc-test",
        "pending_transaction_id": "txn-test",
        "current_stage": None,
        "agent_log": [],
        "auditor_output": {},
        "audit_approved": None,
        "audit_rejection_reason": None,
        "audit_decision": None,
        "audit_feedback": None,
        "tributario_output": {},
        "company_config": None,
    }
    state.update(overrides)
    return state


def test_contador_example_service_expense_output_is_sensible() -> None:
    """Example: consulting service should map to balanced debit/credit entries."""
    expected_output = {
        "fecha_registro": "2026-03-01",
        "tipo_documento": "factura",
        "descripcion_general": "Registro factura servicio de consultoria tributaria",
        "asientos": [
            {
                "cuenta_puc": "5135",
                "nombre_cuenta": "Servicios",
                "tipo_movimiento": "debito",
                "valor": 1190000,
                "descripcion": "Gasto por consultoria",
            },
            {
                "cuenta_puc": "2205",
                "nombre_cuenta": "Proveedores nacionales",
                "tipo_movimiento": "credito",
                "valor": 1190000,
                "descripcion": "CxP proveedor",
            },
        ],
        "total_debitos": 1190000,
        "total_creditos": 1190000,
    }

    mock_llm = MagicMock()
    mock_llm.extract_contador_output.return_value = expected_output

    with (
        patch("app.agents.contador_agent.get_llm_client", return_value=mock_llm),
        patch(
            "app.services.rag_service.get_rag_service",
            side_effect=Exception("rag unavailable"),
        ),
    ):
        state = _base_state()
        result = contador_node(state)

    assert result["error"] is None
    assert result["result"]["status"] == "clasificado"
    assert result["current_agent"] == "contador"
    assert result["current_stage"] == "contador"
    assert (
        result["contador_output"]["total_debitos"]
        == result["contador_output"]["total_creditos"]
    )
    assert result["contador_output"]["asientos"][0]["cuenta_puc"] == "5135"
    assert result["contador_output"]["asientos"][1]["cuenta_puc"] == "2205"


def test_contador_example_retry_feedback_roundtrip() -> None:
    """Example: schema feedback should be forwarded to Gemini and then cleared."""
    mock_llm = MagicMock()
    mock_llm.extract_contador_output.return_value = {
        "fecha_registro": "2026-03-01",
        "tipo_documento": "factura",
        "descripcion_general": "Asiento corregido",
        "asientos": [
            {
                "cuenta_puc": "5135",
                "nombre_cuenta": "Servicios",
                "tipo_movimiento": "debito",
                "valor": 500000,
            },
            {
                "cuenta_puc": "2205",
                "nombre_cuenta": "Proveedores",
                "tipo_movimiento": "credito",
                "valor": 500000,
            },
        ],
        "total_debitos": 500000,
        "total_creditos": 500000,
    }

    feedback = "Debitos y creditos no cuadran. Corrige la partida doble."
    state = _base_state(correction_feedback=feedback, retry_count=1)

    with (
        patch("app.agents.contador_agent.get_llm_client", return_value=mock_llm),
        patch(
            "app.services.rag_service.get_rag_service",
            side_effect=Exception("rag unavailable"),
        ),
    ):
        result = contador_node(state)

    kwargs = mock_llm.extract_contador_output.call_args.kwargs
    assert kwargs["correction_feedback"] == feedback
    assert result["correction_feedback"] is None


def test_contador_example_unbalanced_output_triggers_validation_retry() -> None:
    """Example: an unbalanced output should produce correction feedback, not crash."""
    unbalanced = {
        "fecha_registro": "2026-03-01",
        "tipo_documento": "factura",
        "descripcion_general": "Asiento invalido",
        "asientos": [
            {
                "cuenta_puc": "5135",
                "nombre_cuenta": "Servicios",
                "tipo_movimiento": "debito",
                "valor": 1000000,
            },
            {
                "cuenta_puc": "2205",
                "nombre_cuenta": "Proveedores",
                "tipo_movimiento": "credito",
                "valor": 900000,
            },
        ],
        "total_debitos": 1000000,
        "total_creditos": 900000,
    }

    state = _base_state(contador_output=unbalanced, interpreted_data=unbalanced)
    result = validate_contador_output_node(state)

    assert result["error"] is None
    assert result["correction_feedback"] is not None
    assert result["retry_count"] == 1
    assert len(result["validation_history"]) == 1
    assert result["validation_history"][0]["is_valid"] is False


def test_contador_prompt_includes_transaction_fields_and_rag_context() -> None:
    """Example: Gemini prompt should include tx details and provided RAG snippets."""
    client = LLMClient.__new__(LLMClient)

    captured: list[str] = []
    mock_response = MagicMock()
    mock_response.model_dump.return_value = {
        "fecha_registro": "2026-03-01",
        "tipo_documento": "factura",
        "descripcion_general": "ok",
        "asientos": [],
        "total_debitos": 0,
        "total_creditos": 0,
    }

    def fake_invoke(schema_cls, prompt):  # noqa: ANN001
        captured.append(prompt)
        return mock_response

    client._invoke = fake_invoke  # type: ignore[method-assign]

    tx = [
        {
            "fecha": "2026-03-01",
            "nit_emisor": "900123456",
            "total": 1190000,
            "descripcion": "Consultoria",
        }
    ]
    rag = [{"content": "PUC 5135: Servicios profesionales."}]

    client.extract_contador_output(raw_transactions=tx, rag_context=rag)

    prompt_text = captured[0]
    assert "900123456" in prompt_text
    assert "1190000" in prompt_text
    assert "Contexto normativo/RAG" in prompt_text
    assert "PUC 5135" in prompt_text


def test_contador_prompt_uses_fallback_when_rag_context_is_empty() -> None:
    """Example: prompt should still be coherent if no RAG context is available."""
    client = LLMClient.__new__(LLMClient)

    captured: list[str] = []
    mock_response = MagicMock()
    mock_response.model_dump.return_value = {
        "fecha_registro": "2026-03-01",
        "tipo_documento": "factura",
        "descripcion_general": "ok",
        "asientos": [],
        "total_debitos": 0,
        "total_creditos": 0,
    }

    def fake_invoke(schema_cls, prompt):  # noqa: ANN001
        captured.append(prompt)
        return mock_response

    client._invoke = fake_invoke  # type: ignore[method-assign]

    tx = [
        {
            "fecha": "2026-03-01",
            "nit_emisor": "900123456",
            "total": 1190000,
            "descripcion": "Consultoria",
        }
    ]

    client.extract_contador_output(raw_transactions=tx, rag_context=[])
    prompt_text = captured[0]

    assert "Sin contexto normativo adicional." in prompt_text
