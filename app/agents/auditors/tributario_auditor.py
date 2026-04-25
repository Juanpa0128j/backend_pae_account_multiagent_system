"""Deterministic tributario auditor — IVA rate and tax structure checks."""

import time
from decimal import Decimal

from app.agents.state import AgentState
from app.models.audit import AuditFinding, AuditReport, AuditTarget, Severity

_VALID_IVA_RATES: frozenset[Decimal] = frozenset(
    {Decimal("0"), Decimal("0.05"), Decimal("0.19")}
)
_RATE_TOLERANCE = Decimal("0.001")


def _iva_rate_valid(tarifa: Decimal) -> bool:
    return any(abs(tarifa - valid) < _RATE_TOLERANCE for valid in _VALID_IVA_RATES)


def run(state: AgentState, attempt: int = 1) -> AuditReport:
    """Run deterministic tributario checks and return an AuditReport."""
    t0 = time.monotonic()
    findings: list[AuditFinding] = []

    tributario_output = state.get("tributario_output") or {}

    if not tributario_output:
        duration_ms = (time.monotonic() - t0) * 1000
        return AuditReport(
            target=AuditTarget.TRIBUTARIO,
            approved=True,
            findings=[],
            attempt=attempt,
            duration_ms=duration_ms,
        )

    impuestos = tributario_output.get("impuestos") or []

    for imp in impuestos:
        if not isinstance(imp, dict):
            continue
        tipo = str(imp.get("tipo", "")).lower()
        if "iva" not in tipo:
            continue

        raw_tarifa = imp.get("tarifa")
        if raw_tarifa is None:
            continue
        try:
            tarifa = Decimal(str(raw_tarifa))
        except Exception:
            continue

        if not _iva_rate_valid(tarifa):
            findings.append(
                AuditFinding(
                    target=AuditTarget.TRIBUTARIO,
                    rule_id="TRIB-IVA-RATE-INVALID",
                    severity=Severity.ERROR,
                    fixable=True,
                    responsible_agent="tributario",
                    technical_message=(
                        f"IVA tarifa {tarifa} not in {{0, 0.05, 0.19}} (Art. 468 ET)."
                    ),
                    user_message_es=(
                        f"La tarifa de IVA {float(tarifa):.0%} no es válida en Colombia. "
                        "Las tarifas permitidas son 0%, 5% o 19%."
                    ),
                    suggested_action_es=(
                        "Corrija la tarifa de IVA al valor correspondiente "
                        "(0%, 5% o 19%) según el Art. 468 ET."
                    ),
                    evidence={"tarifa": str(tarifa), "tipo": tipo},
                )
            )

    has_blocker = any(f.severity == Severity.BLOCKER for f in findings)
    has_error = any(f.severity == Severity.ERROR for f in findings)
    approved = not has_blocker and not has_error

    duration_ms = (time.monotonic() - t0) * 1000
    return AuditReport(
        target=AuditTarget.TRIBUTARIO,
        approved=approved,
        findings=findings,
        attempt=attempt,
        duration_ms=duration_ms,
    )
