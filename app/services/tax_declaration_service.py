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
      240805 (IVA generado 19%), 240802 (IVA descontable)

    Prorrateo (Art. 490 ET): if ingresos totales (clase 4) exceed the taxable
    base implied by IVA generado, the taxpayer has excluded/exempt sales.
    IVA descontable on common costs must be prorated by the gravado fraction.
    The prorated value is flagged requires_review=True for accountant confirmation.
    """
    fields: List[DraftField] = []
    warnings: List[DraftWarning] = []

    # IVA generado: scan all 2408xx subaccounts (240802=descontable, 240805=19% generado,
    # plus rate-specific subaccounts for 5%/exempt/INC). Treat the standard 19% slot
    # explicitly so the field downstream still reflects only that rate.
    iva_generado_19 = _exact_credit(ledger, "240805")
    iva_descontable = _exact_debit(ledger, "240802")

    # ── Prorrateo detection (Art. 490 ET) ──────────────────────────────────
    # Default to False (más seguro): only assume IVA responsibility when the
    # company explicitly opted in via settings. A None value should NOT default
    # to True — that produces misleading prorateo on simplified-régimen tenants.
    iva_responsable = bool(getattr(settings, "iva_responsable", False))
    tasa_iva = (
        float(settings.tasa_iva_general)
        if getattr(settings, "tasa_iva_general", None) is not None
        else 0.19
    )
    ingresos_totales = _sum_credits(ledger, "4")
    # Infer taxable sales base from IVA generated; remainder are excluded/exempt
    if not iva_responsable:
        base_gravada = 0.0
        ingresos_no_gravados = 0.0
        operaciones_mixtas = False
    else:
        base_gravada = (iva_generado_19 / tasa_iva) if tasa_iva else 0.0
        ingresos_no_gravados = max(0.0, ingresos_totales - base_gravada)
        operaciones_mixtas = ingresos_no_gravados > 1.0  # >$1 to ignore float noise

    if operaciones_mixtas and ingresos_totales > 0:
        factor_prorrateo = base_gravada / ingresos_totales
        iva_descontable_prorateable = round(iva_descontable * factor_prorrateo, 2)
    else:
        # Non-IVA-responsable: no proration applies. Pass-through preserves the
        # ledger figure for accountant review without flagging a false warning.
        factor_prorrateo = 1.0
        iva_descontable_prorateable = iva_descontable

    # Preserve sign: positive = IVA a pagar, negative = saldo a favor del período
    iva_neto = iva_generado_19 - iva_descontable_prorateable

    fields.append(
        DraftField(
            "42",
            "IVA generado tarifa general 19%",
            round(iva_generado_19, 2),
            "cuenta_240805",
            "high",
            False,
        )
    )
    fields.append(
        DraftField(
            "66",
            (
                "IVA descontable (compras y servicios)"
                if not operaciones_mixtas
                else f"IVA descontable prorateable (factor {factor_prorrateo:.4%})"
            ),
            round(iva_descontable_prorateable, 2),
            "cuenta_240802",
            "high",
            operaciones_mixtas,  # requires_review when prorated
        )
    )
    if operaciones_mixtas:
        fields.append(
            DraftField(
                "66_base",
                "IVA descontable total antes de prorrateo",
                round(iva_descontable, 2),
                "cuenta_240802",
                "medium",
                True,
            )
        )
        warnings.append(
            DraftWarning(
                "66",
                f"Prorrateo IVA (Art. 490 ET): ingresos no gravados estimados "
                f"${ingresos_no_gravados:,.2f} de ${ingresos_totales:,.2f} totales. "
                f"Factor aplicado: {factor_prorrateo:.4%}. "
                "Confirme factor con desglose real de ventas gravadas vs. excluidas/exentas.",
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
    label_89 = (
        "Total IVA a pagar (calculado)"
        if iva_neto >= 0
        else "Saldo a favor del período (calculado)"
    )
    fields.append(
        DraftField(
            "89",
            label_89,
            round(iva_neto, 2),
            "calculado",
            "high",
            False,
        )
    )
    if iva_neto < 0:
        warnings.append(
            DraftWarning(
                "89",
                f"Saldo a favor de ${abs(iva_neto):,.2f} — IVA descontable excede al generado. "
                "Verifique si aplica devolución o imputación al siguiente período.",
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
    *,
    db: Optional[Any] = None,
    year: Optional[int] = None,
    company_nit: Optional[str] = None,
) -> tuple[List[DraftField], List[DraftWarning]]:
    """
    F110 Renta PJ draft — full DIAN renglones with auto-calculation.

    Renglones produced (DIAN F110 2026):
      26  Total activos
      27  Total pasivos
      40  Renta bruta (clase 4 créditos)
      52  Costos (clase 6 débitos)
      60  Gastos deducibles (clase 5 débitos)
      63  ICA deducible (511505+521505) — ya incluido en gastos clase 5; Ley 2277/2022 Art. 19
      f110_renta_liquida_ordinaria  Renta líquida ordinaria = 40-52-60
      f110_perdidas_compensar       Pérdidas fiscales por compensar (Art. 147)
      f110_rentas_exentas           Rentas exentas (manual)
      72  Renta líquida gravable = max(0, RLO - pérdidas - exentas)
      80  Impuesto básico = 72 × tasa_renta
      86_donaciones Descuento donaciones (Art. 257)
      86_iva_capital Descuento IVA bienes capital (Art. 258-1)
      86_educacion  Descuento inversión educación/innovación (Art. 256)
      86_otros      Otros descuentos tributarios
      86  Total descuentos tributarios
      88  Impuesto neto = max(0, 80-86)
      92  Retenciones del año (135515+135518)
      93  Saldo a pagar / saldo a favor = 88 - 92
      95  Anticipo año siguiente = max(0, 88×0.75 - retenciones_año_anterior)
      96  Saldo final = 93 + 95
      97  Sanciones (manual)
    """
    fields: List[DraftField] = []
    warnings: List[DraftWarning] = []

    # ── Balance sheet fields ─────────────────────────────────────────────────
    activos = _sum_debits(ledger, "1")
    pasivos = _sum_credits(ledger, "2")

    fields.extend(
        [
            DraftField(
                "26", "Total activos", round(activos, 2), "clase_1_puc", "high", False
            ),
            DraftField(
                "27", "Total pasivos", round(pasivos, 2), "clase_2_puc", "high", False
            ),
        ]
    )

    # ── Renta bruta, costos, gastos ─────────────────────────────────────────
    renta_bruta = _sum_credits(ledger, "4")
    costos = _sum_debits(ledger, "6")
    gastos_deducibles = _sum_debits(ledger, "5")

    fields.extend(
        [
            DraftField(
                "40",
                "Renta bruta (ingresos clase 4)",
                round(renta_bruta, 2),
                "clase_4_puc",
                "high",
                False,
            ),
            DraftField(
                "52",
                "Costos (clase 6)",
                round(costos, 2),
                "clase_6_puc",
                "high",
                False,
            ),
            DraftField(
                "60",
                "Gastos deducibles (clase 5)",
                round(gastos_deducibles, 2),
                "clase_5_puc",
                "high",
                False,
            ),
        ]
    )

    # ── Renta líquida ordinaria — may come from F2516 ───────────────────────
    rlo_journal = renta_bruta - costos - gastos_deducibles
    rlo_source = "journal"
    rlo_requires_review = True
    renta_liquida_ordinaria = rlo_journal

    if db is not None and year is not None and company_nit is not None:
        f2516 = db_service.get_latest_f2516_reviewed(db, company_nit, year)
        if f2516 is not None:
            # Extract renta líquida conciliada from F2516 fields_json (renglon "4")
            for fld in f2516.fields_json or []:
                if fld.get("renglon") == "4":
                    try:
                        renta_liquida_ordinaria = float(fld["value"])
                        rlo_source = "f2516"
                        rlo_requires_review = False
                    except (KeyError, TypeError, ValueError):
                        pass
                    break

    fields.append(
        DraftField(
            "f110_renta_liquida_ordinaria",
            "Renta líquida ordinaria",
            round(renta_liquida_ordinaria, 2),
            rlo_source,
            "high" if rlo_source == "f2516" else "medium",
            rlo_requires_review,
        )
    )

    if renta_liquida_ordinaria < 0:
        warnings.append(
            DraftWarning(
                "f110_renta_liquida_ordinaria",
                f"Pérdida fiscal del período estimada de ${abs(renta_liquida_ordinaria):,.2f}. "
                "Art. 147 ET: pérdidas compensables en los 12 años siguientes.",
            )
        )

    # ── Pérdidas fiscales por compensar ─────────────────────────────────────
    perdidas_sum = 0.0
    perdidas_requires_review = False
    if db is not None and year is not None and company_nit is not None:
        perdidas_dec = db_service.sum_perdidas_disponibles(db, company_nit, year)
        perdidas_sum = float(perdidas_dec)
        perdidas_requires_review = perdidas_sum > 0

    fields.append(
        DraftField(
            "f110_perdidas_compensar",
            "Pérdidas fiscales por compensar (Art. 147 ET)",
            round(perdidas_sum, 2),
            "perdidas_fiscales_acumuladas" if perdidas_sum > 0 else "journal",
            "high" if perdidas_sum > 0 else "medium",
            perdidas_requires_review,
        )
    )

    # ── Rentas exentas (manual) ──────────────────────────────────────────────
    fields.append(
        DraftField(
            "f110_rentas_exentas",
            "Rentas exentas",
            0.0,
            "input_manual",
            "low",
            True,
        )
    )

    # ── Renta líquida gravable ───────────────────────────────────────────────
    renta_liquida_gravable = max(
        0.0,
        renta_liquida_ordinaria - perdidas_sum,  # rentas_exentas start at 0
    )

    fields.append(
        DraftField(
            "72",
            "Renta líquida gravable",
            round(renta_liquida_gravable, 2),
            "calculado",
            "medium",
            True,
        )
    )

    # ── Impuesto básico ──────────────────────────────────────────────────────
    # Lookup regulatory tarifa first; fall back to company_settings.tasa_renta
    import logging as _logging

    _log = _logging.getLogger(__name__)

    tarifa_info: dict | None = None
    if db is not None and year is not None:
        try:
            regimen = getattr(settings, "regimen_tributario", None) or "ordinario"
            actividad = getattr(settings, "actividad_economica", None) or "general"
            tarifa_info = db_service.get_tarifa_renta(db, regimen, actividad, year)
        except Exception:
            tarifa_info = None

    if tarifa_info is not None:
        tasa_efectiva = tarifa_info["tarifa_efectiva"]
        base_legal_label = tarifa_info.get("base_legal") or "Art. 240 ET"
    else:
        tasa_efectiva = float(settings.tasa_renta) if settings.tasa_renta else 0.35
        base_legal_label = "Art. 240 ET (Ley 2277/2022)"
        if tarifa_info is None and db is not None and year is not None:
            _log.warning(
                "get_tarifa_renta returned None for regimen=%s actividad=%s year=%s — "
                "falling back to company_settings.tasa_renta=%.4f",
                getattr(settings, "regimen_tributario", "ordinario"),
                getattr(settings, "actividad_economica", "general"),
                year,
                tasa_efectiva,
            )

    impuesto_basico = round(renta_liquida_gravable * tasa_efectiva, 2)

    fields.append(
        DraftField(
            "80",
            f"Impuesto básico de renta (tasa {tasa_efectiva:.0%}) — {base_legal_label}",
            impuesto_basico,
            "calculado" if tarifa_info is None else f"tarifas_renta:{base_legal_label}",
            "medium",
            True,
        )
    )

    # ── Descuentos tributarios (itemized) ───────────────────────────────────
    # ICA NOT a descuento — Ley 2277/2022 Art. 19 converted it to deducción 100%
    # (Art. 115 ET). Already flows via class 5 PUC 511505/521505 into gastos.
    ica_pagado = _exact_debit(ledger, "511505") + _exact_debit(ledger, "521505")

    # Expose as renglon "63" for informational purposes (ICA flows as deducción via clase 5)
    fields.append(
        DraftField(
            "63",
            "ICA deducible (511505+521505) — ya incluido en gastos clase 5",
            round(ica_pagado, 2),
            "cuentas_511505_521505",
            "high",
            False,
        )
    )

    fields.extend(
        [
            # ICA NOT a descuento — Ley 2277/2022 Art. 19 converted it to deducción 100%
            # (Art. 115 ET). Already flows via class 5 PUC 511505/521505 into gastos.
            DraftField(
                "86_donaciones",
                "Descuento donaciones (Art. 257)",
                0.0,
                "input_manual",
                "low",
                True,
            ),
            DraftField(
                "86_iva_capital",
                "Descuento IVA bienes de capital (Art. 258-1)",
                0.0,
                "input_manual",
                "low",
                True,
            ),
            DraftField(
                "86_educacion",
                "Descuento inversión educación / innovación (Art. 256)",
                0.0,
                "input_manual",
                "low",
                True,
            ),
            DraftField(
                "86_otros",
                "Otros descuentos tributarios",
                0.0,
                "input_manual",
                "low",
                True,
            ),
        ]
    )

    total_descuentos = 0.0  # ICA removed as descuento per Ley 2277/2022 Art. 19
    fields.append(
        DraftField(
            "86",
            "Total descuentos tributarios",
            round(total_descuentos, 2),
            "calculado",
            "medium",
            True,
        )
    )

    # ── Impuesto neto ────────────────────────────────────────────────────────
    impuesto_neto = max(0.0, impuesto_basico - total_descuentos)
    fields.append(
        DraftField(
            "88",
            "Impuesto neto de renta",
            round(impuesto_neto, 2),
            "calculado",
            "medium",
            True,
        )
    )

    # ── Retenciones del año ──────────────────────────────────────────────────
    retenciones_anio = 0.0
    if db is not None and year is not None and company_nit is not None:
        retenciones_dec = db_service.sum_retenciones_anio(db, company_nit, year)
        retenciones_anio = float(retenciones_dec)
    else:
        # Fallback: read from ledger (for tests without DB)
        retenciones_anio = _exact_debit(ledger, "135518") + _exact_debit(
            ledger, "135515"
        )

    fields.append(
        DraftField(
            "92",
            "Retenciones en la fuente a favor del año",
            round(retenciones_anio, 2),
            "cuentas_135518_135515",
            "high",
            False,
        )
    )

    # ── Saldo a pagar / a favor ──────────────────────────────────────────────
    saldo = impuesto_neto - retenciones_anio
    fields.append(
        DraftField(
            "93",
            "Saldo a pagar" if saldo >= 0 else "Saldo a favor",
            round(saldo, 2),
            "calculado",
            "medium",
            True,
        )
    )

    if saldo < 0:
        warnings.append(
            DraftWarning(
                "93",
                f"Saldo a favor de ${abs(saldo):,.2f} — retenciones exceden el impuesto neto. "
                "Verifique si aplica devolución o compensación (Art. 815 ET).",
            )
        )

    # ── Anticipo año siguiente (Art. 807 ET) ─────────────────────────────────
    # anticipo = max(0, impuesto_neto × 0.75 - retenciones_año_anterior)
    retenciones_anio_anterior = 0.0
    retenciones_anterior_warning = None
    if db is not None and year is not None and company_nit is not None:
        ret_ant_dec = db_service.sum_retenciones_anio(db, company_nit, year - 1)
        retenciones_anio_anterior = float(ret_ant_dec)
        if retenciones_anio_anterior == 0.0:
            retenciones_anterior_warning = (
                f"No se encontraron retenciones para el año {year - 1}. "
                "Anticipo calculado asumiendo retenciones anteriores = $0."
            )

    anticipo = max(0.0, impuesto_neto * 0.75 - retenciones_anio_anterior)
    fields.append(
        DraftField(
            "95",
            "Anticipo año siguiente (Art. 807 ET)",
            round(anticipo, 2),
            "calculado",
            "medium",
            True,
        )
    )

    if retenciones_anterior_warning:
        warnings.append(DraftWarning("95", retenciones_anterior_warning))

    # ── Saldo final ──────────────────────────────────────────────────────────
    saldo_final = saldo + anticipo
    fields.append(
        DraftField(
            "96",
            "Saldo final (a pagar + anticipo)",
            round(saldo_final, 2),
            "calculado",
            "medium",
            True,
        )
    )

    # ── Sanciones ────────────────────────────────────────────────────────────
    fields.append(
        DraftField("97", "Sanciones (si aplica)", 0.0, "input_manual", "low", True)
    )

    # ── Standard warnings ────────────────────────────────────────────────────
    warnings.extend(
        [
            DraftWarning(
                "f110_renta_liquida_ordinaria",
                "Renta líquida ordinaria es estimación contable — la depuración fiscal puede diferir "
                "(diferencias temporarias, deducciones especiales). "
                + (
                    "Valor tomado de F2516 revisado."
                    if rlo_source == "f2516"
                    else "Sin F2516 revisado: valor calculado desde el libro mayor."
                ),
            ),
            DraftWarning(
                "86",
                "Descuentos tributarios requieren verificación explícita del contador antes de presentar.",
            ),
            DraftWarning(
                "general",
                "F110 requiere F2516 (Conciliación Fiscal) antes de presentar. "
                "Art. 772-1 ET obliga la conciliación fiscal anual.",
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
    # Preserve sign: negative = saldo a favor (reteica excedió ICA+avisos)
    total_a_pagar = ica_a_pagar + avisos_tableros - reteica_favor

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
                (
                    "Total ICA a pagar (estimado)"
                    if total_a_pagar >= 0
                    else "Saldo a favor (reteica excede ICA+avisos)"
                ),
                round(total_a_pagar, 2),
                "calculado",
                "medium",
                True,
            ),
        ]
    )

    if total_a_pagar < 0:
        warnings.append(
            DraftWarning(
                "10",
                f"Saldo a favor de ${abs(total_a_pagar):,.2f} — ReteICA recibida excede ICA+avisos. "
                "Verifique si aplica devolución municipal o imputación al siguiente período.",
            )
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


def _build_f2516(
    _ledger: List[Dict[str, Any]],
    _settings: CompanySettings,
) -> tuple[List[DraftField], List[DraftWarning]]:
    """
    F2516 Conciliación Fiscal — registration stub (Art. 772-1 ET).

    The system cannot auto-fill this form; all fields require explicit accountant
    input. This builder creates a draft record so the F110 prerequisite check
    can verify that the accountant has acknowledged and registered the F2516.
    """
    fields = [
        DraftField(
            "1",
            "Patrimonio contable (del balance fiscal)",
            0.0,
            "input_manual",
            "low",
            True,
        ),
        DraftField(
            "2",
            "Diferencias temporarias activas acumuladas",
            0.0,
            "input_manual",
            "low",
            True,
        ),
        DraftField(
            "3",
            "Diferencias temporarias pasivas acumuladas",
            0.0,
            "input_manual",
            "low",
            True,
        ),
        DraftField(
            "4",
            "Renta líquida fiscal conciliada",
            0.0,
            "input_manual",
            "low",
            True,
        ),
    ]
    warnings = [
        DraftWarning(
            "general",
            "F2516 no puede generarse automáticamente. Complete todos los campos "
            "con el desglose de diferencias temporarias (NIIF vs fiscal) según "
            "Resolución DIAN 000049/2019. Este registro habilita la generación de F110.",
        )
    ]
    return fields, warnings


_BUILDERS = {
    "F300": _build_f300,
    "F350": _build_f350,
    "F110": _build_f110,
    "F2516": _build_f2516,
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
        form_type: "F300" | "F350" | "F110" | "F2516" | "ICA"
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

    # F110 requires F2516 (Conciliación Fiscal) to have been registered first.
    # Art. 772-1 ET obliges fiscal reconciliation before income tax filing.
    if form_type == "F110":
        year = period_end.year
        # Empty draft stubs do not satisfy the regulatory requirement: require
        # the F2516 to have moved past 'draft' status (reviewed or filed) so
        # we have evidence the accountant actually completed the reconciliation.
        f2516 = (
            db.query(TaxDeclarationDraft)
            .filter(
                TaxDeclarationDraft.company_nit == company_nit,
                TaxDeclarationDraft.form_type == "F2516",
                TaxDeclarationDraft.year == year,
            )
            .order_by(TaxDeclarationDraft.created_at.desc())
            .first()
        )
        if not f2516:
            raise ValueError(
                f"F110 para {company_nit} año {year} requiere F2516 (Conciliación Fiscal) "
                f"registrado previamente (Art. 772-1 ET). "
                f"Genere primero el borrador F2516 y revíselo antes de generar el F110."
            )
        if str(f2516.status).lower() not in {"reviewed", "filed"}:
            raise ValueError(
                f"F110 para {company_nit} año {year} requiere que el F2516 esté "
                f"revisado o presentado (estado actual: {f2516.status}). "
                f"Complete los campos requeridos del F2516 y márquelo como 'reviewed' "
                f"antes de generar el F110."
            )

    start_dt = datetime.combine(period_start, datetime.min.time())
    end_dt = datetime.combine(period_end, datetime.max.time().replace(microsecond=0))

    ledger = db_service.get_general_ledger(
        db=db,
        start_date=start_dt,
        end_date=end_dt,
        company_nit=company_nit,
    )

    builder = _BUILDERS[form_type]
    if form_type == "F110":
        draft_fields, draft_warnings = _build_f110(
            ledger, settings, db=db, year=period_end.year, company_nit=company_nit
        )
    else:
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


class FieldNotFoundError(ValueError):
    """Raised when attempting to update a renglon that does not exist on the draft."""


class FieldNotEditableError(ValueError):
    """Raised when attempting to update a field that is not flagged requires_review."""


# Renglones that must never be edited through this endpoint.
_RESERVED_RENGLONES = frozenset({"_disclaimer"})


def update_draft_field(
    db: Session,
    draft_id: str,
    renglon: str,
    new_value: float,
) -> Optional[TaxDeclarationDraft]:
    """
    Accountant updates a requires_review field value.
    Marks the field requires_review=False and confidence=high after update.

    Raises:
        FieldNotFoundError: if `renglon` is not on the draft
        FieldNotEditableError: if field is reserved or not flagged for review
    """
    if renglon in _RESERVED_RENGLONES:
        raise FieldNotEditableError(
            f"Renglon {renglon!r} is reserved and cannot be edited"
        )

    draft = get_draft(db, draft_id)
    if not draft:
        return None

    target = next((f for f in draft.fields_json if f["renglon"] == renglon), None)
    if target is None:
        raise FieldNotFoundError(f"Renglon {renglon!r} not found on draft {draft_id!r}")
    if not target.get("requires_review", False):
        raise FieldNotEditableError(
            f"Renglon {renglon!r} is not flagged requires_review=True and cannot be edited"
        )

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
