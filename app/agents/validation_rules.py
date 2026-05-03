"""
Validation nodes for the ingest and process graphs.

Extracted from `supervisor.py` to keep routing (FSM) and validation
(schema checks + business rules) in separate modules. The three public
nodes are:

- `validate_output_node`         — generic schema validation (ingest graph)
- `validate_contador_output_node` — contador schema + PUC existence rule
- `validate_auditor_output_node`  — auditor schema + audit decision propagation

All three append to `state["validation_history"]` and honour the retry
semantics exposed by `validation_engine.OutputValidator`.
"""

from app.agents.agent_utils import append_log
from app.agents.state import AgentState
from app.core.database import SessionLocal
from app.core.logger import get_logger
from app.services import db_service
from app.services.validation_engine import ValidationResult, get_validator

logger = get_logger("app.agents.validation_rules")

MAX_CONTADOR_RETRIES = 3
MAX_AUDITOR_RETRIES = 3

# Phase 4 — per-agent retry budgets and global circuit breaker
RETRY_BUDGETS: dict[str, int] = {
    "ingest": 1,
    "contador": 2,
    "tributario": 2,
}
GLOBAL_AUDIT_FAILURES = 5


# ---------------------------------------------------------------------------
# PUC helpers (used by contador validation)
# ---------------------------------------------------------------------------


def _resolve_puc_code(db, raw_code: str) -> tuple[str | None, str | None]:
    """Resolve a possibly invalid PUC code to an active account code.

    Returns a tuple of (resolved_code, resolved_name).
    """
    code = str(raw_code or "").strip()
    if not code:
        return None, None

    existing = db_service.validate_puc_exists(db, code)
    if existing:
        return code, str(getattr(existing, "nombre", "") or "").strip() or None

    candidates: list[str] = []
    if len(code) >= 6:
        candidates.extend([code[:4], code[:2]])
    elif len(code) == 5:
        candidates.extend([code[:4], code[:2]])
    elif len(code) == 4:
        candidates.append(code[:2])

    class_defaults = {
        "1": ["130505", "110505", "1305", "1105"],
        "2": ["220505", "2205", "2105", "2335"],
        "3": ["3105"],
        "4": ["4170", "4135"],
        "5": ["519595", "5195", "5135", "5110"],
        "6": ["6170", "6135"],
    }
    first_digit = code[:1]
    candidates.extend(class_defaults.get(first_digit, []))

    seen = set()
    for candidate in candidates:
        cand = str(candidate).strip()
        if not cand or cand in seen:
            continue
        seen.add(cand)
        row = db_service.validate_puc_exists(db, cand)
        if row:
            return cand, str(getattr(row, "nombre", "") or "").strip() or None

    return None, None


def _normalize_contador_puc_codes(contador_output: dict) -> dict:
    """Replace missing/non-active PUC codes with active fallback equivalents."""
    asientos = (
        contador_output.get("asientos", []) if isinstance(contador_output, dict) else []
    )
    if not isinstance(asientos, list) or not asientos:
        return contador_output

    db = SessionLocal()
    try:
        for asiento in asientos:
            if not isinstance(asiento, dict):
                continue

            raw_code = str(asiento.get("cuenta_puc") or "").strip()
            if not raw_code:
                continue

            resolved_code, resolved_name = _resolve_puc_code(db, raw_code)
            if resolved_code and resolved_code != raw_code:
                logger.warning(
                    "Supervisor: remapped missing PUC code %s -> %s",
                    raw_code,
                    resolved_code,
                )
                asiento["cuenta_puc"] = resolved_code
                if resolved_name:
                    asiento["nombre_cuenta"] = resolved_name

    finally:
        db.close()

    return contador_output


def _missing_puc_codes(contador_output: dict) -> list[str]:
    """Return missing PUC codes from DB for a contador output payload."""
    asientos = contador_output.get("asientos", [])
    codes = sorted(
        {str(a.get("cuenta_puc", "")).strip() for a in asientos if a.get("cuenta_puc")}
    )
    if not codes:
        return []

    db = SessionLocal()
    try:
        return [code for code in codes if not db_service.validate_puc_exists(db, code)]
    except Exception as e:
        msg = f"DB error during PUC validation: {e}"
        logger.error(msg)
        raise RuntimeError(msg) from e
    finally:
        db.close()


