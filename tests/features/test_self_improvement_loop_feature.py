"""Feature tests for the Phase 4 self-improvement loop redesign.

Covers every routing branch in supervisor_node when current_agent == "auditor":
  1. Approved → db_persist
  2. Unfixable BLOCKER → error_terminal + unfixable_findings populated
  3. LLM rejection (no fixable findings) → contador (within budget)
  4. LLM rejection budget exhausted → error_terminal + giveup_record
  5. Fixable finding → responsible agent (pinpointed)
  6. Fixable finding budget exhausted → error_terminal + giveup_record
  7. Global circuit breaker (GLOBAL_AUDIT_FAILURES) → error_terminal
"""

from unittest.mock import patch

import pytest

from app.agents.audit_utils import build_pinpointed_prompt, record_giveup
from app.agents.validation_rules import GLOBAL_AUDIT_FAILURES, RETRY_BUDGETS
from app.models.audit import AuditFinding, AuditTarget, Severity

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _finding(
    rule_id="CONT-BALANCE-MISMATCH",
    severity=Severity.BLOCKER,
    fixable=True,
    responsible_agent="contador",
    evidence=None,
    suggested_action_es="Verifica el balance",
) -> AuditFinding:
    return AuditFinding(
        target=AuditTarget.CONTADOR,
        rule_id=rule_id,
        severity=severity,
        fixable=fixable,
        responsible_agent=responsible_agent,
        technical_message="Test finding",
        user_message_es="Hallazgo de prueba",
        suggested_action_es=suggested_action_es,
        evidence=evidence or {"key": "val"},
    )


def _audit_report(approved=False, findings=None):
    return {
        "target": "contador",
        "approved": approved,
        "findings": [f.model_dump() for f in (findings or [])],
        "attempt": 1,
        "duration_ms": 1.0,
    }


def _state(
    audit_approved=False,
    audit_reports=None,
    audit_rejection_count=0,
    retry_budget=None,
    unfixable_findings=None,
):
    return {
        "agent_log": [],
        "pipeline_warnings": [],
        "unfixable_findings": unfixable_findings or [],
        "audit_reports": audit_reports or [],
        "audit_approved": audit_approved,
        "audit_rejection_count": audit_rejection_count,
        "audit_rejection_reason": None,
        "audit_feedback": None,
        "retry_budget": retry_budget or {},
        "giveup_record": None,
        "correction_feedback": None,
        "current_agent": "auditor",
        "error": None,
        "mode": "process",
        "current_stage": "auditor",
    }


def _run_supervisor_auditor_branch(state):
    """Run the supervisor auditor branch, bypassing schema validation."""
    from app.agents.supervisor import supervisor_node

    with patch("app.agents.supervisor.validate_auditor_output_node") as mock_validate:
        mock_validate.side_effect = lambda s: s
        return supervisor_node(state)


# ---------------------------------------------------------------------------
# build_pinpointed_prompt
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildPinpointedPrompt:
    def test_contains_rule_id(self):
        findings = [_finding(rule_id="TRIB-IVA-RATE-INVALID")]
        prompt = build_pinpointed_prompt(findings)
        assert "TRIB-IVA-RATE-INVALID" in prompt

    def test_contains_evidence(self):
        findings = [_finding(evidence={"tarifa": "0.12"})]
        prompt = build_pinpointed_prompt(findings)
        assert "tarifa" in prompt

    def test_contains_suggested_action(self):
        findings = [_finding(suggested_action_es="Corrige la tarifa")]
        prompt = build_pinpointed_prompt(findings)
        assert "Corrige la tarifa" in prompt

    def test_multiple_findings_numbered(self):
        findings = [_finding(rule_id="A"), _finding(rule_id="B")]
        prompt = build_pinpointed_prompt(findings)
        assert "[1]" in prompt
        assert "[2]" in prompt

    def test_no_suggested_action_omits_accion_line(self):
        findings = [_finding(suggested_action_es=None)]
        prompt = build_pinpointed_prompt(findings)
        assert "Acción" not in prompt


# ---------------------------------------------------------------------------
# record_giveup
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRecordGiveup:
    def _base_state(self):
        return {
            "agent_log": [],
            "giveup_record": None,
            "audit_reports": [_audit_report(approved=False)],
        }

    def test_sets_giveup_record(self):
        state = self._base_state()
        record_giveup(state, "contador", [])
        assert state["giveup_record"] is not None
        assert state["giveup_record"]["target"] == "contador"

    def test_explanation_es_contains_target(self):
        state = self._base_state()
        record_giveup(state, "tributario", [])
        explanation = state["giveup_record"]["explanation_es"]
        assert "tributario" in explanation

    def test_last_findings_stored(self):
        state = self._base_state()
        f = _finding(rule_id="CONT-X")
        record_giveup(state, "contador", [f])
        last = state["giveup_record"]["last_findings"]
        assert len(last) == 1
        assert last[0]["rule_id"] == "CONT-X"

    def test_emits_audit_giveup_log(self):
        state = self._base_state()
        record_giveup(state, "contador", [])
        events = [e["event"] for e in state["agent_log"]]
        assert "audit_giveup" in events


