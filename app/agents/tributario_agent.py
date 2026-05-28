"""
Agente Tributario (Tax Specialist)

Role (docs/Diseño de arquitectura de agente):
  - Receives classified journal entries from Contador (ContadorOutput).
  - Calculates Retefuente, ReteICA, IVA using deterministic Python functions.
  - Queries RAG normativo for relevant legal articles.
  - Calls the LLM to validate rates and produce legal justification (TaxJustification).
  - Returns TributarioOutput enriched with tax liability accounts (2365 Retefuente, 2368 ReteICA, 240802 IVA descontable).

Tax rates (Colombian legislation, UVT 2026 = $52.374):
  - Retefuente servicios: 4% declarantes, 6% no declarantes (Art. 401 ET)
  - Retefuente compras:   2.5% declarantes, 3.5% no declarantes (Art. 401 ET)
  - Retefuente honorarios/comisiones PJ: 11%; no declarantes: 10%
  - ReteICA:    0.69% default (Decreto 2048/1992)
  - IVA:        19% general (Art. 468 ET), 5% reducida, 0% exento
"""

import logging
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.agents.agent_utils import append_log
from app.agents.state import AgentState
from app.core.llm_client import get_llm_client
from app.services.rag_service import get_rag_service

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tax rate constants — deterministic, never computed by LLM
# ---------------------------------------------------------------------------

TASA_RETEFUENTE: dict[str, Decimal] = {
    # Servicios generales — Art. 401 ET, tarifa depende de si es declarante
    "servicios": Decimal("0.04"),  # declarantes de renta (default)
    "servicios_no_declarante": Decimal("0.06"),  # no declarantes
    # Compras generales — Art. 401 ET
    "bienes": Decimal("0.025"),  # declarantes
    "bienes_no_declarante": Decimal("0.035"),  # no declarantes
    # Honorarios y comisiones — Art. 392 ET
    "honorarios": Decimal("0.11"),  # personas jurídicas o PN contratos >3.300 UVT
    "honorarios_no_declarante": Decimal("0.10"),  # no declarantes
    # Arrendamientos
    "arrendamiento_muebles": Decimal("0.04"),  # bienes muebles
    "arrendamiento": Decimal("0.035"),  # bienes inmuebles declarantes
    "arrendamiento_no_declarante": Decimal("0.035"),  # bienes inmuebles no declarantes
}
TASA_RETEICA_DEFAULT = Decimal("0.0069")  # Tarifa Cali / default municipal

# UVT fallback constants — used when DB has no row for the current year.
# Update every January when DIAN publishes the new UVT via Resolución.
UVT_FALLBACK = Decimal("52374")  # UVT 2026 — Resolución 000238 de 2025
# Keep legacy alias so any external references (tests, scripts) remain valid.
UVT_2026 = UVT_FALLBACK
BASE_MINIMA_RETEFUENTE_UVT: dict[str, Decimal] = {
    "servicios": Decimal("4"),  # 4 UVT/mes — Art. 392 ET servicios generales
    "bienes": Decimal("27"),  # 27 UVT/mes — Art. 401 ET compras
    "arrendamiento": Decimal("27"),  # 27 UVT/mes — Art. 401 ET arrendamientos
}
BASE_MINIMA_RETEICA_UVT = Decimal("4")  # 4 UVT/mes (referencia conservadora)

TASA_IVA: dict[str, Decimal] = {
    "general": Decimal("0.19"),  # Art. 477 ET — tarifa general
    "reducida": Decimal("0.05"),  # Servicios especiales
    "exento": Decimal("0.00"),  # Bienes/servicios exentos
}

# ICA — Impuesto de Industria y Comercio (Ley 14/1983, Decreto 1333/1986)
TASA_ICA_DEFAULT = Decimal("0.00690")  # 6.9‰ — conservative national reference
CUENTA_ICA_GASTO_ADMIN = "511505"  # Gasto ICA administración
CUENTA_ICA_GASTO_VENTAS = "521505"  # Gasto ICA ventas
CUENTA_ICA_GASTO = "511505"  # alias for backward compat — default to admin
CUENTA_ICA_PASIVO = "2368"  # ReteICA por pagar (2026)

# Renta — Art. 240 ET (Ley 2277/2022, vigente año fiscal 2023+)
TASA_RENTA = Decimal("0.35")  # 35% tarifa general sociedades
CUENTA_RENTA_GASTO = "540502"  # Provisión Impuesto de Renta
CUENTA_RENTA_PASIVO = "240405"  # Impuesto de Renta por Pagar

# PUC ranges
PUC_SERVICIOS_START = 5000
PUC_SERVICIOS_END = 5999
PUC_INGRESOS_START = 4000
PUC_INGRESOS_END = 4999

# Tax liability PUC accounts — corrected per Carolina García, Contadora Pública.
# Compra defaults (the company is the buyer, withholds and owes to DIAN).
CUENTA_RETEFUENTE = "2365"  # Retención en la Fuente por pagar (pasivo)
CUENTA_RETEICA = "2368"  # Retención ICA por pagar (pasivo)
CUENTA_IVA_DESCONTABLE = "240802"  # IVA descontable (activo, débito)
CUENTA_IVA = CUENTA_IVA_DESCONTABLE  # Backwards-compat alias
# Venta accounts (the company is the seller, retenciones are received as anticipo).
CUENTA_IVA_GENERADO = "240805"  # IVA generado (pasivo, crédito)
CUENTA_RETEFTE_RECIBIDA = "135515"  # Autorretenciones / retefuente a favor (activo)
CUENTA_RETEFTE_RECIBIDA_ALT = "135518"  # Anticipo impuesto renta (alternativa)
CUENTA_RETEICA_RECIBIDA = "135517"  # ReteICA a favor (activo)


# Doc types where the company is the SELLER. Buyer-side doc types fall through to compra defaults.
_VENTA_DOC_TYPES = frozenset(
    {
        "factura_venta",
        "nota_debito_venta",
        "nota_credito_venta",
        "recibo_caja",
    }
)


def _tax_accounts_for(doc_type: str, cuenta_ica_propio: str | None = None) -> dict:
    """Return PUC accounts + movement direction for the given doc_type.

    For VENTA (the company is the seller):
      - IVA generado is a CREDIT to 240805 (pasivo).
      - Retenciones practicadas by the buyer are DEBITED to 135515/135517 (activo,
        anticipos a favor de la empresa frente a DIAN / municipio).
      - ICA gasto/por_pagar NOT injected automatically — handled at declaration time.

    For COMPRA-like (default, the company is the buyer):
      - IVA descontable is a DEBIT to 240802 (activo recuperable).
      - Retenciones practicadas a proveedores son CRÉDITOS a 2365/2368 (pasivo a DIAN).
      - ICA gasto/por_pagar applied when the line has ingreso credits (legacy).
    """
    ica_pasivo = cuenta_ica_propio or CUENTA_RETEICA
    if doc_type in _VENTA_DOC_TYPES:
        return {
            "iva": (CUENTA_IVA_GENERADO, "credito"),
            "iva_nombre": "IVA Generado",
            "iva_detalle": "IVA generado en venta",
            "retefuente": (CUENTA_RETEFTE_RECIBIDA, "debito"),
            "retefuente_nombre": "Retefuente recibida (anticipo)",
            "retefuente_detalle": "Retención en la fuente practicada por el comprador — anticipo a favor",
            "reteica": (CUENTA_RETEICA_RECIBIDA, "debito"),
            "reteica_nombre": "ReteICA recibida (anticipo)",
            "reteica_detalle": "Retención ICA practicada por el comprador — anticipo a favor",
            "ica_gasto": None,
            "ica_por_pagar": None,
        }
    return {
        "iva": (CUENTA_IVA_DESCONTABLE, "debito"),
        "iva_nombre": "IVA Descontable",
        "iva_detalle": "IVA descontable",
        "retefuente": (CUENTA_RETEFUENTE, "credito"),
        "retefuente_nombre": "Retención en la Fuente por Pagar",
        "retefuente_detalle": "Retención en la fuente por pagar — Artículo 365 ET",
        "reteica": (ica_pasivo, "credito"),
        "reteica_nombre": "Retención ICA por Pagar",
        "reteica_detalle": "Retención ICA por pagar — Decreto 2048/1992",
        "ica_gasto": ("511505", "debito"),
        "ica_gasto_nombre": "Gasto ICA",
        "ica_gasto_detalle": "Gasto ICA — Ley 14/1983, Art. 342 Ley 1955/2019",
        "ica_por_pagar": (ica_pasivo, "credito"),
        "ica_por_pagar_nombre": "ICA por Pagar",
        "ica_por_pagar_detalle": "ICA por Pagar — Decreto 1333/1986",
    }


