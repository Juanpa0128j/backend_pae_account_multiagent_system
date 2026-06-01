"""
Process pipeline supervisor.

Validates staged input and routes to the contador worker.
Full re-entry routing extracted from supervisor.py for separation of concerns.
"""

from app.agents.agent_utils import append_log
from app.agents.audit_utils import build_pinpointed_prompt, record_giveup
from app.agents.state import AgentState
from app.agents.validation_rules import (
    GLOBAL_AUDIT_FAILURES,
    MAX_AUDITOR_RETRIES,
    RETRY_BUDGETS,
    SINGLE_PASS_DOC_TYPES,
    validate_auditor_output_node,
    validate_contador_output_node,
)
from app.models.audit import AuditFinding, Severity
from app.core.logger import get_logger
from app.services.tributario_normalizer import (
    normalize_tributario_output as _normalize_tributario_output,
)
from app.services.validation_engine import ValidationResult, get_validator

logger = get_logger("app.agents.routing.process_router")


def process_supervisor_node(state: AgentState) -> AgentState:
    """Process supervisor: validates staged input and routes to contador worker."""
    if not state.get("validation_history"):
        state["validation_history"] = []
    if state.get("retry_count") is None:
        state["retry_count"] = 0
    if state.get("correction_feedback") is None:
        state["correction_feedback"] = None
    if state.get("agent_log") is None:
        state["agent_log"] = []

    raw_txs = state.get("raw_transactions", [])
    if not raw_txs:
        state["error"] = "Process supervisor: no staged transactions to process"
        append_log(state, "supervisor", "routing_error", {"reason": "no_transactions"})
        return state

    state["mode"] = "process"
    state["current_agent"] = "contador"
    state["current_stage"] = "routing"
    append_log(state, "supervisor", "routing_complete", {"next_agent": "contador"})
    return state


