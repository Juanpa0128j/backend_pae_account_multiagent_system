"""
Agente Tributario (Tax Specialist)

Role (docs/Diseño de arquitectura de agente):
  - Receives classified journal entries from Contador (ContadorOutput).
  - Calculates Retefuente, ReteICA, IVA using deterministic Python functions.
  - Queries RAG normativo for relevant legal articles.
  - Calls Gemini to validate rates and produce legal justification (TaxJustification).
  - Returns TributarioOutput enriched with tax liability accounts (240815 Retefuente, 236540 ReteICA, 240802 IVA descontable).

Tax rates (Colombian legislation):
  - Retefuente: 11% servicios (Art. 383 ET), 3% bienes (Art. 401 ET), 10% arrendamiento
  - ReteICA:    0.69% default Cali (Decreto 2048/1992)
  - IVA:        19% general (Art. 477 ET), 5% reducida, 0% exento
"""

import logging
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.agents.agent_utils import append_log
from app.agents.state import AgentState
from app.core.gemini_client import get_gemini_client
from app.services.rag_service import get_rag_service

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tax rate constants — deterministic, never computed by LLM
# ---------------------------------------------------------------------------

TASA_RETEFUENTE: dict[str, Decimal] = {
    "servicios": Decimal("0.11"),  # Art. 383 ET — servicios generales
    "bienes": Decimal("0.03"),  # Art. 401 ET — compras de bienes
    "arrendamiento": Decimal("0.10"),  # Art. 401 ET — arrendamientos
}
TASA_RETEICA_DEFAULT = Decimal("0.0069")  # Tarifa Cali / default municipal

TASA_IVA: dict[str, Decimal] = {
    "general": Decimal("0.19"),  # Art. 477 ET — tarifa general
    "reducida": Decimal("0.05"),  # Servicios especiales
    "exento": Decimal("0.00"),  # Bienes/servicios exentos
}

# ICA — Impuesto de Industria y Comercio (Ley 14/1983, Decreto 1333/1986)
TASA_ICA_DEFAULT = Decimal("0.00690")  # 6.9‰ — conservative national reference
CUENTA_ICA_GASTO = "540101"  # Gasto ICA
CUENTA_ICA_PASIVO = "240808"  # ICA por Pagar

# Renta — Art. 240 ET (Ley 2277/2022, vigente año fiscal 2023+)
TASA_RENTA = Decimal("0.35")  # 35% tarifa general sociedades
CUENTA_RENTA_GASTO = "540502"  # Provisión Impuesto de Renta
CUENTA_RENTA_PASIVO = "240405"  # Impuesto de Renta por Pagar

# PUC ranges
PUC_SERVICIOS_START = 5000
PUC_SERVICIOS_END = 5999
PUC_INGRESOS_START = 4000
PUC_INGRESOS_END = 4999

# Tax liability PUC sub-accounts — aligned with persist_node._build_journal_entries
CUENTA_RETEFUENTE = "240815"  # Retención en la Fuente - Servicios
CUENTA_RETEICA = "236540"  # ReteICA por pagar
CUENTA_IVA = "240802"  # IVA descontable


# ---------------------------------------------------------------------------
# Helper: detect transaction type from PUC codes in asientos
# ---------------------------------------------------------------------------


def _detect_transaction_type(asientos: list[dict]) -> str:
    """
    Infer transaction type from debit PUC codes.

    Returns 'servicios', 'bienes', or 'arrendamiento'.
    Defaults to 'bienes' if no 5xxx debit line is found.
    """
    for asiento in asientos:
        if (asiento.get("tipo_movimiento") or "").lower() == "debito":
            puc_raw = str(asiento.get("cuenta_puc", "")).strip()
            if puc_raw.isdigit():
                puc_int = int(puc_raw)
                if PUC_SERVICIOS_START <= puc_int <= PUC_SERVICIOS_END:
                    desc = (asiento.get("descripcion") or "").lower()
                    if "arrendamiento" in desc or "alquiler" in desc:
                        return "arrendamiento"
                    return "servicios"
    return "bienes"


def _has_iva_in_asientos(asientos: list[dict]) -> tuple[bool, Decimal]:
    """
    Check if IVA already exists in contador asientos.

    Matches PUC code '2408' (header account) or codes starting with '240802'
    (IVA descontable sub-accounts).  Deliberately excludes '240815' (Retefuente)
    which shares the '2408' prefix but is NOT an IVA account.
    """
    for asiento in asientos:
        puc_raw = str(asiento.get("cuenta_puc", "")).strip()
        if puc_raw == "2408" or puc_raw.startswith("240802"):
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


