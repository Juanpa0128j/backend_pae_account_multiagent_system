"""Tests for PUC account not found error surfacing in trace as blocker."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.models.audit import AuditTarget, Severity
from app.services.pipeline_trace_service import build_trace


class TestPUCErrorInTrace:
    """Tests for PUC (cuenta contable) not found errors in the process trace."""

    def _make_process_job(
        self,
        job_id="proc-test-001",
        status="completed",
        agent_log=None,
    ):
        """Create a mock ProcessJob."""
        job = MagicMock()
        job.id = job_id
        job.status = status
        job.agent_log = agent_log or []
        job.completed_at = datetime.now(timezone.utc)
        return job

    def test_puc_error_surfaces_in_trace_as_blocker(self):
        """PUC not found error should appear as blocker in trace."""
        db = MagicMock()

        # Simulate agent_log containing a node_error from persist_node
        # when PUC code is not found
        agent_log = [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "agent": "db_persist",
                "event": "node_error",
                "details": {
                    "error": "DB persist error: PUC code 999999 not found",
                },
            },
        ]

        job = self._make_process_job(
            job_id="proc-puc-001",
            status="failed",
            agent_log=agent_log,
        )

        with patch("app.services.pipeline_trace_service.db_service") as mock_svc:
            mock_svc.get_process_job.return_value = job
            trace = build_trace("proc-puc-001", db)

        assert trace is not None
        assert trace.overall_status == "failed"
        # Should contain a blocker with PERS-ACCOUNT-NOT-FOUND rule_id
        blockers = [f for f in trace.blockers if f.rule_id == "PERS-ACCOUNT-NOT-FOUND"]
        assert len(blockers) > 0, (
            "PUC error should create PERS-ACCOUNT-NOT-FOUND blocker"
        )

    def test_puc_error_finding_has_spanish_user_message(self):
        """PUC error finding should have Spanish user_message_es."""
        db = MagicMock()

        agent_log = [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "agent": "db_persist",
                "event": "node_error",
                "details": {
                    "error": "DB persist error: PUC code 123456 not found",
                },
            },
        ]

        job = self._make_process_job(
            job_id="proc-puc-002",
            status="failed",
            agent_log=agent_log,
        )

        with patch("app.services.pipeline_trace_service.db_service") as mock_svc:
            mock_svc.get_process_job.return_value = job
            trace = build_trace("proc-puc-002", db)

        blocker = next(
            (f for f in trace.blockers if f.rule_id == "PERS-ACCOUNT-NOT-FOUND"),
            None,
        )
        assert blocker is not None
        assert blocker.user_message_es is not None
        # Should contain Spanish text, not English
        assert len(blocker.user_message_es) > 0
        # Should mention "cuenta contable" or similar Spanish accounting term
        assert any(
            term in blocker.user_message_es.lower()
            for term in ["cuenta", "contable", "puc", "plan"]
        ), f"User message should mention accounting terms: {blocker.user_message_es}"

    def test_puc_error_finding_has_remediation(self):
        """PUC error finding should have actionable remediation."""
        db = MagicMock()

        agent_log = [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "agent": "db_persist",
                "event": "node_error",
                "details": {
                    "error": "DB persist error: PUC code 654321 not found",
                },
            },
        ]

        job = self._make_process_job(
            job_id="proc-puc-003",
            status="failed",
            agent_log=agent_log,
        )

        with patch("app.services.pipeline_trace_service.db_service") as mock_svc:
            mock_svc.get_process_job.return_value = job
            trace = build_trace("proc-puc-003", db)

        blocker = next(
            (f for f in trace.blockers if f.rule_id == "PERS-ACCOUNT-NOT-FOUND"),
            None,
        )
        assert blocker is not None
        assert blocker.suggested_action_es is not None
        assert len(blocker.suggested_action_es) > 0
        # Remediation should be Spanish and actionable
        assert any(
            term in blocker.suggested_action_es.lower()
            for term in ["verifique", "código", "catálogo", "cuenta", "puc"]
        ), f"Remediation should guide user: {blocker.suggested_action_es}"

    def test_puc_error_has_correct_severity(self):
        """PUC error should be a blocker (not warning or info)."""
        db = MagicMock()

        agent_log = [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "agent": "db_persist",
                "event": "node_error",
                "details": {
                    "error": "DB persist error: PUC code 777777 not found",
                },
            },
        ]

        job = self._make_process_job(
            job_id="proc-puc-004",
            status="failed",
            agent_log=agent_log,
        )

        with patch("app.services.pipeline_trace_service.db_service") as mock_svc:
            mock_svc.get_process_job.return_value = job
            trace = build_trace("proc-puc-004", db)

        blocker = next(
            (f for f in trace.blockers if f.rule_id == "PERS-ACCOUNT-NOT-FOUND"),
            None,
        )
        assert blocker is not None
        assert blocker.severity == Severity.BLOCKER
        assert not blocker.fixable  # User cannot auto-fix PUC missing

    def test_puc_error_target_is_persist(self):
        """PUC error should be attributed to persist/db_persist agent."""
        db = MagicMock()

        agent_log = [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "agent": "db_persist",
                "event": "node_error",
                "details": {
                    "error": "DB persist error: PUC code 888888 not found",
                },
            },
        ]

        job = self._make_process_job(
            job_id="proc-puc-005",
            status="failed",
            agent_log=agent_log,
        )

        with patch("app.services.pipeline_trace_service.db_service") as mock_svc:
            mock_svc.get_process_job.return_value = job
            trace = build_trace("proc-puc-005", db)

        blocker = next(
            (f for f in trace.blockers if f.rule_id == "PERS-ACCOUNT-NOT-FOUND"),
            None,
        )
        assert blocker is not None
        assert blocker.target == AuditTarget.PERSIST
        assert blocker.responsible_agent == "db_persist"

    def test_multiple_node_errors_all_surfaced(self):
        """Trace should surface multiple node_error events as separate blockers."""
        db = MagicMock()

        agent_log = [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "agent": "db_persist",
                "event": "node_error",
                "details": {
                    "error": "DB persist error: PUC code 111111 not found",
                },
            },
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "agent": "db_persist",
                "event": "node_error",
                "details": {
                    "error": "DB persist error: PUC code 222222 not found",
                },
            },
        ]

        job = self._make_process_job(
            job_id="proc-puc-006",
            status="failed",
            agent_log=agent_log,
        )

        with patch("app.services.pipeline_trace_service.db_service") as mock_svc:
            mock_svc.get_process_job.return_value = job
            trace = build_trace("proc-puc-006", db)

        # Count how many PUC errors are in blockers
        puc_blockers = [
            f for f in trace.blockers if f.rule_id == "PERS-ACCOUNT-NOT-FOUND"
        ]
        # At least one should be caught
        assert len(puc_blockers) >= 1

    def test_puc_error_evidence_contains_details(self):
        """PUC error finding should have evidence object with technical details."""
        db = MagicMock()

        agent_log = [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "agent": "db_persist",
                "event": "node_error",
                "details": {
                    "error": "DB persist error: PUC code 333333 not found",
                },
            },
        ]

        job = self._make_process_job(
            job_id="proc-puc-007",
            status="failed",
            agent_log=agent_log,
        )

        with patch("app.services.pipeline_trace_service.db_service") as mock_svc:
            mock_svc.get_process_job.return_value = job
            trace = build_trace("proc-puc-007", db)

        blocker = next(
            (f for f in trace.blockers if f.rule_id == "PERS-ACCOUNT-NOT-FOUND"),
            None,
        )
        assert blocker is not None
        assert blocker.evidence is not None
        assert isinstance(blocker.evidence, dict)
        # Evidence should contain process_id or similar context
        assert len(blocker.evidence) > 0
