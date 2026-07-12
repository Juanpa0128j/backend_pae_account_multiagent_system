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

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.services import db_service
from app.services.dian_forms import FormCatalog, get_catalog, has_catalog
from app.services.dian_forms.f350_2026 import CONCEPT_CASILLAS as _F350_CONCEPT_CASILLAS
from app.models.database import CompanySettings, TaxDeclarationDraft
from app.services.tax_constants import (
    TIPO_IVA_EXCLUIDO,
    TIPO_IVA_EXENTO,
    TIPO_IVA_EXPORTACION,
    TIPO_IVA_GRAVADO_5,
    TIPO_IVA_GRAVADO_19,
    TIPO_IVA_NO_GRAVADO,
)

_HELP_TEXTS: dict[str, str] = json.loads(
    (Path(__file__).parent.parent / "data" / "dian_field_help.json").read_text(
        encoding="utf-8"
    )
)

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
    help_text: str | None = None
    seccion: str = ""
    es_subtotal: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "renglon": self.renglon,
            "label": self.label,
            "value": self.value,
            "source": self.source,
            "confidence": self.confidence,
            "requires_review": self.requires_review,
            "help_text": self.help_text,
            "seccion": self.seccion,
            "es_subtotal": self.es_subtotal,
        }


@dataclass
class DraftWarning:
    field: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return {"field": self.field, "message": self.message}


@dataclass
class Computed:
    """A value the builder computed for one official casilla."""

    value: float
    source: str
    confidence: str = "high"
    requires_review: bool = False


def build_draft_from_catalog(
    catalog: "FormCatalog", computed: Dict[str, Computed]
) -> List[DraftField]:
    """Project computed values onto the full official casilla catalog.

    Every official casilla becomes a ``DraftField`` (in form order):
      * subtotal casillas are evaluated from their instructivo formula;
      * casillas the builder computed use the real value/source/confidence;
      * ``manual`` casillas (data the system cannot see) default to 0 and are
        flagged ``requires_review``;
      * remaining ``computed`` casillas with no ledger movement default to 0
        with source ``sin_movimiento`` (a real zero, not a gap — not flagged).

    This makes the resulting draft mirror the real form casilla-by-casilla.
    """
    values: Dict[str, float] = {}
    fields: List[DraftField] = []
    for c in catalog.casillas:
        if c.tipo == "header":
            continue
        if c.tipo == "subtotal" and c.formula is not None:
            val = round(c.formula(lambda n: values.get(n, 0.0)), 2)
            values[c.numero] = val
            fields.append(
                DraftField(
                    c.numero,
                    c.label,
                    val,
                    "calculado",
                    "high",
                    c.requires_review,
                    help_text=c.help_text,
                    seccion=c.seccion,
                    es_subtotal=True,
                )
            )
            continue
        comp = computed.get(c.numero)
        if comp is not None:
            values[c.numero] = comp.value
            fields.append(
                DraftField(
                    c.numero,
                    c.label,
                    round(comp.value, 2),
                    comp.source,
                    comp.confidence,
                    comp.requires_review,
                    help_text=c.help_text,
                    seccion=c.seccion,
                )
            )
        elif c.tipo == "manual":
            values[c.numero] = 0.0
            fields.append(
                DraftField(
                    c.numero,
                    c.label,
                    0.0,
                    "diligenciar_manual",
                    "low",
                    True,
                    help_text=c.help_text,
                    seccion=c.seccion,
                )
            )
        else:
            values[c.numero] = 0.0
            fields.append(
                DraftField(
                    c.numero,
                    c.label,
                    0.0,
                    "sin_movimiento",
                    "medium",
                    False,
                    help_text=c.help_text,
                    seccion=c.seccion,
                )
            )
    return fields


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


def _compute_prorrateo_factor(
    revenue_by_tipo: Dict[str, float],
) -> tuple[float, Dict[str, float], bool]:
    """Compute Art. 490 ET prorrateo factor.

    Returns ``(factor, totals, requires_review)`` where:
      * ``factor`` = (gravados + exportaciones) / total_ingresos clasificados.
        Exportaciones cuentan como gravadas para descontable (Art. 481 ET).
      * ``totals`` decomposes the revenue by bucket for downstream renglones.
      * ``requires_review`` is True whenever the operation is "mixta"
        (i.e. ``0 < factor < 1``) or when revenue is partially unclassified.
    """
    gravado_19 = float(revenue_by_tipo.get(TIPO_IVA_GRAVADO_19, 0.0))
    gravado_5 = float(revenue_by_tipo.get(TIPO_IVA_GRAVADO_5, 0.0))
    exento = float(revenue_by_tipo.get(TIPO_IVA_EXENTO, 0.0))
    excluido = float(revenue_by_tipo.get(TIPO_IVA_EXCLUIDO, 0.0))
    exportacion = float(revenue_by_tipo.get(TIPO_IVA_EXPORTACION, 0.0))
    no_gravado = float(revenue_by_tipo.get(TIPO_IVA_NO_GRAVADO, 0.0))
    sin_clasificar = float(revenue_by_tipo.get("sin_clasificar", 0.0))

    totals = {
        "gravado_19": round(gravado_19, 2),
        "gravado_5": round(gravado_5, 2),
        "exento": round(exento, 2),
        "excluido": round(excluido, 2),
        "exportacion": round(exportacion, 2),
        "no_gravado": round(no_gravado, 2),
        "sin_clasificar": round(sin_clasificar, 2),
    }

    # Numerator: operaciones con derecho a descontable.
    # Exentas (Art. 477/478) y exportaciones (Art. 481) preservan descontable.
    descontable_eligible = gravado_19 + gravado_5 + exento + exportacion
    # Denominator: total de ingresos clasificados (excluye no_gravado y
    # sin_clasificar para no contaminar el factor con renglones no asignables).
    total_clasificado = descontable_eligible + excluido

    if total_clasificado <= 0:
        # No hay base para prorratear. Si hubo ingresos sin clasificar,
        # marcar para revisión y devolver factor 1.0 (no recortar descontable).
        factor = 1.0
        requires_review = sin_clasificar > 0
        return factor, totals, requires_review

    factor = descontable_eligible / total_clasificado
    requires_review = factor < 1.0 or sin_clasificar > 0
    return factor, totals, requires_review


