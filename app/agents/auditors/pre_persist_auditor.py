"""Deterministic pre-persist auditor for process pipeline integrity checks."""

import time
from decimal import Decimal, InvalidOperation

from app.agents.state import AgentState
from app.models.audit import AuditFinding, AuditReport, AuditTarget, Severity

_BALANCE_TOLERANCE = Decimal("0.01")


def _to_decimal(value) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def run(state: AgentState, attempt: int = 1) -> AuditReport:
    """Run pre-persist deterministic checks and return an AuditReport."""
    t0 = time.monotonic()
    findings: list[AuditFinding] = []

    if state.get("mode") != "process":
        return AuditReport(
            target=AuditTarget.PRE_PERSIST,
            approved=True,
            findings=[],
            attempt=attempt,
            duration_ms=(time.monotonic() - t0) * 1000,
        )

    pending_id = str(state.get("pending_transaction_id") or "").strip()
    if not pending_id:
        findings.append(
            AuditFinding(
                target=AuditTarget.PRE_PERSIST,
                rule_id="PREP-MISSING-PENDING-TRANSACTION",
                severity=Severity.BLOCKER,
                fixable=False,
                responsible_agent="persist",
                technical_message="pending_transaction_id is missing in process mode.",
                user_message_es=(
                    "No se encontró la transacción pendiente que se iba a contabilizar."
                ),
                suggested_action_es="Vuelva a ejecutar el proceso de contabilización.",
            )
        )

    contador_output = state.get("contador_output") or {}
    asientos = (
        contador_output.get("asientos", []) if isinstance(contador_output, dict) else []
    )
    if not asientos:
        findings.append(
            AuditFinding(
                target=AuditTarget.PRE_PERSIST,
                rule_id="PREP-NO-ASIENTOS",
                severity=Severity.BLOCKER,
                fixable=False,
                responsible_agent="persist",
                technical_message="contador_output has no asientos to persist.",
                user_message_es="No hay asientos contables para persistir.",
                suggested_action_es="Vuelva a procesar el documento para generar asientos.",
            )
        )
    else:
        total_debitos = Decimal("0")
        total_creditos = Decimal("0")
        seen_rows: set[tuple[str, str, str]] = set()

        for asiento in asientos:
            if not isinstance(asiento, dict):
                continue
            tipo = str(asiento.get("tipo_movimiento", "")).lower()
            cuenta = str(asiento.get("cuenta_puc", "")).strip()
            valor = _to_decimal(asiento.get("valor"))

            if tipo == "debito":
                total_debitos += valor
            elif tipo == "credito":
                total_creditos += valor

            if not cuenta:
                findings.append(
                    AuditFinding(
                        target=AuditTarget.PRE_PERSIST,
                        rule_id="PREP-MISSING-CUENTA-PUC",
                        severity=Severity.ERROR,
                        fixable=True,
                        responsible_agent="contador",
                        technical_message="At least one asiento is missing cuenta_puc.",
                        user_message_es="Existe un asiento sin cuenta PUC.",
                        suggested_action_es="Corrija los asientos para incluir todas las cuentas PUC.",
                    )
                )

            row_key = (tipo, cuenta, str(valor))
            if row_key in seen_rows:
                findings.append(
                    AuditFinding(
                        target=AuditTarget.PRE_PERSIST,
                        rule_id="PREP-DUPLICATE-ASIENTO",
                        severity=Severity.WARNING,
                        fixable=True,
                        responsible_agent="contador",
                        technical_message="Duplicate asiento line detected in contador output.",
                        user_message_es="Se detectaron líneas repetidas en los asientos.",
                        suggested_action_es="Revise si hay duplicados antes de guardar.",
                    )
                )
            else:
                seen_rows.add(row_key)

        diff = abs(total_debitos - total_creditos)
        if diff > _BALANCE_TOLERANCE:
            findings.append(
                AuditFinding(
                    target=AuditTarget.PRE_PERSIST,
                    rule_id="PREP-PARTIDA-DOBLE-MISMATCH",
                    severity=Severity.BLOCKER,
                    fixable=False,
                    responsible_agent="persist",
                    technical_message=(
                        f"Pre-persist double-entry mismatch: debitos={total_debitos}, "
                        f"creditos={total_creditos}, diferencia={diff}"
                    ),
                    user_message_es="Los asientos no cumplen partida doble.",
                    suggested_action_es="Corrija el descuadre antes de persistir.",
                    evidence={
                        "total_debitos": str(total_debitos),
                        "total_creditos": str(total_creditos),
                        "diferencia": str(diff),
                    },
                )
            )

    has_blocker = any(f.severity == Severity.BLOCKER for f in findings)
    has_error = any(f.severity == Severity.ERROR for f in findings)

    return AuditReport(
        target=AuditTarget.PRE_PERSIST,
        approved=not has_blocker and not has_error,
        findings=findings,
        attempt=attempt,
        duration_ms=(time.monotonic() - t0) * 1000,
    )
