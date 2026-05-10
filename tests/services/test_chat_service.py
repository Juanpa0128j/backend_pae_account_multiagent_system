"""Tests for the Reportero Chatbot service."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from app.models.chat_schemas import ChatRequest, ChatResponse, FinancialDataCard

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_MOCK_INTENT_BALANCE = {
    "intent": "balance",
    "needs_data": True,
    "rag_query": "NIIF balance general activos pasivos",
    "explanation": "User asked about balance sheet",
}

_MOCK_INTENT_GENERAL = {
    "intent": "general_question",
    "needs_data": False,
    "rag_query": None,
    "explanation": "General question, no data needed",
}

_MOCK_BALANCE_DATA = {
    "report_type": "balance_sheet",
    "activos": 120_000_000,
    "pasivos": 40_000_000,
    "patrimonio": 50_000_000,
    "utilidad_neta": 20_000_000,
    "patrimonio_total": 80_000_000,
    "cuadre": True,
}

_MOCK_LLM_RESPONSE = {
    "respuesta": "Tu empresa tiene activos por $120M COP.",
    "puntos_clave": ["Activos: $120M", "Cuadre verificado"],
    "referencias_normativas": ["Art. 383 ET"],
}


def _make_request(**kwargs) -> ChatRequest:
    defaults = {"message": "¿Cuál es mi balance general?", "company_nit": "800999888-2"}
    defaults.update(kwargs)
    return ChatRequest(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestClassifyIntent:
    """Tests for intent classification."""

    @patch("app.core.llm_client.get_llm_client")
    def test_classify_intent_returns_valid_intent(self, mock_get_llm):
        from app.services.chat_service import classify_intent

        mock_client = MagicMock()
        mock_client.classify_chat_intent.return_value = _MOCK_INTENT_BALANCE
        mock_get_llm.return_value = mock_client

        result = classify_intent("¿Cuál es mi balance?", [])

        assert result["intent"] == "balance"
        assert result["needs_data"] is True
        mock_client.classify_chat_intent.assert_called_once()

    @patch("app.core.llm_client.get_llm_client")
    def test_classify_intent_falls_back_on_error(self, mock_get_llm):
        from app.services.chat_service import classify_intent

        mock_client = MagicMock()
        mock_client.classify_chat_intent.side_effect = RuntimeError("LLM down")
        mock_get_llm.return_value = mock_client

        result = classify_intent("test", [])

        assert result["intent"] == "general_question"
        assert result["needs_data"] is False


class TestGatherFinancialData:
    """Tests for data gathering from reportero builders."""

    @patch("app.core.database.SessionLocal")
    @patch(
        "app.services.report_builders.build_balance", return_value=_MOCK_BALANCE_DATA
    )
    def test_gather_balance_data(self, mock_build, mock_session_cls):
        from app.services.chat_service import gather_financial_data

        mock_session_cls.return_value = MagicMock()
        request = _make_request()

        data, cards = gather_financial_data(_MOCK_INTENT_BALANCE, request)

        assert data is not None
        assert data["activos"] == 120_000_000
        assert len(cards) == 1
        assert cards[0].card_type == "balance"
        assert cards[0].title == "Balance General"

    def test_gather_no_data_for_general_question(self):
        from app.services.chat_service import gather_financial_data

        request = _make_request()
        data, cards = gather_financial_data(_MOCK_INTENT_GENERAL, request)

        assert data is None
        assert cards == []


class TestHandleChatMessage:
    """Tests for the non-streaming chat handler."""

    @patch("app.services.chat_service._session_exists", return_value=False)
    @patch("app.services.chat_service.create_session", return_value="chat_test_123")
    @patch("app.services.chat_service.save_message", return_value="msg_test_1")
    @patch("app.services.chat_service.load_recent_messages", return_value=[])
    @patch(
        "app.services.chat_service.classify_intent", return_value=_MOCK_INTENT_BALANCE
    )
    @patch("app.services.chat_service.gather_financial_data")
    @patch("app.services.chat_service.fetch_rag_context", return_value="")
    @patch("app.core.llm_client.get_llm_client")
    def test_full_chat_flow(
        self,
        mock_get_llm,
        mock_rag,
        mock_gather,
        mock_classify,
        mock_load,
        mock_save,
        mock_create,
        mock_exists,
    ):
        from app.services.chat_service import handle_chat_message

        mock_gather.return_value = (
            _MOCK_BALANCE_DATA,
            [
                FinancialDataCard(
                    card_type="balance",
                    title="Balance General",
                    data=_MOCK_BALANCE_DATA,
                )
            ],
        )
        mock_client = MagicMock()
        mock_client.generate_chat_response.return_value = _MOCK_LLM_RESPONSE
        mock_get_llm.return_value = mock_client

        request = _make_request()
        response = handle_chat_message(request)

        assert isinstance(response, ChatResponse)
        assert response.session_id == "chat_test_123"
        assert response.reply == "Tu empresa tiene activos por $120M COP."
        assert response.intent_detected == "balance"
        assert len(response.data_cards) == 1
        assert response.sources == ["Art. 383 ET"]

    @patch("app.services.chat_service._session_exists", return_value=False)
    @patch("app.services.chat_service.create_session", return_value="chat_test_456")
    @patch("app.services.chat_service.save_message", return_value="msg_test_2")
    @patch("app.services.chat_service.load_recent_messages", return_value=[])
    @patch(
        "app.services.chat_service.classify_intent", return_value=_MOCK_INTENT_GENERAL
    )
    @patch("app.services.chat_service.gather_financial_data", return_value=(None, []))
    @patch("app.services.chat_service.fetch_rag_context", return_value="")
    @patch("app.core.llm_client.get_llm_client")
    def test_general_question_no_data_cards(
        self,
        mock_get_llm,
        mock_rag,
        mock_gather,
        mock_classify,
        mock_load,
        mock_save,
        mock_create,
        mock_exists,
    ):
        from app.services.chat_service import handle_chat_message

        mock_client = MagicMock()
        mock_client.generate_chat_response.return_value = {
            "respuesta": "El IVA general en Colombia es del 19%.",
            "puntos_clave": [],
            "referencias_normativas": ["Art. 468 ET"],
        }
        mock_get_llm.return_value = mock_client

        request = _make_request(message="¿Cuál es la tarifa de IVA?")
        response = handle_chat_message(request)

        assert response.intent_detected == "general_question"
        assert response.data_cards == []
        assert "19%" in response.reply


class TestHandleChatStream:
    """Tests for the streaming chat handler."""

    @patch("app.services.chat_service._session_exists", return_value=False)
    @patch("app.services.chat_service.create_session", return_value="chat_stream_1")
    @patch("app.services.chat_service.save_message", return_value="msg_s1")
    @patch("app.services.chat_service.load_recent_messages", return_value=[])
    @patch(
        "app.services.chat_service.classify_intent", return_value=_MOCK_INTENT_GENERAL
    )
    @patch("app.services.chat_service.gather_financial_data", return_value=(None, []))
    @patch("app.services.chat_service.fetch_rag_context", return_value="")
    @patch("app.core.llm_client.get_llm_client")
    def test_stream_yields_events(
        self,
        mock_get_llm,
        mock_rag,
        mock_gather,
        mock_classify,
        mock_load,
        mock_save,
        mock_create,
        mock_exists,
    ):
        from app.services.chat_service import handle_chat_stream

        mock_client = MagicMock()
        mock_client.stream_chat_response.return_value = iter(["Hola", " mundo"])
        mock_get_llm.return_value = mock_client

        request = _make_request(message="Hola")
        events = list(handle_chat_stream(request))

        # Should have: 2 tokens + 1 data + 1 done + thinking events
        event_types = [e["event"] for e in events]
        assert event_types.count("token") == 2
        assert "data" in event_types
        assert "done" in event_types
        # Reasoning panel: at least intent, params, gathering_data, rag,
        # generating, complete (6 phases)
        assert event_types.count("thinking") >= 6

        # Verify done event has session_id
        done_event = [e for e in events if e["event"] == "done"][0]
        assert "chat_stream_1" in done_event["data"]

    @patch("app.services.chat_service._session_exists", return_value=False)
    @patch("app.services.chat_service.create_session", return_value="chat_stream_2")
    @patch("app.services.chat_service.save_message", return_value="msg_s2")
    @patch("app.services.chat_service.load_recent_messages", return_value=[])
    @patch(
        "app.services.chat_service.classify_intent", return_value=_MOCK_INTENT_GENERAL
    )
    @patch("app.services.chat_service.gather_financial_data", return_value=(None, []))
    @patch("app.services.chat_service.fetch_rag_context", return_value="")
    @patch("app.core.llm_client.get_llm_client")
    def test_stream_emits_thinking_events_in_order(
        self,
        mock_get_llm,
        mock_rag,
        mock_gather,
        mock_classify,
        mock_load,
        mock_save,
        mock_create,
        mock_exists,
    ):
        """Thinking events must appear in the canonical reasoning order."""
        import json

        from app.services.chat_service import handle_chat_stream

        mock_client = MagicMock()
        mock_client.stream_chat_response.return_value = iter(["ok"])
        mock_get_llm.return_value = mock_client

        request = _make_request(message="Hola", company_nit="900111111")
        events = list(handle_chat_stream(request))

        thinking_phases = [
            json.loads(e["data"])["thinking"]["phase"]
            for e in events
            if e["event"] == "thinking"
        ]
        # Canonical order; "complete" is always last
        assert thinking_phases[:5] == [
            "intent",
            "params",
            "gathering_data",
            "rag",
            "generating",
        ]
        assert thinking_phases[-1] == "complete"

        # Each thinking step must include a label
        thinking_payloads = [
            json.loads(e["data"])["thinking"]
            for e in events
            if e["event"] == "thinking"
        ]
        for step in thinking_payloads:
            assert isinstance(step.get("label"), str) and step["label"]

        # `params` step should reflect the company_nit forwarded
        params_step = next(s for s in thinking_payloads if s["phase"] == "params")
        assert "900111111" in (params_step.get("detail") or "")

        # save_message should have been invoked with reasoning kwarg populated
        save_calls = mock_save.call_args_list
        assistant_call = next(
            c for c in save_calls if c.args and c.args[1] == "assistant"
        )
        assert assistant_call.kwargs.get("reasoning")
        assert len(assistant_call.kwargs["reasoning"]) >= 6