def _build_f300(
    ledger: List[Dict[str, Any]],
    settings: CompanySettings,
    revenue_by_tipo: Optional[Dict[str, float]] = None,
) -> tuple[Dict[str, Computed], List[DraftWarning]]:
    """
    F300 IVA draft — filled from PUC accounts:
      240805 (IVA generado 19%), 240802 (IVA descontable)

    Prorrateo (Art. 490 ET): if ingresos totales (clase 4) exceed the taxable
    base implied by IVA generado, the taxpayer has excluded/exempt sales.
    IVA descontable on common costs must be prorated by the gravado fraction.
    The prorated value is flagged requires_review=True for accountant confirmation.
    """
    computed: Dict[str, Computed] = {}
    warnings: List[DraftWarning] = []

    # IVA generado: scan all 2408xx subaccounts (240802=descontable, 240805=19% generado,
    # plus rate-specific subaccounts for 5%/exempt/INC). Treat the standard 19% slot
    # explicitly so the field downstream still reflects only that rate.
    iva_generado_19 = _exact_credit(ledger, "240805")
    iva_generado_5 = _exact_credit(ledger, "240807")
    iva_descontable = _exact_debit(ledger, "240802")

    # ── Prorrateo Art. 490 ET ──────────────────────────────────────────────
    # Default to False (más seguro): only assume IVA responsibility when the
    # company explicitly opted in via settings.
    iva_responsable = bool(getattr(settings, "iva_responsable", False))
    ingresos_totales_ledger = _sum_credits(ledger, "4")

    # Bucket totals from the per-transaction `tipo_iva` column. When the
    # caller omits the breakdown (legacy path / unit tests), fall back to
    # an empty dict — the helper then yields factor=1.0 and requires_review
    # is False, preserving previous behavior.
    revenue_by_tipo = revenue_by_tipo or {}
    factor_prorrateo, bucket_totals, prorrateo_review = _compute_prorrateo_factor(
        revenue_by_tipo
    )

    if not iva_responsable:
        # Non-IVA-responsable: no proration applies. Pass-through preserves the
        # ledger figure for accountant review without flagging a false warning.
        factor_prorrateo = 1.0
        iva_descontable_prorateable = iva_descontable
        operaciones_mixtas = False
    else:
        iva_descontable_prorateable = round(iva_descontable * factor_prorrateo, 2)
        operaciones_mixtas = factor_prorrateo < 1.0 or prorrateo_review

    # Preserve sign: positive = IVA a pagar, negative = saldo a favor del período
    iva_neto = (iva_generado_19 + iva_generado_5) - iva_descontable_prorateable

    # ── Ingresos por tipo IVA → casillas oficiales ─────────────────────────
    total_ingresos_clasificados = (
        bucket_totals["gravado_19"]
        + bucket_totals["gravado_5"]
        + bucket_totals["exento"]
        + bucket_totals["excluido"]
        + bucket_totals["exportacion"]
    )
    # Ingresos (casilla oficial): 28 gravadas tarifa general, 27 gravadas 5%,
    # 35 exentas, 39 excluidas, 30 exportación bienes, 40 no gravadas.
    computed["28"] = Computed(
        round(bucket_totals["gravado_19"], 2),
        "tipo_iva=gravado_19",
        "high" if bucket_totals["gravado_19"] > 0 else "medium",
    )
    computed["27"] = Computed(
        round(bucket_totals["gravado_5"], 2),
        "tipo_iva=gravado_5",
        "high" if bucket_totals["gravado_5"] > 0 else "medium",
    )
    computed["35"] = Computed(
        round(bucket_totals["exento"], 2),
        "tipo_iva=exento",
        "high" if bucket_totals["exento"] > 0 else "medium",
    )
    computed["39"] = Computed(
        round(bucket_totals["excluido"], 2),
        "tipo_iva=excluido",
        "high" if bucket_totals["excluido"] > 0 else "medium",
    )
    computed["30"] = Computed(
        round(bucket_totals["exportacion"], 2),
        "tipo_iva=exportacion",
        "high" if bucket_totals["exportacion"] > 0 else "medium",
    )
    computed["40"] = Computed(
        round(bucket_totals["no_gravado"], 2),
        "tipo_iva=no_gravado",
        "high" if bucket_totals["no_gravado"] > 0 else "medium",
    )

    # Impuesto generado (casilla oficial): 59 tarifa general, 58 tarifa 5%.
    computed["59"] = Computed(round(iva_generado_19, 2), "cuenta_240805", "high")
    computed["58"] = Computed(
        round(iva_generado_5, 2),
        "cuenta_240807",
        "high" if iva_generado_5 > 0 else "medium",
    )

    # Impuesto descontable: el sistema tiene el total 240802 (ya prorrateado
    # Art. 490 ET) sin desglose por tarifa/tipo → se ubica en la casilla 72
    # (compras de bienes a la tarifa general, el bucket más común) y se marca
    # para revisión del contador cuando hay operaciones mixtas.
    computed["72"] = Computed(
        round(iva_descontable_prorateable, 2),
        f"cuenta_240802 (factor prorrateo {factor_prorrateo:.4%})",
        "high",
        operaciones_mixtas,
    )

    if operaciones_mixtas:
        warnings.append(
            DraftWarning(
                "72",
                f"Prorrateo IVA (Art. 490 ET): gravadas+exportaciones+exentas "
                f"${(bucket_totals['gravado_19'] + bucket_totals['gravado_5'] + bucket_totals['exento'] + bucket_totals['exportacion']):,.2f} "
                f"de ${total_ingresos_clasificados:,.2f} clasificados. "
                f"Factor aplicado: {factor_prorrateo:.4%}. "
                "El IVA descontable (casilla 72) va sin desglose por tarifa — "
                "confirme la distribución en casillas 68-79 con el contador.",
            )
        )
    if bucket_totals["sin_clasificar"] > 0:
        warnings.append(
            DraftWarning(
                "72",
                f"Ingresos sin clasificar por tipo_iva: ${bucket_totals['sin_clasificar']:,.2f}. "
                "Revise transacciones con tipo_iva NULL antes de presentar.",
            )
        )
    if (
        iva_responsable
        and total_ingresos_clasificados == 0
        and ingresos_totales_ledger > 0
    ):
        warnings.append(
            DraftWarning(
                "28",
                "Ingresos en libro mayor sin clasificación tipo_iva. "
                "F300 casillas 27-40 quedan en cero — requiere clasificación manual.",
            )
        )
    if iva_neto < 0:
        warnings.append(
            DraftWarning(
                "83",
                f"Saldo a favor de ${abs(iva_neto):,.2f} — IVA descontable excede al generado. "
                "Verifique si aplica devolución o imputación al siguiente período.",
            )
        )

    warnings.append(
        DraftWarning(
            "84",
            "Saldo a favor período anterior debe tomarse de la declaración presentada del período inmediatamente anterior.",
        )
    )
    warnings.append(
        DraftWarning(
            "87",
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

    return computed, warnings


# ---------------------------------------------------------------------------
# F350 — Retención en la Fuente
# ---------------------------------------------------------------------------


def _build_f350(
    ledger: List[Dict[str, Any]],
    settings: CompanySettings,
    *,
    db: Optional[Any] = None,
    company_nit: Optional[str] = None,
    period_start: Optional[datetime] = None,
    period_end: Optional[datetime] = None,
) -> tuple[Dict[str, Computed], List[DraftWarning]]:
    """F350 Retefuente draft — mapped to official DIAN casillas.

    Each active tax_concepts row with monto > 0 is routed to its official
    retención casilla via ``CONCEPT_CASILLAS`` ((categoria, aplica_a) -> (base,
    retención)). Colliding concepts accumulate. When ``tarifa_default`` is known
    the base casilla is back-estimated (monto / tarifa). Concepts without a
    mapping fall into "Otros pagos" (54 PJ / 108 PN).

    ReteICA is intentionally NOT emitted — it is municipal and lives only on the
    ICA declaration, never on the national F350 (audit fix).
    """
    computed: Dict[str, Computed] = {}
    warnings: List[DraftWarning] = []

    retefuente_total = _exact_credit(ledger, "2365")

    def _add(casilla: str, monto: float, source: str, review: bool = False) -> None:
        prev = computed.get(casilla)
        if prev is None:
            computed[casilla] = Computed(round(monto, 2), source, "high", review)
        else:
            computed[casilla] = Computed(
                round(prev.value + monto, 2),
                prev.source,
                "high",
                prev.requires_review or review,
            )

    # ── Concepto-discriminated casillas ─────────────────────────────────────
    concepto_rows: List[Dict[str, Any]] = []
    if db is not None:
        try:
            concepto_rows = db_service.list_tax_concepts(db, activo=True)
        except Exception as err:  # pragma: no cover — defensive
            warnings.append(
                DraftWarning(
                    "general",
                    f"No se pudo cargar tax_concepts: {err}. Usando F350 legacy.",
                )
            )

    total_discriminated = 0.0
    for concept in concepto_rows:
        code = concept["code"]
        categoria = concept["categoria"]
        aplica_a = concept["aplica_a"]
        # ReteICA is municipal — never on F350.
        if categoria == "ica":
            continue
        try:
            if categoria == "salarios":
                # Nómina is skipped by tributario_agent; read directly from items JSONB
                monto = db_service.sum_nomina_retefuente(
                    db,
                    company_nit=company_nit,
                    start_date=period_start,
                    end_date=period_end,
                )
            else:
                monto = float(
                    db_service.sum_retencion_by_concepto(
                        db,
                        concepto_code=code,
                        company_nit=company_nit,
                        start_date=period_start,
                        end_date=period_end,
                    )
                )
        except Exception as err:  # pragma: no cover — defensive
            warnings.append(
                DraftWarning("130", f"Error sumando retención para {code}: {err}")
            )
            continue

        if monto <= 0:
            continue

        if categoria != "salarios":
            total_discriminated += monto

        mapping = _F350_CONCEPT_CASILLAS.get((categoria, aplica_a))
        if mapping is None:
            # Unmapped concept → "Otros pagos sujetos a retención".
            ret_casilla = "108" if aplica_a == "PN" else "54"
            base_casilla = "92" if aplica_a == "PN" else "41"
        else:
            base_casilla, ret_casilla = mapping

        _add(ret_casilla, monto, f"concepto_{code}")
        # Back-estimate the base from the tarifa when available.
        tarifa_default = concept.get("tarifa_default")
        try:
            tarifa = float(tarifa_default) if tarifa_default is not None else 0.0
        except (TypeError, ValueError):
            tarifa = 0.0
        if tarifa > 0:
            _add(base_casilla, monto / tarifa, f"base estimada {tarifa:.2%}", True)

    # ── Unclassified retenciones → "Otros pagos" (PJ) + warning ─────────────
    if db is not None:
        try:
            unclassified = db_service.count_unclassified_retenciones(
                db,
                company_nit=company_nit,
                start_date=period_start,
                end_date=period_end,
            )
        except Exception:
            unclassified = 0
        gap = round(retefuente_total - total_discriminated, 2)
        if unclassified > 0 or gap > 0.01:
            if gap > 0.01:
                _add("54", gap, "sin_clasificar", True)
            warnings.append(
                DraftWarning(
                    "54",
                    (
                        f"{unclassified} transacción(es) con retefuente sin "
                        "concepto_retencion (${:,.2f}). Clasifíquelas para ubicarlas "
                        "en su casilla F350 correcta.".format(max(gap, 0.0))
                    ),
                )
            )

    warnings.append(
        DraftWarning(
            "129",
            "Pagos al exterior (casillas 55-58/109-112): verifique convenios de "
            "doble tributación aplicables.",
        )
    )

    return computed, warnings


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
) -> tuple[Dict[str, Computed], List[DraftWarning]]:
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
      86_*  Descuentos tributarios itemizados (Art. 254/255/256/256-1/257/257-2/258-1/ZOMAC/otros)
      86  Total descuentos tributarios = Σ renglones 86_*
      88  Impuesto neto = max(0, 80-86)
      92  Retenciones del año (135515+135518)
      93  Saldo a pagar / saldo a favor = 88 - 92
      95_metodo1  Anticipo método 1 = max(0, 88×0.75 - retenciones_año_anterior)
      95_metodo2  Anticipo método 2 = max(0, promedio(88_año, 88_año-1)×0.75 - retenciones_año_anterior)
      95  Anticipo año siguiente (Art. 807 ET) = mayor(método 1, método 2)
      96  Saldo final = 93 + 95
      97  Sanciones (manual)
    """
    computed: Dict[str, Computed] = {}
    warnings: List[DraftWarning] = []

    # ── Patrimonio — desglose por clase PUC (casillas 36-45) ─────────────────
    # El formato pide el patrimonio por tipo de activo; se desglosa desde el
    # libro mayor contable. Si hay F2516 revisado, el pasivo fiscal (249) se usa
    # directamente y el total de activos fiscal (199) se contrasta como aviso.
    activos_contables = _sum_debits(ledger, "1")
    pasivos = _sum_credits(ledger, "2")
    pasivos_source = "clase_2_puc"
    f2516_activos_fiscal: Optional[float] = None
    f2516_rlo: Optional[float] = None

    if db is not None and year is not None and company_nit is not None:
        f2516_reviewed = db_service.get_latest_f2516_reviewed(db, company_nit, year)
        if f2516_reviewed is not None:
            f2516_fields = {
                fld.get("renglon"): fld for fld in (f2516_reviewed.fields_json or [])
            }
            try:
                if "199" in f2516_fields:
                    f2516_activos_fiscal = float(f2516_fields["199"]["value"])
                if "249" in f2516_fields:
                    pasivos = float(f2516_fields["249"]["value"])
                    pasivos_source = "f2516:249"
                if "4" in f2516_fields:
                    f2516_rlo = float(f2516_fields["4"]["value"])
            except (KeyError, TypeError, ValueError):
                pass

    computed["36"] = Computed(round(_sum_debits(ledger, "11"), 2), "clase_11_puc")
    computed["37"] = Computed(round(_sum_debits(ledger, "12"), 2), "clase_12_puc")
    computed["38"] = Computed(round(_sum_debits(ledger, "13"), 2), "clase_13_puc")
    computed["39"] = Computed(round(_sum_debits(ledger, "14"), 2), "clase_14_puc")
    computed["40"] = Computed(round(_sum_debits(ledger, "16"), 2), "clase_16_puc")
    computed["42"] = Computed(round(_sum_debits(ledger, "15"), 2), "clase_15_puc")
    computed["43"] = Computed(
        round(
            _sum_debits(ledger, "17")
            + _sum_debits(ledger, "18")
            + _sum_debits(ledger, "19"),
            2,
        ),
        "clase_17-19_puc",
    )
    computed["45"] = Computed(round(pasivos, 2), pasivos_source)

    if (
        f2516_activos_fiscal is not None
        and abs(f2516_activos_fiscal - activos_contables) > 1.0
    ):
        warnings.append(
            DraftWarning(
                "44",
                f"Activos fiscales en F2516 (${f2516_activos_fiscal:,.2f}) difieren del "
                f"total contable (${activos_contables:,.2f}). El desglose 36-43 es contable; "
                "ajuste por diferencias fiscales (Art. 261 ET) antes de presentar.",
            )
        )
    else:
        warnings.append(
            DraftWarning(
                "44",
                "Patrimonio desglosado desde el libro mayor contable. "
                "Requiere ajustes fiscales (Art. 261 ET) antes de presentar.",
            )
        )

    # ── Ingresos, costos, gastos → casillas componentes ─────────────────────
    renta_bruta = _sum_credits(ledger, "4")
    costos = _sum_debits(ledger, "6")
    gastos_deducibles = _sum_debits(ledger, "5")
    gastos_admin = _sum_debits(ledger, "51")
    gastos_ventas = _sum_debits(ledger, "52")
    gastos_financieros = _sum_debits(ledger, "53")
    gastos_otros = round(
        gastos_deducibles - gastos_admin - gastos_ventas - gastos_financieros, 2
    )

    computed["47"] = Computed(round(_sum_credits(ledger, "41"), 2), "clase_41_puc")
    computed["57"] = Computed(round(_sum_credits(ledger, "42"), 2), "clase_42_puc")
    computed["62"] = Computed(round(costos, 2), "clase_6_puc")
    computed["63"] = Computed(round(gastos_admin, 2), "clase_51_puc")
    computed["64"] = Computed(round(gastos_ventas, 2), "clase_52_puc")
    computed["65"] = Computed(round(gastos_financieros, 2), "clase_53_puc")
    computed["66"] = Computed(max(0.0, gastos_otros), "clase_5_resto")

    # ── Renta líquida ordinaria / compensaciones ────────────────────────────
    # La casilla 72 (RLO) y la 79 (renta líquida gravable) las calcula el
    # catálogo desde las componentes; aquí derivamos el valor para el impuesto
    # y lo contrastamos contra el F2516 conciliado.
    rlo_journal = round(renta_bruta - costos - gastos_deducibles, 2)

    if rlo_journal < 0:
        warnings.append(
            DraftWarning(
                "72",
                f"Pérdida fiscal del período estimada de ${abs(rlo_journal):,.2f}. "
                "Art. 147 ET: pérdidas compensables en los años siguientes.",
            )
        )
    if f2516_rlo is not None and abs(f2516_rlo - rlo_journal) > 1.0:
        warnings.append(
            DraftWarning(
                "72",
                f"Renta líquida conciliada en F2516: ${f2516_rlo:,.2f} (el borrador "
                f"muestra ${max(rlo_journal, 0.0):,.2f} desde el libro mayor). "
                "Use el valor fiscal conciliado.",
            )
        )

    # Pérdidas por compensar → casilla 74 (Compensaciones)
    perdidas_sum = 0.0
    if db is not None and year is not None and company_nit is not None:
        perdidas_dec = db_service.sum_perdidas_disponibles(db, company_nit, year)
        perdidas_sum = float(perdidas_dec)
    computed["74"] = Computed(
        round(perdidas_sum, 2),
        "perdidas_fiscales_acumuladas" if perdidas_sum > 0 else "journal",
        "high" if perdidas_sum > 0 else "medium",
        perdidas_sum > 0,
    )

    # Renta líquida gravable (base del impuesto) — coincide con la casilla 79
    # que calcula el catálogo: max(0, (72 - 74)).
    renta_liquida_gravable = max(0.0, max(0.0, rlo_journal) - perdidas_sum)

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

    # Impuesto sobre la renta líquida gravable → casilla 84.
    computed["84"] = Computed(
        impuesto_basico,
        f"tarifa {tasa_efectiva:.0%} — {base_legal_label}",
        "high",
        True,
    )

    # Descuentos tributarios → casilla 93 (manual). El impuesto neto (casilla 94)
    # lo calcula el catálogo (91 + 92 - 93). ICA NO es descuento — es deducción
    # 100% (Ley 2277/2022 Art. 19 / Art. 115 ET) y ya fluye por la clase 5.
    total_descuentos = 0.0
    impuesto_neto = max(0.0, impuesto_basico - total_descuentos)

    # ── Retenciones del año → casilla 106 (Otras retenciones) ────────────────
    retenciones_anio = 0.0
    if db is not None and year is not None and company_nit is not None:
        retenciones_dec = db_service.sum_retenciones_anio(db, company_nit, year)
        retenciones_anio = float(retenciones_dec)
    else:
        # Fallback: read from ledger (for tests without DB)
        retenciones_anio = _exact_debit(ledger, "135518") + _exact_debit(
            ledger, "135515"
        )

    computed["106"] = Computed(
        round(retenciones_anio, 2), "cuentas_135518_135515", "high"
    )

    # ── Saldo a pagar / a favor (solo para advertencia; casillas 111/113/114
    # las calcula el catálogo) ────────────────────────────────────────────────
    saldo = impuesto_neto - retenciones_anio
    if saldo < 0:
        warnings.append(
            DraftWarning(
                "114",
                f"Saldo a favor de ${abs(saldo):,.2f} — retenciones exceden el impuesto neto. "
                "Verifique si aplica devolución o compensación (Art. 815 ET).",
            )
        )

    # ── Anticipo año siguiente (Art. 807 ET) ─────────────────────────────────
    # El Art. 807 admite DOS bases para liquidar el anticipo y el contribuyente
    # puede optar por cualquiera. Por criterio del CPA tomamos la MAYOR de las dos
    # y, a ambas, se les resta la retención del año (inciso 2.º):
    #   Método 1: impuesto_neto del año                       × porcentaje
    #   Método 2: promedio(impuesto_neto año, año anterior)   × porcentaje
    # El porcentaje es 75% para declarantes con 3+ años; 25%/50% el 1.º/2.º año
    # (no se infiere automáticamente — el contador debe ajustarlo). Ambos campos
    # quedan visibles (95_metodo1 / 95_metodo2) y el renglón 95 requiere revisión.
    ANTICIPO_PORCENTAJE = 0.75

    retenciones_anio_anterior = 0.0
    retenciones_anterior_warning = None
    impuesto_neto_anterior: float | None = None
    if db is not None and year is not None and company_nit is not None:
        ret_ant_dec = db_service.sum_retenciones_anio(db, company_nit, year - 1)
        retenciones_anio_anterior = float(ret_ant_dec)
        if retenciones_anio_anterior == 0.0:
            retenciones_anterior_warning = (
                f"No se encontraron retenciones para el año {year - 1}. "
                "Anticipo calculado asumiendo retenciones anteriores = $0."
            )
        neto_ant = db_service.get_impuesto_neto_anio(db, company_nit, year - 1)
        if neto_ant is not None:
            impuesto_neto_anterior = float(neto_ant)

    # Método 1 — base = impuesto neto del año.
    anticipo_metodo1 = max(
        0.0, impuesto_neto * ANTICIPO_PORCENTAJE - retenciones_anio_anterior
    )
    # Método 2 — base = promedio del impuesto neto del año y del año anterior.
    # Solo disponible si existe declaración de renta del año anterior.
    anticipo_metodo2: Optional[float] = None
    if impuesto_neto_anterior is not None:
        promedio_neto = (impuesto_neto + impuesto_neto_anterior) / 2
        anticipo_metodo2 = max(
            0.0, promedio_neto * ANTICIPO_PORCENTAJE - retenciones_anio_anterior
        )

    # Anticipo (Art. 807) = la MAYOR de las dos bases → casilla 108.
    anticipo = (
        max(anticipo_metodo1, anticipo_metodo2)
        if anticipo_metodo2 is not None
        else anticipo_metodo1
    )
    computed["108"] = Computed(
        round(anticipo, 2), "art_807_mayor_de_dos_metodos", "high", True
    )

    if retenciones_anterior_warning:
        warnings.append(DraftWarning("108", retenciones_anterior_warning))
    if anticipo_metodo2 is None:
        warnings.append(
            DraftWarning(
                "108",
                "Método 2 (promedio de los dos últimos años, Art. 807) no disponible: "
                "falta la declaración de renta del año anterior. Se usó el método 1 "
                f"(${anticipo_metodo1:,.2f}). Verifique el porcentaje de anticipo "
                "(25%/50%/75% según los años como declarante).",
            )
        )
    else:
        warnings.append(
            DraftWarning(
                "108",
                f"Art. 807 permite optar por la base mayor o menor; se tomó la MAYOR "
                f"(${anticipo:,.2f}). Método 1=${anticipo_metodo1:,.2f}, "
                f"Método 2=${anticipo_metodo2:,.2f}. Confirme con el contador la base "
                "y el porcentaje (25%/50%/75%).",
            )
        )

    # ── Standard warnings ────────────────────────────────────────────────────
    warnings.extend(
        [
            DraftWarning(
                "93",
                "Descuentos tributarios requieren verificación explícita del contador "
                "antes de presentar (casilla 93).",
            ),
            DraftWarning(
                "general",
                "F110 requiere F2516 (Conciliación Fiscal) antes de presentar. "
                "Art. 772-1 ET obliga la conciliación fiscal anual.",
            ),
        ]
    )

    return computed, warnings


# ---------------------------------------------------------------------------
# ICA Municipal
# ---------------------------------------------------------------------------


def _build_ica(
    ledger: List[Dict[str, Any]],
    settings: CompanySettings,
) -> tuple[Dict[str, Computed], List[DraftWarning]]:
    """
    ICA Municipal draft (casillas del formato genérico municipal).
    Ingresos brutos: clase 4. Tarifa: CompanySettings.tasa_ica.
    ReteICA a favor: cuenta 2368 (débitos = retenciones recibidas de clientes).
    """
    computed: Dict[str, Computed] = {}
    warnings: List[DraftWarning] = []

    ingresos_brutos = _sum_credits(ledger, "4")
    tasa_ica = float(settings.tasa_ica) if settings.tasa_ica else 0.00690
    ica_a_pagar = round(ingresos_brutos * tasa_ica, 2)
    avisos_tableros = round(ica_a_pagar * 0.15, 2)
    reteica_favor = _exact_debit(ledger, "2368")

    computed["1"] = Computed(round(ingresos_brutos, 2), "clase_4_puc", "high")
    computed["4"] = Computed(ica_a_pagar, f"calculado (tarifa {tasa_ica:.4%})", "high")
    computed["5"] = Computed(avisos_tableros, "calculado (15% ICA)", "high")
    computed["8"] = Computed(round(reteica_favor, 2), "cuenta_2368", "high")

    # Estimate the final balance only to raise a saldo-a-favor warning; the
    # actual casilla 12 is computed by the catalog formula.
    total_a_pagar = ica_a_pagar + avisos_tableros - reteica_favor
    if total_a_pagar < 0:
        warnings.append(
            DraftWarning(
                "12",
                f"Saldo a favor de ${abs(total_a_pagar):,.2f} — ReteICA recibida excede ICA+avisos. "
                "Verifique si aplica devolución municipal o imputación al siguiente período.",
            )
        )

    warnings.extend(
        [
            DraftWarning(
                "6",
                f"Sobretasa bomberil varía por municipio ({settings.ciudad or 'no configurado'}) — verifique tarifa vigente.",
            ),
            DraftWarning(
                "9",
                "Anticipo del año anterior requiere la declaración del período anterior.",
            ),
            DraftWarning(
                "general",
                f"Tarifa ICA usada: {tasa_ica:.4%} para {settings.ciudad or 'ciudad no configurada'} — confirme que corresponde al CIIU {settings.codigo_ciiu or 'no configurado'}.",
            ),
        ]
    )

    return computed, warnings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_DISCLAIMER = (
    "BORRADOR — Este sistema genera pre-liquidaciones para revisión del Contador Público. "
    "La responsabilidad de la declaración final recae en el profesional habilitado "
    "(Ley 43/1990). Todos los campos marcados requires_review=True requieren acción "
    "explícita antes de presentar."
)


_TASA_IMPUESTO_DIFERIDO = 0.35  # Ley 2277/2022 Art. 240 ET


def _f2516_seccion(renglon: str) -> str:
    """Map an F2516 renglón to its official worksheet section (H2/H3/H4)."""
    if renglon == "4":
        return "Resumen fiscal"
    try:
        n = int(renglon)
    except (TypeError, ValueError):
        return "General"
    if 100 <= n <= 199:
        return "ESF — Activos"
    if 200 <= n <= 299:
        return "ESF — Pasivos y patrimonio"
    if 300 <= n <= 399:
        return "ERI — Ingresos"
    if 400 <= n <= 499:
        return "ERI — Costos"
    if 500 <= n <= 599:
        return "ERI — Gastos"
    if 600 <= n <= 699:
        return "ERI — Renta líquida fiscal"
    if 700 <= n <= 799:
        return "Conciliación e impuesto diferido"
    return "General"


def _build_f2516(
    ledger: List[Dict[str, Any]],
    _settings: CompanySettings,
    *,
    db: Optional[Any] = None,
    year: Optional[int] = None,
    company_nit: Optional[str] = None,
) -> tuple[List[DraftField], List[DraftWarning]]:
    """
    F2516 Conciliación Fiscal v9 — auto-poblado desde libro mayor + ajustes_fiscales.

    Estructura simplificada (Res. DIAN 000049/2019, Art. 772-1 ET):
      ESF: activos / pasivos / patrimonio (contable, ajustes, fiscal)
      ERI: ingresos / costos / gastos (contable, ajustes, fiscal) → renta líquida
      Conciliación: diferencias permanentes / temporarias → impuesto diferido (35%)

    Cada renglón fiscal usa ledger (clase PUC) cuando no hay ajustes_fiscales;
    los ajustes se suman como (valor_fiscal - valor_contable). El campo
    ``requires_review`` queda True para conceptos sin ajustes registrados.
    Renglón 4 (Renta líquida fiscal conciliada) se mantiene para compatibilidad
    con _build_f110 que ya lo lee.
    """
    fields: List[DraftField] = []
    warnings: List[DraftWarning] = []

    # ── Helper: load ajustes from DB grouped by seccion ──────────────────────
    ajustes_by_seccion: Dict[str, List[Any]] = {}
    if db is not None and year is not None and company_nit is not None:
        try:
            rows = db_service.list_ajustes_fiscales(db, company_nit, year)
            for r in rows:
                ajustes_by_seccion.setdefault(r.seccion, []).append(r)
        except Exception:
            ajustes_by_seccion = {}

    def _sum_ajustes_delta(seccion: str) -> float:
        """Sum (valor_fiscal - valor_contable) for a given seccion."""
        return sum(
            float(r.valor_fiscal) - float(r.valor_contable)
            for r in ajustes_by_seccion.get(seccion, [])
        )

    def _has_ajustes(seccion: str) -> bool:
        return bool(ajustes_by_seccion.get(seccion))

    # ── ESF — Estado de Situación Financiera ─────────────────────────────────
    efectivo = _sum_debits(ledger, "11") + _sum_debits(ledger, "12")
    cxc = _sum_debits(ledger, "13")
    inventarios = _sum_debits(ledger, "14")
    ppe = _sum_debits(ledger, "15")
    intangibles = _sum_debits(ledger, "16")

    fields.extend(
        [
            DraftField(
                "100",
                "Efectivo y equivalentes (clase 11-12)",
                round(efectivo, 2),
                "journal_entries",
                "high",
                False,
                help_text=_HELP_TEXTS.get("100"),
            ),
            DraftField(
                "130",
                "Cuentas por cobrar (clase 13)",
                round(cxc, 2),
                "journal_entries",
                "high",
                False,
                help_text=_HELP_TEXTS.get("130"),
            ),
            DraftField(
                "150",
                "Inventarios (clase 14)",
                round(inventarios, 2),
                "journal_entries",
                "high",
                False,
                help_text=_HELP_TEXTS.get("150"),
            ),
            DraftField(
                "160",
                "Propiedad, planta y equipo (clase 15)",
                round(ppe, 2),
                "journal_entries",
                "high",
                False,
                help_text=_HELP_TEXTS.get("160"),
            ),
            DraftField(
                "180",
                "Intangibles (clase 16)",
                round(intangibles, 2),
                "journal_entries",
                "high",
                False,
                help_text=_HELP_TEXTS.get("180"),
            ),
        ]
    )

    total_activos_contables = _sum_debits(ledger, "1")
    ajustes_activos = _sum_ajustes_delta("ESF_ACTIVO")
    total_activos_fiscales = total_activos_contables + ajustes_activos

    fields.extend(
        [
            DraftField(
                "190",
                "Total activos contables",
                round(total_activos_contables, 2),
                "journal_entries",
                "high",
                False,
                help_text=_HELP_TEXTS.get("190"),
            ),
            DraftField(
                "191",
                "Ajustes fiscales sobre activos",
                round(ajustes_activos, 2),
                "ajustes_fiscales" if _has_ajustes("ESF_ACTIVO") else "calculated",
                "high" if _has_ajustes("ESF_ACTIVO") else "low",
                not _has_ajustes("ESF_ACTIVO"),
                help_text=_HELP_TEXTS.get("191"),
            ),
            DraftField(
                "199",
                "Total activos fiscales",
                round(total_activos_fiscales, 2),
                "calculated",
                "medium",
                False,
                help_text=_HELP_TEXTS.get("199"),
            ),
        ]
    )

    pasivos_corrientes = (
        _sum_credits(ledger, "21")
        + _sum_credits(ledger, "22")
        + _sum_credits(ledger, "23")
    )
    pasivos_no_corrientes = (
        _sum_credits(ledger, "24")
        + _sum_credits(ledger, "25")
        + _sum_credits(ledger, "26")
        + _sum_credits(ledger, "27")
        + _sum_credits(ledger, "28")
        + _sum_credits(ledger, "29")
    )
    total_pasivos_contables = _sum_credits(ledger, "2")
    ajustes_pasivos = _sum_ajustes_delta("ESF_PASIVO")
    total_pasivos_fiscales = total_pasivos_contables + ajustes_pasivos

    fields.extend(
        [
            DraftField(
                "200",
                "Pasivos corrientes (clase 21-23)",
                round(pasivos_corrientes, 2),
                "journal_entries",
                "high",
                False,
                help_text=_HELP_TEXTS.get("200"),
            ),
            DraftField(
                "220",
                "Pasivos no corrientes",
                round(pasivos_no_corrientes, 2),
                "journal_entries",
                "high",
                False,
                help_text=_HELP_TEXTS.get("220"),
            ),
            DraftField(
                "240",
                "Total pasivos contables",
                round(total_pasivos_contables, 2),
                "journal_entries",
                "high",
                False,
                help_text=_HELP_TEXTS.get("240"),
            ),
            DraftField(
                "241",
                "Ajustes fiscales sobre pasivos",
                round(ajustes_pasivos, 2),
                "ajustes_fiscales" if _has_ajustes("ESF_PASIVO") else "calculated",
                "high" if _has_ajustes("ESF_PASIVO") else "low",
                not _has_ajustes("ESF_PASIVO"),
                help_text=_HELP_TEXTS.get("241"),
            ),
            DraftField(
                "249",
                "Total pasivos fiscales",
                round(total_pasivos_fiscales, 2),
                "calculated",
                "medium",
                False,
                help_text=_HELP_TEXTS.get("249"),
            ),
        ]
    )

    patrimonio_fiscal = total_activos_fiscales - total_pasivos_fiscales
    fields.append(
        DraftField(
            "290",
            "Patrimonio fiscal (199 - 249)",
            round(patrimonio_fiscal, 2),
            "calculated",
            "medium",
            False,
            help_text=_HELP_TEXTS.get("290"),
        )
    )

    # ── ERI — Estado de Resultados Integral ──────────────────────────────────
    ingresos_op = _sum_credits(ledger, "41")
    ingresos_no_op = _sum_credits(ledger, "42")
    total_ingresos_contables = _sum_credits(ledger, "4")
    ajustes_ingresos = _sum_ajustes_delta("ERI_INGRESO")
    total_ingresos_fiscales = total_ingresos_contables + ajustes_ingresos

    fields.extend(
        [
            DraftField(
                "300",
                "Ingresos operacionales (clase 41)",
                round(ingresos_op, 2),
                "journal_entries",
                "high",
                False,
                help_text=_HELP_TEXTS.get("300"),
            ),
            DraftField(
                "310",
                "Ingresos no operacionales (clase 42)",
                round(ingresos_no_op, 2),
                "journal_entries",
                "high",
                False,
                help_text=_HELP_TEXTS.get("310"),
            ),
            DraftField(
                "320",
                "Ajustes fiscales sobre ingresos",
                round(ajustes_ingresos, 2),
                "ajustes_fiscales" if _has_ajustes("ERI_INGRESO") else "calculated",
                "high" if _has_ajustes("ERI_INGRESO") else "low",
                not _has_ajustes("ERI_INGRESO"),
                help_text=_HELP_TEXTS.get("320"),
            ),
            DraftField(
                "329",
                "Total ingresos fiscales",
                round(total_ingresos_fiscales, 2),
                "calculated",
                "medium",
                False,
                help_text=_HELP_TEXTS.get("329"),
            ),
        ]
    )

    costos_contables = _sum_debits(ledger, "6")
    ajustes_costos = _sum_ajustes_delta("ERI_COSTO")
    total_costos_fiscales = costos_contables + ajustes_costos

    fields.extend(
        [
            DraftField(
                "400",
                "Costos (clase 6)",
                round(costos_contables, 2),
                "journal_entries",
                "high",
                False,
                help_text=_HELP_TEXTS.get("400"),
            ),
            DraftField(
                "410",
                "Ajustes fiscales sobre costos",
                round(ajustes_costos, 2),
                "ajustes_fiscales" if _has_ajustes("ERI_COSTO") else "calculated",
                "high" if _has_ajustes("ERI_COSTO") else "low",
                not _has_ajustes("ERI_COSTO"),
                help_text=_HELP_TEXTS.get("410"),
            ),
            DraftField(
                "419",
                "Total costos fiscales",
                round(total_costos_fiscales, 2),
                "calculated",
                "medium",
                False,
                help_text=_HELP_TEXTS.get("419"),
            ),
        ]
    )

    gastos_op = _sum_debits(ledger, "51") + _sum_debits(ledger, "52")
    gastos_no_op = _sum_debits(ledger, "53")
    total_gastos_contables = _sum_debits(ledger, "5")
    ajustes_gastos = _sum_ajustes_delta("ERI_GASTO")
    total_gastos_fiscales = total_gastos_contables + ajustes_gastos

    fields.extend(
        [
            DraftField(
                "500",
                "Gastos operacionales (clase 51-52)",
                round(gastos_op, 2),
                "journal_entries",
                "high",
                False,
                help_text=_HELP_TEXTS.get("500"),
            ),
            DraftField(
                "510",
                "Gastos no operacionales (clase 53)",
                round(gastos_no_op, 2),
                "journal_entries",
                "high",
                False,
                help_text=_HELP_TEXTS.get("510"),
            ),
            DraftField(
                "520",
                "Ajustes fiscales sobre gastos (Art. 107 ET)",
                round(ajustes_gastos, 2),
                "ajustes_fiscales" if _has_ajustes("ERI_GASTO") else "calculated",
                "high" if _has_ajustes("ERI_GASTO") else "low",
                not _has_ajustes("ERI_GASTO"),
                help_text=_HELP_TEXTS.get("520"),
            ),
            DraftField(
                "529",
                "Total gastos fiscales",
                round(total_gastos_fiscales, 2),
                "calculated",
                "medium",
                False,
                help_text=_HELP_TEXTS.get("529"),
            ),
        ]
    )

    renta_liquida_fiscal = (
        total_ingresos_fiscales - total_costos_fiscales - total_gastos_fiscales
    )
    fields.append(
        DraftField(
            "600",
            "Renta líquida fiscal (329 - 419 - 529)",
            round(renta_liquida_fiscal, 2),
            "calculated",
            "medium",
            False,
            help_text=_HELP_TEXTS.get("600"),
        )
    )

    # ── Conciliación: diferencias permanentes / temporarias ──────────────────
    perm = 0.0
    temp_imp = 0.0
    temp_ded = 0.0
    for rows in ajustes_by_seccion.values():
        for r in rows:
            delta = float(r.valor_fiscal) - float(r.valor_contable)
            if r.tipo_diferencia == "permanente":
                perm += delta
            elif r.tipo_diferencia == "temporaria_imponible":
                temp_imp += delta
            elif r.tipo_diferencia == "temporaria_deducible":
                temp_ded += delta

    impuesto_diferido_neto = round((temp_imp - temp_ded) * _TASA_IMPUESTO_DIFERIDO, 2)

    fields.extend(
        [
            DraftField(
                "700",
                "Diferencias permanentes",
                round(perm, 2),
                "ajustes_fiscales" if ajustes_by_seccion else "calculated",
                "high" if ajustes_by_seccion else "low",
                not ajustes_by_seccion,
                help_text=_HELP_TEXTS.get("700"),
            ),
            DraftField(
                "710",
                "Diferencias temporarias imponibles",
                round(temp_imp, 2),
                "ajustes_fiscales" if ajustes_by_seccion else "calculated",
                "high" if ajustes_by_seccion else "low",
                not ajustes_by_seccion,
                help_text=_HELP_TEXTS.get("710"),
            ),
            DraftField(
                "720",
                "Diferencias temporarias deducibles",
                round(temp_ded, 2),
                "ajustes_fiscales" if ajustes_by_seccion else "calculated",
                "high" if ajustes_by_seccion else "low",
                not ajustes_by_seccion,
                help_text=_HELP_TEXTS.get("720"),
            ),
            DraftField(
                "730",
                f"Impuesto diferido neto ({_TASA_IMPUESTO_DIFERIDO:.0%} sobre temporarias)",
                impuesto_diferido_neto,
                "calculated",
                "medium",
                True,
                help_text=_HELP_TEXTS.get("730"),
            ),
        ]
    )

    # ── Compatibility renglón "4" — used by _build_f110 ──────────────────────
    fields.append(
        DraftField(
            "4",
            "Renta líquida fiscal conciliada",
            round(renta_liquida_fiscal, 2),
            "calculated" if not ajustes_by_seccion else "ajustes_fiscales",
            "medium" if not ajustes_by_seccion else "high",
            not ajustes_by_seccion,
            help_text=_HELP_TEXTS.get("4"),
        )
    )

    # ── Warnings ─────────────────────────────────────────────────────────────
    if not ledger:
        warnings.append(
            DraftWarning(
                "general",
                "Sin movimientos contables en el período — todos los renglones quedan en 0.",
            )
        )
    if not ajustes_by_seccion:
        warnings.append(
            DraftWarning(
                "ajustes_fiscales",
                "No se encontraron ajustes fiscales registrados. "
                "Renglones fiscales = renglones contables. Registre los ajustes "
                "(provisiones no deducibles, depreciación acelerada, gastos Art. 107 ET, etc.) "
                "vía PUT /api/v1/tax/ajustes-fiscales antes de presentar.",
            )
        )
    warnings.append(
        DraftWarning(
            "general",
            "F2516 Conciliación Fiscal (Art. 772-1 ET, Res. DIAN 000049/2019). "
            "Valores fiscales se calculan como contable + ajustes_fiscales. "
            "Marque el draft como 'reviewed' para habilitar la generación de F110.",
        )
    )
    # Group the F2516 renglones into the official worksheet sections so the
    # on-screen view and PDF facsimile render them like the prevalidador hojas.
    for f in fields:
        f.seccion = _f2516_seccion(f.renglon)
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
    # Art. 772-1 ET obliges fiscal reconciliation before income tax filing,
    # BUT only when previous-year gross income ≥ 45.000 UVT × UVT_value.
    f2516_skipped_below_threshold = False
    if form_type == "F110":
        year = period_end.year
        # Threshold check: F2516 obligatorio solo si ingresos brutos fiscales
        # del año anterior ≥ 45.000 UVT (Art. 772-1 ET).
        uvt_year = db_service.get_uvt(db, year)
        if uvt_year is not None:
            prev_year = year - 1
            prev_start = datetime(prev_year, 1, 1, 0, 0, 0)
            prev_end = datetime(prev_year, 12, 31, 23, 59, 59)
            prev_ledger = db_service.get_general_ledger(
                db=db,
                start_date=prev_start,
                end_date=prev_end,
                company_nit=company_nit,
            )
            prev_gross = _sum_credits(prev_ledger, "4")
            threshold = float(uvt_year) * 45000
            if prev_gross < threshold:
                f2516_skipped_below_threshold = True
    if form_type == "F110" and not f2516_skipped_below_threshold:
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
    # Catalog-backed forms: the builder returns a {casilla_oficial -> Computed}
    # map and the official catalog projects it into the full form (all casillas,
    # subtotals via formula, manual boxes flagged). This makes the draft mirror
    # the real DIAN form casilla-by-casilla.
    computed: Optional[Dict[str, Computed]] = None
    if form_type == "F110":
        computed, draft_warnings = _build_f110(
            ledger, settings, db=db, year=period_end.year, company_nit=company_nit
        )
    elif form_type == "F350":
        computed, draft_warnings = _build_f350(
            ledger,
            settings,
            db=db,
            company_nit=company_nit,
            period_start=start_dt,
            period_end=end_dt,
        )
    elif form_type == "F300":
        try:
            revenue_by_tipo = db_service.get_revenue_by_tipo_iva(
                db=db,
                start_date=start_dt,
                end_date=end_dt,
                company_nit=company_nit,
            )
        except Exception:
            # Defensive: F300 must still draft even if the breakdown query
            # fails (e.g. tipo_iva column not yet migrated). The builder
            # falls back to legacy behavior when revenue_by_tipo is empty.
            revenue_by_tipo = {}
        if not isinstance(revenue_by_tipo, dict):
            revenue_by_tipo = {}
        computed, draft_warnings = _build_f300(
            ledger, settings, revenue_by_tipo=revenue_by_tipo
        )
    elif form_type == "ICA":
        computed, draft_warnings = _build_ica(ledger, settings)
    elif form_type == "F2516":
        draft_fields, draft_warnings = _build_f2516(
            ledger, settings, db=db, year=period_end.year, company_nit=company_nit
        )
    else:
        draft_fields, draft_warnings = builder(ledger, settings)

    if computed is not None and has_catalog(form_type):
        catalog = get_catalog(form_type, year=period_end.year)
        draft_fields = build_draft_from_catalog(catalog, computed)

    if form_type == "F110" and f2516_skipped_below_threshold:
        draft_warnings.append(
            DraftWarning(
                "f2516",
                "F2516 no obligatorio (ingresos < 45.000 UVT)",
            )
        )

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
