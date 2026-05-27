"""
Preflight validation service for DIAN declaration generation.

Returns a structured report of blockers / warnings / info checks that the UI
can render before allowing the user to click "Generar borrador". Re-uses
the same db_service helpers used by tax_declaration_service so signals
stay consistent between preflight and actual generation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.database import (
    CompanySettings,
    ProcessJob,
    ProcessStatus,
    TransactionPending,
)
from app.services import db_service

VALID_FORM_TYPES = {"F300", "F350", "F110", "F2516", "ICA"}

SEVERITY_BLOCKER = "blocker"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

CTA_SETTINGS = "/settings"


def _cta_tax_constants(year: int) -> str:
    return f"/settings/tax-constants?year={year}"


def _cta_f2516(year: int) -> str:
    return f"/tax?tab=declarations&form=F2516&year={year}"


@dataclass
class PreflightCheck:
    code: str
    severity: str  # "blocker" | "warning" | "info"
    passed: bool
    message: str
    cta_path: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "code": self.code,
            "severity": self.severity,
            "passed": self.passed,
            "message": self.message,
            "cta_path": self.cta_path,
        }
        if self.metadata:
            out["metadata"] = self.metadata
        return out


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sum_credits_class4(ledger: List[Dict[str, Any]]) -> float:
    return sum(r["total_credit"] for r in ledger if r["account"].startswith("4"))


def _gross_income_for_year(db: Session, company_nit: str, year: int) -> float:
    """Sum of clase 4 credits over [year-01-01, year-12-31]."""
    start_dt = datetime(year, 1, 1, 0, 0, 0)
    end_dt = datetime(year, 12, 31, 23, 59, 59)
    ledger = db_service.get_general_ledger(
        db=db,
        start_date=start_dt,
        end_date=end_dt,
        company_nit=company_nit,
    )
    return _sum_credits_class4(ledger)


def _settings_complete(settings: Optional[CompanySettings]) -> tuple[bool, list[str]]:
    """Return (complete, missing_fields)."""
    if settings is None:
        return False, [
            "nit",
            "ciudad",
            "codigo_ciiu",
            "regimen_tributario",
            "actividad_economica",
        ]
    required = [
        "nit",
        "ciudad",
        "codigo_ciiu",
        "regimen_tributario",
        "actividad_economica",
    ]
    missing = [f for f in required if not getattr(settings, f, None)]
    return (len(missing) == 0), missing


def _count_pending_hitl(
    db: Session, company_nit: str, period_start: date, period_end: date
) -> int:
    """
    Count process jobs in PENDING_AUDIT_REVIEW state with at least one
    TransactionPending in the requested period for this company.
    """
    start_dt = datetime.combine(period_start, datetime.min.time())
    end_dt = datetime.combine(period_end, datetime.max.time().replace(microsecond=0))
    return (
        db.query(ProcessJob)
        .join(
            TransactionPending,
            TransactionPending.ingest_id == ProcessJob.ingest_id,
        )
        .filter(
            ProcessJob.status == ProcessStatus.PENDING_AUDIT_REVIEW,
            TransactionPending.company_nit == company_nit,
            TransactionPending.fecha >= start_dt,
            TransactionPending.fecha <= end_dt,
        )
        .distinct()
        .count()
    )


# ---------------------------------------------------------------------------
# Base checks (all forms)
# ---------------------------------------------------------------------------


def _check_company_settings(
    settings: Optional[CompanySettings],
) -> PreflightCheck:
    complete, missing = _settings_complete(settings)
    if complete:
        return PreflightCheck(
            code="COMPANY_SETTINGS_COMPLETE",
            severity=SEVERITY_BLOCKER,
            passed=True,
            message="Configuración de la empresa completa.",
            cta_path=None,
        )
    return PreflightCheck(
        code="COMPANY_SETTINGS_COMPLETE",
        severity=SEVERITY_BLOCKER,
        passed=False,
        message=(
            "Faltan campos en la configuración de la empresa: " + ", ".join(missing)
        ),
        cta_path=CTA_SETTINGS,
        metadata={"missing_fields": missing},
    )


def _check_ledger_not_empty(
    db: Session, company_nit: str, period_start: date, period_end: date
) -> PreflightCheck:
    start_dt = datetime.combine(period_start, datetime.min.time())
    end_dt = datetime.combine(period_end, datetime.max.time().replace(microsecond=0))
    ledger = db_service.get_general_ledger(
        db=db,
        start_date=start_dt,
        end_date=end_dt,
        company_nit=company_nit,
    )
    if ledger:
        return PreflightCheck(
            code="LEDGER_NOT_EMPTY",
            severity=SEVERITY_BLOCKER,
            passed=True,
            message=f"Libro mayor contiene {len(ledger)} cuentas en el período.",
            metadata={"accounts": len(ledger)},
        )
    return PreflightCheck(
        code="LEDGER_NOT_EMPTY",
        severity=SEVERITY_BLOCKER,
        passed=False,
        message=(
            "No hay movimientos contables en el período seleccionado. "
            "Cargue documentos y procéselos antes de generar la declaración."
        ),
    )


def _check_uvt_year(db: Session, year: int) -> PreflightCheck:
    uvt = db_service.get_uvt(db, year)
    if uvt is not None:
        return PreflightCheck(
            code="UVT_YEAR_AVAILABLE",
            severity=SEVERITY_BLOCKER,
            passed=True,
            message=f"UVT {year} disponible: {uvt}.",
            metadata={"uvt": str(uvt)},
        )
    return PreflightCheck(
        code="UVT_YEAR_AVAILABLE",
        severity=SEVERITY_BLOCKER,
        passed=False,
        message=(
            f"No hay valor UVT registrado para el año {year}. "
            f"Regístrelo en la configuración de constantes tributarias."
        ),
        cta_path=_cta_tax_constants(year),
    )


def _check_period_not_future(period_end: date) -> PreflightCheck:
    today = date.today()
    if period_end <= today:
        return PreflightCheck(
            code="PERIOD_NOT_FUTURE",
            severity=SEVERITY_WARNING,
            passed=True,
            message="El período no termina en el futuro.",
        )
    return PreflightCheck(
        code="PERIOD_NOT_FUTURE",
        severity=SEVERITY_WARNING,
        passed=False,
        message=(
            f"El período termina el {period_end.isoformat()}, una fecha futura. "
            f"Verifique que sea correcto."
        ),
    )


def _check_no_pending_hitl(
    db: Session, company_nit: str, period_start: date, period_end: date
) -> PreflightCheck:
    try:
        pending = _count_pending_hitl(db, company_nit, period_start, period_end)
    except Exception:
        # Defensive: if status column or fecha column not present, treat as 0
        pending = 0
    if pending == 0:
        return PreflightCheck(
            code="NO_PENDING_HITL",
            severity=SEVERITY_WARNING,
            passed=True,
            message="No hay transacciones pendientes de revisión humana en el período.",
        )
    return PreflightCheck(
        code="NO_PENDING_HITL",
        severity=SEVERITY_WARNING,
        passed=False,
        message=(
            f"Hay {pending} transacción(es) en estado 'pending_audit_review'. "
            f"Resuelva las revisiones antes de declarar para evitar omisiones."
        ),
        metadata={"pending_count": pending},
    )


# ---------------------------------------------------------------------------
# Form-specific checks
# ---------------------------------------------------------------------------


def _check_iva_responsable(settings: CompanySettings) -> PreflightCheck:
    if bool(getattr(settings, "iva_responsable", False)):
        return PreflightCheck(
            code="IVA_RESPONSABLE",
            severity=SEVERITY_BLOCKER,
            passed=True,
            message="Empresa responsable de IVA.",
        )
    return PreflightCheck(
        code="IVA_RESPONSABLE",
        severity=SEVERITY_BLOCKER,
        passed=False,
        message=(
            "La empresa no está marcada como responsable de IVA. "
            "No es posible generar F300."
        ),
        cta_path=CTA_SETTINGS,
    )


def _check_tasa_iva_general(settings: CompanySettings) -> PreflightCheck:
    tasa = getattr(settings, "tasa_iva_general", None)
    if tasa is not None and Decimal(str(tasa)) > 0:
        return PreflightCheck(
            code="TASA_IVA_GENERAL",
            severity=SEVERITY_BLOCKER,
            passed=True,
            message=f"Tasa IVA general configurada: {tasa}.",
        )
    return PreflightCheck(
        code="TASA_IVA_GENERAL",
        severity=SEVERITY_BLOCKER,
        passed=False,
        message="Falta configurar la tasa de IVA general.",
        cta_path=CTA_SETTINGS,
    )


def _aplica_reteica(settings: CompanySettings) -> bool:
    """Best-effort: settings.aplica_reteica if present, else tasa_reteica > 0."""
    if hasattr(settings, "aplica_reteica"):
        val = getattr(settings, "aplica_reteica")
        if val is not None:
            return bool(val)
    tasa = getattr(settings, "tasa_reteica", None)
    if tasa is None:
        return False
    try:
        return Decimal(str(tasa)) > 0
    except Exception:
        return False


def _check_reteica_tarifa(
    db: Session, settings: CompanySettings
) -> Optional[PreflightCheck]:
    """Skip (return None) if company doesn't apply reteica."""
    if not _aplica_reteica(settings):
        return None
    ciudad = getattr(settings, "ciudad", None) or ""
    ciiu = getattr(settings, "codigo_ciiu", None) or ""
    tarifa = db_service.get_reteica_tarifa(db, ciudad, ciiu)
    if tarifa is not None:
        return PreflightCheck(
            code="RETEICA_TARIFA_AVAILABLE",
            severity=SEVERITY_BLOCKER,
            passed=True,
            message=f"Tarifa ReteICA disponible para {ciudad}/{ciiu}: {tarifa}.",
            metadata={"tarifa": str(tarifa)},
        )
    return PreflightCheck(
        code="RETEICA_TARIFA_AVAILABLE",
        severity=SEVERITY_BLOCKER,
        passed=False,
        message=(
            f"No hay tarifa ReteICA registrada para {ciudad}/{ciiu}. "
            f"Regístrela antes de continuar."
        ),
        cta_path=CTA_SETTINGS,
    )