def _hydrate_contador_account_names(contador_output: dict) -> dict:
    """Fill missing `nombre_cuenta` values in contador asientos.

    Priority:
    1) PUC catalog name from DB by `cuenta_puc`
    2) Existing asiento `descripcion`
    3) Fallback to the account code itself
    """
    asientos = (
        contador_output.get("asientos", []) if isinstance(contador_output, dict) else []
    )
    if not isinstance(asientos, list) or not asientos:
        return contador_output

    db = SessionLocal()
    try:
        for asiento in asientos:
            if not isinstance(asiento, dict):
                continue

            nombre_actual = str(asiento.get("nombre_cuenta") or "").strip()
            if nombre_actual:
                continue

            cuenta_puc = str(asiento.get("cuenta_puc") or "").strip()
            nombre_puc = ""
            if cuenta_puc:
                cuenta = db_service.validate_puc_exists(db, cuenta_puc)
                if cuenta and getattr(cuenta, "nombre", None):
                    nombre_puc = str(cuenta.nombre).strip()

            nombre_fallback = (
                nombre_puc
                or str(asiento.get("descripcion") or "").strip()
                or cuenta_puc
                or "Cuenta contable"
            )
            asiento["nombre_cuenta"] = nombre_fallback
    finally:
        db.close()

    return contador_output


# ---------------------------------------------------------------------------
# Validation nodes
# ---------------------------------------------------------------------------


def validate_output_node(state: AgentState) -> AgentState:
    """Generic schema validation node (used by ingest graph)."""
    if state.get("error"):
        return state

    agent_name = state.get("current_agent", "ingesta")
    raw_output = state.get("interpreted_data", {})
    attempt = state.get("retry_count", 0) + 1

    append_log(state, agent_name, "validation_start", {"attempt": attempt})

    validator = get_validator()
    result: ValidationResult = validator.validate(
        agent_name, raw_output, attempt=attempt
    )

    state["validation_history"].append(
        {
            "agent_name": agent_name,
            "attempt": attempt,
            "is_valid": result.is_valid,
            "errors": result.errors,
            "timestamp": result.timestamp,
        }
    )

    if result.is_valid:
        logger.info(f"Supervisor: output from '{agent_name}' VALID (attempt {attempt})")
        state["correction_feedback"] = None
        state["retry_count"] = 0
        if result.validated_output:
            state["result"]["validated_data"] = result.validated_output.model_dump(
                mode="json"
            )
        append_log(state, agent_name, "validation_success", {"attempt": attempt})
        return state

    if validator.should_retry(result):
        logger.warning(
            f"Supervisor: output from '{agent_name}' INVALID — "
            f"scheduling retry {attempt}/{validator.MAX_RETRIES}"
        )
        state["correction_feedback"] = validator.build_correction_prompt(result)
        state["retry_count"] = attempt
        append_log(
            state,
            agent_name,
            "validation_failure",
            {
                "attempt": attempt,
                "error_count": len(result.errors),
                "will_retry": True,
            },
        )
        return state

    logger.error(
        f"Supervisor: output from '{agent_name}' failed after {attempt} attempts"
    )
    state["error"] = (
        f"Schema validation failed for '{agent_name}' after {attempt} attempts. "
        f"Last errors:\n{result.error_summary()}"
    )
    state["correction_feedback"] = None
    state["result"]["status"] = "validation_error"
    state["result"]["validation_errors"] = result.errors
    append_log(
        state,
        agent_name,
        "validation_exhausted",
        {
            "attempt": attempt,
            "errors": result.errors[:3],
        },
    )
    return state


