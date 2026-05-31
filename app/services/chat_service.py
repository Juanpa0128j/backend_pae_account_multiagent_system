"""
Reportero Chatbot service.

Orchestrates: intent classification → data gathering → response generation.
Reuses the existing reportero builders and db_service for financial queries.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timezone
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
- Si la pregunta menciona "IVA", "iva generado", "iva descontable", "iva a pagar" → iva (NUNCA general_question)
- Si la pregunta menciona "retención", "retefuente", "reteica", "retenciones" → withholdings (NUNCA general_question)
- Si la pregunta menciona "impuestos a pagar", "qué impuestos debo", "impuestos pendientes" Y combina IVA+retenciones → analysis (incluye ambas categorías)
- general_question SOLO para preguntas conceptuales sin datos del usuario (ej. "qué es el IVA"). Si la pregunta pide CIFRAS o ESTADO de impuestos → usa iva / withholdings / analysis.
- Si la pregunta menciona varias cosas, elige la intención PRINCIPAL.

EXTRACCIÓN DE PERÍODO (period_start / period_end):
- Hoy es {today}. Resuelve meses/años relativos contra esta fecha.
- Si el usuario nombra un período, devuelve period_start y period_end en ISO YYYY-MM-DD:
  * "balance de diciembre 2025" → period_start=2025-12-01, period_end=2025-12-31
  * "enero" (sin año) → usa el año más reciente cuyo enero ya ocurrió o está en curso respecto a hoy
  * "primer trimestre 2026" → period_start=2026-01-01, period_end=2026-03-31
  * "el año pasado" → period_start=AAAA-01-01, period_end=AAAA-12-31 del año anterior
- Si NO menciona ningún período, deja period_start y period_end en null (se usará el más reciente disponible).
- Un balance es un corte a fecha: para "balance de diciembre" lo relevante es period_end (último día del mes).

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

## Tipo de empresa (Vía A vs Vía B)
- **Vía A (build_from_scratch):** la empresa cargó documentos fuente (facturas,
  extractos, declaraciones). Tienes asientos contables individuales (libro
  diario) y puedes hacer drill-down a cada movimiento.
- **Vía B (work_with_existing):** la empresa cargó estados financieros ya
  consolidados (balance general, estado de resultados, libro auxiliar). NO
  tienes asientos individuales; responde con los totales del estado disponible
  y aclara que no hay detalle movimiento a movimiento.
- El contexto inyectado indica cuál aplica (campo `pathway`). Si en Vía B te
  piden algo que sólo existe en Vía A (IVA por factura, retenciones por
  movimiento, conteos de transacciones procesadas), dilo explícitamente:
  "Esta empresa cargó estados financieros agregados; para ver el detalle por
  movimiento haría falta cargar los documentos fuente (Vía A)."

## Período de las cifras
- Los datos traen un campo `period_end` (y a veces `period_start`): indica SIEMPRE
  a qué fecha/período corresponden las cifras que reportas.
- NUNCA presentes cifras de un período como si fueran de otro. Si el usuario pide
  un mes y los datos son de otro, acláralo.
- Si recibes una tarjeta `period_not_found`, NO inventes ni reutilices otro
  período: dile al usuario que ese período no está cargado y lista los disponibles
  (`available_periods`).

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
        history_text=history_text or "(sin historial)",
        message=message,
        today=datetime.now(timezone.utc).date().isoformat(),
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


def _parse_iso_date(value: Any) -> date | None:
    """Parse an ISO ``YYYY-MM-DD`` string to ``date``; tolerate junk → None."""
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _build_params(request: ChatRequest, intent: dict | None = None) -> dict:
    """Build reportero params. Explicit request dates win; otherwise fall back
    to the period the intent classifier extracted from the message text."""
    params: dict[str, Any] = {}
    start = request.start_date or _parse_iso_date((intent or {}).get("period_start"))
    end = request.end_date or _parse_iso_date((intent or {}).get("period_end"))
    if start:
        params["start_date"] = start.isoformat()
    if end:
        params["end_date"] = end.isoformat()
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
    params = _build_params(request, intent)

    from app.core.database import SessionLocal
    from app.services import db_service

    db = SessionLocal()
    try:
        # Pathway-aware branch: Vía B companies have data in `financial_statements`
        # only. The Vía A builders read journal_entry_lines and would return
        # zeros for them — route to via_b_service instead.
        pathway: str | None = None
        if request.company_nit:
            try:
                pathway = db_service.get_company_locked_pathway(db, request.company_nit)
            except Exception as exc:
                logger.warning("locked_pathway lookup failed (non-fatal): %s", exc)
        if pathway == "work_with_existing":
            period_end = request.end_date or _parse_iso_date(intent.get("period_end"))
            return _gather_via_b(intent_name, request, db, period_end)

        data: dict | None = None
        card_type = intent_name
        title = ""
        _ratios_balance_for_llm: dict | None = (
            None  # set in ratios branch; used to enrich LLM context
        )

        if intent_name == "balance":
            from app.agents.reportero_agent import _build_balance

            data = _build_balance(db, params, db_service)
            title = "Balance General"

        elif intent_name == "pnl":
            from app.agents.reportero_agent import _build_pnl

            data = _build_pnl(db, params, db_service)
            title = "Estado de Resultados"

        elif intent_name == "cashflow":
            from app.agents.reportero_agent import _build_cashflow

            data = _build_cashflow(db, params, db_service)
            title = "Flujo de Caja"

        elif intent_name == "iva":
            from app.agents.reportero_agent import _build_iva

            data = _build_iva(db, params, db_service)
            title = "Reporte IVA"

        elif intent_name == "withholdings":
            from app.agents.reportero_agent import _build_withholdings

            data = _build_withholdings(db, params, db_service)
            title = "Retenciones"

        elif intent_name == "analysis":
            from app.agents.reportero_agent import _build_analysis

            data = _build_analysis(db, params, db_service)
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
            from app.agents.reportero_agent import _compute_ratios

            balance = db_service.get_balance_sheet(db, company_nit=request.company_nit)
            ledger = db_service.get_general_ledger(db, company_nit=request.company_nit)
            data = _compute_ratios(ledger, balance)
            title = "Ratios Financieros"
            # Stash raw balance so we can enrich the LLM context below. Without
            # the raw values (activos, ingresos, utilidad) the LLM hallucinates
            # ratio percentages that contradict the card.
            _ratios_balance_for_llm = balance

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

        # For ratios: enrich the LLM-facing dict with the raw balance so the
        # model can cite real values instead of inventing percentages from the
        # system-prompt formula guidance. Card already captured the flat dict
        # above, so its rendering is unaffected.
        if intent_name == "ratios" and data and _ratios_balance_for_llm is not None:
            data = {"balance_summary": _ratios_balance_for_llm, "ratios": data}

        return data, cards

    except Exception as exc:
        logger.error("gather_financial_data failed for intent=%s: %s", intent_name, exc)
        return None, []
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Vía B data gathering
# ---------------------------------------------------------------------------


# Intents that have no Vía B equivalent — Vía B uploads aggregated statements,
# not source documents, so there is no per-movement IVA / retención detail and
# no transaction counts. The LLM gets a `not_applicable` card so it can answer
# explicitly instead of saying "no data" or fabricating numbers.
_VIA_B_NOT_APPLICABLE_INTENTS = frozenset({"iva", "withholdings"})


def _not_applicable_card(intent_name: str) -> FinancialDataCard:
    return FinancialDataCard(
        card_type="not_applicable",
        title=f"{_intent_label(intent_name)} (no disponible)",
        data={
            "reason": "via_b",
            "intent": intent_name,
            "explanation": (
                "Esta empresa cargó estados financieros agregados (Vía B); "
                "no existen movimientos individuales para calcular este reporte."
            ),
        },
    )


# Vía B intents backed by a single statement type — used to look up which
# periods are available when the requested one isn't found. Multi-statement
# intents (dashboard / analysis / ratios) point to balance_general because
# that's the most common upload and gives the user a useful "did you mean
# this period?" hint.
_VIA_B_INTENT_STATEMENT_TYPE = {
    "balance": "balance_general",
    "pnl": "estado_resultados",
    "cashflow": "libro_auxiliar",
    "top_accounts": "libro_auxiliar",
    "ratios": "balance_general",
    "dashboard": "balance_general",
    "analysis": "balance_general",
}


def _period_label(period_end: date) -> str:
    """Human month-year label, e.g. 'diciembre de 2025'."""
    meses = [
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "octubre",
        "noviembre",
        "diciembre",
    ]
    return f"{meses[period_end.month - 1]} de {period_end.year}"


def _period_not_found_card(
    intent_name: str, period_end: date, available: list[str]
) -> FinancialDataCard:
    """Card telling the LLM the requested period isn't loaded — and which are.

    Prevents the model from passing off the latest snapshot as the requested
    month (the bug where a January balance was reported as December).
    """
    return FinancialDataCard(
        card_type="period_not_found",
        title=f"{_intent_label(intent_name)} — {_period_label(period_end)} no disponible",
        data={
            "reason": "via_b_period_not_found",
            "intent": intent_name,
            "requested_period": period_end.isoformat(),
            "available_periods": available,
            "explanation": (
                f"No hay un estado cargado para {_period_label(period_end)}. "
                "NO uses cifras de otro período como si fueran de este. "
                "Informa al usuario los períodos disponibles."
            ),
        },
    )


def _gather_via_b(
    intent_name: str,
    request: ChatRequest,
    db,
    period_end: date | None = None,
) -> tuple[dict | None, list[FinancialDataCard]]:
    """Route a Vía B request to the appropriate via_b_service reader.

    Returns the same ``(data, cards)`` tuple the Vía A branch yields, with a
    ``pathway: 'work_with_existing'`` marker so the response prompt can frame
    its answer correctly. ``period_end`` selects the statement for a specific
    month; when the user named a period that isn't loaded, a
    ``period_not_found`` card is returned instead of silently using the latest.
    """
    from app.services import via_b_service

    company_nit = request.company_nit
    assert company_nit, "_gather_via_b requires company_nit"

    if intent_name in _VIA_B_NOT_APPLICABLE_INTENTS:
        card = _not_applicable_card(intent_name)
        return card.data, [card]

    data: dict | None = None
    title = ""
    card_type = intent_name
    # Set in the ratios branch below; used to enrich the LLM-facing dict with
    # the raw balance/pnl so the model can cite concrete values instead of
    # inventing percentages from the formula guidance in the system prompt.
    _ratios_extra_for_llm: dict | None = None

    if intent_name == "balance":
        data = via_b_service.get_balance(db, company_nit, period_end)
        title = "Balance General (Vía B)"

    elif intent_name == "pnl":
        data = via_b_service.get_pnl(db, company_nit, period_end)
        title = "Estado de Resultados (Vía B)"

    elif intent_name == "cashflow":
        data = via_b_service.get_cashflow(db, company_nit, period_end)
        title = "Flujo de Caja (Vía B)"

    elif intent_name == "top_accounts":
        data = via_b_service.get_top_accounts(
            db, company_nit, limit=5, period_end=period_end
        )
        title = "Cuentas con Mayor Movimiento (Vía B)"

    elif intent_name == "ratios":
        balance = via_b_service.get_balance(db, company_nit, period_end)
        pnl = via_b_service.get_pnl(db, company_nit, period_end)
        if balance or pnl:
            data = _compute_ratios_via_b(balance, pnl)
            title = "Ratios Financieros (Vía B)"
            _ratios_extra_for_llm = {
                "balance_summary": balance,
                "estado_resultados": pnl,
            }

    elif intent_name in ("dashboard", "analysis"):
        # When a specific period is requested, the per-statement readers
        # already enforce exact-month match — fall through to `period_not_found`
        # if either core statement is missing for that month. Without this
        # check the latest-period overrides would mask the gap and the chat
        # would label e.g. January figures as the user's requested November.
        balance = via_b_service.get_balance(db, company_nit, period_end)
        pnl = via_b_service.get_pnl(db, company_nit, period_end)
        if period_end is not None and balance is None and pnl is None:
            data = None
        else:
            overrides = via_b_service.get_dashboard_overrides(db, company_nit)
            data = {
                **overrides,
                "balance": balance,
                "estado_resultados": pnl,
            }
        title = (
            "Resumen General (Vía B)"
            if intent_name == "dashboard"
            else "Análisis Financiero (Vía B)"
        )

    if data is None:
        # Distinguish "you asked for a period we don't have" from "nothing
        # uploaded at all" — the former lists the periods that DO exist.
        stmt_type = _VIA_B_INTENT_STATEMENT_TYPE.get(intent_name)
        if period_end is not None and stmt_type:
            available = via_b_service.list_periods(db, company_nit, stmt_type)
            if available:
                card = _period_not_found_card(intent_name, period_end, available)
                return card.data, [card]
        # No statement uploaded yet for this intent — surface that explicitly
        # so the LLM tells the user instead of inventing numbers.
        empty_card = FinancialDataCard(
            card_type="empty_via_b",
            title=f"{_intent_label(intent_name)} (sin datos cargados)",
            data={
                "reason": "via_b_no_statement",
                "intent": intent_name,
                "explanation": (
                    "Esta empresa no ha cargado el estado financiero necesario "
                    "para responder esta consulta."
                ),
            },
        )
        return empty_card.data, [empty_card]

    # Card always sees the flat dict (so RatiosCard etc. render unchanged).
    card_payload = {**data, "pathway": "work_with_existing"}
    card = FinancialDataCard(card_type=card_type, title=title, data=card_payload)

    # For ratios: enrich the LLM-facing dict with raw balance + estado_resultados
    # so the model cites concrete values instead of inventing percentages from
    # the formula guidance. Card already captured the flat shape above.
    if intent_name == "ratios" and _ratios_extra_for_llm is not None:
        enriched = {
            **_ratios_extra_for_llm,
            "ratios": data,
            "pathway": "work_with_existing",
        }
    else:
        enriched = card_payload

    return enriched, [card]


def _compute_ratios_via_b(balance: dict | None, pnl: dict | None) -> dict:
    """Compute the headline ratios using Vía B totals only.

    Keys, units, and rounding mirror the Vía A ``_compute_ratios`` in
    ``reportero_agent`` so the same frontend RatiosCard renders both pathways:
    ``margen_neto`` and ``roa`` are pre-formatted percentages (e.g. ``39.7``
    means 39.7 %), while ``razon_endeudamiento`` / ``deuda_patrimonio`` /
    ``rotacion_activos`` stay as plain ratios. Vía B doesn't break out activos
    vs pasivos corrientes, so ``razon_corriente`` and ``prueba_acida`` are
    ``None`` and the LLM is told to explain the limitation.
    """
    activos = float((balance or {}).get("activos") or 0)
    pasivos = float((balance or {}).get("pasivos") or 0)
    patrimonio_total = float((balance or {}).get("patrimonio_total") or 0)
    utilidad_neta = float((pnl or {}).get("utilidad_neta") or 0)
    ingresos = float((pnl or {}).get("total_ingresos") or 0)

    def _pct(num: float, den: float) -> float | None:
        if not den:
            return None
        return round((num / den) * 100, 2)

    def _ratio(num: float, den: float) -> float | None:
        if not den:
            return None
        return round(num / den, 4)

    return {
        "report_type": "ratios",
        "source": "via_b",
        "razon_corriente": None,
        "prueba_acida": None,
        "margen_neto": _pct(utilidad_neta, ingresos),
        "roa": _pct(utilidad_neta, activos),
        "razon_endeudamiento": _ratio(pasivos, activos),
        "deuda_patrimonio": _ratio(pasivos, patrimonio_total),
        "rotacion_activos": _ratio(ingresos, activos),
        "patrimonio_total": patrimonio_total,
        "nota": (
            "Razón corriente y prueba ácida no se calculan porque el balance "
            "Vía B no separa activos/pasivos corrientes."
        ),
    }


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


# ---------------------------------------------------------------------------
# Reasoning-trace copy helpers (accountant-friendly Spanish — no jargon)
# ---------------------------------------------------------------------------


def _periodo_label(start, end) -> str:
    """Human period label for the reasoning trace, e.g. '01/01/2026 → 31/01/2026'."""

    def _fmt(d) -> str:
        return d.strftime("%d/%m/%Y")

    if start and end:
        return f"{_fmt(start)} → {_fmt(end)}"
    if start:
        return f"desde {_fmt(start)}"
    if end:
        return f"hasta {_fmt(end)}"
    return "todos los periodos"


def _params_detail(request: ChatRequest) -> str:
    """'Empresa NIT X · <periodo>' for the params reasoning step."""
    periodo = _periodo_label(request.start_date, request.end_date)
    if request.company_nit:
        return f"Empresa NIT {request.company_nit} · {periodo}"
    return f"Empresa actual · {periodo}"


def _gathering_label(needs_data: bool) -> str:
    return (
        "Revisé tus libros contables"
        if needs_data
        else "No fue necesario consultar cifras"
    )


def _gathering_detail(needs_data: bool, n_cards: int) -> str:
    if not needs_data:
        return "Esta pregunta no requería datos contables"
    plural = "reporte" if n_cards == 1 else "reportes"
    return f"{n_cards} {plural} consultado{'s' if n_cards != 1 else ''}"


def _rag_detail(rag_query, rag_context: str) -> str:
    if not rag_query:
        return "No se requirió consultar normativa"
    n = len(rag_context.split("---")) if rag_context else 0
    plural = "referencia" if n == 1 else "referencias"
    return f"Revisé {n} {plural} (Estatuto Tributario / PUC / NIIF)"


def _generacion_segundos(ms: int) -> str:
    """'0,7 s' — Spanish decimal comma."""
    return f"{ms / 1000:.1f}".replace(".", ",") + " s"


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
            label=f"Entendí tu consulta: {_intent_label(intent_name)}",
            detail=(
                "Requiere consultar tus cifras contables"
                if needs_data
                else "Pregunta general — no necesita cifras"
            ),
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
    )

    # 5. Show resolved parameters (NIT, fechas)
    yield _emit(
        _thinking_step(
            phase="params",
            label="Empresa y periodo",
            detail=_params_detail(request),
        )
    )

    # 6. Gather financial data
    t0 = time.perf_counter()
    financial_data, data_cards = gather_financial_data(intent, request)
    yield _emit(
        _thinking_step(
            phase="gathering_data",
            label=_gathering_label(needs_data),
            detail=_gathering_detail(needs_data, len(data_cards)),
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
            label="Normativa colombiana",
            detail=_rag_detail(rag_query, rag_context),
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
            label="Redacté la respuesta",
            detail=f"Modelo de IA: {llm.model_label}",
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
            label="Respuesta lista",
            detail=f"Generada en {_generacion_segundos(generation_ms)}",
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
            label=f"Entendí tu consulta: {_intent_label(intent_name)}",
            detail=(
                "Requiere consultar tus cifras contables"
                if needs_data
                else "Pregunta general — no necesita cifras"
            ),
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
    )

    # Params summary
    reasoning_steps.append(
        _thinking_step(
            phase="params",
            label="Empresa y periodo",
            detail=_params_detail(request),
        )
    )

    # Data + RAG
    t0 = time.perf_counter()
    financial_data, data_cards = gather_financial_data(intent, request)
    reasoning_steps.append(
        _thinking_step(
            phase="gathering_data",
            label=_gathering_label(needs_data),
            detail=_gathering_detail(needs_data, len(data_cards)),
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
    )

    t0 = time.perf_counter()
    rag_query = intent.get("rag_query")
    rag_context = fetch_rag_context(rag_query)
    reasoning_steps.append(
        _thinking_step(
            phase="rag",
            label="Normativa colombiana",
            detail=_rag_detail(rag_query, rag_context),
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
            label="Redacté la respuesta",
            detail=f"Modelo de IA: {llm.model_label}",
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
            label="Respuesta lista",
            detail=f"Generada en {_generacion_segundos(int((time.perf_counter() - pipeline_start) * 1000))}",
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
