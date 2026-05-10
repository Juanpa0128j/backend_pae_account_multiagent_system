"""Process pipeline router.

Handles contador → tributario → auditor routing with validation,
retry loops, and self-improvement logic.
"""

from app.agents.agent_utils import append_log
from app.agents.audit_utils import build_pinpointed_prompt, record_giveup
from app.agents.state import AgentState
from app.agents.validation_rules import (
    GLOBAL_AUDIT_FAILURES,
    MAX_AUDITOR_RETRIES,
    RETRY_BUDGETS,
    validate_auditor_output_node,
    validate_contador_output_node,
)
from app.core.logger import get_logger
from app.models.audit import AuditFinding, Severity
from app.services.tributario_normalizer import normalize_tributario_output
from app.services.validation_engine import get_validator

logger = get_logger("app.agents.routing.process_router")


def route(state: AgentState) -> AgentState:
    """Route process pipeline based on current_agent."""
    current = state.get("current_agent", "")

    if not current:
        # Pipeline start
        raw_txs = state.get("raw_transactions", [])
        if not raw_txs:
            state["error"] = "Process supervisor: no staged transactions to process"
            logger.error(state["error"])
            append_log(
                state, "supervisor", "routing_error", {"reason": "no_transactions"}
            )
            return state
        state["current_agent"] = "contador"
        state["current_stage"] = "routing"
        append_log(
            state,
            "supervisor",
            "routing_complete",
            {"next_agent": "contador", "mode": "process"},
        )
        return state

    if current == "contador":
        return _route_after_contador(state)

    if current == "tributario":
        return _route_after_tributario(state)

    if current == "auditor":
        return _route_after_auditor(state)

    return state


def _route_after_contador(state: AgentState) -> AgentState:
    state = validate_contador_output_node(state)
    if state.get("correction_feedback"):
        state["current_agent"] = "contador"
        append_log(
            state,
            "supervisor",
            "routing_complete",
            {"next_agent": "contador", "reason": "validation_failed"},
        )
    elif state.get("current_agent") == "audit_review_terminal":
        append_log(
            state,
            "supervisor",
            "routing_complete",
            {
                "next_agent": "audit_review_terminal",
                "reason": "contador_validation_exhausted_hitl",
            },
        )
    elif state.get("error"):
        state["current_agent"] = ""
        append_log(
            state,
            "supervisor",
            "routing_error",
            {"reason": "contador_validation_exhausted"},
        )
    else:
        state["current_agent"] = "tributario"
        append_log(
            state, "supervisor", "routing_complete", {"next_agent": "tributario"}
        )
    return state


def _route_after_tributario(state: AgentState) -> AgentState:
    tributario_out = normalize_tributario_output(
        state, state.get("tributario_output", {})
    )
    validator = get_validator()
    result = validator.validate("tributario", tributario_out, attempt=1)
    if result.is_valid:
        logger.info("process_router: tributario output VALID — routing to auditor")
        if result.validated_output:
            state["tributario_output"] = result.validated_output.model_dump(mode="json")
        else:
            state["tributario_output"] = tributario_out
        state["current_agent"] = "auditor"
        append_log(state, "supervisor", "routing_complete", {"next_agent": "auditor"})
    else:
        logger.error(
            "process_router: tributario output INVALID — %s", result.error_summary()
        )
        state["error"] = (
            f"Tributario output schema validation failed: {result.error_summary()}"
        )
        state["current_agent"] = ""
        append_log(
            state,
            "supervisor",
            "routing_error",
            {"reason": "tributario_validation_failed", "errors": result.errors[:3]},
        )
    return state


def _route_after_auditor(state: AgentState) -> AgentState:
    if state.get("force_persist"):
        state["current_agent"] = "db_persist"
        append_log(
            state,
            "supervisor",
            "routing_complete",
            {"next_agent": "db_persist", "reason": "force_persist"},
        )
        return state

    state = validate_auditor_output_node(state)
    if state.get("correction_feedback"):
        state["current_agent"] = "auditor"
        append_log(
            state,
            "supervisor",
            "routing_complete",
            {"next_agent": "auditor", "reason": "validation_failed"},
        )
        return state
    elif state.get("error"):
        state["current_agent"] = ""
        append_log(
            state,
            "supervisor",
            "routing_error",
            {"reason": "auditor_validation_exhausted"},
        )
        return state
    elif state.get("audit_approved") is False:
        return _handle_auditor_rejection(state)
    else:
        state["current_agent"] = "db_persist"
        append_log(
            state,
            "supervisor",
            "routing_complete",
            {"next_agent": "db_persist", "decision": "approved"},
        )
        return state