def _check_iva_periodicidad(
    db: Session,
    company_nit: str,
    period_start: date,
    period_end: date,
) -> PreflightCheck:
    """
    Art. 600 ET — bimestral if previous-year gross income >= 92.000 UVT,
    cuatrimestral otherwise. Warn if period length doesn't match expected.
    """
    days = (period_end - period_start).days + 1
    prev_year = period_end.year - 1
    uvt_prev = db_service.get_uvt(db, prev_year)
    if uvt_prev is None:
        return PreflightCheck(
            code="IVA_PERIODICIDAD_VALID",
            severity=SEVERITY_WARNING,
            passed=True,
            message=(
                f"No se puede validar periodicidad IVA (Art. 600 ET): "
                f"falta UVT del año {prev_year}."
            ),
            metadata={"days": days},
        )
    gross_prev = _gross_income_for_year(db, company_nit, prev_year)
    threshold = float(uvt_prev) * 92000
    expected = "bimestral" if gross_prev >= threshold else "cuatrimestral"
    if expected == "bimestral":
        ok = 55 <= days <= 65
        rango = "60 ± 5"
    else:
        ok = 110 <= days <= 130
        rango = "120 ± 10"
    if ok:
        return PreflightCheck(
            code="IVA_PERIODICIDAD_VALID",
            severity=SEVERITY_WARNING,
            passed=True,
            message=(
                f"Periodicidad IVA esperada: {expected} ({rango} días). "
                f"Período actual: {days} días."
            ),
            metadata={"expected": expected, "days": days},
        )
    return PreflightCheck(
        code="IVA_PERIODICIDAD_VALID",
        severity=SEVERITY_WARNING,
        passed=False,
        message=(
            f"Periodicidad IVA esperada {expected} ({rango} días) pero el período "
            f"tiene {days} días. Verifique si el período es correcto."
        ),
        metadata={
            "expected": expected,
            "days": days,
            "gross_prev_year": gross_prev,
            "threshold": threshold,
        },
    )


