"""
Output Validation Engine for the Multi-Agent System.

Validates that every agent output complies with its Pydantic schema.
Tracks validation metrics (Schema Compliance Rate) and supports
automatic retry routing through the Supervisor.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ValidationError

from app.models.agent_outputs import AGENT_OUTPUT_SCHEMAS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class ValidationStatus(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    ERROR = "error"


@dataclass
class ValidationResult:
    """Result of validating one agent output."""

    agent_name: str
    status: ValidationStatus
    validated_output: Optional[BaseModel] = None
    errors: list[dict[str, Any]] = field(default_factory=list)
    raw_output: Optional[dict[str, Any]] = None
    attempt: int = 1
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    duration_ms: float = 0.0

    @property
    def is_valid(self) -> bool:
        return self.status == ValidationStatus.VALID

    def error_summary(self) -> str:
        """Human-readable error summary for re-routing feedback."""
        if not self.errors:
            return ""
        lines = [
            f"Validation failed for agent '{self.agent_name}' (attempt {self.attempt}):"
        ]
        for err in self.errors:
            loc = " → ".join(str(loc_part) for loc_part in err.get("loc", []))
            msg = err.get("msg", "unknown error")
            lines.append(f"  - [{loc}] {msg}")
        return "\n".join(lines)


@dataclass
class ComplianceRecord:
    """Tracks one validation event for metrics."""

    agent_name: str
    is_compliant: bool
    attempt: int
    timestamp: str
    errors_count: int = 0


# ---------------------------------------------------------------------------
# Validation Engine
# ---------------------------------------------------------------------------


class OutputValidator:
    """
    Central validation engine for all agent outputs.

    Usage:
        validator = OutputValidator()
        result = validator.validate("ingesta", raw_dict)
        if not result.is_valid:
            # Re-route with result.error_summary() as correction feedback
            ...

    Metrics:
        validator.schema_compliance_rate()          # overall
        validator.schema_compliance_rate("ingesta") # per-agent
        validator.get_metrics()                     # full report
    """

    MAX_RETRIES: int = 3  # max correction attempts before hard failure

    def __init__(self) -> None:
        self._history: list[ComplianceRecord] = []

    # -- core validation ---------------------------------------------------

    def validate(
        self,
        agent_name: str,
        raw_output: dict[str, Any],
        *,
        attempt: int = 1,
    ) -> ValidationResult:
        """
        Validate *raw_output* against the schema registered for *agent_name*.

        Args:
            agent_name: Key in AGENT_OUTPUT_SCHEMAS (e.g. "ingesta").
            raw_output: Dictionary produced by the agent (typically parsed
                        from Gemini JSON).
            attempt: Current retry attempt (1-based).

        Returns:
            ValidationResult with status, parsed model (if valid), or errors.
        """
        schema_cls = AGENT_OUTPUT_SCHEMAS.get(agent_name)
        if schema_cls is None:
            logger.error(f"No schema registered for agent '{agent_name}'")
            result = ValidationResult(
                agent_name=agent_name,
                status=ValidationStatus.ERROR,
                raw_output=raw_output,
                attempt=attempt,
                errors=[
                    {
                        "loc": ["__root__"],
                        "msg": f"No schema registered for agent '{agent_name}'",
                        "type": "configuration_error",
                    }
                ],
            )
            self._record(result)
            return result

        start = time.perf_counter()
        try:
            validated = schema_cls.model_validate(raw_output)
            duration = (time.perf_counter() - start) * 1000

            result = ValidationResult(
                agent_name=agent_name,
                status=ValidationStatus.VALID,
                validated_output=validated,
                raw_output=raw_output,
                attempt=attempt,
                duration_ms=round(duration, 2),
            )
            logger.info(
                f"Validation PASSED for '{agent_name}' "
                f"(attempt {attempt}, {result.duration_ms}ms)"
            )

        except ValidationError as exc:
            duration = (time.perf_counter() - start) * 1000
            errors = exc.errors()

            result = ValidationResult(
                agent_name=agent_name,
                status=ValidationStatus.INVALID,
                raw_output=raw_output,
                attempt=attempt,
                duration_ms=round(duration, 2),
                errors=[
                    {
                        "loc": list(e.get("loc", [])),
                        "msg": e.get("msg", ""),
                        "type": e.get("type", ""),
                        "input": e.get("input"),
                    }
                    for e in errors
                ],
            )
            logger.warning(
                f"Validation FAILED for '{agent_name}' "
                f"(attempt {attempt}): {len(errors)} error(s)\n"
                f"{result.error_summary()}"
            )

        except Exception as exc:
            duration = (time.perf_counter() - start) * 1000
            result = ValidationResult(
                agent_name=agent_name,
                status=ValidationStatus.ERROR,
                raw_output=raw_output,
                attempt=attempt,
                duration_ms=round(duration, 2),
                errors=[
                    {
                        "loc": ["__root__"],
                        "msg": str(exc),
                        "type": "unexpected_error",
                    }
                ],
            )
            logger.error(
                f"Validation ERROR for '{agent_name}' (attempt {attempt}): {exc}",
                exc_info=True,
            )

        self._record(result)
        return result

    # -- retry helper ------------------------------------------------------

    def should_retry(self, result: ValidationResult) -> bool:
        """Check whether the failed output should be retried."""
        return not result.is_valid and result.attempt < self.MAX_RETRIES

    def build_correction_prompt(self, result: ValidationResult) -> str:
        """
        Build a correction prompt to send back to the agent,
        explaining exactly what failed and what is expected.
        """
        schema_cls = AGENT_OUTPUT_SCHEMAS.get(result.agent_name)
        schema_json = schema_cls.model_json_schema() if schema_cls else {}

        prompt_lines = [
            "Tu salida anterior NO cumplió el esquema requerido.",
            "",
            "=== ERRORES ENCONTRADOS ===",
            result.error_summary(),
            "",
            "=== ESQUEMA ESPERADO ===",
            str(schema_json),
            "",
            "Por favor, genera una nueva salida en JSON válido que cumpla "
            "estrictamente con el esquema. No incluyas texto adicional.",
        ]
        return "\n".join(prompt_lines)

    # -- metrics -----------------------------------------------------------

    def schema_compliance_rate(self, agent_name: Optional[str] = None) -> float:
        """
        Calculate Schema Compliance Rate.

        Args:
            agent_name: If provided, rate for that agent only.
                        If None, overall rate across all agents.

        Returns:
            Float between 0.0 and 1.0.  Returns 1.0 if no records exist.
        """
        records = self._history
        if agent_name:
            records = [r for r in records if r.agent_name == agent_name]

        if not records:
            return 1.0

        compliant = sum(1 for r in records if r.is_compliant)
        return round(compliant / len(records), 4)

    def get_metrics(self) -> dict[str, Any]:
        """
        Full metrics report.

        Returns dict with:
        - overall_compliance_rate
        - per_agent_compliance_rate  {agent_name: rate}
        - total_validations
        - total_passed
        - total_failed
        - per_agent_detail  {agent_name: {passed, failed, rate}}
        """
        if not self._history:
            return {
                "overall_compliance_rate": 1.0,
                "per_agent_compliance_rate": {},
                "total_validations": 0,
                "total_passed": 0,
                "total_failed": 0,
                "per_agent_detail": {},
            }

        agents = set(r.agent_name for r in self._history)
        per_agent: dict[str, dict[str, Any]] = {}

        for agent in agents:
            recs = [r for r in self._history if r.agent_name == agent]
            passed = sum(1 for r in recs if r.is_compliant)
            failed = len(recs) - passed
            per_agent[agent] = {
                "passed": passed,
                "failed": failed,
                "total": len(recs),
                "rate": round(passed / len(recs), 4) if recs else 1.0,
            }

        total = len(self._history)
        total_passed = sum(1 for r in self._history if r.is_compliant)

        return {
            "overall_compliance_rate": round(total_passed / total, 4),
            "per_agent_compliance_rate": {a: d["rate"] for a, d in per_agent.items()},
            "total_validations": total,
            "total_passed": total_passed,
            "total_failed": total - total_passed,
            "per_agent_detail": per_agent,
        }

    def reset_metrics(self) -> None:
        """Clear all compliance history."""
        self._history.clear()

    # -- internal ----------------------------------------------------------

    def _record(self, result: ValidationResult) -> None:
        self._history.append(
            ComplianceRecord(
                agent_name=result.agent_name,
                is_compliant=result.is_valid,
                attempt=result.attempt,
                timestamp=result.timestamp,
                errors_count=len(result.errors),
            )
        )


# ---------------------------------------------------------------------------
# Singleton instance – importable from anywhere
# ---------------------------------------------------------------------------

_validator_instance: Optional[OutputValidator] = None
_validator_lock = threading.Lock()


def get_validator() -> OutputValidator:
    """Get or create the global OutputValidator singleton."""
    global _validator_instance
    if _validator_instance is None:
        with _validator_lock:
            if _validator_instance is None:
                _validator_instance = OutputValidator()
    return _validator_instance
