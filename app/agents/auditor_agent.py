"""
Auditor worker node for the process graph.

Receives the ContadorOutput (journal entries) and the original raw
transactions, then uses the LLM to perform a qualitative audit review
following Colombian NIIF/DIAN standards.

The auditor node produces a structured AuditorOutput that includes:
  - approval decision (aprobado: bool)
  - risk level (nivel_riesgo: bajo/medio/alto/critico)
  - findings list (hallazgos)
  - quality score (puntaje_calidad: 0-100)
  - executive summary (resumen)

Deterministic checks (partida doble balance, PUC existence) are
performed *before* this node by validate_contador_output_node, so
the LLM focuses purely on semantic/qualitative review.

On retry (when correction_feedback is present), the invalid output
and schema errors are re-sent to the LLM for self-correction.
"""

import logging

from app.agents.agent_utils import append_log
from app.agents.llm_retry import is_parse_error, llm_with_parse_retry
from app.agents.state import AgentState
from app.core.llm_client import get_llm_client

logger = logging.getLogger(__name__)


def _check_cobro_sin_factura(state: AgentState, contador_output: dict) -> None:
    """WARNING — cobro contra cuenta por cobrar sin factura previa.

    When the asientos CREDIT a class-1 receivable account (13xxxx) and the
    accumulated posted DEBITS for that account+company are insufficient to
    cover the credit, the account will go net-credit and the balance sheet
    will present it as anticipo de cliente. Likely the originating factura
    de venta (referencia_factura) was never uploaded. Never a BLOCKER —
    persistence must not be blocked.
    """
    from decimal import Decimal, InvalidOperation

    from sqlalchemy import func

    from app.agents.audit_utils import append_finding
    from app.core.database import SessionLocal
    from app.models.audit import AuditFinding, AuditTarget, Severity
    from app.models.database import (
        JournalEntryLine,
        TransactionPosted,
        TransactionStatus,
    )

    asientos = contador_output.get("asientos") or []
    net_credit_by_account: dict[str, Decimal] = {}
    for line in asientos:
        if not isinstance(line, dict):
            continue
        code = str(line.get("cuenta_puc") or "").strip()
        if not code.startswith("13"):
            continue
        try:
            valor = Decimal(str(line.get("valor") or 0))
        except InvalidOperation:
            valor = Decimal("0")
        tipo = (line.get("tipo_movimiento") or "").lower()
        current = net_credit_by_account.get(code, Decimal("0"))
        if tipo == "credito":
            net_credit_by_account[code] = current + valor
        elif tipo == "debito":
            net_credit_by_account[code] = current - valor
    credited = {c: v for c, v in net_credit_by_account.items() if v > 0}
    if not credited:
        return

    company_nit = state.get("company_nit")
    raw_transactions = state.get("raw_transactions") or []
    base_tx = raw_transactions[0] if raw_transactions else {}
    referencia = str((base_tx or {}).get("referencia_factura") or "").strip()

    _db = SessionLocal()
    try:
        for code, credit in credited.items():
            query = (
                _db.query(
                    func.coalesce(func.sum(JournalEntryLine.debito), 0),
                    func.coalesce(func.sum(JournalEntryLine.credito), 0),
                )
                .join(
                    TransactionPosted,
                    JournalEntryLine.transaction_posted_id == TransactionPosted.id,
                )
                .filter(
                    TransactionPosted.status == TransactionStatus.POSTED,
                    JournalEntryLine.cuenta_puc == code,
                )
            )
            if company_nit:
                query = query.filter(JournalEntryLine.company_nit == company_nit)
            posted_debits, posted_credits = query.one()
            posted_net_debit = Decimal(str(posted_debits or 0)) - Decimal(
                str(posted_credits or 0)
            )
            if posted_net_debit >= credit:
                continue
            ref_es = f" (referencia {referencia})" if referencia else ""
            append_finding(
                state,
                AuditFinding(
                    target=AuditTarget.PRE_PERSIST,
                    rule_id="AUD-COBRO-SIN-FACTURA",
                    severity=Severity.WARNING,
                    fixable=False,
                    responsible_agent="persist",
                    technical_message=(
                        f"Credit of {credit} to receivable account {code} exceeds "
                        f"its posted net-debit balance {posted_net_debit} "
                        f"(company_nit={company_nit}); the account will go "
                        "net-credit and be presented as anticipo de cliente."
                    ),
                    user_message_es=(
                        f"Cobro registrado contra la cuenta {code} sin factura de "
                        f"venta previa contabilizada{ref_es}. La cuenta quedará "
                        "con saldo acreedor y se presentará como anticipo de "
                        "cliente."
                    ),
                    suggested_action_es=(
                        "Suba y procese la factura de venta de origen para que el "
                        "cobro cruce contra la cuenta por cobrar."
                    ),
                    evidence={
                        "cuenta_puc": code,
                        "credito_transaccion": str(credit),
                        "saldo_debito_contabilizado": str(posted_net_debit),
                        "referencia_factura": referencia or None,
                    },
                ),
            )
    finally:
        _db.close()


