"""Post-processing corrector for contador asientos.

When the contador LLM returns the catch-all `5195` (Otros Gastos Diversos)
for a line whose description clearly identifies a more specific expense
account, this module rewrites the line to the correct PUC code.

It runs after the LLM call and before the Pydantic validator so the final
ContadorOutput uses concrete subaccounts whenever possible.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable

logger = logging.getLogger(__name__)


# Keyword -> PUC code. Order matters: the first match wins, so put the more
# specific patterns before broader ones (e.g. "intereses cesantias" before
# "cesantias"). Keys are matched against a normalized (lowercased,
# accent-folded) description, so write them lowercase and unaccented.
_KEYWORD_TO_PUC: tuple[tuple[str, str], ...] = (
    # Personal / nomina
    ("intereses cesantias", "510515"),
    ("prima de servicios", "510518"),
    ("prima servicios", "510518"),
    ("cesantias", "510510"),
    ("vacaciones", "510521"),
    ("eps", "510527"),
    ("salud empleado", "510527"),
    ("aportes a arp", "510530"),
    ("aportes a arl", "510530"),
    ("aporte arl", "510530"),
    ("arl", "510530"),
    ("aportes a pension", "510533"),
    ("aporte pension", "510533"),
    ("fondo de pensiones", "510533"),
    ("pension", "510533"),
    ("seguridad social", "510527"),
    ("caja de compensacion", "510568"),
    ("caja compensacion", "510568"),
    ("caja de compensación", "510568"),
    ("comfama", "510568"),
    ("comfenalco", "510568"),
    ("compensar", "510568"),
    ("colsubsidio", "510568"),
    ("cafam", "510568"),
    ("sena", "510569"),
    ("icbf", "510570"),
    ("parafiscal", "510568"),
    ("salario", "510505"),
    ("sueldo", "510505"),
    ("nomina", "510505"),
    # Servicios / honorarios — order: most specific first (juridico before asesor)
    ("juridico", "511515"),
    ("juridica", "511515"),
    ("abogado", "511515"),
    ("legal", "511515"),
    ("contador", "511505"),
    ("contabilidad", "511505"),
    ("auditor", "511505"),
    ("honorario", "511505"),
    ("comision banco", "530525"),
    ("comision", "511510"),
    ("servicio tecnico", "511525"),
    ("asesor", "511595"),
    # Operativos / servicios públicos
    ("combustible", "513025"),
    ("gasolina", "513025"),
    ("acpm", "513025"),
    ("servicio publico", "513540"),
    ("servicios publicos", "513540"),
    ("energia", "513540"),
    ("acueducto", "513540"),
    ("alcantarillado", "513540"),
    ("agua", "513540"),
    ("gas natural", "513540"),
    ("celular", "513540"),
    ("telefono", "513540"),
    ("internet", "513540"),
    ("transporte", "513550"),
    ("flete", "513550"),
    ("acarreo", "513550"),
    ("mensajeria", "513550"),
    # Mantenimiento / aseo / vigilancia
    ("mantenimiento", "514505"),
    ("reparacion", "514505"),
    ("aseo", "514515"),
    ("limpieza", "514515"),
    ("vigilancia", "514525"),
    ("seguridad", "514525"),
    # Útiles / papelería / software
    ("papeleria", "519525"),
    ("utiles", "519525"),
    ("licencia", "519525"),
    ("software", "519525"),
    ("dominio", "519525"),
    ("hosting", "519525"),
    # Tributarios
    ("ica", "521505"),
    ("impuesto de industria", "521505"),
    ("4x1000", "529505"),
    ("4 por mil", "529505"),
    ("4 mil", "529505"),
    ("gravamen movimientos", "529505"),
    ("gmf", "529505"),
    # Publicidad / mercadeo
    ("publicidad", "523510"),
    ("propaganda", "523510"),
    ("mercadeo", "523510"),
    # Membresias / afiliaciones
    ("cuota afiliacion", "519520"),
    ("afiliacion", "519520"),
    ("camara comercio", "519520"),
    # Financieros
    ("interes", "530520"),
    # Inventario / activos (debit-side, not strictly gasto)
    ("inventario", "143005"),
    ("equipo de computo", "152405"),
    ("equipo de oficina", "152805"),
)


_ACCENT_TABLE = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")


# Text tokens the LLM sometimes emits as cuenta_puc instead of a numeric code.
# These leak from source documents where a column is labelled "Cuenta" but the
# value is actually a counterparty descriptor. Map to the closest catalogue
# account; if no good guess, fall back to 519595 so the schema validator stops
# blocking the asiento and the auditor can flag it.
_TEXT_TOKEN_TO_PUC: tuple[tuple[str, str], ...] = (
    ("banco", "111005"),
    ("bancaria", "111005"),
    ("caja menor", "110510"),
    ("caja", "110505"),
    ("cliente", "130505"),
    ("deudor", "130505"),
    ("cuentas por cobrar", "130505"),
    ("cxc", "130505"),
    ("proveedor", "220505"),
    ("cuentas por pagar", "220505"),
    ("cxp", "220505"),
    ("acreedor", "233525"),
    ("nomina por pagar", "250505"),
    ("salarios por pagar", "250505"),
    ("retencion", "236540"),
    ("iva por pagar", "240805"),
    ("iva descontable", "240810"),
    ("iva", "240805"),
    ("ica", "236805"),
    ("ingreso", "413535"),
    ("venta", "413535"),
    ("gasto", "519595"),
    ("compra", "143505"),
    ("inventario", "143505"),
    ("activo", "151005"),
    ("pasivo", "220505"),
    ("patrimonio", "310505"),
    ("capital", "310505"),
)

_TEXT_TOKEN_FALLBACK = "519595"  # Otros Gastos Diversos


def _normalize(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.translate(_ACCENT_TABLE).lower()).strip()


def _suggest_puc(description: str) -> str | None:
    norm = _normalize(description)
    if not norm:
        return None
    for keyword, code in _KEYWORD_TO_PUC:
        if keyword in norm:
            return code
    return None


def correct_5195_fallback(line: dict) -> dict:
    """Rewrite a debit line stuck on cuenta_puc=5195 to a more specific
    expense subaccount when the description matches a known keyword.

    Only debit lines are touched; the credit side of a CE is bank/CxP and
    never falls to 5195 legitimately.
    """
    cuenta = str(line.get("cuenta_puc") or "").strip()
    if cuenta != "5195":
        return line
    if str(line.get("tipo_movimiento") or "").lower() != "debito":
        return line
    description = str(
        line.get("descripcion") or line.get("concepto") or line.get("description") or ""
    )
    suggested = _suggest_puc(description)
    if not suggested:
        # Persist as 5195 but surface a warning so it shows up in audit logs.
        # CLAUDE.md mandates a logger.warning whenever the 5195/519595 fallback
        # is used — otherwise these silently accumulate as "Gastos Diversos".
        logger.warning(
            "contador_puc_corrector: 5195 (Gastos Diversos) fallback persisted "
            "without remap — descripcion=%r. Consider extending _suggest_puc "
            "keyword list or adding doc-type guidance in contador prompt.",
            description[:120],
        )
        return line
    logger.info(
        "contador_puc_corrector: rewriting 5195 -> %s based on description=%r",
        suggested,
        description[:120],
    )
    line["cuenta_puc"] = suggested
    return line


# Doc subtypes that represent cash outflow / bank movements. In these the
# credit side must hit a cash account (banco/caja); 220505 (CxP) cred is a
# misclassification inherited from factura_compra patterns.
_BANK_OUTFLOW_DOC_SUBTYPES = frozenset(
    {
        "comprobante_egreso",
        "extracto_bancario",
        "conciliacion_bancaria",
    }
)


def correct_ce_220505_credit(line: dict, doc_subtype: str) -> dict:
    """When a doc representing a cash outflow (CE, extracto bancario,
    conciliación) credits 220505 (Proveedores) it has inherited the
    factura_compra pattern by mistake. The credit side must go to 111005
    (Banco) or 110505 (Caja). Swap 220505 cred -> 111005.

    Function name kept for backwards compatibility; the rule now covers
    every `_BANK_OUTFLOW_DOC_SUBTYPES` value.
    """
    if doc_subtype not in _BANK_OUTFLOW_DOC_SUBTYPES:
        return line
    cuenta = str(line.get("cuenta_puc") or "").strip()
    mov = str(line.get("tipo_movimiento") or "").lower()
    if cuenta != "220505" or mov != "credito":
        return line
    logger.info(
        "contador_puc_corrector: %s 220505 cred -> 111005 (Banco) — descripcion=%r",
        doc_subtype,
        str(line.get("descripcion") or "")[:120],
    )
    line["cuenta_puc"] = "111005"
    return line


# Class codes (4-digit) that should specialize into a 6-digit subaccount.
# Only classes 4 (ingresos), 5 (gastos), 6 (costos) require this — assets
# (1xxx), liabilities (2xxx) and equity (3xxx) commonly use 4-digit parents
# directly when no subaccount applies.
_CLASS_REQUIRES_SPECIALIZATION = ("4", "5", "6")


# Cross-class corrections: when the LLM picks a 4-digit code in the wrong
# class entirely (e.g. "5110" with description "Bancos" — should be 111005
# in class 1, not class 5). The same-class corrector (`correct_class_only_codes`)
# explicitly refuses cross-class swaps via the `startswith(cuenta[0])` guard,
# so we need a separate corrector that runs FIRST with explicit (wrong, keywords,
# target) tuples for the known LLM mistakes we observe in production.
_CROSS_CLASS_CORRECTIONS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    # (wrong_cuenta, keyword_substrings_in_description, target_cuenta)
    ("5110", ("banco", "bancos"), "111005"),
    ("5305", ("interes", "mora"), "530520"),
    ("5135", ("agua", "internet", "servicio publico", "servicios publicos"), "513540"),
    ("4135", ("venta", "comercio"), "413535"),
)


def correct_cross_class_codes(line: dict) -> dict:
    """Fix LLM mistakes where it placed a known account in the wrong class.

    Runs BEFORE `correct_class_only_codes` — if a swap applies, the line
    becomes a valid 6-digit subaccount and the same-class corrector no-ops.
    Without this pass the same-class corrector would block the swap because
    it requires the suggestion to start with the same class digit.
    """
    cuenta = str(line.get("cuenta_puc") or "").strip()
    description = str(
        line.get("descripcion") or line.get("concepto") or line.get("description") or ""
    ).lower()
    for wrong, keywords, target in _CROSS_CLASS_CORRECTIONS:
        if cuenta == wrong and any(kw in description for kw in keywords):
            logger.info(
                "contador_puc_corrector: cross-class swap %s -> %s based on description=%r",
                cuenta,
                target,
                description[:120],
            )
            line["cuenta_puc"] = target
            return line
    return line


def correct_class_only_codes(line: dict) -> dict:
    """When the LLM returns a 4-digit class-level code for an income or
    expense line, attempt to specialize via keyword matching. If no
    keyword matches the code is left untouched and a warning is logged so
    a downstream validator can flag it.
    """
    cuenta = str(line.get("cuenta_puc") or "").strip()
    if len(cuenta) > 4 or len(cuenta) < 3:
        return line
    if not cuenta or cuenta[0] not in _CLASS_REQUIRES_SPECIALIZATION:
        return line
    # Cover every field the contador or downstream may have populated. The
    # cuenta name (puc_descripcion / nombre_cuenta) is often more informative
    # than the journal-line descripcion for keyword matching.
    description = " ".join(
        str(line.get(k) or "")
        for k in (
            "descripcion",
            "concepto",
            "description",
            "puc_descripcion",
            "nombre_cuenta",
            "nombre",
            "detalle",
        )
    )
    suggested = _suggest_puc(description)
    if not suggested or not suggested.startswith(cuenta[0]):
        logger.warning(
            "contador_puc_corrector: 4-digit class code %s for %s line (descripcion=%r) — "
            "no keyword match, leaving unchanged",
            cuenta,
            line.get("tipo_movimiento") or "?",
            description[:120],
        )
        return line
    logger.info(
        "contador_puc_corrector: specializing class %s -> %s based on description=%r",
        cuenta,
        suggested,
        description[:120],
    )
    line["cuenta_puc"] = suggested
    return line


_PUC_NUMERIC_RE = re.compile(r"^\d{1,12}$")


def correct_non_numeric_puc(line: dict) -> dict:
    """Replace non-numeric cuenta_puc strings (e.g. 'TERCERO', 'BANCO', 'CLIENTE')
    with the closest numeric catalogue account.

    The schema validator requires `^\\d{1,12}$`. When the LLM leaks a column
    label or counterparty descriptor as the account code, every retry fails
    with the same Pydantic error. Map text tokens to numeric codes BEFORE
    other correctors run; if no token matches, fall back to 519595 (Gastos
    Diversos) and emit a warning so the auditor can flag the line.
    """
    raw_cuenta = line.get("cuenta_puc")
    if raw_cuenta is None:
        return line
    cuenta = str(raw_cuenta).strip()
    if not cuenta or _PUC_NUMERIC_RE.match(cuenta):
        return line

    norm = _normalize(cuenta)
    # First try keyword match against the cuenta value itself.
    matched_code: str | None = None
    for keyword, code in _TEXT_TOKEN_TO_PUC:
        if keyword in norm:
            matched_code = code
            break

    # Fall back: try the descripcion / nombre_cuenta fields.
    if matched_code is None:
        description = " ".join(
            str(line.get(k) or "")
            for k in (
                "descripcion",
                "concepto",
                "description",
                "puc_descripcion",
                "nombre_cuenta",
            )
        )
        matched_code = _suggest_puc(description)

    if matched_code is None:
        matched_code = _TEXT_TOKEN_FALLBACK
        logger.warning(
            "contador_puc_corrector: non-numeric cuenta_puc %r — no token match, "
            "falling back to %s (Gastos Diversos). Audit recommended.",
            cuenta[:60],
            _TEXT_TOKEN_FALLBACK,
        )
    else:
        logger.info(
            "contador_puc_corrector: non-numeric cuenta_puc %r -> %s",
            cuenta[:60],
            matched_code,
        )

    line["cuenta_puc"] = matched_code
    return line


def correct_aux_codes_beyond_catalog(line: dict, *, db=None) -> dict:
    """Normalise auxiliary PUC codes that are longer than what the company
    catalogue supports.

    Comprobantes printed by a company often use 7-9 digit auxiliary codes
    (e.g. ``11200501`` — bank account 1120 with internal suffix ``0501``).
    Those auxiliary tails are configuration of the issuer's ERP, not part of
    the standard PUC, and they break joins against ``cuentas_puc`` (which
    only carries 4-digit groups and 6-digit subaccounts per Decreto 2650/1993).

    Behaviour:
      - If the code length is <=6, leave it alone (regular corrector pipeline
        handles those cases).
      - Otherwise, try the 6-digit prefix; if that prefix exists in the seeded
        ``cuentas_puc`` catalogue, use it. Else fall back to the 4-digit prefix
        if that exists. Else leave the original code untouched and log a
        warning so the auditor surfaces it.

    The catalogue lookup is dynamic (DB query) — no hardcoded mappings.

    Pass ``db`` (an open SQLAlchemy session) to reuse a single connection
    across multiple asiento lines. When omitted, a local session is opened
    and closed for backward compatibility.
    """
    cuenta = str(line.get("cuenta_puc") or "").strip()
    if len(cuenta) <= 6 or not cuenta.isdigit():
        return line

    try:
        from app.core.database import SessionLocal
        from app.models.database import CuentaPUC
    except Exception as imp_err:
        logger.warning(
            "contador_puc_corrector: PUC catalog import failed (%s); leaving %s",
            imp_err,
            cuenta,
        )
        return line

    own_session = db is None
    try:
        if own_session:
            db = SessionLocal()
        for parent_len in (6, 4):
            candidate = cuenta[:parent_len]
            row = (
                db.query(CuentaPUC).filter(CuentaPUC.codigo == candidate).one_or_none()
            )
            if row is not None:
                logger.info(
                    "contador_puc_corrector: aux code %s normalised to catalogue parent %s",
                    cuenta,
                    candidate,
                )
                line["cuenta_puc"] = candidate
                return line
        logger.warning(
            "contador_puc_corrector: aux code %s has no 6- or 4-digit parent in catalog; leaving as-is",
            cuenta,
        )
    except Exception as db_err:
        logger.warning(
            "contador_puc_corrector: PUC parent lookup failed for %s: %s",
            cuenta,
            db_err,
        )
    finally:
        if own_session and db is not None:
            try:
                db.close()
            except Exception:
                pass
    return line


def correct_asiento_lines(lines: Iterable[dict], doc_subtype: str = "") -> list[dict]:
    """Apply all line-level corrections in sequence. Each corrector is
    idempotent and returns the line unchanged when its rule doesn't match.

    Opens a single DB session shared across all line corrections to avoid
    N+1 SessionLocal() opens when documents have many asiento lines.
    """
    from app.core.database import SessionLocal

    out: list[dict] = []
    db = SessionLocal()
    try:
        for raw in lines:
            line = dict(raw)
            line = correct_non_numeric_puc(line)
            line = correct_aux_codes_beyond_catalog(line, db=db)
            line = correct_cross_class_codes(line)
            line = correct_5195_fallback(line)
            line = correct_ce_220505_credit(line, doc_subtype)
            line = correct_class_only_codes(line)
            out.append(line)
    finally:
        try:
            db.close()
        except Exception:
            pass
    return out


def correct_contador_output(output: dict, doc_subtype: str = "") -> dict:
    """Walk a ContadorOutput dict and rewrite any 5195 entries.

    The active ContadorOutput schema flattens journal entries into
    `output["asientos"]` (each item is one debit or credit line). Older
    shapes used `output["asientos"][*]["lineas"]` or
    `output["journal_entries"]`. All three layouts are supported so the
    corrector remains robust to schema evolution.

    `doc_subtype` is the granular frontend doc type (e.g. "comprobante_egreso")
    needed by `correct_ce_220505_credit` to scope its rule.
    """
    if not isinstance(output, dict):
        return output

    asientos = output.get("asientos")
    if isinstance(asientos, list):
        if asientos and isinstance(asientos[0], dict) and "lineas" in asientos[0]:
            for asiento in asientos:
                if isinstance(asiento, dict):
                    lineas = asiento.get("lineas")
                    if isinstance(lineas, list):
                        asiento["lineas"] = correct_asiento_lines(lineas, doc_subtype)
        else:
            output["asientos"] = correct_asiento_lines(asientos, doc_subtype)

    journal = output.get("journal_entries")
    if isinstance(journal, list):
        output["journal_entries"] = correct_asiento_lines(journal, doc_subtype)

    return output