def _check_base_minima_available(db: Session, year: int) -> PreflightCheck:
    constants = db_service.list_tax_constants(db, year)
    base_minima = constants.get("base_minima") or []
    if base_minima:
        return PreflightCheck(
            code="BASE_MINIMA_AVAILABLE",
            severity=SEVERITY_BLOCKER,
            passed=True,
            message=f"Bases mínimas registradas para {year} ({len(base_minima)} conceptos).",
        )
    return PreflightCheck(
        code="BASE_MINIMA_AVAILABLE",
        severity=SEVERITY_BLOCKER,
        passed=False,
        message=(
            f"No hay bases mínimas registradas para {year}. "
            f"Configúrelas antes de generar el F350."
        ),
        cta_path=_cta_tax_constants(year),
    )


def _check_f2516_reviewed(db: Session, company_nit: str, year: int) -> PreflightCheck:
    """
    F2516 only required when previous-year gross income >= 45.000 UVT × UVT_value
    (Art. 772-1 ET).
    """
    uvt = db_service.get_uvt(db, year)
    if uvt is None:
        return PreflightCheck(
            code="F2516_REVIEWED",
            severity=SEVERITY_BLOCKER,
            passed=False,
            message=(
                f"No se puede evaluar la obligatoriedad de F2516: "
                f"falta UVT del año {year}."
            ),
            cta_path=_cta_tax_constants(year),
        )
    prev_year = year - 1
    gross_prev = _gross_income_for_year(db, company_nit, prev_year)
    threshold = float(uvt) * 45000
    if gross_prev < threshold:
        return PreflightCheck(
            code="F2516_REVIEWED",
            severity=SEVERITY_BLOCKER,
            passed=True,
            message=(
                f"F2516 no obligatorio: ingresos brutos {prev_year} "
                f"({gross_prev:,.0f}) por debajo de 45.000 UVT ({threshold:,.0f})."
            ),
            metadata={
                "gross_prev_year": gross_prev,
                "threshold": threshold,
                "required": False,
            },
        )
    f2516 = db_service.get_latest_f2516_reviewed(db, company_nit, year)
    if f2516 is not None:
        return PreflightCheck(
            code="F2516_REVIEWED",
            severity=SEVERITY_BLOCKER,
            passed=True,
            message=f"F2516 revisado disponible para {year}.",
            metadata={"required": True},
        )
    return PreflightCheck(
        code="F2516_REVIEWED",
        severity=SEVERITY_BLOCKER,
        passed=False,
        message=(
            f"F110 requiere F2516 (Conciliación Fiscal) en estado 'reviewed' "
            f"para el año {year} (Art. 772-1 ET — ingresos ≥ 45.000 UVT)."
        ),
        cta_path=_cta_f2516(year),
        metadata={
            "gross_prev_year": gross_prev,
            "threshold": threshold,
            "required": True,
        },
    )


