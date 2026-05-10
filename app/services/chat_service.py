"""
Reportero Chatbot service.

Orchestrates: intent classification → data gathering → response generation.
Reuses the existing reportero builders and db_service for financial queries.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import func

from app.models.chat_schemas import (
    ChatReasoningStep,
    ChatRequest,
    ChatResponse,
    FinancialDataCard,
    SessionSummary,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ID generation (same pattern as db_service._generate_id)
# ---------------------------------------------------------------------------


def _gen_id(prefix: str) -> str:
    ts = int(datetime.now(timezone.utc).timestamp())
    return f"{prefix}{ts}_{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_INTENT_PROMPT = """\
Eres un clasificador de intenciones para un chatbot contable colombiano.

Dada la pregunta del usuario (y opcionalmente historial reciente), clasifica la intención.

Intenciones posibles:
- balance       → Balance General / Estado de Situación Financiera / activos / pasivos / patrimonio
- pnl           → Estado de Resultados / P&L / ingresos vs gastos / utilidad / pérdidas y ganancias
- cashflow      → Flujo de Caja / efectivo disponible / movimientos de caja
- iva           → IVA generado, descontable, a pagar
- withholdings  → Retenciones (Retefuente, ReteICA)
- analysis      → Análisis financiero integral. USA ESTA INTENCIÓN cuando pregunten por:
                   * Proyecciones / predicciones / estimaciones futuras
                   * Pronóstico de ingresos, gastos, utilidad o flujo de caja
                   * Análisis completo / salud financiera / diagnóstico
                   * Insights / recomendaciones / alertas financieras
                   * Tendencias / cómo va la empresa / hacia dónde va
                   * Anomalías / movimientos inusuales
                   * Cualquier combinación de ratios + proyecciones + recomendaciones
- top_accounts  → Cuentas con mayor movimiento
- ratios        → Ratios financieros / indicadores / KPIs. USA ESTA INTENCIÓN cuando pregunten por:
                   * Liquidez / razón corriente / prueba ácida
                   * Rentabilidad / margen neto / ROA / ROE
                   * Endeudamiento / apalancamiento / deuda sobre patrimonio
                   * Eficiencia operativa / rotación de activos / rotaciones
                   * Indicadores clave de desempeño
- dashboard     → Resumen rápido / overview general
- explanation   → Explicar un concepto contable/tributario (usa RAG normativo)
- general_question → Pregunta general que no requiere datos de BD

REGLAS DE CLASIFICACIÓN:
- Si la pregunta menciona "proyecciones", "predicciones", "futuro", "próximos meses", "estimación" → analysis
- Si la pregunta menciona "ratios", "indicadores", "liquidez", "rentabilidad", "endeudamiento", "rotación" → ratios
- Si la pregunta pide "análisis", "insights", "recomendaciones", "alertas", "salud financiera" → analysis
- Si la pregunta menciona varias cosas, elige la intención PRINCIPAL.

Historial reciente:
{history_text}

Pregunta del usuario:
{message}
"""

_CHATBOT_SYSTEM_PROMPT = """\
Eres un asistente financiero experto en contabilidad colombiana (NIIF, PUC, Estatuto Tributario).
Respondes de forma clara, concisa y amigable en español.

## Marco normativo
- NIIF adoptadas en Colombia, PUC (Decreto 2650/1993), Estatuto Tributario
- Clase 1=Activos, 2=Pasivos, 3=Patrimonio, 4=Ingresos, 5=Gastos, 6=CMV

## Ratios que conoces
- Razón Corriente = Activos Corrientes / Pasivos Corrientes (ideal > 1.5)
- Prueba Ácida = (AC - Inventarios) / PC (ideal > 1.0)
- Margen Neto = Utilidad / Ingresos × 100
- ROA = Utilidad / Activos × 100
- Endeudamiento = Pasivos / Activos (alerta si > 0.7)

## Reglas
- Usa cifras concretas cuando tengas datos. Formatea moneda como COP.
- Cita artículos normativos cuando sea relevante.
- Si no hay datos suficientes, dilo explícitamente.
- Responde en español con formato Markdown.
- Sé conversacional pero preciso.
"""

_RESPONSE_PROMPT = """\
{system_prompt}