def validate_contador_output_node(state: AgentState) -> AgentState:
    """Validate contador output schema + PUC existence business rule."""
    if state.get("error"):
        return state

    agent_name = "contador"
    raw_output = state.get("contador_output") or state.get("interpreted_data", {})
    raw_output = _hydrate_contador_account_names(raw_output)
    attempt = state.get("retry_count", 0) + 1

    append_log(state, agent_name, "validation_start", {"attempt": attempt})

    validator = get_validator()
    result: ValidationResult = validator.validate(
        agent_name, raw_output, attempt=attempt
    )

    state["validation_history"].append(
        {
            "agent_name": agent_name,
            "attempt": attempt,
            "is_valid": result.is_valid,
            "errors": result.errors,
            "timestamp": result.timestamp,
        }
    )

    if not result.is_valid:
        if validator.should_retry(result):
            state["correction_feedback"] = validator.build_correction_prompt(result)
            state["retry_count"] = attempt
            append_log(
                state,
                agent_name,
                "validation_failure",
                {
                    "attempt": attempt,
                    "error_count": len(result.errors),
                    "will_retry": True,
                },
            )
            return state

        from app.agents.audit_utils import record_giveup
        from app.models.audit import AuditFinding, Severity

        error_list = result.errors[:3] if result.errors else []
        user_msg = (
            f"El agente '{agent_name}' no pudo generar una respuesta válida "
            f"después de {attempt} intentos. "
            + (
                f"Errores: {'; '.join(str(e) for e in error_list)}"
                if error_list
                else ""
            )
        )
        schema_finding = AuditFinding(
            rule_id="SCHEMA_VALIDATION_EXHAUSTED",
            severity=Severity.BLOCKER,
            fixable=False,
            responsible_agent=agent_name,
            evidence={"attempt": attempt, "errors": error_list},
            user_message_es=user_msg,
            suggested_action_es=(
                "Revise el documento fuente y verifique que los datos sean legibles "
                "y estén en el formato esperado. Si el problema persiste, contacte soporte."
            ),
        )
        record_giveup(state, agent_name, [schema_finding])
        state["result"]["status"] = "validation_error"
        state["result"]["validation_errors"] = result.errors
        state["correction_feedback"] = None
        state["current_agent"] = "audit_review_terminal"
        append_log(
            state,
            agent_name,
            "validation_exhausted",
            {
                "attempt": attempt,
                "errors": error_list,
            },
        )
        return state

    validated = (
        result.validated_output.model_dump(mode="json")
        if result.validated_output
        else raw_output
    )
    validated = _normalize_contador_puc_codes(validated)

    try:
        missing = _missing_puc_codes(validated)
    except Exception as e:
        msg = f"DB error during PUC validation: {e}"
        logger.error(msg)
        state["error"] = msg
        append_log(state, agent_name, "node_error", {"error": msg})
        return state

    if missing:
        missing_msg = (
            "Los siguientes codigos PUC no existen o no estan activos en base de datos: "
            + ", ".join(missing)
            + ". Corrige los asientos usando codigos PUC validos."
        )
        if attempt < validator.MAX_RETRIES:
            state["correction_feedback"] = missing_msg
            state["retry_count"] = attempt
            append_log(
                state,
                agent_name,
                "validation_failure",
                {
                    "attempt": attempt,
                    "reason": "missing_puc",
                    "missing": missing,
                },
            )
            return state

        # Exhausted retries for PUC validation — pause for HITL review instead of
        # aborting, so the user can correct the document or override.
        from app.agents.audit_utils import record_giveup
        from app.models.audit import AuditFinding, Severity

        puc_finding = AuditFinding(
            rule_id="PUC_CODES_NOT_FOUND",
            severity=Severity.BLOCKER,
            fixable=False,
            responsible_agent=agent_name,
            evidence={"missing_codes": missing},
            user_message_es=(
                f"Los siguientes códigos PUC no existen en la base de datos: "
                f"{', '.join(missing)}. El agente no pudo corregirlos automáticamente "
                f"tras {attempt} intentos."
            ),
            suggested_action_es=(
                "Revise el documento y corrija los códigos PUC inválidos, "
                "o cargue un documento actualizado con los códigos correctos."
            ),
        )
        record_giveup(state, agent_name, [puc_finding])
        state["current_agent"] = "audit_review_terminal"
        state["correction_feedback"] = None
        append_log(
            state,
            agent_name,
            "validation_exhausted",
            {
                "attempt": attempt,
                "reason": "puc_not_found",
                "missing": missing,
                "next_agent": "audit_review_terminal",
            },
        )
        return state

    state["correction_feedback"] = None
    state["retry_count"] = 0
    state["contador_output"] = validated
    state["interpreted_data"] = validated
    state["current_stage"] = "validated"
    state["result"]["validated_data"] = validated
    append_log(state, agent_name, "validation_success", {"attempt": attempt})
    return state


def validate_auditor_output_node(state: AgentState) -> AgentState:
    """Validate AuditorOutput schema and propagate audit decision into state."""
    if state.get("error"):
        return state

    agent_name = "auditor"
    raw_output = state.get("auditor_output") or {}
    attempt = state.get("retry_count", 0) + 1

    validator = get_validator()
    result: ValidationResult = validator.validate(
        agent_name, raw_output, attempt=attempt
    )

    state["validation_history"].append(
        {
            "agent_name": agent_name,
            "attempt": attempt,
            "is_valid": result.is_valid,
            "errors": result.errors,
            "timestamp": result.timestamp,
        }
    )

    if not result.is_valid:
        if validator.should_retry(result):
            state["correction_feedback"] = validator.build_correction_prompt(result)
            state["retry_count"] = attempt
            return state
        state["error"] = (
            f"Schema validation failed for '{agent_name}' after {attempt} attempts. "
            f"Last errors:\n{result.error_summary()}"
        )
        if not state.get("result"):
            state["result"] = {}
        state["result"]["status"] = "validation_error"
        state["result"]["validation_errors"] = result.errors
        state["correction_feedback"] = None
        return state

    validated = (
        result.validated_output.model_dump(mode="json")
        if result.validated_output
        else raw_output
    )
    state["audit_approved"] = validated.get("aprobado", False)
    state["audit_rejection_reason"] = (
        validated.get("resumen") if not validated.get("aprobado") else None
    )
    state["audit_decision"] = "approved" if validated.get("aprobado") else "rejected"
    state["audit_feedback"] = (
        validated.get("resumen") if not validated.get("aprobado") else None
    )
    state["auditor_output"] = validated
    state["correction_feedback"] = None
    state["retry_count"] = 0
    state["current_stage"] = "audit_complete"
    if not state.get("result"):
        state["result"] = {}
    state["result"]["auditor_output"] = validated
    return state
