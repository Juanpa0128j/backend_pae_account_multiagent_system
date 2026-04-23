"""
Tax Declaration Service — Pre-filled DIAN draft generator (2026)

Generates draft tax forms from journal entry data so accountants can review
and submit. Responsibility for final filing remains with the Contador Público
(Ley 43/1990). Every field marked requires_review=True needs explicit accountant
action before submission.

Supported forms:
  F300 — Declaración de IVA (bimestral / cuatrimestral)
  F350 — Retención en la Fuente (mensual)
  F110 — Renta Personas Jurídicas (anual, cuotas)
  ICA  — Industria y Comercio municipal

Usage:
    from app.services.tax_declaration_service import generate_declaration_draft

    draft = generate_declaration_draft(
        db=db,
        company_nit="900123456",
        form_type="F300",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 2, 28),
    )
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.services import db_service
from app.models.database import CompanySettings, TaxDeclarationDraft

# ---------------------------------------------------------------------------
# Internal data structures
# ---------------------------------------------------------------------------


@dataclass
class DraftField:
    renglon: str
    label: str
    value: float
    source: str
    confidence: str  # "high" | "medium" | "low"
    requires_review: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "renglon": self.renglon,
            "label": self.label,
            "value": self.value,
            "source": self.source,
            "confidence": self.confidence,
            "requires_review": self.requires_review,
        }


@dataclass
class DraftWarning:
    field: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return {"field": self.field, "message": self.message}


# ---------------------------------------------------------------------------
# Ledger helpers
# ---------------------------------------------------------------------------


def _sum_credits(ledger: List[Dict[str, Any]], prefix: str) -> float:
    return sum(r["total_credit"] for r in ledger if r["account"].startswith(prefix))


def _sum_debits(ledger: List[Dict[str, Any]], prefix: str) -> float:
    return sum(r["total_debit"] for r in ledger if r["account"].startswith(prefix))


def _exact_credit(ledger: List[Dict[str, Any]], account: str) -> float:
    for r in ledger:
        if r["account"] == account:
            return r["total_credit"]
    return 0.0


def _exact_debit(ledger: List[Dict[str, Any]], account: str) -> float:
    for r in ledger:
        if r["account"] == account:
            return r["total_debit"]
    return 0.0


# ---------------------------------------------------------------------------
# F300 — Declaración de IVA
# ---------------------------------------------------------------------------


def _build_f300(
    ledger: List[Dict[str, Any]],
    settings: CompanySettings,
) -> tuple[List[DraftField], List[DraftWarning]]:
    """
    F300 IVA draft — filled from PUC accounts:
      240808 (IVA generado), 240802 (IVA descontable)
    """
    fields: List[DraftField] = []
    warnings: List[DraftWarning] = []

    iva_generado_19 = _exact_credit(ledger, "240808")
    iva_descontable = _exact_debit(ledger, "240802")
    iva_neto = max(0.0, iva_generado_19 - iva_descontable)

    fields.append(
        DraftField(
            "42",
            "IVA generado tarifa general 19%",
            round(iva_generado_19, 2),
            "cuenta_240808",
            "high",
            False,
        )
    )
    fields.append(
        DraftField(
            "66",
            "IVA descontable (compras y servicios)",
            round(iva_descontable, 2),
            "cuenta_240802",
            "high",
            False,
        )
    )
    fields.append(
        DraftField(
            "84",
            "Saldo a favor período anterior",
            0.0,
            "declaracion_anterior",
            "low",
            True,
        )
    )
    fields.append(
        DraftField(
            "89",
            "Total IVA a pagar (calculado)",
            round(iva_neto, 2),
            "calculado",
            "high",
            False,
        )
    )
    fields.append(
        DraftField("97", "Sanciones (si aplica)", 0.0, "input_manual", "low", True)
    )

    warnings.append(
        DraftWarning(
            "84",
            "Saldo a favor período anterior debe tomarse de la declaración presentada del período inmediatamente anterior.",
        )
    )
    warnings.append(
        DraftWarning(
            "97",
            "Sanciones requieren análisis del contador — verifique extemporaneidad o inexactitud.",
        )
    )

    if not settings.iva_responsable:
        warnings.append(
            DraftWarning(
                "general",
                "Empresa configurada como no responsable de IVA. Verifique que F300 aplica.",
            )
        )

    return fields, warnings


# ---------------------------------------------------------------------------
# F350 — Retención en la Fuente
# ---------------------------------------------------------------------------


def _build_f350(
    ledger: List[Dict[str, Any]],
    settings: CompanySettings,
) -> tuple[List[DraftField], List[DraftWarning]]:
    """
    F350 Retefuente draft — pulled from cuenta 2365 (Retefuente por pagar).
    cuenta 2368 used for ReteICA practicada.
    """
    fields: List[DraftField] = []
    warnings: List[DraftWarning] = []

    retefuente_total = _exact_credit(ledger, "2365")
    reteica_practicada = _exact_credit(ledger, "2368")

    fields.append(
        DraftField(
            "25",
            "Retenciones practicadas — Compras y Servicios",
            round(retefuente_total, 2),
            "cuenta_2365",
            "high",
            False,
        )
    )
    fields.append(
        DraftField(
            "35",
            "Retenciones ICA practicadas",
            round(reteica_practicada, 2),
            "cuenta_2368",
            "high",
            False,
        )
    )
    fields.append(
        DraftField(
            "50",
            "Retenciones sobre salarios (Art. 383 ET)",
            0.0,
            "nomina_no_disponible",
            "low",
            True,
        )
    )
    fields.append(
        DraftField(
            "75",
            "Pagos al exterior sujetos a retención",
            0.0,
            "input_manual",
            "low",
            True,
        )
    )
    fields.append(
        DraftField("97", "Sanciones (si aplica)", 0.0, "input_manual", "low", True)
    )

    warnings.append(
        DraftWarning(
            "50",
            "Retenciones sobre salarios requieren datos de nómina — no disponibles en el sistema.",
        )
    )
    warnings.append(
        DraftWarning(
            "75",
            "Pagos al exterior: verifique convenios de doble tributación aplicables.",
        )
    )

    return fields, warnings


# ---------------------------------------------------------------------------
# F110 — Renta Personas Jurídicas
# ---------------------------------------------------------------------------


def _build_f110(
    ledger: List[Dict[str, Any]],
    settings: CompanySettings,
) -> tuple[List[DraftField], List[DraftWarning]]:
    """
    F110 Renta PJ draft — requires full-year ledger.
    ICA deducible: 511505 + 521505.
    Retenciones a favor: 135518 / 135515.
    """
    fields: List[DraftField] = []
    warnings: List[DraftWarning] = []

    activos = _sum_debits(ledger, "1")
    pasivos = _sum_credits(ledger, "2")
    ingresos_brutos = _sum_credits(ledger, "4")
    costos_venta = _sum_debits(ledger, "6")
    gastos_operacionales = _sum_debits(ledger, "5")
    ica_deducible = _exact_debit(ledger, "511505") + _exact_debit(ledger, "521505")
    retenciones_favor = _exact_debit(ledger, "135518") + _exact_debit(ledger, "135515")

    renta_liquida = max(
        0.0, ingresos_brutos - costos_venta - gastos_operacionales - ica_deducible
    )
    tasa_renta = float(settings.tasa_renta) if settings.tasa_renta else 0.35
    impuesto_basico = round(renta_liquida * tasa_renta, 2)
    total_a_cargo = max(0.0, impuesto_basico - retenciones_favor)

    fields.extend(
        [
            DraftField(
                "26", "Total activos", round(activos, 2), "clase_1_puc", "high", False
            ),
            DraftField(
                "27", "Total pasivos", round(pasivos, 2), "clase_2_puc", "high", False
            ),
            DraftField(
                "40",
                "Ingresos brutos operacionales",
                round(ingresos_brutos, 2),
                "clase_4_puc",
                "high",
                False,
            ),
            DraftField(
                "52",
                "Costos de venta",
                round(costos_venta, 2),
                "clase_6_puc",
                "high",
                False,
            ),
            DraftField(
                "60",
                "Gastos operacionales",
                round(gastos_operacionales, 2),
                "clase_5_puc",
                "high",
                False,
            ),
            DraftField(
                "63",
                "ICA deducible (511505+521505)",
                round(ica_deducible, 2),
                "cuentas_511505_521505",
                "high",
                False,
            ),
            DraftField(
                "72",
                "Renta líquida gravable (estimada)",
                round(renta_liquida, 2),
                "calculado",
                "medium",
                True,
            ),
            DraftField(
                "80",
                "Impuesto básico de renta",
                round(impuesto_basico, 2),
                "calculado",
                "medium",
                True,
            ),
            DraftField(
                "86", "Descuentos tributarios", 0.0, "input_manual", "low", True
            ),
            DraftField(
                "88",
                "Total impuesto a cargo",
                round(total_a_cargo, 2),
                "calculado",
                "medium",
                True,
            ),
            DraftField(
                "92",
                "Retenciones en la fuente a favor",
                round(retenciones_favor, 2),
                "cuentas_135518_135515",
                "high",
                False,
            ),
            DraftField(
                "95", "Anticipo año siguiente", 0.0, "requiere_historico", "low", True
            ),
            DraftField("97", "Sanciones (si aplica)", 0.0, "input_manual", "low", True),
        ]
    )

    warnings.extend(
        [
            DraftWarning(
                "72",
                "Renta líquida es estimación contable — la depuración fiscal puede diferir (diferencias temporarias, deducciones especiales).",
            ),
            DraftWarning(
                "86",
                "Descuentos tributarios (donaciones, ICA pagado, otros) requieren clasificación explícita del contador.",
            ),
            DraftWarning(
                "95",
                "Anticipo año siguiente requiere histórico de dos años — calcule según Art. 807 ET.",
            ),
            DraftWarning(
                "general",
                "F110 requiere F2516 (Conciliación Fiscal) antes de presentar. El sistema no genera F2516 automáticamente.",
            ),
        ]
    )

    return fields, warnings


# ---------------------------------------------------------------------------
# ICA Municipal
# ---------------------------------------------------------------------------


def _build_ica(
    ledger: List[Dict[str, Any]],
    settings: CompanySettings,
) -> tuple[List[DraftField], List[DraftWarning]]:
    """
    ICA Municipal draft.
    Ingresos brutos: clase 4. Tarifa: CompanySettings.tasa_ica.
    ReteICA a favor: cuenta 2368 (débitos = retenciones recibidas de clientes).
    """
    fields: List[DraftField] = []
    warnings: List[DraftWarning] = []

    ingresos_brutos = _sum_credits(ledger, "4")
    tasa_ica = float(settings.tasa_ica) if settings.tasa_ica else 0.00690
    ica_a_pagar = round(ingresos_brutos * tasa_ica, 2)
    avisos_tableros = round(ica_a_pagar * 0.15, 2)
    reteica_favor = _exact_debit(ledger, "2368")
    total_a_pagar = max(0.0, ica_a_pagar + avisos_tableros - reteica_favor)

    fields.extend(
        [
            DraftField(
                "1",
                "Ingresos brutos del período",
                round(ingresos_brutos, 2),
                "clase_4_puc",
                "high",
                False,
            ),
            DraftField(
                "2",
                f"ICA a pagar (tasa {tasa_ica:.4%})",
                ica_a_pagar,
                "calculado",
                "high",
                False,
            ),
            DraftField(
                "3",
                "Avisos y tableros (15% ICA)",
                avisos_tableros,
                "calculado",
                "high",
                False,
            ),
            DraftField(
                "4", "Sobretasa bomberil", 0.0, "municipio_especifico", "low", True
            ),
            DraftField(
                "5",
                "ReteICA a favor (cuenta 2368)",
                round(reteica_favor, 2),
                "cuenta_2368",
                "high",
                False,
            ),
            DraftField(
                "6", "Anticipo período anterior", 0.0, "requiere_historico", "low", True
            ),
            DraftField(
                "10",
                "Total ICA a pagar (estimado)",
                round(total_a_pagar, 2),
                "calculado",
                "medium",
                True,
            ),
        ]
    )

    warnings.extend(
        [
            DraftWarning(
                "4",
                f"Sobretasa bomberil varía por municipio ({settings.ciudad or 'no configurado'}) — verifique tarifa vigente.",
            ),
            DraftWarning(
                "6",
                "Anticipo período anterior requiere la declaración del período anterior.",
            ),
            DraftWarning(
                "general",
                f"Tarifa ICA usada: {tasa_ica:.4%} para {settings.ciudad or 'ciudad no configurada'} — confirme que corresponde al CIIU {settings.codigo_ciiu or 'no configurado'}.",
            ),
        ]
    )

    return fields, warnings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_DISCLAIMER = (
    "BORRADOR — Este sistema genera pre-liquidaciones para revisión del Contador Público. "
    "La responsabilidad de la declaración final recae en el profesional habilitado "
    "(Ley 43/1990). Todos los campos marcados requires_review=True requieren acción "
    "explícita antes de presentar."
)

_BUILDERS = {
    "F300": _build_f300,
    "F350": _build_f350,
    "F110": _build_f110,
    "ICA": _build_ica,
}


def generate_declaration_draft(
    db: Session,
    company_nit: str,
    form_type: str,
    period_start: date,
    period_end: date,
) -> TaxDeclarationDraft:
    """
    Generate and persist a pre-filled DIAN declaration draft.

    Args:
        db: SQLAlchemy session
        company_nit: Company NIT (tenant identifier)
        form_type: "F300" | "F350" | "F110" | "ICA"
        period_start: First day of the declaration period
        period_end: Last day of the declaration period

    Returns:
        Persisted TaxDeclarationDraft ORM object

    Raises:
        ValueError: if form_type is unsupported or company not found
    """
    if form_type not in _BUILDERS:
        raise ValueError(
            f"Unsupported form_type: {form_type}. Must be one of {list(_BUILDERS)}"
        )

    settings = (
        db.query(CompanySettings).filter(CompanySettings.nit == company_nit).first()
    )
    if not settings:
        raise ValueError(f"CompanySettings not found for NIT: {company_nit}")

    start_dt = datetime.combine(period_start, datetime.min.time())
    end_dt = datetime.combine(period_end, datetime.max.time().replace(microsecond=0))

    ledger = db_service.get_general_ledger(
        db=db,
        start_date=start_dt,
        end_date=end_dt,
        company_nit=company_nit,
    )

    builder = _BUILDERS[form_type]
    draft_fields, draft_warnings = builder(ledger, settings)

    disclaimer_field = {
        "renglon": "_disclaimer",
        "label": "Aviso legal",
        "value": _DISCLAIMER,
        "source": "sistema",
        "confidence": "high",
        "requires_review": False,
    }

    draft = TaxDeclarationDraft(
        id=str(uuid.uuid4()),
        company_nit=company_nit,
        form_type=form_type,
        period_start=period_start.isoformat(),
        period_end=period_end.isoformat(),
        year=period_end.year,
        status="draft",
        fields_json=[f.to_dict() for f in draft_fields] + [disclaimer_field],
        warnings_json=[w.to_dict() for w in draft_warnings],
    )

    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft


def get_draft(db: Session, draft_id: str) -> Optional[TaxDeclarationDraft]:
    """Retrieve a draft by ID."""
    return (
        db.query(TaxDeclarationDraft).filter(TaxDeclarationDraft.id == draft_id).first()
    )


def update_draft_field(
    db: Session,
    draft_id: str,
    renglon: str,
    new_value: float,
) -> Optional[TaxDeclarationDraft]:
    """
    Accountant updates a requires_review field value.
    Marks the field requires_review=False and confidence=high after update.
    """
    draft = get_draft(db, draft_id)
    if not draft:
        return None

    updated_fields = []
    for f in draft.fields_json:
        if f["renglon"] == renglon:
            f = dict(f)
            f["value"] = new_value
            f["requires_review"] = False
            f["confidence"] = "high"
        updated_fields.append(f)

    draft.fields_json = updated_fields
    db.commit()
    db.refresh(draft)
    return draft