{history_section}

=== DATOS FINANCIEROS ===
{financial_data}

=== CONTEXTO NORMATIVO (RAG) ===
{rag_context}

=== PREGUNTA DEL USUARIO ===
{message}

Responde de forma conversacional, citando las cifras relevantes de los datos proporcionados.
"""


# ---------------------------------------------------------------------------
# Session & message persistence
# ---------------------------------------------------------------------------


def _get_db():
    from app.core.database import SessionLocal

    return SessionLocal()


def create_session(company_nit: str | None, title: str | None = None) -> str:
    from app.models.database import ChatSession

    db = _get_db()
    try:
        session_id = _gen_id("chat_")
        session = ChatSession(id=session_id, company_nit=company_nit, title=title)
        db.add(session)
        db.commit()
        return session_id
    finally:
        db.close()


def save_message(
    session_id: str,
    role: str,
    content: str,
    *,
    data_cards: list[dict] | None = None,
    intent: str | None = None,
    sources: list[str] | None = None,
    reasoning: list[dict] | None = None,
) -> str:
    from app.models.database import ChatMessageRecord, ChatSession

    db = _get_db()
    try:
        msg_id = _gen_id("msg_")
        msg = ChatMessageRecord(
            id=msg_id,
            session_id=session_id,
            role=role,
            content=content,
            data_cards=data_cards,
            intent=intent,
            sources=sources,
            reasoning=reasoning,
        )
        db.add(msg)
        # Always bump updated_at; set title from first user message
        session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
        if session:
            session.updated_at = func.now()
            if role == "user" and not session.title:
                session.title = content[:100]
        db.commit()
        return msg_id
    finally:
        db.close()


def load_recent_messages(session_id: str, limit: int = 10) -> list[dict]:
    from app.models.database import ChatMessageRecord

    db = _get_db()
    try:
        rows = (
            db.query(ChatMessageRecord)
            .filter(ChatMessageRecord.session_id == session_id)
            .order_by(ChatMessageRecord.created_at.desc())
            .limit(limit)
            .all()
        )
        rows.reverse()  # oldest first
        return [{"role": r.role, "content": r.content} for r in rows]
    finally:
        db.close()


def list_sessions(company_nit: str | None = None) -> list[SessionSummary]:
    from sqlalchemy import func as sa_func
    from app.models.database import ChatSession, ChatMessageRecord

    db = _get_db()
    try:
        q = (
            db.query(
                ChatSession,
                sa_func.count(ChatMessageRecord.id).label("msg_count"),
            )
            .outerjoin(ChatMessageRecord)
            .group_by(ChatSession.id)
        )
        if company_nit:
            q = q.filter(ChatSession.company_nit == company_nit)
        q = q.order_by(ChatSession.updated_at.desc())
        results = q.limit(50).all()
        return [
            SessionSummary(
                id=s.id,
                title=s.title,
                company_nit=s.company_nit,
                message_count=cnt,
                created_at=s.created_at.isoformat() if s.created_at else None,
                updated_at=s.updated_at.isoformat() if s.updated_at else None,
            )
            for s, cnt in results
        ]
    finally:
        db.close()


def get_session_messages(session_id: str) -> list[dict]:
    from app.models.database import ChatMessageRecord

    db = _get_db()
    try:
        rows = (
            db.query(ChatMessageRecord)
            .filter(ChatMessageRecord.session_id == session_id)
            .order_by(ChatMessageRecord.created_at.asc())
            .all()
        )
        return [
            {
                "id": r.id,
                "role": r.role,
                "content": r.content,
                "data_cards": r.data_cards,
                "intent": r.intent,
                "sources": r.sources,
                "reasoning": r.reasoning,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    finally:
        db.close()


def delete_session(session_id: str) -> bool:
    from app.models.database import ChatSession

    db = _get_db()
    try:
        session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
        if not session:
            return False
        db.delete(session)
        db.commit()
        return True
    finally:
        db.close()


def _session_exists(session_id: str) -> bool:
    from app.models.database import ChatSession

    db = _get_db()
    try:
        return (
            db.query(ChatSession).filter(ChatSession.id == session_id).first()
            is not None
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Intent classification
# ---------------------------------------------------------------------------


def classify_intent(message: str, history: list[dict]) -> dict:
    from app.core.llm_client import get_llm_client

    history_text = ""
    if history:
        recent = history[-6:]  # last 3 turns
        lines = [f"{m['role']}: {m['content'][:200]}" for m in recent]
        history_text = "\n".join(lines)

    prompt = _INTENT_PROMPT.format(
        history_text=history_text or "(sin historial)", message=message
    )

    # Intents that ALWAYS require financial data from the DB, regardless of
    # what the LLM decides for needs_data.  This prevents the chatbot from
    # replying "I don't have data" when the data is right there.
    _DATA_REQUIRED_INTENTS = frozenset(
        {
            "balance",
            "pnl",
            "cashflow",
            "iva",
            "withholdings",
            "analysis",
            "ratios",
            "top_accounts",
            "dashboard",
        }
    )

    try:
        llm = get_llm_client()
        result = llm.classify_chat_intent(prompt)
        # Override needs_data for intents that always require DB data
        if result.get("intent") in _DATA_REQUIRED_INTENTS:
            result["needs_data"] = True
        return result
    except Exception as exc:
        logger.warning(
            "Intent classification failed (%s), defaulting to general_question", exc
        )
        return {
            "intent": "general_question",
            "needs_data": False,
            "rag_query": None,
            "explanation": f"Classification error: {exc}",
        }


# ---------------------------------------------------------------------------
# Data gathering (reusing reportero builders)
# ---------------------------------------------------------------------------


def _build_params(request: ChatRequest) -> dict:
    params: dict[str, Any] = {}
    if request.start_date:
        params["start_date"] = request.start_date.isoformat()
    if request.end_date:
        params["end_date"] = request.end_date.isoformat()
    if request.company_nit:
        params["company_nit"] = request.company_nit
    return params


def gather_financial_data(
    intent: dict,
    request: ChatRequest,
) -> tuple[dict | None, list[FinancialDataCard]]:
    """Call the appropriate reportero builder. Returns (raw_data, cards)."""
    if not intent.get("needs_data"):
        return None, []

    intent_name = intent["intent"]
    params = _build_params(request)

    from app.core.database import SessionLocal
    from app.services import db_service

    db = SessionLocal()
    try:
        data: dict | None = None
        card_type = intent_name
        title = ""

        if intent_name == "balance":
            from app.services.report_builders import build_balance

            data = build_balance(db, params, db_service)
            title = "Balance General"

        elif intent_name == "pnl":
            from app.services.report_builders import build_pnl

            data = build_pnl(db, params, db_service)
            title = "Estado de Resultados"

        elif intent_name == "cashflow":
            from app.services.report_builders import build_cashflow

            data = build_cashflow(db, params, db_service)
            title = "Flujo de Caja"

        elif intent_name == "iva":
            from app.services.report_builders import build_iva

            data = build_iva(db, params, db_service)
            title = "Reporte IVA"

        elif intent_name == "withholdings":
            from app.services.report_builders import build_withholdings

            data = build_withholdings(db, params, db_service)
            title = "Retenciones"

        elif intent_name == "analysis":
            from app.services.report_builders import build_analysis

            data = build_analysis(db, params, db_service)
            title = "Análisis Financiero"

        elif intent_name == "top_accounts":
            top_debit = db_service.get_top_accounts(
                db,
                request.start_date,
                request.end_date,
                by="debit",
                limit=5,
                company_nit=request.company_nit,
            )
            top_credit = db_service.get_top_accounts(
                db,
                request.start_date,
                request.end_date,
                by="credit",
                limit=5,
                company_nit=request.company_nit,
            )
            data = {"top_debit": top_debit, "top_credit": top_credit}
            title = "Cuentas con Mayor Movimiento"

        elif intent_name == "ratios":
            from app.services.report_builders._base import _compute_ratios

            balance = db_service.get_balance_sheet(db, company_nit=request.company_nit)
            ledger = db_service.get_general_ledger(db, company_nit=request.company_nit)
            data = _compute_ratios(ledger, balance)
            title = "Ratios Financieros"

        elif intent_name == "dashboard":
            balance = db_service.get_balance_sheet(db, company_nit=request.company_nit)
            txn_counts = db_service.get_transaction_counts_by_status(
                db, company_nit=request.company_nit
            )
            data = {**balance, "transacciones_por_estado": txn_counts}
            title = "Resumen General"

        cards: list[FinancialDataCard] = []
        if data:
            cards.append(FinancialDataCard(card_type=card_type, title=title, data=data))

        return data, cards

    except Exception as exc:
        logger.error("gather_financial_data failed for intent=%s: %s", intent_name, exc)
        return None, []
    finally:
        db.close()


# ---------------------------------------------------------------------------
# RAG context
# ---------------------------------------------------------------------------


def fetch_rag_context(query: str | None) -> str:
    if not query:
        return ""
    try:
        from app.agents.reportero_agent import _fetch_rag_context_text

        return _fetch_rag_context_text(query, n_results=5)
    except Exception as exc:
        logger.warning("RAG fetch failed (non-fatal): %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Response prompt builder
# ---------------------------------------------------------------------------


def _build_response_prompt(
    message: str,
    history: list[dict],
    financial_data: dict | None,
    rag_context: str,
) -> str:
    history_section = ""
    if history:
        recent = history[-6:]
        lines = [
            f"{'Usuario' if m['role'] == 'user' else 'Asistente'}: {m['content'][:300]}"
            for m in recent
        ]
        history_section = "=== HISTORIAL DE CONVERSACIÓN ===\n" + "\n".join(lines)

    data_text = "Sin datos financieros para esta consulta."
    if financial_data:
        data_text = json.dumps(
            financial_data, ensure_ascii=False, indent=2, default=str
        )

    return _RESPONSE_PROMPT.format(
        system_prompt=_CHATBOT_SYSTEM_PROMPT,
        history_section=history_section,
        financial_data=data_text,
        rag_context=rag_context or "Sin contexto normativo adicional.",
        message=message,
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _resolve_session(session_id: str | None, company_nit: str | None) -> str:
    """Return an existing session_id or create a new session."""
    if session_id and _session_exists(session_id):
        return session_id
    return create_session(company_nit)


# ---------------------------------------------------------------------------
# Streaming chat handler (yields SSE-ready dicts)
# ---------------------------------------------------------------------------


def _thinking_step(
    phase: str,
    label: str,
    detail: str | None = None,
    duration_ms: int | None = None,
    status: str = "done",
) -> dict:
    """Build a single reasoning trace step with current UTC timestamp."""
    from datetime import datetime, timezone

    return {
        "phase": phase,
        "label": label,
        "detail": detail,
        "duration_ms": duration_ms,
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _intent_label(intent_name: str) -> str:
    """Human-friendly label for an intent (used in reasoning detail)."""
    return {
        "balance": "Balance General",
        "pnl": "Estado de Resultados",
        "cashflow": "Flujo de Caja",
        "iva": "Reporte IVA",
        "withholdings": "Retenciones",
        "analysis": "Análisis Financiero",
        "top_accounts": "Cuentas con mayor movimiento",
        "ratios": "Ratios Financieros",
        "dashboard": "Resumen del Dashboard",
        "general_question": "Consulta general",
    }.get(intent_name, intent_name)


def handle_chat_stream(request: ChatRequest) -> Iterator[dict]:
    """Full pipeline: session → intent → data → stream → persist.

    Yields dicts with ``event`` and ``data`` keys suitable for SSE.
    Emits ``thinking`` events with the agent's step-by-step trace so the
    frontend can render a reasoning panel similar to OpenAI/Anthropic/Gemini.
    """
    import time

    from app.core.llm_client import get_llm_client

    # 1. Resolve or create session
    session_id = _resolve_session(request.session_id, request.company_nit)

    # 2. Persist user message
    save_message(session_id, "user", request.message)

    # 3. Load conversation memory
    history = load_recent_messages(session_id, limit=10)

    reasoning_steps: list[dict] = []

    def _emit(step: dict) -> dict:
        """Append step to trace and return the SSE event payload."""
        reasoning_steps.append(step)
        return {
            "event": "thinking",
            "data": json.dumps({"thinking": step}, ensure_ascii=False, default=str),
        }

    pipeline_start = time.perf_counter()

    # 4. Classify intent
    t0 = time.perf_counter()
    intent = classify_intent(request.message, history)
    intent_name = intent.get("intent", "general_question")
    needs_data = bool(intent.get("needs_data"))
    yield _emit(
        _thinking_step(
            phase="intent",
            label=f"Intención detectada: {_intent_label(intent_name)}",
            detail=f"intent={intent_name}, needs_data={needs_data}",
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
    )

    # 5. Show resolved parameters (NIT, fechas)
    params_summary_parts: list[str] = []
    if request.company_nit:
        params_summary_parts.append(f"NIT={request.company_nit}")
    if request.start_date:
        params_summary_parts.append(f"desde={request.start_date.isoformat()}")
    if request.end_date:
        params_summary_parts.append(f"hasta={request.end_date.isoformat()}")
    yield _emit(
        _thinking_step(
            phase="params",
            label="Parámetros aplicados",
            detail=", ".join(params_summary_parts) or "sin filtros adicionales",
        )
    )

    # 6. Gather financial data
    t0 = time.perf_counter()
    financial_data, data_cards = gather_financial_data(intent, request)
    yield _emit(
        _thinking_step(
            phase="gathering_data",
            label=(
                "Datos financieros recolectados"
                if needs_data
                else "Sin recolección de datos (consulta general)"
            ),
            detail=(
                f"tarjetas={len(data_cards)}"
                if needs_data
                else "no se requirieron datos"
            ),
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
    )

    # 7. Fetch RAG context
    t0 = time.perf_counter()
    rag_query = intent.get("rag_query")
    rag_context = fetch_rag_context(rag_query)
    yield _emit(
        _thinking_step(
            phase="rag",
            label="Contexto normativo (RAG)",
            detail=(
                f"consulta={rag_query!s} | fragmentos={len(rag_context.split('---')) if rag_context else 0}"
                if rag_query
                else "sin consulta normativa"
            ),
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
    )

    # 8. Build prompt and stream
    prompt = _build_response_prompt(
        request.message, history, financial_data, rag_context
    )
    llm = get_llm_client()
    yield _emit(
        _thinking_step(
            phase="generating",
            label="Generando respuesta",
            detail=f"modelo={getattr(llm, 'name', 'desconocido')}",
        )
    )

    response_chunks: list[str] = []
    generation_start = time.perf_counter()
    try:
        for token in llm.stream_chat_response(prompt):
            response_chunks.append(token)
            yield {
                "event": "token",
                "data": json.dumps({"content": token}, ensure_ascii=False),
            }
    except Exception as exc:
        logger.error("Chat stream error: %s", exc)
        error_msg = "Lo siento, hubo un error generando la respuesta."
        response_chunks = [error_msg]
        yield {
            "event": "token",
            "data": json.dumps({"content": error_msg}, ensure_ascii=False),
        }
    generation_ms = int((time.perf_counter() - generation_start) * 1000)
    full_response = "".join(response_chunks)

    # 9. Send structured data event
    sources: list[str] = []  # Streaming has no structured output for normative refs
    yield {
        "event": "data",
        "data": json.dumps(
            {
                "cards": [c.model_dump() for c in data_cards],
                "intent": intent_name,
                "sources": sources,
            },
            ensure_ascii=False,
            default=str,
        ),
    }

    # 10. Final reasoning step + total duration
    yield _emit(
        _thinking_step(
            phase="complete",
            label="Respuesta entregada",
            detail=f"tokens={len(response_chunks)} | generation_ms={generation_ms}",
            duration_ms=int((time.perf_counter() - pipeline_start) * 1000),
        )
    )

    # 11. Persist assistant message (including the reasoning trace)
    save_message(
        session_id,
        "assistant",
        full_response,
        data_cards=[c.model_dump() for c in data_cards] if data_cards else None,
        intent=intent_name,
        sources=sources or None,
        reasoning=reasoning_steps or None,
    )

    # 12. Done event
    yield {"event": "done", "data": json.dumps({"session_id": session_id})}


# ---------------------------------------------------------------------------
# Non-streaming handler (for E2E script and testing)
# ---------------------------------------------------------------------------


def handle_chat_message(request: ChatRequest) -> ChatResponse:
    """Synchronous (non-streaming) chat handler.

    Internally runs the same pipeline as the streaming handler but
    collects the full response before returning.
    """
    import time

    from app.core.llm_client import get_llm_client

    reasoning_steps: list[dict] = []
    pipeline_start = time.perf_counter()

    # Session
    session_id = _resolve_session(request.session_id, request.company_nit)

    save_message(session_id, "user", request.message)
    history = load_recent_messages(session_id, limit=10)

    # Intent
    t0 = time.perf_counter()
    intent = classify_intent(request.message, history)
    intent_name = intent.get("intent", "general_question")
    needs_data = bool(intent.get("needs_data"))
    reasoning_steps.append(
        _thinking_step(
            phase="intent",
            label=f"Intención detectada: {_intent_label(intent_name)}",
            detail=f"intent={intent_name}, needs_data={needs_data}",
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
    )

    # Params summary
    params_summary_parts: list[str] = []
    if request.company_nit:
        params_summary_parts.append(f"NIT={request.company_nit}")
    if request.start_date:
        params_summary_parts.append(f"desde={request.start_date.isoformat()}")
    if request.end_date:
        params_summary_parts.append(f"hasta={request.end_date.isoformat()}")
    reasoning_steps.append(
        _thinking_step(
            phase="params",
            label="Parámetros aplicados",
            detail=", ".join(params_summary_parts) or "sin filtros adicionales",
        )
    )

    # Data + RAG
    t0 = time.perf_counter()
    financial_data, data_cards = gather_financial_data(intent, request)
    reasoning_steps.append(
        _thinking_step(
            phase="gathering_data",
            label=(
                "Datos financieros recolectados"
                if needs_data
                else "Sin recolección de datos (consulta general)"
            ),
            detail=(
                f"tarjetas={len(data_cards)}"
                if needs_data
                else "no se requirieron datos"
            ),
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
    )

    t0 = time.perf_counter()
    rag_query = intent.get("rag_query")
    rag_context = fetch_rag_context(rag_query)
    reasoning_steps.append(
        _thinking_step(
            phase="rag",
            label="Contexto normativo (RAG)",
            detail=(
                f"consulta={rag_query!s} | fragmentos={len(rag_context.split('---')) if rag_context else 0}"
                if rag_query
                else "sin consulta normativa"
            ),
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
    )

    # Generate response (structured, non-streaming)
    prompt = _build_response_prompt(
        request.message, history, financial_data, rag_context
    )
    llm = get_llm_client()
    reasoning_steps.append(
        _thinking_step(
            phase="generating",
            label="Generando respuesta",
            detail=f"modelo={getattr(llm, 'name', 'desconocido')}",
        )
    )

    try:
        result = llm.generate_chat_response(prompt)
        reply = result.get("respuesta", "")
        sources = result.get("referencias_normativas", [])
    except Exception as exc:
        logger.error("Chat response generation failed: %s", exc)
        reply = f"Lo siento, hubo un error generando la respuesta: {exc}"
        sources = []

    reasoning_steps.append(
        _thinking_step(
            phase="complete",
            label="Respuesta entregada",
            detail=f"caracteres={len(reply)}",
            duration_ms=int((time.perf_counter() - pipeline_start) * 1000),
        )
    )

    # Persist (including reasoning trace)
    save_message(
        session_id,
        "assistant",
        reply,
        data_cards=[c.model_dump() for c in data_cards] if data_cards else None,
        intent=intent_name,
        sources=sources or None,
        reasoning=reasoning_steps or None,
    )

    return ChatResponse(
        reply=reply,
        session_id=session_id,
        data_cards=data_cards,
        intent_detected=intent_name,
        sources=sources,
        reasoning=[ChatReasoningStep(**step) for step in reasoning_steps],
    )