# ---------------------------------------------------------------------------
# Helper: detect transaction type from PUC codes in asientos
# ---------------------------------------------------------------------------


_SERVICIOS_KEYWORDS = (
    "servicio",
    "sostenim",
    "asesor",
    "consultor",
    "club",
    "administr",
    "honorar",
    "comision",
    "mantenim",
    "limpieza",
    "vigilan",
    "publicid",
    "telecomun",
    "transporte",
    "capacit",
    "afilia",
    "fomento",
    "cuota extra",
    "internet",
    "hospedaj",
)
_ARRENDAMIENTO_KEYWORDS = ("arrendam", "alquiler", "renta ", "leasing")
_BIENES_KEYWORDS = (
    "compra de ",
    "insumo",
    "mercanc",
    "inventario",
    "materia prima",
    "repuesto",
    "equipo",
    "papeler",
    "combustible",
)


def _detect_transaction_type(
    asientos: list[dict],
    doc_type: str | None = None,
    items: list[dict] | None = None,
    descripcion_general: str | None = None,
) -> str:
    """
    Infer transaction type from debit PUC codes, falling back to doc_type +
    item/descripcion keywords when no 5xxx debit is present yet (e.g. when the
    detector runs before contador injected the expense line).

    Returns 'servicios', 'bienes', or 'arrendamiento'.
    """
    for asiento in asientos:
        if (asiento.get("tipo_movimiento") or "").lower() == "debito":
            puc_raw = str(asiento.get("cuenta_puc", "")).strip()
            if puc_raw.isdigit():
                puc_int = int(puc_raw)
                if PUC_SERVICIOS_START <= puc_int <= PUC_SERVICIOS_END:
                    desc = (asiento.get("descripcion") or "").lower()
                    if any(kw in desc for kw in _ARRENDAMIENTO_KEYWORDS):
                        return "arrendamiento"
                    return "servicios"

    # Fallback: no 5xxx debit detected. Use doc_type + item/descripcion keywords
    # so the rate table picks the right tarifa even when contador has not yet
    # injected the expense line.
    if (doc_type or "").lower() == "factura_compra":
        haystack_parts: list[str] = []
        if descripcion_general:
            haystack_parts.append(str(descripcion_general).lower())
        for it in items or []:
            if isinstance(it, dict):
                for key in ("descripcion", "concepto", "nombre"):
                    val = it.get(key)
                    if val:
                        haystack_parts.append(str(val).lower())
        haystack = " ".join(haystack_parts)
        if haystack:
            if any(kw in haystack for kw in _ARRENDAMIENTO_KEYWORDS):
                return "arrendamiento"
            if any(kw in haystack for kw in _SERVICIOS_KEYWORDS):
                return "servicios"
            if any(kw in haystack for kw in _BIENES_KEYWORDS):
                return "bienes"
        # Conservative default for factura_compra: servicios (4% vs bienes 2.5%)
        # avoids sub-retención when keywords inconclusive.
        return "servicios"

    return "bienes"