def route_process(state: AgentState) -> AgentState:
    """Full process pipeline routing — handles first entry and all re-entries.

    Copied verbatim from original supervisor_node process block.
    """
    # Ensure state fields expected by validation nodes are initialized
    if not state.get("validation_history"):
        state["validation_history"] = []
    if state.get("retry_count") is None:
        state["retry_count"] = 0
    if state.get("correction_feedback") is None:
        state["correction_feedback"] = None
    if state.get("agent_log") is None:
        state["agent_log"] = []

    current = state.get("current_agent", "")

    # Pipeline start — validate input exists
    if not current:
        raw_txs = state.get("raw_transactions", [])
        if not raw_txs:
            state["error"] = "Process supervisor: no staged transactions to process"
            logger.error(state["error"])
            append_log(
                state,
                "supervisor",
                "routing_error",
                {
                    "reason": "no_transactions",
                },
            )
            return state
        state["current_agent"] = "contador"
        state["current_stage"] = "routing"
        append_log(
            state,
            "supervisor",
            "routing_complete",
            {
                "next_agent": "contador",
                "mode": "process",
            },
        )
        return state

    if current == "contador":
        # Validate contador output before proceeding to tributario
        state = validate_contador_output_node(state)
        if state.get("correction_feedback"):
            # Validation failed — retry contador
            state["current_agent"] = "contador"
            append_log(
                state,
                "supervisor",
                "routing_complete",
                {
                    "next_agent": "contador",
                    "reason": "validation_failed",
                },
            )
        elif state.get("current_agent") == "audit_review_terminal":
            # Validation exhausted — routed to HITL, leave current_agent as-is
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
            # Non-retriable error — terminal
            state["current_agent"] = ""
            append_log(
                state,
                "supervisor",
                "routing_error",
                {
                    "reason": "contador_validation_exhausted",
                },
            )
        else:
            state["current_agent"] = "tributario"
            append_log(
                state,
                "supervisor",
                "routing_complete",
                {
                    "next_agent": "tributario",
                },
            )
        return state

    if current == "tributario":
        # Validate TributarioOutput schema before advancing to auditor
        tributario_out = _normalize_tributario_output(
            state,
            state.get("tributario_output", {}),
        )
        validator = get_validator()
        result: ValidationResult = validator.validate(
            "tributario", tributario_out, attempt=1
        )
        if result.is_valid:
            logger.info("Supervisor: tributario output VALID — routing to auditor")
            if result.validated_output:
                state["tributario_output"] = result.validated_output.model_dump(
                    mode="json"
                )
            else:
                state["tributario_output"] = tributario_out
            state["current_agent"] = "auditor"
            append_log(
                state,
                "supervisor",
                "routing_complete",
                {
                    "next_agent": "auditor",
                },
            )
        else:
            logger.error(
                f"Supervisor: tributario output INVALID — {result.error_summary()}"
            )
            state["error"] = (
                f"Tributario output schema validation failed: {result.error_summary()}"
            )
            state["current_agent"] = ""
            append_log(
                state,
                "supervisor",
                "routing_error",
                {
                    "reason": "tributario_validation_failed",
                    "errors": result.errors[:3],
                },
            )
        return state

    if current == "auditor":
        # If user confirmed force-persist, skip audit and go straight to db_persist.
        if state.get("force_persist"):
            state["current_agent"] = "db_persist"
            append_log(
                state,
                "supervisor",
                "routing_complete",
                {"next_agent": "db_persist", "reason": "force_persist"},
            )
            return state

        # Validate AuditorOutput schema before deciding whether to persist.
        state = validate_auditor_output_node(state)
        if state.get("correction_feedback"):
            state["current_agent"] = "auditor"
            append_log(
                state,
                "supervisor",
                "routing_complete",
                {
                    "next_agent": "auditor",
                    "reason": "validation_failed",
                },
            )
        elif state.get("error"):
            state["current_agent"] = ""
            append_log(
                state,
                "supervisor",
                "routing_error",
                {
                    "reason": "auditor_validation_exhausted",
                },
            )
        elif state.get("audit_approved") is False:
            # --- Phase 4: pinpointed self-improvement loop ---
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
            blockers = [
                f for f in findings if not f.fixable and f.severity == Severity.BLOCKER
            ]

            classification = state.get("document_classification") or {}
            current_doc_type = str(classification.get("doc_type") or "")
            is_single_pass = current_doc_type in SINGLE_PASS_DOC_TYPES

            if blockers:
                # Unfixable BLOCKER — cannot auto-recover, pause for HITL review.
                if state.get("unfixable_findings") is None:
                    state["unfixable_findings"] = []
                state["unfixable_findings"].extend([b.model_dump() for b in blockers])
                rule_ids = [b.rule_id for b in blockers]
                state["error"] = f"Unfixable audit blockers: {rule_ids}"
                record_giveup(state, "contador", blockers)
                state["current_agent"] = "audit_review_terminal"
                logger.error(
                    "Supervisor: Unfixable audit blockers detected — %s", rule_ids
                )
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

            elif is_single_pass:
                # Bank statements / conciliacion: bypass audit retry loop.
                # No IVA/retención, partida_doble trivial — extra iterations
                # only add latency and push past the 300s timeout. Persist
                # with audit warnings for human review.
                state["current_agent"] = "db_persist"
                state["has_warnings"] = True
                logger.info(
                    "Process router: %s — bypassing audit loop (single-pass), "
                    "persisting with %d non-blocker finding(s)",
                    current_doc_type,
                    len(findings),
                )
                append_log(
                    state,
                    "supervisor",
                    "routing_complete",
                    {
                        "next_agent": "db_persist",
                        "reason": "single_pass_doc_type",
                        "doc_type": current_doc_type,
                        "non_blocker_findings": len(findings),
                    },
                )

            elif not fixable:
                # LLM-level rejection without deterministic findings — fall back to
                # contador re-route with per-agent retry budget + global cap.
                rejection_count = state.get("audit_rejection_count", 0) + 1
                state["audit_rejection_count"] = rejection_count
                retry_budget = state.get("retry_budget") or {}
                if "contador" not in retry_budget:
                    retry_budget["contador"] = RETRY_BUDGETS.get("contador", 1)
                retry_budget["contador"] -= 1
                state["retry_budget"] = retry_budget

                global_failures = sum(1 for r in reports if not r.get("approved", True))
                if (
                    retry_budget["contador"] < 0
                    or global_failures >= GLOBAL_AUDIT_FAILURES
                    or rejection_count > MAX_AUDITOR_RETRIES
                ):
                    record_giveup(state, "contador", [])
                    state["current_agent"] = "audit_review_terminal"
                    logger.warning(
                        "Supervisor: Audit give-up (no fixable findings), remaining_budget=%d rejection_count=%d",
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
                        "Supervisor: Auditor rejected (LLM) — re-routing to Contador (remaining budget=%d)",
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

            else:
                # Deterministic fixable findings — route to the responsible agent.
                target = fixable[0].responsible_agent
                routing_target = {
                    "ingest": "ingesta",
                    "persist": "db_persist",
                }.get(target, target)
                retry_budget = state.get("retry_budget") or {}
                if target not in retry_budget:
                    retry_budget[target] = RETRY_BUDGETS.get(target, 1)
                retry_budget[target] -= 1
                state["retry_budget"] = retry_budget

                global_failures = sum(1 for r in reports if not r.get("approved", True))
                if retry_budget[target] < 0 or global_failures >= GLOBAL_AUDIT_FAILURES:
                    record_giveup(state, target, fixable)
                    state["current_agent"] = "audit_review_terminal"
                    logger.warning(
                        "Supervisor: Retry budget exhausted for target=%s global_failures=%d",
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
                        "Supervisor: Routing to %s for self-correction (budget=%d)",
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
        else:
            state["current_agent"] = "db_persist"
            append_log(
                state,
                "supervisor",
                "routing_complete",
                {
                    "next_agent": "db_persist",
                    "decision": "approved",
                },
            )
        return state

    # Unknown process state — fail gracefully
    state["error"] = f"route_process: unknown current_agent '{current}'"
    logger.error(state["error"])
    append_log(
        state,
        "supervisor",
        "routing_error",
        {
            "reason": "unknown_state",
            "current_agent": current,
        },
    )
    return state