def _handle_auditor_rejection(state: AgentState) -> AgentState:
    reports = state.get("audit_reports") or []
    last_report = reports[-1] if reports else {}
    raw_findings = (
        last_report.get("findings", []) if isinstance(last_report, dict) else []
    )
    findings = [AuditFinding(**f) for f in raw_findings if isinstance(f, dict)]

    fixable = [
        f
        for f in findings
        if f.fixable and f.severity in {Severity.ERROR, Severity.BLOCKER}
    ]
    blockers = [f for f in findings if not f.fixable and f.severity == Severity.BLOCKER]

    if blockers:
        if state.get("unfixable_findings") is None:
            state["unfixable_findings"] = []
        state["unfixable_findings"].extend([b.model_dump() for b in blockers])
        rule_ids = [b.rule_id for b in blockers]
        state["error"] = f"Unfixable audit blockers: {rule_ids}"
        record_giveup(state, "contador", blockers)
        state["current_agent"] = "audit_review_terminal"
        logger.error("process_router: Unfixable audit blockers detected — %s", rule_ids)
        append_log(
            state,
            "supervisor",
            "routing_complete",
            {
                "next_agent": "audit_review_terminal",
                "reason": "unfixable_blockers",
                "rule_ids": rule_ids,
            },
        )
        return state

    if not fixable:
        return _handle_llm_rejection(state)

    return _handle_fixable_findings(state, fixable)


def _handle_llm_rejection(state: AgentState) -> AgentState:
    rejection_count = state.get("audit_rejection_count", 0) + 1
    state["audit_rejection_count"] = rejection_count
    retry_budget = state.get("retry_budget") or {}
    if "contador" not in retry_budget:
        retry_budget["contador"] = RETRY_BUDGETS.get("contador", 1)
    retry_budget["contador"] -= 1
    state["retry_budget"] = retry_budget

    reports = state.get("audit_reports") or []
    global_failures = sum(1 for r in reports if not r.get("approved", True))

    if (
        retry_budget["contador"] < 0
        or global_failures >= GLOBAL_AUDIT_FAILURES
        or rejection_count > MAX_AUDITOR_RETRIES
    ):
        record_giveup(state, "contador", [])
        state["current_agent"] = "audit_review_terminal"
        logger.warning(
            "process_router: Audit give-up (no fixable findings), remaining_budget=%d rejection_count=%d",
            retry_budget["contador"],
            rejection_count,
        )
        append_log(
            state,
            "supervisor",
            "routing_complete",
            {
                "next_agent": "audit_review_terminal",
                "reason": "audit_giveup_no_fixable_findings",
                "rejection_count": rejection_count,
                "remaining_budget": retry_budget["contador"],
            },
        )
    else:
        state["current_agent"] = "contador"
        state["correction_feedback"] = (
            state.get("audit_rejection_reason")
            or state.get("audit_feedback")
            or "Audit rejected - please reclassify"
        )
        logger.warning(
            "process_router: Auditor rejected (LLM) — re-routing to Contador (remaining budget=%d)",
            retry_budget["contador"],
        )
        append_log(
            state,
            "supervisor",
            "routing_complete",
            {
                "next_agent": "contador",
                "reason": "audit_rejected_llm",
                "rejection_count": rejection_count,
                "remaining_budget": retry_budget["contador"],
            },
        )
    return state


def _handle_fixable_findings(state: AgentState, fixable: list) -> AgentState:
    target = fixable[0].responsible_agent
    routing_target = {"ingest": "ingesta", "persist": "db_persist"}.get(target, target)
    retry_budget = state.get("retry_budget") or {}
    if target not in retry_budget:
        retry_budget[target] = RETRY_BUDGETS.get(target, 1)
    retry_budget[target] -= 1
    state["retry_budget"] = retry_budget

    reports = state.get("audit_reports") or []
    global_failures = sum(1 for r in reports if not r.get("approved", True))

    if retry_budget[target] < 0 or global_failures >= GLOBAL_AUDIT_FAILURES:
        record_giveup(state, target, fixable)
        state["current_agent"] = "audit_review_terminal"
        logger.warning(
            "process_router: Retry budget exhausted for target=%s global_failures=%d",
            target,
            global_failures,
        )
        append_log(
            state,
            "supervisor",
            "routing_complete",
            {
                "next_agent": "audit_review_terminal",
                "reason": "retry_budget_exhausted",
                "target": target,
                "remaining_budget": retry_budget[target],
                "global_failures": global_failures,
            },
        )
    else:
        state["correction_feedback"] = build_pinpointed_prompt(fixable)
        state["current_agent"] = routing_target
        logger.warning(
            "process_router: Routing to %s for self-correction (budget=%d)",
            routing_target,
            retry_budget[target],
        )
        append_log(
            state,
            "supervisor",
            "routing_complete",
            {
                "next_agent": routing_target,
                "reason": "audit_pinpointed_retry",
                "rule_ids": [f.rule_id for f in fixable],
                "responsible_agent": target,
                "remaining_budget": retry_budget[target],
            },
        )
    return state