def _has_iva_in_asientos(asientos: list[dict]) -> tuple[bool, Decimal]:
    """
    Check if IVA already exists in contador asientos.

    Matches PUC code '2408' (header account) or codes starting with '240802'
    (IVA descontable) or '240805' (IVA generado). Deliberately excludes '240815'
    (Retefuente) which shares the '2408' prefix but is NOT an IVA account.
    """
    for asiento in asientos:
        puc_raw = str(asiento.get("cuenta_puc", "")).strip()
        if (
            puc_raw == "2408"
            or puc_raw.startswith("240802")
            or puc_raw.startswith("240805")
        ):
            valor = asiento.get("valor", 0)
            return True, Decimal(str(valor)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
    return False, Decimal("0")


# ---------------------------------------------------------------------------
# Deterministic tax calculators
# ---------------------------------------------------------------------------


def _has_income_accounts(asientos: list[dict]) -> tuple[bool, Decimal]:
    """
    Returns (True, total_ingresos_brutos) if any CREDIT entry has a PUC 4xxx code.
    Income accounts (ingresos) are credits in the Colombian PUC. Triggers ICA calculation.
    """
    total = Decimal("0")
    found = False
    for a in asientos:
        if (a.get("tipo_movimiento") or "").lower() == "credito":
            puc = str(a.get("cuenta_puc", "")).strip()
            if puc.isdigit() and PUC_INGRESOS_START <= int(puc) <= PUC_INGRESOS_END:
                found = True
                total += Decimal(str(a.get("valor", 0)))
    return found, total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _calc_retefuente(base: Decimal, tipo: str) -> Decimal:
    tasa = TASA_RETEFUENTE.get(tipo, TASA_RETEFUENTE["servicios"])
    return (base * tasa).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _calc_reteica(base: Decimal) -> Decimal:
    return (base * TASA_RETEICA_DEFAULT).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def _calc_iva(base: Decimal, tarifa: str = "general") -> Decimal:
    tasa = TASA_IVA.get(tarifa, TASA_IVA["general"])
    return (base * tasa).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _calc_ica(ingresos_brutos: Decimal, tasa: Decimal = TASA_ICA_DEFAULT) -> Decimal:
    """ICA = ingresos_brutos × tasa. Ley 14/1983 Art. 33, Decreto 1333/1986."""
    return (ingresos_brutos * tasa).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _calc_provision_renta(
    utilidad_neta: Decimal, tasa: Decimal = TASA_RENTA
) -> Decimal:
    """Provisión renta = utilidad_antes_impuestos × 35%. Art. 240 ET. Returns 0 on losses."""
    if utilidad_neta <= Decimal("0"):
        return Decimal("0.00")
    return (utilidad_neta * tasa).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calc_period_renta_provision(
    db_session,
    nit_receptor: str,
    period_start,
    period_end,
    tasa_renta: Decimal = TASA_RENTA,
) -> dict:
    """
    Aggregates journal_entry_lines for the period to compute income tax provision.

      ingresos = SUM(credito) WHERE cuenta_puc LIKE '4%'
      gastos   = SUM(debito)  WHERE cuenta_puc LIKE '5%'
      costos   = SUM(debito)  WHERE cuenta_puc LIKE '6%'
      utilidad = ingresos - gastos - costos

    Returns a dict matching RentaProvisionOutput shape.
    """
    from datetime import datetime
    from sqlalchemy import text as sql_text

    period_end_dt = period_end if isinstance(period_end, date) else date.today()
    period_start_dt = period_start

    where_nit = "AND tercero_nit = :nit" if nit_receptor else ""
    params: dict = {"period_end": period_end_dt, "nit": nit_receptor or ""}
    if period_start_dt:
        date_filter = "AND fecha >= :period_start AND fecha <= :period_end"
        params["period_start"] = period_start_dt
    else:
        date_filter = "AND fecha <= :period_end"

    query = sql_text(f"""
        SELECT
            COALESCE(SUM(CASE WHEN cuenta_puc LIKE '4%' THEN credito ELSE 0 END), 0) AS ingresos,
            COALESCE(SUM(CASE WHEN cuenta_puc LIKE '5%' THEN debito  ELSE 0 END), 0) AS gastos,
            COALESCE(SUM(CASE WHEN cuenta_puc LIKE '6%' THEN debito  ELSE 0 END), 0) AS costos
        FROM journal_entry_lines
        WHERE 1=1 {where_nit} {date_filter}
    """)
    row = db_session.execute(query, params).fetchone()
    ingresos = Decimal(str(row.ingresos if row else 0))
    gastos = Decimal(str(row.gastos if row else 0))
    costos = Decimal(str(row.costos if row else 0))
    utilidad = ingresos - gastos - costos
    provision = _calc_provision_renta(utilidad, tasa_renta)

    return {
        "report_type": "renta_provision",
        "period_start": period_start_dt.isoformat() if period_start_dt else None,
        "period_end": period_end_dt.isoformat(),
        "generated_at": datetime.utcnow().isoformat(),
        "utilidad_antes_impuestos": float(utilidad),
        "tasa_renta": float(tasa_renta),
        "provision_renta": float(provision),
        "cuenta_gasto_puc": CUENTA_RENTA_GASTO,
        "cuenta_pasivo_puc": CUENTA_RENTA_PASIVO,
        "referencias": [
            "Art. 240 Estatuto Tributario colombiano",
            "Ley 2277 de 2022 — tarifa general sociedades 35%",
        ],
    }


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


def _extract_source_taxes(source_doc: dict) -> dict:
    """Extract explicit tax values from the ingest-pipeline's structured extraction dict.

    Priority:
    1. retenciones_aplicadas list  — explicit retefuente/reteica values from source doc
    2. totales.total_iva           — IVA already computed by the issuer
    3. items with es_gravado flag  — granular taxable base from line items
    """
    result: dict = {}

    totales = source_doc.get("totales") or {}
    if isinstance(totales, dict):
        for key in ("total_iva", "total_retenciones"):
            val = totales.get(key)
            if val is not None:
                try:
                    result[key] = Decimal(str(val))
                except Exception:
                    pass

    # Flat root-level IVA fields (nota_credito, nota_debito schemas)
    if "total_iva" not in result:
        for key in ("total_iva_ajustado", "total_iva_adicionado"):
            val = source_doc.get(key)
            if val is not None:
                try:
                    result["total_iva"] = Decimal(str(val))
                    break
                except Exception:
                    pass

    # Retenciones_aplicadas: [{tipo, base, tarifa, valor}]
    retenciones = source_doc.get("retenciones_aplicadas") or []
    if isinstance(retenciones, list):
        parsed_rets = []
        for r in retenciones:
            if not isinstance(r, dict):
                continue
            try:
                parsed_rets.append(
                    {
                        "tipo": str(r.get("tipo", "")).lower(),
                        "base": Decimal(str(r.get("base", 0))),
                        "tarifa": Decimal(str(r.get("tarifa", 0))),
                        "valor": Decimal(str(r.get("valor", 0))),
                    }
                )
            except Exception:
                pass
        if parsed_rets:
            result["retenciones"] = parsed_rets

    # Explicit "no aplicar retención" flag from the source doc
    # (e.g. cuenta_cobro stating "no aplicar retención según Art X ET").
    info_adicional = source_doc.get("informacion_adicional") or {}
    if isinstance(info_adicional, dict):
        flag = info_adicional.get("aplicar_retencion")
        if flag is False:
            result["aplicar_retencion"] = False
            motivo = info_adicional.get("motivo_no_retencion")
            if motivo:
                result["motivo_no_retencion"] = str(motivo)

    # Item-level tax flags for base_gravable override
    items = source_doc.get("items") or []
    if isinstance(items, list) and items:
        base_items = Decimal("0")
        any_flags = False
        for it in items:
            if not isinstance(it, dict):
                continue
            if it.get("es_gravado"):
                any_flags = True
                try:
                    base_items += Decimal(
                        str(
                            it.get("valor_total_sin_impuesto")
                            or it.get("valor_total")
                            or 0
                        )
                    )
                except Exception:
                    pass
            elif it.get("es_excluido") or it.get("es_exento"):
                any_flags = True
        if any_flags and base_items > 0:
            result["base_gravable_from_items"] = base_items.quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

        # Fallback: when extraction did not populate `es_gravado` flags,
        # infer IVA-bearing items from `impuestos[].tarifa > 0` and sum their
        # base_gravable. Needed for facturas with mixed taxable/excluded lines
        # (e.g. club sostenimiento + cuota extraordinaria + fomento) so the
        # downstream retef/reteica base is the IVA-bearing subtotal, not the
        # full mixed subtotal.
        if "base_gravable_from_items" not in result:
            iva_base = Decimal("0")
            saw_mixed = False
            saw_iva_item = False
            for it in items:
                if not isinstance(it, dict):
                    continue
                impuestos = it.get("impuestos") or []
                item_iva_base = Decimal("0")
                item_has_iva = False
                if isinstance(impuestos, list):
                    for imp in impuestos:
                        if not isinstance(imp, dict):
                            continue
                        if str(imp.get("tipo", "")).upper() == "IVA":
                            try:
                                tarifa = Decimal(str(imp.get("tarifa") or 0))
                            except Exception:
                                tarifa = Decimal("0")
                            if tarifa > 0:
                                item_has_iva = True
                                try:
                                    item_iva_base += Decimal(
                                        str(imp.get("base_gravable") or 0)
                                    )
                                except Exception:
                                    pass
                if item_has_iva:
                    saw_iva_item = True
                    iva_base += item_iva_base
                else:
                    saw_mixed = True
            # Only override when the doc actually mixes IVA and non-IVA lines.
            # A pure-IVA factura (all items gravados) leaves base = subtotal,
            # which the downstream calc already handles correctly.
            if saw_iva_item and saw_mixed and iva_base > 0:
                result["base_gravable_from_items"] = iva_base.quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )

    return result


