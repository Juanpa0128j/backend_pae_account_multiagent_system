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


def test_contador_example_unbalanced_output_warns_without_retry() -> None:
    """Unbalanced debits/credits is now a warning — no retry triggered."""
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

    with patch("app.agents.validation_rules.SessionLocal"):
        state = _base_state(contador_output=unbalanced, interpreted_data=unbalanced)
        result = validate_contador_output_node(state)

    assert result["error"] is None
    assert result["correction_feedback"] is None
    assert result["retry_count"] == 0
    assert len(result["validation_history"]) == 1
    assert result["validation_history"][0]["is_valid"] is True


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


def test_exhausted_validation_user_message_has_clean_bullets_not_raw_dicts() -> None:
    """When schema validation is exhausted, user_message_es should show only
    the error msg per bullet point, never raw Pydantic dict traces."""
    invalid = {
        "fecha_registro": "2026-03-01",
        "tipo_documento": "factura",
        "descripcion_general": "Asiento invalido",
        "asientos": [],
        "total_debitos": 0,
        "total_creditos": 0,
    }

    state = _base_state(
        contador_output=invalid, interpreted_data=invalid, retry_count=3
    )
    result = validate_contador_output_node(state)

    assert result.get("giveup_record") is not None
    findings = result["giveup_record"]["last_findings"]
    assert len(findings) > 0
    user_msg = findings[0]["user_message_es"]

    # Must NOT contain raw dict artifacts
    assert "'loc':" not in user_msg
    assert "'type':" not in user_msg
    assert "'input':" not in user_msg

    # Should be formatted as bullet points (lines starting with "-")
    assert any(line.strip().startswith("-") for line in user_msg.splitlines())


def test_multi_tx_loop_calls_extract_per_movement() -> None:
    """When raw_transactions has more than one entry the contador must call the
    LLM once per movement and merge their asientos. Guards against the bug where
    a single LLM call returned one asiento pair and persist cloned it across
    every pending transaction.
    """
    raw_txs = [
        {
            "fecha": "2026-01-04",
            "descripcion": "ABONO INTERESES AHORROS",
            "total": 103.24,
            "bank_direction": "entrada",
        },
        {
            "fecha": "2026-01-05",
            "descripcion": "PAGO DE TERC MADECENTRO COLO",
            "total": 2353292.00,
            "bank_direction": "salida",
        },
        {
            "fecha": "2026-01-06",
            "descripcion": "IMPTO GOBIERNO 4X1000",
            "total": 1397.20,
            "bank_direction": "salida",
        },
    ]
    per_tx_outputs = [
        {
            "fecha_registro": "2026-01-04",
            "tipo_documento": "extracto",
            "descripcion_general": "Abono intereses",
            "asientos": [
                {
                    "cuenta_puc": "111005",
                    "nombre_cuenta": "Bancos",
                    "tipo_movimiento": "debito",
                    "valor": "103.24",
                    "descripcion": "Abono intereses banco",
                },
                {
                    "cuenta_puc": "4210",
                    "nombre_cuenta": "Ingresos financieros",
                    "tipo_movimiento": "credito",
                    "valor": "103.24",
                    "descripcion": "Ingreso intereses",
                },
            ],
            "total_debitos": "103.24",
            "total_creditos": "103.24",
        },
        {
            "fecha_registro": "2026-01-05",
            "tipo_documento": "extracto",
            "descripcion_general": "Pago a terceros",
            "asientos": [
                {
                    "cuenta_puc": "220505",
                    "nombre_cuenta": "Cuentas por pagar",
                    "tipo_movimiento": "debito",
                    "valor": "2353292.00",
                    "descripcion": "Cancelación CxP Madecentro",
                },
                {
                    "cuenta_puc": "111005",
                    "nombre_cuenta": "Bancos",
                    "tipo_movimiento": "credito",
                    "valor": "2353292.00",
                    "descripcion": "Salida bancaria",
                },
            ],
            "total_debitos": "2353292.00",
            "total_creditos": "2353292.00",
        },
        {
            "fecha_registro": "2026-01-06",
            "tipo_documento": "extracto",
            "descripcion_general": "GMF",
            "asientos": [
                {
                    "cuenta_puc": "530525",
                    "nombre_cuenta": "Gastos bancarios",
                    "tipo_movimiento": "debito",
                    "valor": "1397.20",
                    "descripcion": "GMF 4x1000",
                },
                {
                    "cuenta_puc": "111005",
                    "nombre_cuenta": "Bancos",
                    "tipo_movimiento": "credito",
                    "valor": "1397.20",
                    "descripcion": "Salida bancaria por GMF",
                },
            ],
            "total_debitos": "1397.20",
            "total_creditos": "1397.20",
        },
    ]

    mock_llm = MagicMock()
    mock_llm.extract_contador_output.side_effect = per_tx_outputs

    with (
        patch("app.agents.contador_agent.get_llm_client", return_value=mock_llm),
        patch(
            "app.services.rag_service.get_rag_service",
            side_effect=Exception("rag unavailable"),
        ),
    ):
        state = _base_state(raw_transactions=raw_txs)
        state["interpreted_data"] = {"doc_type": "extracto_bancario"}
        result = contador_node(state)

    assert result["error"] is None
    assert mock_llm.extract_contador_output.call_count == 3

    asientos = result["contador_output"]["asientos"]
    # 3 movements × 2 asientos each = 6 entries, all distinct (no clones).
    assert len(asientos) == 6
    pucs = [a["cuenta_puc"] for a in asientos]
    assert pucs.count("111005") == 3  # banco aparece en cada movement
    assert "4210" in pucs
    assert "220505" in pucs
    assert "530525" in pucs

    from decimal import Decimal as _D

    assert _D(result["contador_output"]["total_debitos"]) == _D("2354792.44")
    assert _D(result["contador_output"]["total_creditos"]) == _D("2354792.44")