# ---------------------------------------------------------------------------
# Supervisor auditor branch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSelfImprovementLoop:
    def test_approved_routes_to_db_persist(self):
        state = _state(
            audit_approved=True, audit_reports=[_audit_report(approved=True)]
        )
        result = _run_supervisor_auditor_branch(state)
        assert result["current_agent"] == "db_persist"

    def test_unfixable_blocker_routes_to_error_terminal(self):
        blocker = _finding(severity=Severity.BLOCKER, fixable=False)
        report = _audit_report(approved=False, findings=[blocker])
        state = _state(audit_approved=False, audit_reports=[report])
        result = _run_supervisor_auditor_branch(state)
        assert result["current_agent"] == "audit_review_terminal"

    def test_unfixable_blocker_populates_unfixable_findings(self):
        blocker = _finding(severity=Severity.BLOCKER, fixable=False)
        report = _audit_report(approved=False, findings=[blocker])
        state = _state(audit_approved=False, audit_reports=[report])
        result = _run_supervisor_auditor_branch(state)
        assert len(result["unfixable_findings"]) > 0

    def test_unfixable_blocker_sets_error(self):
        blocker = _finding(severity=Severity.BLOCKER, fixable=False, rule_id="CONT-BAD")
        report = _audit_report(approved=False, findings=[blocker])
        state = _state(audit_approved=False, audit_reports=[report])
        result = _run_supervisor_auditor_branch(state)
        assert result["error"] is not None
        assert "CONT-BAD" in result["error"]

    def test_llm_rejection_routes_to_contador_within_budget(self):
        report = _audit_report(approved=False, findings=[])
        state = _state(
            audit_approved=False, audit_reports=[report], audit_rejection_count=0
        )
        result = _run_supervisor_auditor_branch(state)
        assert result["current_agent"] == "contador"
        assert result["audit_rejection_count"] == 1

    def test_llm_rejection_budget_exhausted_routes_to_error_terminal(self):
        reports = [_audit_report(approved=False, findings=[])] * (
            GLOBAL_AUDIT_FAILURES + 1
        )
        state = _state(
            audit_approved=False,
            audit_reports=reports,
            audit_rejection_count=4,
        )
        result = _run_supervisor_auditor_branch(state)
        assert result["current_agent"] == "audit_review_terminal"

    def test_llm_rejection_budget_exhausted_sets_giveup_record(self):
        reports = [_audit_report(approved=False, findings=[])] * 4
        state = _state(
            audit_approved=False,
            audit_reports=reports,
            audit_rejection_count=4,
        )
        result = _run_supervisor_auditor_branch(state)
        assert result["giveup_record"] is not None

    def test_fixable_finding_routes_to_responsible_agent(self):
        fixable = _finding(
            severity=Severity.ERROR, fixable=True, responsible_agent="tributario"
        )
        report = _audit_report(approved=False, findings=[fixable])
        state = _state(audit_approved=False, audit_reports=[report])
        result = _run_supervisor_auditor_branch(state)
        assert result["current_agent"] == "tributario"

    def test_fixable_finding_sets_pinpointed_correction_feedback(self):
        fixable = _finding(
            severity=Severity.ERROR, fixable=True, rule_id="TRIB-IVA-RATE-INVALID"
        )
        report = _audit_report(approved=False, findings=[fixable])
        state = _state(audit_approved=False, audit_reports=[report])
        result = _run_supervisor_auditor_branch(state)
        assert result["correction_feedback"] is not None
        assert "TRIB-IVA-RATE-INVALID" in result["correction_feedback"]

    def test_fixable_finding_budget_exhausted_routes_to_error_terminal(self):
        fixable = _finding(
            severity=Severity.BLOCKER, fixable=True, responsible_agent="contador"
        )
        budget = {"contador": -1}
        report = _audit_report(approved=False, findings=[fixable])
        state = _state(
            audit_approved=False, audit_reports=[report], retry_budget=budget
        )
        result = _run_supervisor_auditor_branch(state)
        assert result["current_agent"] == "audit_review_terminal"
        assert result["giveup_record"] is not None

    def test_global_circuit_breaker_triggers_giveup(self):
        fixable = _finding(
            severity=Severity.ERROR, fixable=True, responsible_agent="contador"
        )
        reports = [
            _audit_report(approved=False, findings=[fixable])
        ] * GLOBAL_AUDIT_FAILURES
        state = _state(audit_approved=False, audit_reports=reports)
        result = _run_supervisor_auditor_branch(state)
        assert result["current_agent"] == "audit_review_terminal"
        assert result["giveup_record"] is not None

    def test_fixable_finding_decrements_retry_budget(self):
        fixable = _finding(
            severity=Severity.ERROR, fixable=True, responsible_agent="contador"
        )
        report = _audit_report(approved=False, findings=[fixable])
        initial_budget = RETRY_BUDGETS["contador"]
        state = _state(audit_approved=False, audit_reports=[report])
        result = _run_supervisor_auditor_branch(state)
        assert result["retry_budget"]["contador"] == initial_budget - 1