def tributario_node(state: AgentState) -> AgentState:
    """
    Tributario worker node — replaces Sprint-12 stub.

    1. Reads ContadorOutput from state.
    2. Runs deterministic Colombian tax calculations.
    3. Queries RAG normativo for legal context.
    4. Calls the LLM to validate rates and produce TaxJustification.
    5. Stores TributarioOutput in state["tributario_output"].
    """
    if state.get("error"):
        logger.warning("Tributario: skipping due to upstream error")
        return state

    contador_output = state.get("contador_output") or {}
    if not contador_output:
        state["error"] = "Tributario error: no contador_output in state"
        logger.error(state["error"])
        append_log(state, "tributario", "node_error", {"error": state["error"]})
        return state

    asientos: list[dict] = contador_output.get("asientos", [])

    # Tax-payment declarations: the journal entries already ARE the tax settlement.
    # Applying IVA/retefuente/reteICA on top would double-count.
    _TAX_DECLARATION_TYPES = {
        # Tax declarations / payments — contador already books the tax event
        "declaracion_iva",
        "declaracion_ica",
        "autorretencion_ica",
        "recibo_pago_impuesto",
        "declaracion_reteica",
        "anexo_tributario",
        "auxiliar_impuesto",
        "auxiliar_iva",
        "anexo_iva",
        # Payroll — no IVA; employee retenciones handled in salary journal entries
        "nomina",
        "planilla_seguridad_social",
        # Payment / reconciliation documents — taxes already on the source invoice
        "comprobante_egreso",
        "conciliacion_bancaria",
        # Bank statements — movements already occurred; no new tax events
        "extracto_bancario",
        # Journal book — entries pre-exist; tributario must not double-apply taxes
        "libro_diario",
    }
    doc_type = (state.get("document_classification") or {}).get("doc_type", "")
    if doc_type in _TAX_DECLARATION_TYPES:
        logger.info(
            "Tributario: doc_type=%s is a tax declaration — skipping tax application",
            doc_type,
        )
        documento_ref = (
            (state.get("contador_output") or {}).get("descripcion_general", doc_type)
            or doc_type
        )[:100]
        state["tributario_output"] = {
            "fecha_analisis": date.today().isoformat(),
            "documento_referencia": documento_ref,
            "aplica_impuestos": False,
            "impuestos": [],
            "total_impuestos": "0.00",
            "observaciones": (
                f"Documento tipo {doc_type}: los asientos del contador ya registran "
                "el pago/liquidación del impuesto. No se aplican impuestos adicionales."
            ),
            "referencias_legales": [],
            "asientos_enriquecidos": (state.get("contador_output") or {}).get(
                "asientos", []
            ),
        }
        state["interpreted_data"] = state["tributario_output"]
        state["current_agent"] = "tributario"
        state["current_stage"] = "tributario_complete"
        append_log(
            state, "tributario", "skipped_tax_declaration", {"doc_type": doc_type}
        )
        return state

    # Pre-armed asiento passthrough: when the source document already carries
    # a fully booked journal entry (CE, RC, payroll voucher, manual journal),
    # the contador node passed those lines through verbatim. The tributario
    # must NOT inject IVA / retenciones / ICA on top — the issuer already
    # recorded what they intended. We only honour the contador output here.
    if (state.get("source_document") or {}).get("asientos_documento"):
        documento_ref = contador_output.get(
            "descripcion_general", doc_type or "sin referencia"
        )[:100]
        state["tributario_output"] = {
            "fecha_analisis": date.today().isoformat(),
            "documento_referencia": documento_ref,
            "aplica_impuestos": False,
            "impuestos": [],
            "total_impuestos": "0.00",
            "observaciones": (
                "El documento trae un asiento contable pre-armado por el contador "
                "emisor; no se inyectan impuestos adicionales."
            ),
            "referencias_legales": [],
            "asientos_enriquecidos": (state.get("contador_output") or {}).get(
                "asientos", []
            ),
        }
        state["interpreted_data"] = state["tributario_output"]
        state["current_agent"] = "tributario"
        state["current_stage"] = "tributario_complete"
        append_log(
            state,
            "tributario",
            "prearmed_passthrough",
            {"doc_type": doc_type, "lines": len(contador_output.get("asientos", []))},
        )
        logger.info("Tributario: pre-armed asiento detected, skip IVA/retenciones/ICA")
        return state

    # Route tax accounts (IVA, retenciones, ICA) by document direction (venta vs compra-like).
    is_venta = doc_type in _VENTA_DOC_TYPES
    logger.info(
        "Tributario: doc_type=%s → routing %s",
        doc_type or "<empty>",
        "VENTA" if is_venta else "COMPRA",
    )

    # Extract explicit tax values from the source document (ingest pipeline output)
    source_doc = state.get("source_document") or {}
    source_taxes = _extract_source_taxes(source_doc) if source_doc else {}

    # Compute base gravable.
    # Priority: (1) item-level gravado sum from source doc, (2) non-liability debit lines, (3) total_debitos.
    if source_taxes.get("base_gravable_from_items"):
        base_gravable = source_taxes["base_gravable_from_items"]
        logger.info("Tributario: base_gravable from source items = %s", base_gravable)
    else:
        # PUC 2xxx = liabilities (IVA descontable, retenciones) — exclude to avoid inflating taxable base.
        non_tax_debits = [
            Decimal(str(a.get("valor", 0)))
            for a in asientos
            if (a.get("tipo_movimiento") or "").lower() == "debito"
            and not str(a.get("cuenta_puc", "")).startswith("2")
        ]
        base_gravable = (
            sum(non_tax_debits, Decimal("0")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if non_tax_debits
            else Decimal(str(contador_output.get("total_debitos", 0)))
        )

    documento_ref = contador_output.get("descripcion_general", "sin referencia")[:100]

    append_log(
        state,
        "tributario",
        "node_start",
        {
            "base_gravable": str(base_gravable),
            "asientos_count": len(asientos),
        },
    )

    try:
        # ------------------------------------------------------------------
        # Step 1 — Load company tax config from DB.
        # In process mode this is a hard precondition: no silent defaults.
        # ------------------------------------------------------------------
        mode = state.get("mode", "ingest")
        company_config = state.get("company_config")
        # Resolve the tenant's NIT (owner of the ingest). This is NOT the
        # transaction-level `nit_receptor` field (the counterparty/customer in
        # sales invoices) — it's the company that owns the upload, sourced from
        # `state["company_nit"]` (set by invoke_accounting_pipeline).
        company_nit = state.get("company_nit") or None
        if not company_config:
            if not company_nit:
                # Fallback if company_nit wasn't propagated to state.
                for tx in state.get("raw_transactions") or []:
                    company_nit = tx.get("company_nit") or tx.get("nit_receptor")
                    if company_nit:
                        break

            if company_nit:
                try:
                    from app.core.database import SessionLocal
                    from app.services import db_service as _db_svc

                    _db = SessionLocal()
                    try:
                        row = _db_svc.get_company_settings(_db, company_nit)
                        if row:
                            company_config = {
                                "tasa_retefuente_servicios": float(
                                    row.tasa_retefuente_servicios
                                ),
                                "tasa_retefuente_bienes": float(
                                    row.tasa_retefuente_bienes
                                ),
                                "tasa_retefuente_arrendamiento": float(
                                    row.tasa_retefuente_arrendamiento
                                ),
                                "tasa_reteica": float(row.tasa_reteica),
                                "tasa_iva_general": float(row.tasa_iva_general),
                                "iva_responsable": row.iva_responsable,
                                "tasa_ica": float(row.tasa_ica),
                                "tasa_renta": float(row.tasa_renta),
                                "cuenta_ica_propio": (
                                    row.cuenta_ica_propio
                                    if isinstance(
                                        getattr(row, "cuenta_ica_propio", None),
                                        str,
                                    )
                                    else None
                                ),
                                "ciudad": getattr(row, "ciudad", None),
                                "codigo_ciiu": getattr(row, "codigo_ciiu", None),
                            }
                            logger.info(
                                f"Tributario: loaded company settings for NIT {company_nit}"
                            )
                        else:
                            logger.warning(
                                "Tributario: missing company settings for NIT %s",
                                company_nit,
                            )
                    finally:
                        _db.close()
                except Exception as cfg_err:
                    logger.warning(
                        "Tributario: could not load company settings (%s)",
                        cfg_err,
                    )

        has_staged_transactions = bool(state.get("raw_transactions"))
        if mode == "process" and has_staged_transactions and not company_config:
            setup_endpoint = "/api/v1/settings/company/{nit}/setup"
            if company_nit:
                state["error"] = (
                    "Tributario precondition failed: missing company tax settings for "
                    f"NIT {company_nit}. Configure it first at "
                    f"{setup_endpoint.format(nit=company_nit)}"
                )
                details = {
                    "error": state["error"],
                    "company_nit": company_nit,
                    "required_endpoint": setup_endpoint,
                }
            else:
                state["error"] = (
                    "Tributario precondition failed: missing company NIT. "
                    "Cannot resolve company tax settings."
                )
                details = {
                    "error": state["error"],
                    "company_nit": None,
                    "required_endpoint": setup_endpoint,
                }
            logger.error(state["error"])
            append_log(state, "tributario", "node_error", details)
            return state

        append_log(
            state,
            "tributario",
            "config_loaded",
            {
                "source": "db" if company_config else "defaults",
            },
        )

        # Resolve effective tax rates (DB settings override hardcoded defaults)
        iva_responsable = (
            company_config.get("iva_responsable", True) if company_config else True
        )

        tasa_retefuente_efectiva = {
            "servicios": (
                Decimal(str(company_config["tasa_retefuente_servicios"]))
                if company_config
                else TASA_RETEFUENTE["servicios"]
            ),
            "bienes": (
                Decimal(str(company_config["tasa_retefuente_bienes"]))
                if company_config
                else TASA_RETEFUENTE["bienes"]
            ),
            "arrendamiento": (
                Decimal(str(company_config["tasa_retefuente_arrendamiento"]))
                if company_config
                else TASA_RETEFUENTE["arrendamiento"]
            ),
        }
        # ReteICA: attempt municipal tarifa + base_minima_uvt DB lookup
        # base_minima_uvt is per-municipio (Fix 4: Decreto 572 does NOT apply to ReteICA).
        _reteica_db_rate = None
        _reteica_db_base_minima_uvt: "Decimal | None" = None
        if company_config:
            _ciudad = company_config.get("ciudad")
            _ciiu = company_config.get("codigo_ciiu")
            if _ciudad and _ciiu:
                try:
                    _db2 = SessionLocal()
                    try:
                        _reteica_db_rate = _db_svc.get_reteica_tarifa(
                            _db2, _ciudad, _ciiu
                        )
                        _reteica_db_base_minima_uvt = (
                            _db_svc.get_reteica_base_minima_uvt(_db2, _ciudad, _ciiu)
                        )
                        if _reteica_db_rate is not None:
                            logger.debug(
                                "Tributario: ReteICA tarifa from DB for ciudad=%s ciiu=%s → %.4f "
                                "(base_minima_uvt=%s)",
                                _ciudad,
                                _ciiu,
                                _reteica_db_rate,
                                _reteica_db_base_minima_uvt,
                            )
                    finally:
                        _db2.close()
                except Exception as _reteica_err:
                    logger.warning(
                        "Tributario: ReteICA DB lookup failed (%s), falling back to config",
                        _reteica_err,
                    )
        tasa_reteica_efectiva = (
            Decimal(str(_reteica_db_rate))
            if _reteica_db_rate is not None
            else (
                Decimal(str(company_config["tasa_reteica"]))
                if company_config
                else TASA_RETEICA_DEFAULT
            )
        )
        # ------------------------------------------------------------------
        # UVT and base mínima — DB lookup with hardcoded fallback
        # ------------------------------------------------------------------
        # Derive fiscal year and as_of_date from period_start state field or today.
        # as_of_date is passed to get_base_minima for temporal base mínima lookup
        # (Fix 3: Decreto 572 suspended May 7 2026 → date-specific rows apply).
        _period_start_raw = state.get("period_start")
        _as_of_date: "date | None" = None
        if _period_start_raw and isinstance(_period_start_raw, date):
            _fiscal_year = _period_start_raw.year
            _as_of_date = _period_start_raw
        elif _period_start_raw and isinstance(_period_start_raw, str):
            try:
                from datetime import datetime as _dt

                _parsed = _dt.fromisoformat(_period_start_raw[:10]).date()
                _fiscal_year = _parsed.year
                _as_of_date = _parsed
            except (ValueError, TypeError):
                _fiscal_year = date.today().year
        else:
            _fiscal_year = date.today().year

        _uvt_value: Decimal = UVT_FALLBACK
        _base_minima_retefuente: dict[str, Decimal] = dict(BASE_MINIMA_RETEFUENTE_UVT)
        _base_minima_reteica: Decimal = BASE_MINIMA_RETEICA_UVT

        try:
            _db3 = SessionLocal()
            try:
                _uvt_db = _db_svc.get_uvt(_db3, _fiscal_year)
                if _uvt_db is not None:
                    _uvt_value = _uvt_db
                    logger.debug(
                        "Tributario: UVT %d from DB = %s", _fiscal_year, _uvt_value
                    )
                else:
                    logger.debug(
                        "Tributario: UVT %d not in DB, using fallback %s",
                        _fiscal_year,
                        _uvt_value,
                    )
                for _concepto_key in ("servicios", "bienes", "arrendamiento"):
                    _bm_db = _db_svc.get_base_minima(
                        _db3,
                        f"retefuente_{_concepto_key}",
                        _fiscal_year,
                        as_of_date=_as_of_date,
                    )
                    if _bm_db is not None:
                        _base_minima_retefuente[_concepto_key] = _bm_db
                _bm_reteica_db = _db_svc.get_base_minima(
                    _db3, "reteica", _fiscal_year, as_of_date=_as_of_date
                )
                if _bm_reteica_db is not None:
                    _base_minima_reteica = _bm_reteica_db
            finally:
                _db3.close()
        except Exception as _uvt_err:
            logger.warning(
                "Tributario: UVT/base_minima DB lookup failed (%s), using fallback constants",
                _uvt_err,
            )

        # Fix 4: Override reteica base mínima with municipal value when available.
        # ReteICA is municipal — Decreto 572 does NOT apply. Each municipio sets own base.
        # _reteica_db_base_minima_uvt comes from reteica_tarifas.base_minima_uvt column.
        if _reteica_db_base_minima_uvt is not None:
            _base_minima_reteica = _reteica_db_base_minima_uvt
            logger.debug(
                "Tributario: ReteICA base mínima from municipal table = %s UVT",
                _base_minima_reteica,
            )

        tasa_iva_efectiva = (
            Decimal(str(company_config["tasa_iva_general"]))
            if company_config
            else TASA_IVA["general"]
        )
        tasa_ica_efectiva = (
            Decimal(str(company_config["tasa_ica"]))
            if company_config
            else TASA_ICA_DEFAULT
        )
        cuenta_ica_propio = (
            company_config.get("cuenta_ica_propio") if company_config else None
        )
        accounts = _tax_accounts_for(doc_type, cuenta_ica_propio=cuenta_ica_propio)

        # ------------------------------------------------------------------
        # Step 2 — Determine transaction type from PUC codes
        # ------------------------------------------------------------------
        tipo_transaccion = _detect_transaction_type(
            asientos,
            doc_type=doc_type,
            items=(source_doc.get("items") if isinstance(source_doc, dict) else None),
            descripcion_general=contador_output.get("descripcion_general"),
        )
        logger.info(f"Tributario: detected transaction type = {tipo_transaccion}")

        # ------------------------------------------------------------------
        # Step 3 — Deterministic tax calculations using effective rates.
        # Source-document values (from ingest extraction) take priority over
        # computed defaults to avoid recomputing taxes already stated in the doc.
        # ------------------------------------------------------------------
        tasa_retefuente = tasa_retefuente_efectiva.get(
            tipo_transaccion, tasa_retefuente_efectiva["servicios"]
        )

        # Retefuente: use source value when available
        source_ret = None
        for ret in source_taxes.get("retenciones", []):
            if (
                "retefuente" in ret.get("tipo", "").lower()
                or "rte" in ret.get("tipo", "").lower()
            ):
                source_ret = ret["valor"]
                break
        if source_ret is not None:
            retefuente_val = source_ret.quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            logger.info("Tributario: retefuente from source doc = %s", retefuente_val)
        elif is_venta:
            # In factura_venta the seller does NOT self-apply retenciones — they
            # are practiced by the BUYER and reported on the invoice. If the
            # extraction did not pick up a retención line, the legally safe
            # default is 0; do not invent one from the UVT table.
            retefuente_val = Decimal("0.00")
            logger.info(
                "Tributario: retefuente=0 (factura_venta sin retenciones extraídas)"
            )
        elif source_taxes.get("aplicar_retencion") is False:
            # Source doc explicitly states no retention applies (e.g. cuenta_cobro
            # citing Art 383 ET / Art 392 ET base minima exemption).
            retefuente_val = Decimal("0.00")
            logger.info(
                "Tributario: retefuente=0 (source doc aplicar_retencion=false, motivo=%s)",
                source_taxes.get("motivo_no_retencion") or "sin motivo",
            )
        else:
            base_min_uvt = _base_minima_retefuente.get(tipo_transaccion)
            base_minima_pesos = (
                (base_min_uvt * _uvt_value) if base_min_uvt is not None else None
            )
            if base_minima_pesos is not None and base_gravable < base_minima_pesos:
                retefuente_val = Decimal("0.00")
                logger.info(
                    "Tributario: retefuente=0 (base $%s < mínima %s UVT = $%s para tipo=%s)",
                    base_gravable,
                    base_min_uvt,
                    base_minima_pesos,
                    tipo_transaccion,
                )
            else:
                retefuente_val = (base_gravable * tasa_retefuente).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )

        # ReteICA: use source value when available
        source_reteica = None
        for ret in source_taxes.get("retenciones", []):
            if (
                "ica" in ret.get("tipo", "").lower()
                or "reteica" in ret.get("tipo", "").lower()
            ):
                source_reteica = ret["valor"]
                break
        if source_reteica is not None:
            reteica_val = source_reteica.quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            logger.info("Tributario: reteICA from source doc = %s", reteica_val)
        elif is_venta:
            reteica_val = Decimal("0.00")
            logger.info(
                "Tributario: reteICA=0 (factura_venta sin retenciones extraídas)"
            )
        elif source_taxes.get("aplicar_retencion") is False:
            reteica_val = Decimal("0.00")
            logger.info("Tributario: reteICA=0 (source doc aplicar_retencion=false)")
        else:
            base_minima_reteica = _base_minima_reteica * _uvt_value
            if base_gravable < base_minima_reteica:
                reteica_val = Decimal("0.00")
                logger.info(
                    "Tributario: reteICA=0 (base $%s < mínima %s UVT = $%s)",
                    base_gravable,
                    _base_minima_reteica,
                    base_minima_reteica,
                )
            else:
                reteica_val = (base_gravable * tasa_reteica_efectiva).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )

        # IVA: priority order — (1) already in asientos, (2) source doc total_iva, (3) computed
        iva_presente, iva_val_existente = _has_iva_in_asientos(asientos)
        if iva_presente:
            iva_val = iva_val_existente
            logger.info("Tributario: IVA found in asientos = %s", iva_val)
        elif not iva_responsable:
            iva_val = Decimal("0.00")
            logger.info("Tributario: IVA skipped — company is not IVA responsable")
        elif source_taxes.get("total_iva") is not None:
            iva_val = source_taxes["total_iva"].quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            logger.info("Tributario: IVA from source doc = %s", iva_val)
        else:
            iva_val = (base_gravable * tasa_iva_efectiva).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            logger.info("Tributario: IVA calculated = %s", iva_val)

        # ICA — applies when transaction contains income credits (PUC 4xxx)
        has_income, ingresos_brutos = _has_income_accounts(asientos)
        ica_val = Decimal("0.00")
        if has_income:
            ica_val = _calc_ica(ingresos_brutos, tasa_ica_efectiva)
            logger.info(
                f"Tributario: ICA calculated = {ica_val} on ingresos {ingresos_brutos}"
            )
        else:
            logger.info("Tributario: ICA skipped — no PUC 4xxx credit entries detected")

        logger.info(
            f"Tributario: retefuente={retefuente_val}, reteica={reteica_val}, "
            f"iva={iva_val}, ica={ica_val}"
        )

        # ------------------------------------------------------------------
        # Step 4 — RAG normativo lookup
        # ------------------------------------------------------------------
        rag_context = ""
        try:
            rag_service = get_rag_service()
            results = rag_service.search_normativo(
                "retención en la fuente servicios Art 383 Estatuto Tributario IVA"
            )
            rag_context = "\n".join(r.content for r in results) if results else ""
            logger.info(f"Tributario: RAG returned {len(results)} normative chunks")
        except Exception as rag_err:
            logger.warning(f"Tributario: RAG lookup failed (continuing): {rag_err}")

        # ------------------------------------------------------------------
        # Step 5 — LLM justification (structured output)
        # ------------------------------------------------------------------
        tax_amounts = {
            "retefuente": float(retefuente_val),
            "reteica": float(reteica_val),
            "iva": float(iva_val),
            "ica": float(ica_val),
            "tasa_retefuente": f"{float(tasa_retefuente) * 100:.1f}%",
            "tasa_reteica": f"{float(tasa_reteica_efectiva) * 100:.2f}%",
            "tasa_iva": f"{float(tasa_iva_efectiva) * 100:.0f}%",
            "tasa_ica": f"{float(tasa_ica_efectiva) * 1000:.2f}‰",
            "tipo_transaccion": tipo_transaccion,
        }

        llm = get_llm_client()
        try:
            justification = llm.justify_tax_analysis(tax_amounts, rag_context)
        except Exception as llm_err:
            logger.warning(
                f"Tributario: LLM justification failed (using fallback): {llm_err}"
            )
            from app.models.llm_schemas import TaxJustification as _TaxJustification

            justification = _TaxJustification(
                referencias=[
                    "Art. 383 ET",
                    "Art. 401 ET",
                    "Art. 477 ET",
                    "Decreto 2048/1992",
                ],
                justificacion=(
                    "Retenciones aplicadas según tasas vigentes del Estatuto Tributario "
                    "colombiano. Retefuente según Art. 383 ET para servicios; ReteICA según "
                    "tarifas municipales; IVA según Art. 477 ET tarifa general."
                ),
                confirma_tasas=True,
            )
        referencias = justification.referencias
        observaciones = justification.justificacion

        append_log(
            state,
            "tributario",
            "justification_complete",
            {
                "confirma_tasas": justification.confirma_tasas,
                "referencias_count": len(referencias),
            },
        )

        # ------------------------------------------------------------------
        # Step 5 — Build impuestos list (skip zero-amount entries)
        # ------------------------------------------------------------------
        impuestos = []

        if retefuente_val > 0:
            impuestos.append(
                {
                    "tipo_impuesto": "retefuente",
                    "base_gravable": str(base_gravable),
                    "tarifa_porcentaje": str(tasa_retefuente * 100),
                    "valor_impuesto": str(retefuente_val),
                    "cuenta_puc": accounts["retefuente"][0],
                }
            )

        if reteica_val > 0:
            impuestos.append(
                {
                    "tipo_impuesto": "reteica",
                    "base_gravable": str(base_gravable),
                    "tarifa_porcentaje": str(tasa_reteica_efectiva * 100),
                    "valor_impuesto": str(reteica_val),
                    "cuenta_puc": accounts["reteica"][0],
                }
            )

        if iva_val > 0:
            impuestos.append(
                {
                    "tipo_impuesto": "IVA",
                    "base_gravable": str(base_gravable),
                    "tarifa_porcentaje": str(tasa_iva_efectiva * 100),
                    "valor_impuesto": str(iva_val),
                    "cuenta_puc": accounts["iva"][0],
                }
            )

        # ICA is only emitted as a separate tax line on COMPRA-like documents.
        # For VENTA the ICA gasto/por pagar belongs to the municipal declaration,
        # not to each individual invoice. _tax_accounts_for() returns None there.
        if ica_val > Decimal("0") and accounts.get("ica_por_pagar") is not None:
            impuestos.append(
                {
                    "tipo_impuesto": "ica",
                    "base_gravable": str(ingresos_brutos),
                    "tarifa_porcentaje": str(tasa_ica_efectiva * 100),
                    "valor_impuesto": str(ica_val),
                    "cuenta_puc": accounts["ica_por_pagar"][0],
                }
            )

        total_impuestos = sum(
            (Decimal(i["valor_impuesto"]) for i in impuestos),
            Decimal("0"),
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        aplica_impuestos = len(impuestos) > 0

        # ------------------------------------------------------------------
        # Step 6 — Build enriched journal entries (keep double-entry balanced)
        # ------------------------------------------------------------------
        # COMPRA (default): we are the buyer.
        #   - IVA descontable (240802) is a DEBIT asset (recoverable from DIAN).
        #   - Retenciones (retefuente + reteica) become new CREDIT liabilities.
        #   - Net effect on the largest existing CREDIT (e.g. bank / proveedor):
        #       +iva_to_add (buyer pays IVA to vendor on top of base; 0 if present)
        #       -total_retenciones (amounts withheld from vendor payment)
        #
        # VENTA: we are the seller.
        #   - IVA generado (240805) is a CREDIT liability (we owe DIAN).
        #   - Retenciones que el cliente nos practica son DÉBITOS (anticipo a favor:
        #     135515 retefuente, 135517 reteICA).
        #   - Net effect on the largest existing DEBIT (typically 13xxxx CxC):
        #       +iva_to_add (cliente debe pagarnos IVA encima de la base)
        #       -total_retenciones (cliente nos retiene; bajamos lo que efectivamente cobramos)
        asientos_enriquecidos = [dict(a) for a in asientos]  # copy from contador

        total_retenciones = retefuente_val + reteica_val
        iva_to_add = iva_val if not iva_presente else Decimal("0")
        target_movement = "debito" if is_venta else "credito"

        # Sanity: contador may have booked the largest credit/debit as
        # total_factura (IVA already embedded) without emitting a separate
        # 240802/240805 IVA line. In that case _has_iva_in_asientos returned
        # False so iva_to_add > 0 — but adding IVA again would double-count.
        # Detect by comparing the largest target line to source totales.total_factura
        # (or total_a_pagar / total). When they match within $1, subtract IVA
        # from that line BEFORE applying retenciones, and let the separate
        # IVA descontable / IVA generado line be inserted downstream.
        if iva_to_add > 0:
            totales_src = (
                source_doc.get("totales") if isinstance(source_doc, dict) else None
            ) or {}
            total_factura_src = Decimal("0")
            for key in ("total_factura", "total_a_pagar", "total"):
                val = totales_src.get(key)
                if val is not None:
                    try:
                        total_factura_src = Decimal(str(val))
                        if total_factura_src > 0:
                            break
                    except Exception:
                        pass
            if total_factura_src > 0:
                cand = [
                    Decimal(str(e.get("valor", 0)))
                    for e in asientos_enriquecidos
                    if e.get("tipo_movimiento", "").lower() == target_movement
                ]
                largest_now = max(cand) if cand else Decimal("0")
                if largest_now > 0 and abs(largest_now - total_factura_src) < Decimal(
                    "1.00"
                ):
                    logger.info(
                        "Tributario: %s %s = %s ≈ source total_factura %s — "
                        "IVA already embedded; subtracting IVA from line and "
                        "inserting separate IVA descontable",
                        target_movement,
                        "largest",
                        largest_now,
                        total_factura_src,
                    )
                    # Find that largest entry and subtract IVA from it so the
                    # downstream net_adjustment math operates on the pre-IVA base.
                    for entry in asientos_enriquecidos:
                        if (
                            entry.get("tipo_movimiento", "").lower() == target_movement
                            and Decimal(str(entry.get("valor", 0))) == largest_now
                        ):
                            new_val = (largest_now - iva_to_add).quantize(
                                Decimal("0.01"), rounding=ROUND_HALF_UP
                            )
                            entry["valor"] = str(new_val)
                            break

        net_adjustment = iva_to_add - total_retenciones

        if total_retenciones > 0 or iva_to_add > 0:
            candidate_entries = [
                (i, e)
                for i, e in enumerate(asientos_enriquecidos)
                if e.get("tipo_movimiento", "").lower() == target_movement
            ]
            if candidate_entries:
                largest_idx, largest_entry = max(
                    candidate_entries,
                    key=lambda x: Decimal(str(x[1].get("valor", 0))),
                )
                original_valor = Decimal(str(largest_entry.get("valor", 0)))
                adjusted_valor = (original_valor + net_adjustment).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                if adjusted_valor < Decimal("0"):
                    side = "debit" if is_venta else "credit"
                    state["error"] = (
                        f"Tributario error: retenciones ({total_retenciones}) exceed the largest "
                        f"{side} account {largest_entry.get('cuenta_puc')} "
                        f"valor ({original_valor}). Cannot produce a balanced journal entry."
                    )
                    append_log(
                        state, "tributario", "node_error", {"error": state["error"]}
                    )
                    return state
                asientos_enriquecidos[largest_idx]["valor"] = str(adjusted_valor)
                logger.info(
                    "Tributario: adjusted %s %s from %s to %s "
                    "(iva_new=%s, retenciones=%s)",
                    target_movement,
                    largest_entry.get("cuenta_puc"),
                    original_valor,
                    adjusted_valor,
                    iva_to_add,
                    total_retenciones,
                )

        # IVA — for compra: DEBIT 240802 (descontable, activo).
        # For venta: CREDIT 240805 (generado, pasivo a DIAN).
        if iva_to_add > 0:
            iva_acct, iva_mov = accounts["iva"]
            asientos_enriquecidos.append(
                {
                    "cuenta_puc": iva_acct,
                    "nombre_cuenta": accounts["iva_nombre"],
                    "descripcion": accounts["iva_detalle"],
                    "tipo_movimiento": iva_mov,
                    "valor": str(iva_to_add),
                }
            )

        # Retefuente — compra: CREDIT 2365 (pasivo). Venta: DEBIT 135515 (anticipo a favor).
        if retefuente_val > 0:
            ret_acct, ret_mov = accounts["retefuente"]
            ref_label = referencias[0] if referencias else "Art. 365 ET"
            asientos_enriquecidos.append(
                {
                    "cuenta_puc": ret_acct,
                    "nombre_cuenta": accounts["retefuente_nombre"],
                    "descripcion": f"{accounts['retefuente_detalle']} — {ref_label}",
                    "tipo_movimiento": ret_mov,
                    "valor": str(retefuente_val),
                }
            )

        # ReteICA — compra: CREDIT 2368 (pasivo). Venta: DEBIT 135517 (anticipo a favor).
        if reteica_val > 0:
            ric_acct, ric_mov = accounts["reteica"]
            asientos_enriquecidos.append(
                {
                    "cuenta_puc": ric_acct,
                    "nombre_cuenta": accounts["reteica_nombre"],
                    "descripcion": accounts["reteica_detalle"],
                    "tipo_movimiento": ric_mov,
                    "valor": str(reteica_val),
                }
            )

        # ICA — Impuesto de Industria y Comercio. Only emitted on compra-like docs.
        # In venta, ICA is settled at municipal-declaration time, not per invoice.
        if (
            ica_val > Decimal("0")
            and accounts.get("ica_gasto") is not None
            and accounts.get("ica_por_pagar") is not None
        ):
            ica_gasto_acct, ica_gasto_mov = accounts["ica_gasto"]
            ica_pagar_acct, ica_pagar_mov = accounts["ica_por_pagar"]
            asientos_enriquecidos.append(
                {
                    "cuenta_puc": ica_gasto_acct,
                    "nombre_cuenta": accounts["ica_gasto_nombre"],
                    "descripcion": accounts["ica_gasto_detalle"],
                    "tipo_movimiento": ica_gasto_mov,
                    "valor": str(ica_val),
                }
            )
            asientos_enriquecidos.append(
                {
                    "cuenta_puc": ica_pagar_acct,
                    "nombre_cuenta": accounts["ica_por_pagar_nombre"],
                    "descripcion": accounts["ica_por_pagar_detalle"],
                    "tipo_movimiento": ica_pagar_mov,
                    "valor": str(ica_val),
                }
            )

        # ------------------------------------------------------------------
        # Step 7 — Build TributarioOutput-compatible dict
        # ------------------------------------------------------------------
        tributario_output = {
            "fecha_analisis": date.today().isoformat(),
            "documento_referencia": documento_ref,
            "aplica_impuestos": aplica_impuestos,
            "impuestos": impuestos,
            "total_impuestos": str(total_impuestos),
            "observaciones": f"{observaciones} | Referencias: {', '.join(referencias)}",
            "asientos_enriquecidos": asientos_enriquecidos,
            "referencias_legales": referencias,
        }

        state["tributario_output"] = tributario_output
        state["interpreted_data"] = tributario_output
        state["current_agent"] = "tributario"
        state["current_stage"] = "tributario_complete"
        state["correction_feedback"] = None

        # ------------------------------------------------------------------
        # Step 8 — Propagate enriched asientos to contador_output for persistence
        # ------------------------------------------------------------------
        # Update contador_output with enriched asientos so persist_node will
        # store them in the database (not just the UI response).
        try:
            contador_output = state.get("contador_output") or {}
            if isinstance(contador_output, dict):
                # Make a copy to avoid mutating the original fixture/state
                contador_output = dict(contador_output)
                # Update asientos to the enriched version
                contador_output["asientos"] = asientos_enriquecidos

                # Recalculate totals based on enriched asientos
                total_debitos_enriched = Decimal("0")
                total_creditos_enriched = Decimal("0")
                for a in asientos_enriquecidos:
                    valor = Decimal(str(a.get("valor", 0)))
                    if a.get("tipo_movimiento", "").lower() == "debito":
                        total_debitos_enriched += valor
                    else:
                        total_creditos_enriched += valor

                contador_output["total_debitos"] = str(total_debitos_enriched)
                contador_output["total_creditos"] = str(total_creditos_enriched)
                state["contador_output"] = contador_output

                append_log(
                    state,
                    "tributario",
                    "enrichment_propagated",
                    {
                        "asientos_enriched_count": len(asientos_enriquecidos),
                        "total_debitos": str(total_debitos_enriched),
                        "total_creditos": str(total_creditos_enriched),
                    },
                )
                logger.info(
                    f"Tributario: propagated enriched asientos to contador_output "
                    f"(debits={total_debitos_enriched}, credits={total_creditos_enriched})"
                )
        except Exception as enrich_exc:
            logger.warning(
                "Tributario: failed to propagate enriched asientos: %s",
                enrich_exc,
                exc_info=True,
            )
            # Don't fail the node if enrichment propagation fails

        # Propagate legal references to raw_transactions for persistence
        try:
            raw_transactions = state.get("raw_transactions")
            if isinstance(raw_transactions, list) and raw_transactions:
                first_tx = raw_transactions[0]
                if isinstance(first_tx, dict):
                    first_tx["referencias_legales"] = referencias
                    # Add agent reasoning at transaction level
                    tx_agent_reasoning = first_tx.get("agent_reasoning") or {}
                    if not isinstance(tx_agent_reasoning, dict):
                        tx_agent_reasoning = {}
                    tx_agent_reasoning["tributario"] = observaciones
                    first_tx["agent_reasoning"] = tx_agent_reasoning
                    logger.info("Tributario: propagated references to raw_transactions")
        except Exception as ref_exc:
            logger.warning(
                "Tributario: failed to propagate references to raw_transactions: %s",
                ref_exc,
                exc_info=True,
            )
            # Don't fail the node if reference propagation fails

        # Persist reasoning for final API response
        result = state.get("result") or {}
        agent_reasoning = result.get("agent_reasoning") or {}
        agent_reasoning["tributario"] = observaciones
        result["agent_reasoning"] = agent_reasoning
        state["result"] = result

        append_log(
            state,
            "tributario",
            "node_complete",
            {
                "aplica_impuestos": aplica_impuestos,
                "total_impuestos": str(total_impuestos),
                "impuestos_count": len(impuestos),
                "tipo_transaccion": tipo_transaccion,
            },
        )

        logger.info(
            f"Tributario: complete — total_impuestos={total_impuestos}, "
            f"aplica={aplica_impuestos}"
        )

        # Phase 3: deterministic tributario audit
        from app.agents.audit_utils import append_audit_report
        from app.agents.auditors import tributario_auditor

        _trib_report = tributario_auditor.run(state)
        append_audit_report(state, _trib_report)

        return state

    except Exception as e:
        state["error"] = f"Tributario error: {str(e)}"
        logger.error(state["error"], exc_info=True)
        append_log(state, "tributario", "node_error", {"error": str(e)})
        return state