def auditor_node(state: AgentState) -> AgentState:
    """
    Auditor node: performs semantic audit of the contador journal entries.

    Reads:
        state["contador_output"]     – validated ContadorOutput dict
        state["raw_transactions"]    – original staged transaction dicts
        state["correction_feedback"] – schema errors from previous attempt (retry)

    Writes:
        state["auditor_output"]      – AuditorOutput-compatible dict
        state["audit_approved"]      – bool approval decision
        state["audit_decision"]      – "approved" | "rejected"
        state["audit_feedback"]      – rejection reason (if rejected)
        state["current_stage"]       – "auditor"
        state["current_agent"]       – "auditor"
    """
    if state.get("error"):
        logger.warning("auditor: skipping due to upstream error: %s", state["error"])
        return state

    contador_output = state.get("contador_output") or {}
    if not contador_output:
        state["error"] = "auditor: no contador_output in state – run contador first"
        logger.error(state["error"])
        return state

    raw_transactions = state.get("raw_transactions") or []
    is_retry = bool(state.get("correction_feedback"))
    state["current_agent"] = "auditor"
    state["current_stage"] = "auditor"

    append_log(
        state,
        "auditor",
        "node_start",
        {
            "tx_count": len(raw_transactions),
            "is_retry": is_retry,
        },
    )

    try:
        # Phase 3: deterministic contador audit before LLM semantic audit
        from app.agents.audit_utils import append_audit_report
        from app.agents.auditors import contador_auditor

        _contador_report = contador_auditor.run(state)
        append_audit_report(state, _contador_report)

        # WARNING — saldo inicial faltante. When the current asiento credits a
        # cash/bank account (PUC class 1, grupo 11) and the company has no
        # previously posted transactions, the resulting saldo will be negative.
        # That is mathematically valid but accountantly suspicious; surface a
        # WARNING so the user knows to import the opening balance.
        try:
            from app.agents.audit_utils import append_finding
            from app.core.database import SessionLocal
            from app.models.audit import AuditFinding, AuditTarget, Severity
            from app.models.database import TransactionPosted, TransactionStatus

            asientos = contador_output.get("asientos") or []
            cash_credit_accounts: list[str] = []
            for line in asientos:
                if not isinstance(line, dict):
                    continue
                if (line.get("tipo_movimiento") or "").lower() != "credito":
                    continue
                code = str(line.get("cuenta_puc") or "").strip()
                if code.startswith("11"):
                    cash_credit_accounts.append(code)
            if cash_credit_accounts:
                company_nit = state.get("company_nit")
                prior_count = 0
                if company_nit:
                    _db = SessionLocal()
                    try:
                        prior_count = (
                            _db.query(TransactionPosted)
                            .filter(
                                TransactionPosted.company_nit == company_nit,
                                TransactionPosted.status == TransactionStatus.POSTED,
                            )
                            .count()
                        )
                    finally:
                        _db.close()
                if prior_count == 0:
                    append_finding(
                        state,
                        AuditFinding(
                            target=AuditTarget.PRE_PERSIST,
                            rule_id="AUD-SALDO-INICIAL-MISSING",
                            severity=Severity.WARNING,
                            fixable=False,
                            responsible_agent="persist",
                            technical_message=(
                                f"No prior posted transactions for company {company_nit}; "
                                f"credit to cash accounts {sorted(set(cash_credit_accounts))} "
                                "will result in a negative cash balance."
                            ),
                            user_message_es=(
                                "No hay saldo inicial registrado para las cuentas de efectivo "
                                f"{', '.join(sorted(set(cash_credit_accounts)))}. El saldo "
                                "resultante quedará negativo. Considere importar saldos "
                                "iniciales (Vía B) o registrar un asiento de apertura antes "
                                "de continuar."
                            ),
                            suggested_action_es=(
                                "Suba un balance de apertura por Vía B o cree un asiento "
                                "manual debitando 11xx con saldo a inicio de período."
                            ),
                        ),
                    )
        except Exception as warning_err:
            logger.warning(
                "auditor: saldo-inicial warning check failed (non-fatal): %s",
                warning_err,
            )

        # WARNING — cobro contra 13xxxx sin factura de venta previa (la
        # cuenta quedaría con saldo acreedor → anticipo de cliente).
        try:
            _check_cobro_sin_factura(state, contador_output)
        except Exception as warning_err:
            logger.warning(
                "auditor: cobro-sin-factura warning check failed (non-fatal): %s",
                warning_err,
            )

        llm = get_llm_client()

        if is_retry:
            logger.info(
                "auditor: retry attempt %d with correction feedback",
                state.get("retry_count", 1),
            )

        auditor_output = llm_with_parse_retry(
            llm.extract_auditor_output,
            contador_output=contador_output,
            raw_transactions=raw_transactions,
            correction_feedback=state.get("correction_feedback") if is_retry else None,
            agent_label="auditor",
        )

        # Clear correction feedback after consuming it
        state["correction_feedback"] = None

        state["auditor_output"] = auditor_output
        approved = bool(auditor_output.get("aprobado", False))
        state["audit_approved"] = approved
        state["audit_rejection_reason"] = (
            auditor_output.get("resumen") if not approved else None
        )
        # Also set unified field names used by the supervisor FSM
        state["audit_decision"] = "approved" if approved else "rejected"
        state["audit_feedback"] = auditor_output.get("resumen") if not approved else ""

        if not state.get("result"):
            state["result"] = {}
        state["result"]["auditor_output"] = auditor_output
        state["result"]["audit_approved"] = approved

        logger.info(
            "auditor: audit complete — aprobado=%s nivel_riesgo=%s puntaje=%s",
            auditor_output.get("aprobado"),
            auditor_output.get("nivel_riesgo"),
            auditor_output.get("puntaje_calidad"),
        )
        append_log(
            state,
            "auditor",
            "node_complete",
            {
                "approved": approved,
                "nivel_riesgo": auditor_output.get("nivel_riesgo"),
            },
        )

    except Exception as exc:
        if is_parse_error(exc):
            from app.agents.audit_utils import append_finding
            from app.models.audit import AuditFinding, AuditTarget, Severity

            append_finding(
                state,
                AuditFinding(
                    target=AuditTarget.PRE_PERSIST,
                    rule_id="AUD-PARSE-EXHAUSTED",
                    severity=Severity.BLOCKER,
                    fixable=False,
                    responsible_agent="persist",
                    technical_message=str(exc)[:500],
                    user_message_es=(
                        "El auditor no logró producir un dictamen válido tras "
                        "varios intentos."
                    ),
                    suggested_action_es=(
                        "Reintente el procesamiento. Si persiste, contacte al "
                        "equipo técnico — puede haber un problema con el modelo."
                    ),
                    evidence={"exception_type": exc.__class__.__name__},
                ),
            )

        state["error"] = f"auditor error: {exc}"
        logger.error(state["error"], exc_info=True)
        append_log(state, "auditor", "node_error", {"error": str(exc)})
        if not state.get("result"):
            state["result"] = {}
        state["result"]["status"] = "error"
        state["result"]["error"] = state["error"]

    return state