def _check_tarifas_renta(
    db: Session, settings: CompanySettings, year: int
) -> PreflightCheck:
    regimen = getattr(settings, "regimen_tributario", None) or "ordinario"
    actividad = getattr(settings, "actividad_economica", None) or "general"
    tarifa = db_service.get_tarifa_renta(db, regimen, actividad, year)
    if tarifa is not None:
        return PreflightCheck(
            code="TARIFAS_RENTA_AVAILABLE",
            severity=SEVERITY_BLOCKER,
            passed=True,
            message=(f"Tarifa renta disponible para {regimen}/{actividad} en {year}."),
        )
    return PreflightCheck(
        code="TARIFAS_RENTA_AVAILABLE",
        severity=SEVERITY_BLOCKER,
        passed=False,
        message=(
            f"No hay tarifa de renta registrada para régimen={regimen}, "
            f"actividad={actividad}, año={year}."
        ),
        cta_path=_cta_tax_constants(year),
    )


def _check_retenciones_anio_anterior(
    db: Session, company_nit: str, year: int
) -> PreflightCheck:
    total = db_service.sum_retenciones_anio(db, company_nit, year - 1)
    if total > 0:
        return PreflightCheck(
            code="RETENCIONES_ANIO_ANTERIOR",
            severity=SEVERITY_INFO,
            passed=True,
            message=(
                f"Retenciones a favor del año {year - 1}: "
                f"${float(total):,.0f}. Se descontarán del anticipo."
            ),
            metadata={"total": str(total)},
        )
    return PreflightCheck(
        code="RETENCIONES_ANIO_ANTERIOR",
        severity=SEVERITY_INFO,
        passed=False,
        message=(
            f"No se encontraron retenciones a favor en {year - 1}. "
            f"Revise si corresponde."
        ),
        metadata={"total": "0"},
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_preflight(
    db: Session,
    company_nit: str,
    form_type: str,
    period_start: date,
    period_end: date,
) -> Dict[str, Any]:
    """
    Run all preflight checks for the given form_type + period.

    Returns a dict matching the documented response schema:
        {
            "ready": bool,
            "form_type": str,
            "period_start": str,
            "period_end": str,
            "checks": [...],
            "blockers": int,
            "warnings": int,
        }
    """
    if form_type not in VALID_FORM_TYPES:
        raise ValueError(
            f"Unsupported form_type: {form_type}. Must be one of {sorted(VALID_FORM_TYPES)}"
        )

    settings = (
        db.query(CompanySettings).filter(CompanySettings.nit == company_nit).first()
    )

    year = period_end.year
    checks: List[PreflightCheck] = []

    # ── Base checks (all forms) ─────────────────────────────────────────────
    checks.append(_check_company_settings(settings))
    checks.append(_check_ledger_not_empty(db, company_nit, period_start, period_end))
    checks.append(_check_uvt_year(db, year))
    checks.append(_check_period_not_future(period_end))
    checks.append(_check_no_pending_hitl(db, company_nit, period_start, period_end))

    # ── Form-specific checks (only if settings exist) ───────────────────────
    if settings is not None:
        if form_type == "F300":
            checks.append(_check_iva_responsable(settings))
            checks.append(_check_tasa_iva_general(settings))
            reteica = _check_reteica_tarifa(db, settings)
            if reteica is not None:
                checks.append(reteica)
            checks.append(
                _check_iva_periodicidad(db, company_nit, period_start, period_end)
            )
        elif form_type == "F350":
            checks.append(_check_base_minima_available(db, year))
        elif form_type == "F110":
            checks.append(_check_f2516_reviewed(db, company_nit, year))
            checks.append(_check_tarifas_renta(db, settings, year))
            checks.append(_check_retenciones_anio_anterior(db, company_nit, year))
        elif form_type == "ICA":
            reteica = _check_reteica_tarifa(db, settings)
            if reteica is not None:
                checks.append(reteica)
        # F2516 has no additional checks beyond the base.

    blockers = sum(1 for c in checks if c.severity == SEVERITY_BLOCKER and not c.passed)
    warnings = sum(1 for c in checks if c.severity == SEVERITY_WARNING and not c.passed)
    ready = blockers == 0

    return {
        "ready": ready,
        "form_type": form_type,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "checks": [c.to_dict() for c in checks],
        "blockers": blockers,
        "warnings": warnings,
    }
