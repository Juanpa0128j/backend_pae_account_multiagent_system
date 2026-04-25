"""Deterministic ingest auditor — extraction quality checks."""

import time

from app.agents.state import AgentState
from app.models.audit import AuditFinding, AuditReport, AuditTarget, Severity

_SHORT_TEXT_THRESHOLD = 50


def run(state: AgentState, attempt: int = 1) -> AuditReport:
    """Run deterministic ingest quality checks and return an AuditReport."""
    t0 = time.monotonic()
    findings: list[AuditFinding] = []

    raw_text = state.get("raw_text") or ""
    stripped = raw_text.strip()
    doc_classification = state.get("document_classification") or {}
    doc_type = doc_classification.get("doc_type", "")
    interpreted = state.get("interpreted_data") or {}

    # ING-EMPTY-TEXT: nothing was extracted at all
    if not stripped:
        findings.append(
            AuditFinding(
                target=AuditTarget.INGEST,
                rule_id="ING-EMPTY-TEXT",
                severity=Severity.BLOCKER,
                fixable=False,
                responsible_agent="ingest",
                technical_message="Extracted text is empty — document could not be parsed.",
                user_message_es=(
                    "No se pudo extraer texto del documento. "
                    "Verifique que el archivo no esté corrupto."
                ),
                suggested_action_es=(
                    "Intente volver a subir el documento en formato PDF legible o sin protección."
                ),
            )
        )
    elif len(stripped) < _SHORT_TEXT_THRESHOLD:
        # ING-SHORT-TEXT: extraction returned very little text
        findings.append(
            AuditFinding(
                target=AuditTarget.INGEST,
                rule_id="ING-SHORT-TEXT",
                severity=Severity.WARNING,
                fixable=False,
                responsible_agent="ingest",
                technical_message=(
                    f"Extracted text is very short ({len(stripped)} chars) — "
                    "extraction quality may be low."
                ),
                user_message_es="La extracción del documento fue parcial.",
                evidence={"text_chars": len(stripped)},
            )
        )

    # ING-UNCLASSIFIED-DOC: document type unknown
    if not doc_type or doc_type == "otro":
        findings.append(
            AuditFinding(
                target=AuditTarget.INGEST,
                rule_id="ING-UNCLASSIFIED-DOC",
                severity=Severity.WARNING,
                fixable=False,
                responsible_agent="ingest",
                technical_message=(
                    f"Document classified as '{doc_type}' — type uncertain, "
                    "extraction method may be suboptimal."
                ),
                user_message_es=(
                    "El tipo de documento no fue reconocido automáticamente. "
                    "La extracción puede ser imprecisa."
                ),
                evidence={"doc_type": doc_type},
            )
        )

    # ING-NO-INTERPRETED-DATA: Gemini returned nothing despite non-empty raw text
    if not interpreted and stripped:
        findings.append(
            AuditFinding(
                target=AuditTarget.INGEST,
                rule_id="ING-NO-INTERPRETED-DATA",
                severity=Severity.ERROR,
                fixable=True,
                responsible_agent="ingest",
                technical_message="Gemini returned empty interpreted_data despite non-empty text.",
                user_message_es="El sistema no pudo interpretar el contenido del documento.",
                suggested_action_es="Intente procesar el documento nuevamente.",
            )
        )

    has_blocker = any(f.severity == Severity.BLOCKER for f in findings)
    has_error = any(f.severity == Severity.ERROR for f in findings)
    approved = not has_blocker and not has_error

    duration_ms = (time.monotonic() - t0) * 1000
    return AuditReport(
        target=AuditTarget.INGEST,
        approved=approved,
        findings=findings,
        attempt=attempt,
        duration_ms=duration_ms,
    )