def tributario_node(state: AgentState) -> AgentState:
    """
    Tributario worker node — replaces Sprint-12 stub.

    1. Reads ContadorOutput from state.
    2. Runs deterministic Colombian tax calculations.
    3. Queries RAG normativo for legal context.
    4. Calls Gemini to validate rates and produce TaxJustification.
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

    # Compute base gravable from non-tax debit lines only.
    # PUC 2xxx = liabilities (IVA descontable, retenciones) — exclude these debits
    # to avoid inflating the taxable base when the contador already broke out IVA.
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
        nit_receptor = None
        if not company_config:
            for tx in state.get("raw_transactions") or []:
                nit_receptor = tx.get("nit_receptor")
                if nit_receptor:
                    break

            if nit_receptor:
                try:
                    from app.core.database import SessionLocal
                    from app.services import db_service as _db_svc

                    _db = SessionLocal()
                    try:
                        row = _db_svc.get_company_settings(_db, nit_receptor)
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
                            }
                            logger.info(
                                f"Tributario: loaded company settings for NIT {nit_receptor}"
                            )
                        else:
                            logger.warning(
                                "Tributario: missing company settings for NIT %s",
                                nit_receptor,
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
            if nit_receptor:
                state["error"] = (
                    "Tributario precondition failed: missing company tax settings for "
                    f"NIT {nit_receptor}. Configure it first at "
                    f"{setup_endpoint.format(nit=nit_receptor)}"
                )
                details = {
                    "error": state["error"],
                    "nit_receptor": nit_receptor,
                    "required_endpoint": setup_endpoint,
                }
            else:
                state["error"] = (
                    "Tributario precondition failed: missing nit_receptor in raw_transactions. "
                    "Cannot resolve company tax settings."
                )
                details = {
                    "error": state["error"],
                    "nit_receptor": None,
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
        tasa_reteica_efectiva = (
            Decimal(str(company_config["tasa_reteica"]))
            if company_config
            else TASA_RETEICA_DEFAULT
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

        # ------------------------------------------------------------------
        # Step 2 — Determine transaction type from PUC codes
        # ------------------------------------------------------------------
        tipo_transaccion = _detect_transaction_type(asientos)
        logger.info(f"Tributario: detected transaction type = {tipo_transaccion}")

        # ------------------------------------------------------------------
        # Step 3 — Deterministic tax calculations using effective rates
        # ------------------------------------------------------------------
        tasa_retefuente = tasa_retefuente_efectiva.get(
            tipo_transaccion, tasa_retefuente_efectiva["servicios"]
        )
        retefuente_val = (base_gravable * tasa_retefuente).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        reteica_val = (base_gravable * tasa_reteica_efectiva).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # IVA: capture from asientos if already present, otherwise calculate
        iva_presente, iva_val_existente = _has_iva_in_asientos(asientos)
        if iva_presente:
            iva_val = iva_val_existente
            logger.info(f"Tributario: IVA found in asientos = {iva_val}")
        elif not iva_responsable:
            iva_val = Decimal("0.00")
            logger.info("Tributario: IVA skipped — company is not IVA responsable")
        else:
            iva_val = (base_gravable * tasa_iva_efectiva).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            logger.info(f"Tributario: IVA calculated = {iva_val}")

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
        # Step 5 — Gemini justification (structured output)
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

        gemini_client = get_gemini_client()
        try:
            justification = gemini_client.justify_tax_analysis(tax_amounts, rag_context)
        except Exception as gemini_err:
            logger.warning(
                f"Tributario: Gemini justification failed (using fallback): {gemini_err}"
            )
            from app.core.gemini_client import TaxJustification as _TaxJustification

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
                    "cuenta_puc": CUENTA_RETEFUENTE,
                }
            )

        if reteica_val > 0:
            impuestos.append(
                {
                    "tipo_impuesto": "reteica",
                    "base_gravable": str(base_gravable),
                    "tarifa_porcentaje": str(tasa_reteica_efectiva * 100),
                    "valor_impuesto": str(reteica_val),
                    "cuenta_puc": CUENTA_RETEICA,
                }
            )

        if iva_val > 0:
            impuestos.append(
                {
                    "tipo_impuesto": "IVA",
                    "base_gravable": str(base_gravable),
                    "tarifa_porcentaje": str(tasa_iva_efectiva * 100),
                    "valor_impuesto": str(iva_val),
                    "cuenta_puc": CUENTA_IVA,
                }
            )

        if ica_val > Decimal("0"):
            impuestos.append(
                {
                    "tipo_impuesto": "ica",
                    "base_gravable": str(ingresos_brutos),
                    "tarifa_porcentaje": str(tasa_ica_efectiva * 100),
                    "valor_impuesto": str(ica_val),
                    "cuenta_puc": CUENTA_ICA_PASIVO,
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
        # Colombian accounting model for a purchase transaction:
        #   - IVA descontable (240802) is a DEBIT asset (recoverable from DIAN)
        #   - Retenciones (retefuente + reteica) become new CREDIT liabilities
        #   - Net effect on the largest existing CREDIT (e.g. bank / proveedor):
        #       +iva_to_add  (buyer pays IVA to vendor on top of base; 0 if already present)
        #       -total_retenciones  (amounts withheld from vendor payment)
        asientos_enriquecidos = [dict(a) for a in asientos]  # copy from contador

        total_retenciones = retefuente_val + reteica_val
        iva_to_add = iva_val if not iva_presente else Decimal("0")
        net_credit_adjustment = iva_to_add - total_retenciones

        if total_retenciones > 0 or iva_to_add > 0:
            credit_entries = [
                (i, e)
                for i, e in enumerate(asientos_enriquecidos)
                if e.get("tipo_movimiento", "").lower() == "credito"
            ]
            if credit_entries:
                largest_idx, largest_entry = max(
                    credit_entries, key=lambda x: Decimal(str(x[1].get("valor", 0)))
                )
                original_valor = Decimal(str(largest_entry.get("valor", 0)))
                adjusted_valor = (original_valor + net_credit_adjustment).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                # Guard: retenciones must not exceed the amount payable to vendor
                if adjusted_valor < Decimal("0"):
                    state["error"] = (
                        f"Tributario error: retenciones ({total_retenciones}) exceed the largest "
                        f"credit account {largest_entry.get('cuenta_puc')} "
                        f"valor ({original_valor}). Cannot produce a balanced journal entry."
                    )
                    append_log(
                        state, "tributario", "node_error", {"error": state["error"]}
                    )
                    return state
                asientos_enriquecidos[largest_idx]["valor"] = str(adjusted_valor)
                logger.info(
                    f"Tributario: adjusted credit {largest_entry.get('cuenta_puc')} "
                    f"from {original_valor} to {adjusted_valor} "
                    f"(iva_new={iva_to_add}, retenciones={total_retenciones})"
                )

        # IVA descontable — posted as DEBIT (asset, recoverable from DIAN)
        if iva_to_add > 0:
            asientos_enriquecidos.append(
                {
                    "cuenta_puc": CUENTA_IVA,
                    "nombre_cuenta": "IVA Descontable",
                    "descripcion": "IVA descontable — Art. 477 ET",
                    "tipo_movimiento": "debito",
                    "valor": str(iva_to_add),
                }
            )

        # Retenciones — posted as CREDITS (liabilities to DIAN / municipality)
        if retefuente_val > 0:
            asientos_enriquecidos.append(
                {
                    "cuenta_puc": CUENTA_RETEFUENTE,
                    "nombre_cuenta": "Retención en la Fuente por Pagar",
                    "descripcion": f"Retención en la Fuente por pagar — {referencias[0] if referencias else 'Art. 383 ET'}",
                    "tipo_movimiento": "credito",
                    "valor": str(retefuente_val),
                }
            )

        if reteica_val > 0:
            asientos_enriquecidos.append(
                {
                    "cuenta_puc": CUENTA_RETEICA,
                    "nombre_cuenta": "Retención ICA por Pagar",
                    "descripcion": "Retención ICA por pagar — Decreto 2048/1992",
                    "tipo_movimiento": "credito",
                    "valor": str(reteica_val),
                }
            )

        # ICA — Impuesto de Industria y Comercio (Ley 14/1983)
        if ica_val > Decimal("0"):
            asientos_enriquecidos.append(
                {
                    "cuenta_puc": CUENTA_ICA_GASTO,
                    "nombre_cuenta": "Gasto ICA",
                    "descripcion": "Gasto ICA — Ley 14/1983, Art. 342 Ley 1955/2019",
                    "tipo_movimiento": "debito",
                    "valor": str(ica_val),
                }
            )
            asientos_enriquecidos.append(
                {
                    "cuenta_puc": CUENTA_ICA_PASIVO,
                    "nombre_cuenta": "ICA por Pagar",
                    "descripcion": "ICA por Pagar — Decreto 1333/1986",
                    "tipo_movimiento": "credito",
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
        return state

    except Exception as e:
        state["error"] = f"Tributario error: {str(e)}"
        logger.error(state["error"], exc_info=True)
        append_log(state, "tributario", "node_error", {"error": str(e)})
        return state
