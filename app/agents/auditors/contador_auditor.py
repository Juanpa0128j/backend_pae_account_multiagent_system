"""Deterministic contador auditor — double-entry balance and structure checks."""

import time
from decimal import Decimal, InvalidOperation

from app.agents.state import AgentState
from app.models.audit import AuditFinding, AuditReport, AuditTarget, Severity

_BALANCE_TOLERANCE = Decimal("0.01")


def run(state: AgentState, attempt: int = 1) -> AuditReport:
    """Run deterministic contador checks and return an AuditReport."""
    t0 = time.monotonic()
    findings: list[AuditFinding] = []

    contador_output = state.get("contador_output") or {}
    asientos = (
        contador_output.get("asientos", []) if isinstance(contador_output, dict) else []
    )

    # CONT-EMPTY-ASIENTOS: no journal entries produced
    if not asientos:
        findings.append(
            AuditFinding(
                target=AuditTarget.CONTADOR,
                rule_id="CONT-EMPTY-ASIENTOS",
                severity=Severity.BLOCKER,
                fixable=True,
                responsible_agent="contador",
                technical_message="contador_output has no asientos (journal entries).",
                user_message_es=(
                    "No se generaron asientos contables. "
                    "El clasificador debe volver a procesar las transacciones."
                ),
                suggested_action_es="Revise las transacciones fuente y vuelva a clasificar.",
            )
        )
    else:
        # CONT-BALANCE-MISMATCH: debits ≠ credits (partida doble)
        total_debitos = Decimal("0")
        total_creditos = Decimal("0")
        for asiento in asientos:
            if not isinstance(asiento, dict):
                continue
            try:
                valor = Decimal(str(asiento.get("valor", 0)))
            except InvalidOperation:
                valor = Decimal("0")
            tipo = str(asiento.get("tipo_movimiento", "")).lower()
            if tipo == "debito":
                total_debitos += valor
            elif tipo == "credito":
                total_creditos += valor

        diff = abs(total_debitos - total_creditos)
        if diff > _BALANCE_TOLERANCE:
            findings.append(
                AuditFinding(
                    target=AuditTarget.CONTADOR,
                    rule_id="CONT-BALANCE-MISMATCH",
                    severity=Severity.BLOCKER,
                    fixable=True,
                    responsible_agent="contador",
                    technical_message=(
                        f"Partida doble desequilibrada: "
                        f"débitos={total_debitos}, créditos={total_creditos}, "
                        f"diferencia={diff}"
                    ),
                    user_message_es=(
                        f"Los asientos contables no cuadran. "
                        f"Débitos: ${total_debitos:,.2f} / "
                        f"Créditos: ${total_creditos:,.2f}."
                    ),
                    suggested_action_es=(
                        "El clasificador contable debe corregir los asientos "
                        "para que débitos = créditos."
                    ),
                    evidence={
                        "total_debitos": str(total_debitos),
                        "total_creditos": str(total_creditos),
                        "diferencia": str(diff),
                    },
                )
            )

    has_blocker = any(f.severity == Severity.BLOCKER for f in findings)
    has_error = any(f.severity == Severity.ERROR for f in findings)
    approved = not has_blocker and not has_error

    duration_ms = (time.monotonic() - t0) * 1000
    return AuditReport(
        target=AuditTarget.CONTADOR,
        approved=approved,
        findings=findings,
        attempt=attempt,
        duration_ms=duration_ms,
    )
