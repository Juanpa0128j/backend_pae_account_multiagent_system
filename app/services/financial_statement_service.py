"""Internal services for financial statements query and derived statement generation."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.database import FinancialStatement, IngestStatus
from app.services import db_service
from app.services.nit_utils import normalize_nit

_log = logging.getLogger(__name__)


_REQUIRED_DERIVATION_INPUTS = ("balance_general", "estado_resultados", "libro_auxiliar")
_DERIVED_TARGETS = ("flujo_de_caja", "cambios_patrimonio", "notas_estados_financieros")
DERIVED_TARGETS = _DERIVED_TARGETS  # public alias for inter-module use
_FIRST_LEVEL_TYPES = (
    "balance_general",
    "estado_resultados",
    "libro_auxiliar",
    "libro_diario",
)

# Periodicity thresholds — same as the alembic backfill so historical and new
# rows are classified identically. Picked to absorb fiscal-year and partial-
# month variance: a "monthly" close can be 28-31 days, a quarter 89-92, etc.
_ANNUAL_MIN_DAYS = 300
_QUARTERLY_MIN_DAYS = 80
_MONTHLY_MIN_DAYS = 25

# Map LLM-extracted Spanish labels to the canonical English values stored in
# the ``frequency`` column. Lowercased + stripped before lookup.
_PERIODICIDAD_NORMALIZE = {
    "mensual": "monthly",
    "monthly": "monthly",
    "trimestral": "quarterly",
    "quarterly": "quarterly",
    "anual": "annual",
    "annual": "annual",
    "yearly": "annual",
    "personalizado": "custom",
    "custom": "custom",
}


def infer_frequency(
    period_start: datetime | None, period_end: datetime | None
) -> str | None:
    """Classify a statement's span as monthly / quarterly / annual / custom.

    Same thresholds as the alembic backfill in
    ``a7b8c9d0e1f2_add_frequency_to_financial_statements.py``.
    Returns ``None`` when either bound is missing — the caller can decide
    whether to require a review.
    """
    if period_start is None or period_end is None:
        return None
    days = (period_end - period_start).days
    if days >= _ANNUAL_MIN_DAYS:
        return "annual"
    if days >= _QUARTERLY_MIN_DAYS:
        return "quarterly"
    if days >= _MONTHLY_MIN_DAYS:
        return "monthly"
    if days >= 0:
        return "custom"
    return None


def normalize_periodicidad(value: str | None) -> str | None:
    """Translate an LLM-emitted Spanish/English label to the stored value."""
    if not value:
        return None
    return _PERIODICIDAD_NORMALIZE.get(str(value).strip().lower())


def is_annual(stmt: FinancialStatement) -> bool:
    """True when the row is (or computes to) an annual closing.

    Reads ``frequency`` when populated; otherwise falls back to
    :func:`infer_frequency` so legacy rows uploaded before the column existed
    keep working without a manual backfill.
    """
    freq = getattr(stmt, "frequency", None)
    if freq is None:
        freq = infer_frequency(stmt.period_start, stmt.period_end)
    return freq == "annual"


def _libro_auxiliar_is_comprehensive(la_data: dict) -> bool:
    """A LA can stand in for BG+ER when it covers PUC classes 1-7.

    Per Decreto 2650/1993 the chart of accounts splits classes 1-3 (balance)
    from 4-7 (resultados). A libro auxiliar that lists movements in both halves
    is functionally equivalent to having BG + ER together. If the upload only
    covers caja (class 11) or only IVA (class 2408), it's NOT comprehensive
    and the derivation will refuse to use it as the sole source.
    """
    if not isinstance(la_data, dict):
        return False
    classes_present: set[str] = set()
    for line in la_data.get("lines") or la_data.get("accounts") or []:
        if not isinstance(line, dict):
            continue
        code = str(line.get("cuenta_puc") or line.get("codigo") or "")
        if code:
            classes_present.add(code[0])
    has_balance_classes = {"1", "2", "3"}.issubset(classes_present)
    has_pnl_classes = bool({"4", "5", "6", "7"} & classes_present)
    return has_balance_classes and has_pnl_classes


def synthesize_balance_from_libro_auxiliar(la_data: dict) -> dict:
    """Reconstruct a balance_general payload from a comprehensive LA.

    Aggregates débitos − créditos per cuenta_puc, then groups class 1 / 2 / 3
    accounts into activos / pasivos / patrimonio. Signs follow the
    debit-natured convention for class 1 (assets positive when net debit) and
    credit-natured for classes 2-3 (liabilities/equity positive when net
    credit). Shape mirrors the LLM-extracted balance_general so downstream
    derivation can consume it transparently.
    """
    totals: dict[str, dict[str, Any]] = {}
    for line in (la_data or {}).get("lines") or (la_data or {}).get("accounts") or []:
        if not isinstance(line, dict):
            continue
        code = str(line.get("cuenta_puc") or line.get("codigo") or "")
        if not code or code[0] not in ("1", "2", "3"):
            continue
        try:
            debit = float(line.get("debito") or 0)
            credit = float(line.get("credito") or 0)
        except (TypeError, ValueError):
            debit = credit = 0.0
        entry = totals.setdefault(
            code,
            {
                "cuenta_puc": code,
                "nombre": str(line.get("cuenta_nombre") or line.get("nombre") or ""),
                "_debit": 0.0,
                "_credit": 0.0,
            },
        )
        entry["_debit"] += debit
        entry["_credit"] += credit

    accounts: list[dict[str, Any]] = []
    total_activos = total_pasivos = total_patrimonio = 0.0
    for entry in totals.values():
        code = entry["cuenta_puc"]
        debit = entry["_debit"]
        credit = entry["_credit"]
        # class 1 = debit-natured; classes 2 & 3 = credit-natured.
        saldo = (debit - credit) if code.startswith("1") else (credit - debit)
        accounts.append(
            {
                "cuenta_puc": code,
                "nombre": entry["nombre"],
                "saldo": saldo,
            }
        )
        if code.startswith("1"):
            total_activos += saldo
        elif code.startswith("2"):
            total_pasivos += saldo
        elif code.startswith("3"):
            total_patrimonio += saldo

    accounts.sort(key=lambda a: a["cuenta_puc"])
    return {
        "accounts": accounts,
        "total_activos": total_activos,
        "total_pasivos": total_pasivos,
        "total_patrimonio": total_patrimonio,
        "source": "synthesized_from_libro_auxiliar",
    }


def synthesize_income_statement_from_libro_auxiliar(la_data: dict) -> dict:
    """Reconstruct an estado_resultados payload from a comprehensive LA.

    Class 4 = ingresos (credit-natured), class 5 = gastos (debit-natured),
    class 6 = costo de ventas, class 7 = costos de producción (both
    debit-natured). Utilidad neta = ingresos − costo − gastos.
    """
    totals: dict[str, dict[str, Any]] = {}
    for line in (la_data or {}).get("lines") or (la_data or {}).get("accounts") or []:
        if not isinstance(line, dict):
            continue
        code = str(line.get("cuenta_puc") or line.get("codigo") or "")
        if not code or code[0] not in ("4", "5", "6", "7"):
            continue
        try:
            debit = float(line.get("debito") or 0)
            credit = float(line.get("credito") or 0)
        except (TypeError, ValueError):
            debit = credit = 0.0
        entry = totals.setdefault(
            code,
            {
                "cuenta_puc": code,
                "nombre": str(line.get("cuenta_nombre") or line.get("nombre") or ""),
                "_debit": 0.0,
                "_credit": 0.0,
            },
        )
        entry["_debit"] += debit
        entry["_credit"] += credit

    accounts: list[dict[str, Any]] = []
    total_ingresos = total_gastos = total_costo = 0.0
    for entry in totals.values():
        code = entry["cuenta_puc"]
        debit = entry["_debit"]
        credit = entry["_credit"]
        saldo = (credit - debit) if code.startswith("4") else (debit - credit)
        accounts.append(
            {
                "cuenta_puc": code,
                "nombre": entry["nombre"],
                "saldo": saldo,
            }
        )
        if code.startswith("4"):
            total_ingresos += saldo
        elif code.startswith("5"):
            total_gastos += saldo
        elif code.startswith("6") or code.startswith("7"):
            total_costo += saldo

    accounts.sort(key=lambda a: a["cuenta_puc"])
    utilidad_neta = total_ingresos - total_costo - total_gastos
    return {
        "accounts": accounts,
        "total_ingresos": total_ingresos,
        "total_costo_ventas": total_costo,
        "total_gastos": total_gastos,
        "utilidad_bruta": total_ingresos - total_costo,
        "utilidad_neta": utilidad_neta,
        "source": "synthesized_from_libro_auxiliar",
    }


def _first_level_type_exists(
    db,
    *,
    company_nit: str,
    statement_type: str,
    period_start: datetime | None,
    period_end: datetime | None,
) -> bool:
    rows = db_service.get_financial_statements(
        db,
        company_nit=company_nit,
        statement_type=statement_type,
        period_start=period_start,
        period_end=period_end,
    )
    return len(rows) > 0


# Sentinel file_path used to mark synthetic IngestJobs created only to satisfy
# the FK constraint on FinancialStatement. Listing endpoints must filter these
# out so the user does not see phantom "uploads" they never made.
SYNTHETIC_INGEST_FILE_PATH = "internal://journal_derived"


def is_synthetic_ingest_job(job) -> bool:
    """True if the IngestJob was created only as an FK target for derived statements."""
    file_path = getattr(job, "file_path", None)
    return file_path == SYNTHETIC_INGEST_FILE_PATH


def _create_derivation_ingest_job(
    db, company_nit: str, period_end: datetime, doc_type: str
):
    """Create a synthetic IngestJob to satisfy the FK constraint on FinancialStatement.

    Tagged with SYNTHETIC_INGEST_FILE_PATH so listing endpoints can filter it out.
    """
    ingest_job = db_service.create_ingest_job(
        db,
        file_name=f"journal_derived_{company_nit}_{period_end.date().isoformat()}_{doc_type}",
        file_path=SYNTHETIC_INGEST_FILE_PATH,
        commit=False,
    )
    ingest_job.status = IngestStatus.COMPLETED
    ingest_job.document_type = doc_type
    ingest_job.pathway = "build_from_scratch"
    return ingest_job


def _build_bg_data_from_journal(
    db,
    *,
    company_nit: str,
    cutoff_date: datetime,
    period_start: datetime,
    period_end: datetime,
) -> dict[str, Any]:
    """Build a balance_general data dict from posted journal entries up to cutoff_date.

    The balance is CUMULATIVE: it sums every posted entry with ``fecha <= cutoff_date``
    (get_balance_sheet / get_general_ledger both accumulate from the beginning of time).

    This single source of truth is used for two purposes:
    - Current-period BG: cutoff = period_end.
    - Prior-period opening snapshot: cutoff = the instant before period_start.

    Because the opening balance is recomputed from the journal on every call, Via A
    derivation never depends on a previously-persisted prior BG — it is correct
    regardless of the order periods are derived in, and is refreshed automatically
    when new documents are processed.

    get_general_ledger returns {account, name, total_debit, total_credit, net_balance}
    where net_balance = debito - credito for all classes. We convert to natural-balance
    saldos: debit-natural classes (1,5,6) keep D-C as positive; credit-natural classes
    (2,3,4) use C-D as positive.
    """
    bg_raw = db_service.get_balance_sheet(
        db, cutoff_date=cutoff_date, company_nit=company_nit
    )
    ledger_cumulative = db_service.get_general_ledger(
        db, None, cutoff_date, company_nit
    )
    bg_accounts = []
    for row in ledger_cumulative:
        code = str(row.get("account") or "")
        if not code or not code[0].isdigit():
            continue
        clase = int(code[0])
        d = Decimal(str(row.get("total_debit") or 0))
        c = Decimal(str(row.get("total_credit") or 0))
        saldo = (d - c) if clase in (1, 5, 6) else (c - d)
        bg_accounts.append(
            {
                "cuenta_puc": code,
                "nombre": row.get("name") or "",
                "saldo": float(saldo),
            }
        )
    # Vía A never closes class 4/5 to class 36 (resultados del ejercicio) in journal
    # entries. Synthesize a class 36 leaf so _compute_equity_changes can find the
    # period result and set saldo_final correctly.
    net_profit = Decimal(str(bg_raw.get("net_profit") or 0))
    has_class36 = any(a["cuenta_puc"].startswith("36") for a in bg_accounts)
    if net_profit != 0 and not has_class36:
        bg_accounts.append(
            {
                "cuenta_puc": "360505",
                "nombre": "Utilidad del ejercicio",
                "saldo": float(net_profit),
            }
        )
    return {
        "tipo": "balance_general",
        "entidad": {"nit": company_nit},
        "periodo_inicio": period_start.date().isoformat(),
        "periodo_fin": period_end.date().isoformat(),
        "total_activos": bg_raw.get("assets", 0),
        "total_pasivos": bg_raw.get("liabilities", 0),
        "total_patrimonio": bg_raw.get("total_equity", 0),
        "utilidad_neta": bg_raw.get("net_profit", 0),
        "patrimonio_sin_utilidad": bg_raw.get("equity", 0),
        "cuadre": bg_raw.get("is_balanced", False),
        "moneda": "COP",
        "source": "derived_from_journal",
        "accounts": bg_accounts,
    }


def _f(value) -> float:
    """Best-effort float for debito/credito strings; 0.0 on bad input."""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def build_libro_auxiliar_cuentas(
    period_lines: list[dict],
    prior_lines: list[dict],
    name_map: dict[str, str] | None = None,
) -> list[dict]:
    """Group journal lines into per-account subsidiary ledgers (cuentas[]).

    A real libro auxiliar is one running balance PER account — unlike an
    all-accounts aggregate, whose saldo is always 0 because debits == credits.

    For each account:
      * ``saldo_inicial`` = net (débito − crédito) of every posted line dated
        before the period (carried forward).
      * ``movimientos`` = the period's lines with a running ``saldo`` column.
      * ``saldo_final`` = saldo_inicial + Σdébitos − Σcréditos.

    The running ``saldo`` is debit-positive (deudor +, acreedor −) so the sign
    tells the reader the account's nature without a separate column.
    """
    name_map = name_map or {}

    saldo_inicial: dict[str, float] = {}
    for line in prior_lines:
        code = line.get("cuenta_puc")
        if not code:
            continue
        saldo_inicial[code] = (
            saldo_inicial.get(code, 0.0)
            + _f(line.get("debito"))
            - _f(line.get("credito"))
        )

    movimientos_por_cuenta: dict[str, list[dict]] = {}
    for line in period_lines:
        code = line.get("cuenta_puc")
        if not code:
            continue
        movimientos_por_cuenta.setdefault(code, []).append(line)

    cuentas: list[dict] = []
    for code in sorted(set(saldo_inicial) | set(movimientos_por_cuenta)):
        si = round(saldo_inicial.get(code, 0.0), 2)
        saldo = si
        total_deb = total_cred = 0.0
        movimientos: list[dict] = []
        for line in movimientos_por_cuenta.get(code, []):
            deb = _f(line.get("debito"))
            cred = _f(line.get("credito"))
            saldo += deb - cred
            total_deb += deb
            total_cred += cred
            movimientos.append({**line, "saldo": round(saldo, 2)})
        # Skip accounts with neither an opening balance nor period movement.
        if si == 0 and not movimientos:
            continue
        # Name resolution (covers every case): catalog/ledger map first; for
        # codes outside the catalog (e.g. an LLM-emitted 4-digit code) fall back
        # to the movement's own cuenta_nombre.
        nombre = name_map.get(code) or ""
        if not nombre:
            nombre = next(
                (m.get("cuenta_nombre") for m in movimientos if m.get("cuenta_nombre")),
                "",
            )
        cuentas.append(
            {
                "cuenta_puc": code,
                "nombre": nombre,
                "saldo_inicial": si,
                "total_debitos": round(total_deb, 2),
                "total_creditos": round(total_cred, 2),
                "saldo_final": round(si + total_deb - total_cred, 2),
                "movimientos": movimientos,
            }
        )
    return cuentas


def build_first_level_from_journal_entries(
    db,
    *,
    company_nit: str,
    period_start: datetime,
    period_end: datetime,
    frequency: str | None = None,
) -> dict[str, Any]:
    """Build and persist first-level FinancialStatement records from JournalEntryLines.

    Creates balance_general, estado_resultados, libro_auxiliar, libro_diario records
    with source_mode='derived_from_journal'. Idempotent: skips any type already present.

    ``frequency`` ('monthly' | 'quarterly' | 'annual' | 'custom') is stamped on every
    row so the annual gate (NIC 7) can distinguish a manually-generated annual close
    from a monthly one. When None, the row's frequency is left NULL and downstream
    ``is_annual`` falls back to span inference.
    """
    normalized_nit = normalize_nit(company_nit)
    created: dict[str, str] = {}
    skipped: list[str] = []
    build_errors: dict[str, str] = {}

    # --- Balance General ---
    if not _first_level_type_exists(
        db,
        company_nit=normalized_nit,
        statement_type="balance_general",
        period_start=period_start,
        period_end=period_end,
    ):
        try:
            # Cumulative BG as of period_end (uses cutoff_date semantics, not a range).
            bg_data = _build_bg_data_from_journal(
                db,
                company_nit=normalized_nit,
                cutoff_date=period_end,
                period_start=period_start,
                period_end=period_end,
            )
            ingest_job = _create_derivation_ingest_job(
                db, normalized_nit, period_end, "balance_general"
            )
            stmt = db_service.create_financial_statement(
                db,
                ingest_id=ingest_job.id,
                statement_type="balance_general",
                entity_nit=normalized_nit,
                period_start=period_start,
                period_end=period_end,
                source_mode="derived_from_journal",
                frequency=frequency,
                data=bg_data,
                commit=True,
            )
            created["balance_general"] = stmt.id
        except Exception as exc:
            _log.error(
                "build_first_level: failed to create balance_general: %s",
                exc,
                exc_info=True,
            )
            build_errors["balance_general"] = str(exc)
            skipped.append("balance_general")
    else:
        skipped.append("balance_general")

    # --- Estado de Resultados ---
    if not _first_level_type_exists(
        db,
        company_nit=normalized_nit,
        statement_type="estado_resultados",
        period_start=period_start,
        period_end=period_end,
    ):
        try:
            er_raw = db_service.get_pnl(
                db,
                company_nit=normalized_nit,
                start_date=period_start,
                end_date=period_end,
            )
            # Build accounts list so derivation can find ER leaves by PUC prefix.
            er_accounts = [
                {"cuenta_puc": item["cuenta_puc"], "saldo": item["valor"]}
                for item in (
                    er_raw.get("ingresos", [])
                    + er_raw.get("gastos", [])
                    + er_raw.get("costo_ventas", [])
                )
                if isinstance(item, dict) and item.get("cuenta_puc")
            ]
            er_data = {
                "tipo": "estado_resultados",
                "entidad": {"nit": normalized_nit},
                "periodo_inicio": period_start.date().isoformat(),
                "periodo_fin": period_end.date().isoformat(),
                "ingresos": er_raw.get("ingresos", []),
                "gastos": er_raw.get("gastos", []),
                "costo_ventas": er_raw.get("costo_ventas", []),
                "utilidad_bruta": er_raw.get("utilidad_bruta", 0),
                "utilidad_neta": er_raw.get("utilidad_neta", 0),
                "moneda": "COP",
                "source": "derived_from_journal",
                "accounts": er_accounts,
            }
            ingest_job = _create_derivation_ingest_job(
                db, normalized_nit, period_end, "estado_resultados"
            )
            stmt = db_service.create_financial_statement(
                db,
                ingest_id=ingest_job.id,
                statement_type="estado_resultados",
                entity_nit=normalized_nit,
                period_start=period_start,
                period_end=period_end,
                source_mode="derived_from_journal",
                frequency=frequency,
                data=er_data,
                commit=True,
            )
            created["estado_resultados"] = stmt.id
        except Exception as exc:
            _log.error(
                "build_first_level: failed to create estado_resultados: %s",
                exc,
                exc_info=True,
            )
            build_errors["estado_resultados"] = str(exc)
            skipped.append("estado_resultados")
    else:
        skipped.append("estado_resultados")

    # --- Libro Auxiliar ---
    if not _first_level_type_exists(
        db,
        company_nit=normalized_nit,
        statement_type="libro_auxiliar",
        period_start=period_start,
        period_end=period_end,
    ):
        try:
            # get_general_ledger returns a List[Dict] directly
            ledger = db_service.get_general_ledger(
                db, period_start, period_end, normalized_nit
            )
            # Individual journal entry lines needed by _compute_equity_changes
            # (reads la_data["lines"] for debito/credito per account/period).
            journal_lines_for_la = db_service.get_journal_entry_lines(
                db,
                company_nit=normalized_nit,
                start_date=period_start,
                end_date=period_end,
            )
            la_lines = (
                journal_lines_for_la if isinstance(journal_lines_for_la, list) else []
            )
            # Lines dated BEFORE the period → opening balances per account.
            prior_raw = db_service.get_journal_entry_lines(
                db,
                company_nit=normalized_nit,
                start_date=None,
                end_date=period_start,
            )
            ps_str = period_start.date().isoformat()
            prior_lines = [
                ln
                for ln in (prior_raw if isinstance(prior_raw, list) else [])
                if (ln.get("fecha") or "")[:10] < ps_str
            ]
            ledger_list = ledger if isinstance(ledger, list) else []
            # Authoritative names from the PUC catalog (covers accounts with no
            # period movement too), then override with the period ledger's
            # catalog-resolved name. get_general_ledger returns {account, name}.
            name_map = {
                c.codigo: c.nombre for c in db_service.get_all_puc(db) if c.codigo
            }
            for acct in ledger_list:
                code = acct.get("account") or acct.get("cuenta_puc")
                if code and acct.get("name"):
                    name_map[code] = acct["name"]
            cuentas = build_libro_auxiliar_cuentas(la_lines, prior_lines, name_map)
            # Top-level totals are the movement VOLUME (deb == cred for a balanced
            # all-accounts ledger). Per-account opening/closing balances live in
            # cuentas[] — that's where a non-zero saldo is actually meaningful.
            total_debitos = round(sum(_f(ln.get("debito")) for ln in la_lines), 2)
            total_creditos = round(sum(_f(ln.get("credito")) for ln in la_lines), 2)
            la_data = {
                "tipo": "libro_auxiliar",
                "entidad": {"nit": normalized_nit},
                "periodo_inicio": period_start.date().isoformat(),
                "periodo_fin": period_end.date().isoformat(),
                "accounts": ledger_list,
                "lines": la_lines,
                "cuentas": cuentas,
                "total_debitos": total_debitos,
                "total_creditos": total_creditos,
                "moneda": "COP",
                "source": "derived_from_journal",
            }
            ingest_job = _create_derivation_ingest_job(
                db, normalized_nit, period_end, "libro_auxiliar"
            )
            stmt = db_service.create_financial_statement(
                db,
                ingest_id=ingest_job.id,
                statement_type="libro_auxiliar",
                entity_nit=normalized_nit,
                period_start=period_start,
                period_end=period_end,
                source_mode="derived_from_journal",
                frequency=frequency,
                data=la_data,
                commit=True,
            )
            created["libro_auxiliar"] = stmt.id
        except Exception as exc:
            _log.error(
                "build_first_level: failed to create libro_auxiliar: %s",
                exc,
                exc_info=True,
            )
            build_errors["libro_auxiliar"] = str(exc)
            skipped.append("libro_auxiliar")
    else:
        skipped.append("libro_auxiliar")

    # --- Libro Diario ---
    if not _first_level_type_exists(
        db,
        company_nit=normalized_nit,
        statement_type="libro_diario",
        period_start=period_start,
        period_end=period_end,
    ):
        try:
            journal_lines = db_service.get_journal_entry_lines(
                db,
                company_nit=normalized_nit,
                start_date=period_start,
                end_date=period_end,
            )
            ld_data = {
                "tipo": "libro_diario",
                "entidad": {"nit": normalized_nit},
                "periodo_inicio": period_start.date().isoformat(),
                "periodo_fin": period_end.date().isoformat(),
                "asientos": journal_lines if isinstance(journal_lines, list) else [],
                "moneda": "COP",
                "source": "derived_from_journal",
            }
            ingest_job = _create_derivation_ingest_job(
                db, normalized_nit, period_end, "libro_diario"
            )
            stmt = db_service.create_financial_statement(
                db,
                ingest_id=ingest_job.id,
                statement_type="libro_diario",
                entity_nit=normalized_nit,
                period_start=period_start,
                period_end=period_end,
                source_mode="derived_from_journal",
                frequency=frequency,
                data=ld_data,
                commit=True,
            )
            created["libro_diario"] = stmt.id
        except Exception as exc:
            _log.warning("build_first_level: failed to create libro_diario: %s", exc)
            skipped.append("libro_diario")
    else:
        skipped.append("libro_diario")

    return {
        "status": "built",
        "company_nit": normalized_nit,
        "created": created,
        "skipped": len(skipped),
        "skipped_types": skipped,
        "build_errors": build_errors,
    }


class BusinessRuleError(RuntimeError):
    """Raised when business preconditions for derived statements are not met."""


def list_financial_statements(
    *,
    company_nit: str,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    statement_type: str | None = None,
    source_mode: str | None = None,
    db: Session | None = None,
) -> list[dict[str, Any]]:
    """Internal read API for financial statements filtered by company and period.

    If ``db`` is provided (e.g. FastAPI request-scoped session), reuse it.
    Otherwise open a short-lived session.
    """
    normalized_nit = normalize_nit(company_nit)

    owned_session = db is None
    if owned_session:
        db = SessionLocal()
    try:
        rows = db_service.get_financial_statements(
            db,
            company_nit=normalized_nit,
            statement_type=statement_type,
            period_start=period_start,
            period_end=period_end,
            source_mode=source_mode,
        )
        return [
            {
                "id": row.id,
                "ingest_id": row.ingest_id,
                "statement_type": row.statement_type,
                "period_start": (
                    row.period_start.isoformat() if row.period_start else None
                ),
                "period_end": row.period_end.isoformat() if row.period_end else None,
                "entity_nit": row.entity_nit,
                "source_mode": row.source_mode,
                # Falls back to span-based inference when the column is NULL
                # (legacy rows or LLM extraction blanks). Keeps the FE filters
                # working uniformly across old and new data.
                "frequency": row.frequency
                or infer_frequency(row.period_start, row.period_end),
                "data": row.data,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    finally:
        if owned_session and db is not None:
            db.close()


# ─── Derivation helpers (v3 — full NIC 7 / NIC 1) ─────────────────────────────


def _dec(value: Any) -> Decimal:
    """Best-effort Decimal cast. None / invalid → 0."""
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _load_prior_balance(
    db: Session,
    company_nit: str,
    period_start: datetime,
    *,
    via_a: bool = False,
) -> "FinancialStatement | None":
    """Return the most recent balance_general with period_end < period_start.

    Used to compute working-capital variations and prior cash position required
    for the NIC 7 indirect method. Returning None signals the caller to raise a
    BusinessRuleError so the API surfaces a 409 instead of silently degrading.

    via_a=True  — accepts derived_from_journal (Via A prior periods)
    via_a=False — only direct (Via B uploaded balances), enforcing pathway separation
    """
    from app.models.database import FinancialStatement  # local to avoid cycle

    allowed_modes = ["direct", "derived_from_journal"] if via_a else ["direct"]
    return (
        db.query(FinancialStatement)
        .filter(
            FinancialStatement.entity_nit == company_nit,
            FinancialStatement.statement_type == "balance_general",
            FinancialStatement.source_mode.in_(allowed_modes),
            FinancialStatement.period_end < period_start,
        )
        .order_by(FinancialStatement.period_end.desc())
        .first()
    )


def _load_prior_libro_auxiliar(
    db: Session,
    company_nit: str,
    period_start: datetime,
    *,
    via_a: bool = False,
) -> "FinancialStatement | None":
    """Fallback for ``_load_prior_balance`` when only a LA is on file.

    Per Decreto 2650/1993 + NIC 7, a comprehensive libro auxiliar can stand in
    for a prior balance_general because summing class 1-3 movements yields the
    same closing position. This lets us compute working-capital variations
    even when the user only uploaded LAs for both years.

    via_a=True  — accepts derived_from_journal (Via A prior periods)
    via_a=False — only direct (Via B uploaded ledgers)
    """
    from app.models.database import FinancialStatement

    allowed_modes = ["direct", "derived_from_journal"] if via_a else ["direct"]
    return (
        db.query(FinancialStatement)
        .filter(
            FinancialStatement.entity_nit == company_nit,
            FinancialStatement.statement_type == "libro_auxiliar",
            FinancialStatement.source_mode.in_(allowed_modes),
            FinancialStatement.period_end < period_start,
        )
        .order_by(FinancialStatement.period_end.desc())
        .first()
    )


def _sum_account_balances(
    accounts: list[dict[str, Any]] | None, *prefixes: str
) -> Decimal:
    """Sum the `saldo` of accounts whose cuenta_puc starts with any prefix.

    DEPRECATED for new code — prefer `_sum_leaves` when the accounts list may
    contain hierarchical aggregates (group + sub-account + leaf). This helper
    does NOT dedupe and will double-count multi-level extractions.
    """
    if not isinstance(accounts, list):
        return Decimal("0")
    total = Decimal("0")
    for acc in accounts:
        if not isinstance(acc, dict):
            continue
        code = str(acc.get("cuenta_puc") or acc.get("codigo") or "")
        if any(code.startswith(p) for p in prefixes):
            total += _dec(acc.get("saldo") or acc.get("valor"))
    return total


def _is_valid_puc_code(code: str) -> bool:
    """A PUC code is at most 8 digits (class → group → account → sub-account,
    2 chars each). Anything longer is almost certainly an LLM hallucination
    (typically the saldo itself echoed as the code).
    """
    if not code:
        return False
    return code.isdigit() and 1 <= len(code) <= 8


def _leaf_accounts(accounts: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Return only leaf accounts — those whose cuenta_puc is not a strict prefix
    of any other code in the same list.

    The LLM sometimes emits hierarchical PUC rows (e.g. class ``11``, group
    ``1120``, account ``112005``, sub-account ``11200501``) where the shorter
    codes are aggregates of the longer ones. Summing all rows double-counts.
    This helper keeps only the deepest level per branch so subsequent prefix
    sums are accurate regardless of how the LLM ordered the output.

    Also drops rows whose ``cuenta_puc`` is not a valid PUC code (e.g. when
    the LLM hallucinates a code from a "TOTAL" line and writes the saldo as
    the cuenta_puc — see the 169098236 case observed in production).
    """
    if not isinstance(accounts, list):
        return []
    # First filter: keep only entries whose cuenta_puc looks like a real PUC code.
    valid = [
        a
        for a in accounts
        if isinstance(a, dict) and _is_valid_puc_code(str(a.get("cuenta_puc") or ""))
    ]
    codes = sorted({str(a.get("cuenta_puc") or "") for a in valid})
    aggregates: set[str] = set()
    for code in codes:
        if not code:
            continue
        for other in codes:
            if other != code and other.startswith(code) and len(other) > len(code):
                aggregates.add(code)
                break
    return [a for a in valid if str(a.get("cuenta_puc") or "") not in aggregates]


def _sum_leaves(
    leaves: list[dict[str, Any]],
    *prefixes: str,
    exclude: tuple[str, ...] = (),
) -> Decimal:
    """Sum saldos of leaf accounts whose code matches any prefix and no exclude."""
    total = Decimal("0")
    for acc in leaves:
        code = str(acc.get("cuenta_puc") or acc.get("codigo") or "")
        if not any(code.startswith(p) for p in prefixes):
            continue
        if exclude and any(code.startswith(e) for e in exclude):
            continue
        total += _dec(acc.get("saldo") or acc.get("valor"))
    return total


def _nested(data: dict[str, Any] | None, *path: str) -> Decimal:
    """Walk a nested dict pulling Decimals. Missing keys → 0."""
    cur: Any = data or {}
    for key in path:
        if not isinstance(cur, dict):
            return Decimal("0")
        cur = cur.get(key)
        if cur is None:
            return Decimal("0")
    return _dec(cur)


def _compute_cash_flow_indirect(
    *,
    company_nit: str,
    period_start: datetime,
    period_end: datetime,
    bg_data: dict[str, Any],
    prior_bg_data: dict[str, Any],
    er_data: dict[str, Any],
    la_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """Full NIC 7 indirect method using BG (current + prior) + ER (+ optional LA).

    v4: reads from leaf-level `accounts[]` (deduped via _leaf_accounts) using
    PUC class prefixes. This is robust to the LLM emitting hierarchical rows or
    inconsistent NIIF categorization between the two BGs (corriente vs no
    corriente, bruto vs neto). Falls back to nested keys only when the
    accounts list is empty.

    ``la_data=None`` is supported when the user uploads only BG+ER without a
    libro auxiliar (Path A from the normative analysis). In that case the
    ``dividendos_pagados`` figure is reported as zero with a note — the rest
    of the cash flow is unaffected.
    """

    # ── Deduped leaf accounts (so hierarchical aggregates don't double-count) ──
    leaves_now = _leaf_accounts(bg_data.get("accounts"))
    leaves_prior = _leaf_accounts(prior_bg_data.get("accounts"))
    er_leaves = _leaf_accounts(er_data.get("accounts"))

    def _bg_metric(prefix_args, exclude=(), nested_path=None):
        """Sum leaves by prefix; fallback to nested-key if accounts are empty."""
        prefixes = (
            (prefix_args,) if isinstance(prefix_args, str) else tuple(prefix_args)
        )
        now = _sum_leaves(leaves_now, *prefixes, exclude=exclude)
        prior = _sum_leaves(leaves_prior, *prefixes, exclude=exclude)
        if now == 0 and prior == 0 and nested_path:
            now = _nested(bg_data, *nested_path)
            prior = _nested(prior_bg_data, *nested_path)
        return now, prior

    # ── Starting point: utilidad neta ──────────────────────────────────
    utilidad_neta = _dec(er_data.get("utilidad_neta"))

    # ── Add-back: depreciation (BG diff preferred, ER accounts fallback) ──
    dep_acum_now = _sum_leaves(leaves_now, "159")
    dep_acum_prior = _sum_leaves(leaves_prior, "159")
    if dep_acum_now or dep_acum_prior:
        # 159* saldos are negative (contra-asset). The expense for the period is
        # the absolute change between two snapshots.
        depreciacion_periodo = abs(dep_acum_now - dep_acum_prior)
    else:
        depreciacion_periodo = _sum_leaves(er_leaves, "5160", "5260", "7560")

    # ── Provisions (gasto-side, class 519x) ────────────────────────────
    provisiones = _sum_leaves(er_leaves, "519")

    # ── Working capital variations (PUC class prefixes) ────────────────
    # Assets side: clase 13 (all deudores: clientes, socios, anticipos de
    # impuestos / retenciones recibidas) + clase 14 (inventarios).
    cxc_now, cxc_prior = _bg_metric(
        "13", nested_path=("activos_corrientes", "cuentas_por_cobrar_comerciales")
    )
    inv_now, inv_prior = _bg_metric(
        "14", nested_path=("activos_corrientes", "inventarios")
    )
    # Liabilities side: clase 22 (proveedores) + 23 (cuentas por pagar) +
    # 24 (impuestos por pagar: IVA, retenciones, renta corriente) +
    # 25 (obligaciones laborales) + 26 (provisiones laborales).
    # We sum them together as "operating liabilities" — splitting hairs about
    # commercial vs tax payables is meaningless for the NIC 7 cuadre, what
    # matters is that all operating-classified liability movement is captured.
    op_liab_now, op_liab_prior = _bg_metric(
        ("22", "23", "24", "25", "26"),
        nested_path=None,  # fallback handled below — sum multiple keys
    )
    if op_liab_now == 0 and op_liab_prior == 0:
        for key in (
            "cuentas_por_pagar_comerciales",
            "obligaciones_laborales",
            "impuestos_por_pagar",
            "obligaciones_financieras_cp",
        ):
            op_liab_now += _nested(bg_data, "pasivos_corrientes", key)
            op_liab_prior += _nested(prior_bg_data, "pasivos_corrientes", key)

    delta_cxc = cxc_now - cxc_prior
    delta_inv = inv_now - inv_prior
    delta_op_liab = op_liab_now - op_liab_prior

    # `utilidad_neta` is already after-tax; do not subtract impuesto_renta
    # again. The tax movement (cash actually paid vs accrued) shows up via the
    # `Δ clase 24` term inside `delta_op_liab`.

    flujo_operacion = (
        utilidad_neta
        + depreciacion_periodo
        + provisiones
        - delta_cxc
        - delta_inv
        + delta_op_liab
    )

    # ── Investment: Δ PPE bruto + Δ intangibles + Δ inversiones (class 12) ──
    # PPE bruto = class 15 minus 159 (depreciation already accounted for in
    # the operating add-back).
    ppe_now, ppe_prior = _bg_metric(
        "15",
        exclude=("159",),
        nested_path=("activos_no_corrientes", "propiedades_planta_equipo"),
    )
    intan_now, intan_prior = _bg_metric(
        "16", nested_path=("activos_no_corrientes", "intangibles")
    )
    # Class 12 = Inversiones (covers both corto and largo plazo; the LLM-imposed
    # corriente/no corriente split is too brittle here).
    inv_lp_now, inv_lp_prior = _bg_metric(
        "12", nested_path=("activos_no_corrientes", "inversiones_largo_plazo")
    )
    flujo_inversion = (
        -(ppe_now - ppe_prior) - (intan_now - intan_prior) - (inv_lp_now - inv_lp_prior)
    )

    # ── Financing: Δ obligaciones financieras (class 21, cp+lp together) + Δ capital - dividendos ──
    ob_fin_now = _sum_leaves(leaves_now, "21")
    ob_fin_prior = _sum_leaves(leaves_prior, "21")
    if ob_fin_now == 0 and ob_fin_prior == 0:
        ob_fin_now = _nested(
            bg_data, "pasivos_corrientes", "obligaciones_financieras_cp"
        ) + _nested(bg_data, "pasivos_no_corrientes", "obligaciones_financieras_lp")
        ob_fin_prior = _nested(
            prior_bg_data, "pasivos_corrientes", "obligaciones_financieras_cp"
        ) + _nested(
            prior_bg_data, "pasivos_no_corrientes", "obligaciones_financieras_lp"
        )
    capital_now, capital_prior = _bg_metric(
        "31", nested_path=("patrimonio", "capital_social")
    )

    # Dividendos pagados: LA lines with cuenta_puc startswith "3705" and debito>0.
    # Optional input — when LA wasn't uploaded (Path A), dividends fall through
    # as zero with a note so the user knows the figure is incomplete.
    dividendos = Decimal("0")
    dividendos_nota: str | None = None
    if la_data is None:
        dividendos_nota = (
            "Dividendos pagados no calculados: libro auxiliar no cargado. "
            "El flujo de financiación reportado es aproximado."
        )
    else:
        la_lines = la_data.get("lines") or []
        if isinstance(la_lines, list):
            for line in la_lines:
                if not isinstance(line, dict):
                    continue
                code = str(line.get("cuenta_puc") or "")
                if code.startswith("3705"):
                    dividendos += _dec(line.get("debito"))

    flujo_financiacion = (
        (ob_fin_now - ob_fin_prior) + (capital_now - capital_prior) - dividendos
    )

    # ── Cash positions ────────────────────────────────────────────────
    efectivo_fin = _sum_leaves(leaves_now, "11")
    if efectivo_fin == 0:
        efectivo_fin = _nested(bg_data, "activos_corrientes", "efectivo_equivalentes")
    efectivo_inicio = _sum_leaves(leaves_prior, "11")
    if efectivo_inicio == 0:
        efectivo_inicio = _nested(
            prior_bg_data, "activos_corrientes", "efectivo_equivalentes"
        )

    aumento_neto = flujo_operacion + flujo_inversion + flujo_financiacion
    expected_fin = efectivo_inicio + aumento_neto
    diferencia = efectivo_fin - expected_fin
    tolerance = max(abs(efectivo_fin) * Decimal("0.005"), Decimal("1"))
    verificacion = abs(diferencia) <= tolerance

    return {
        "tipo": "flujo_de_caja",
        "entidad": {"nit": company_nit},
        "periodo_inicio": period_start.date().isoformat(),
        "periodo_fin": period_end.date().isoformat(),
        "metodo": "indirecto",
        "base_presentacion": "NIIF_pymes",
        "moneda": "COP",
        "flujo_neto_operacion": float(flujo_operacion),
        "flujo_neto_inversion": float(flujo_inversion),
        "flujo_neto_financiacion": float(flujo_financiacion),
        "aumento_disminucion_neto": float(aumento_neto),
        "efectivo_inicio_periodo": float(efectivo_inicio),
        "efectivo_fin_periodo": float(efectivo_fin),
        "verificacion": verificacion,
        "informacion_adicional": {
            "derivation_basis": list(_REQUIRED_DERIVATION_INPUTS),
            "rule_version": "v4",
            "source": "leaf_accounts_by_puc_class",
            "adjustments": {
                "utilidad_neta": float(utilidad_neta),
                "depreciacion_periodo": float(depreciacion_periodo),
                "provisiones": float(provisiones),
                "delta_cuentas_por_cobrar": float(delta_cxc),
                "delta_inventarios": float(delta_inv),
                "delta_pasivos_operacionales": float(delta_op_liab),
                "delta_ppe": float(ppe_now - ppe_prior),
                "delta_intangibles": float(intan_now - intan_prior),
                "delta_inversiones": float(inv_lp_now - inv_lp_prior),
                "delta_obligaciones_financieras": float(ob_fin_now - ob_fin_prior),
                "delta_capital_social": float(capital_now - capital_prior),
                "dividendos_pagados": float(dividendos),
                **(
                    {"dividendos_pagados_nota": dividendos_nota}
                    if dividendos_nota
                    else {}
                ),
            },
            "nic7_identity": {
                "expected_fin": float(expected_fin),
                "actual_fin": float(efectivo_fin),
                "diferencia": float(diferencia),
                "tolerance": float(tolerance),
            },
        },
    }


_EQUITY_COMPONENTS: tuple[tuple[str, str, str], ...] = (
    # (label_key,                 puc_prefix, nested_path_in_bg_patrimonio)
    ("capital_social", "31", "capital_social"),
    ("reservas", "33", "reservas"),
    ("resultados_del_ejercicio", "36", "resultados_del_ejercicio"),
    ("resultados_acumulados", "37", "resultados_acumulados"),
    ("otro_resultado_integral", "38", "otro_resultado_integral"),
)


def _compute_equity_changes(
    *,
    company_nit: str,
    period_start: datetime,
    period_end: datetime,
    bg_data: dict[str, Any],
    prior_bg_data: dict[str, Any],
    er_data: dict[str, Any],
    la_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build per-component equity changes (NIC 1 / Sección 6 NIIF Pymes).

    v4: reads saldos from leaf-level `accounts[]` filtered by PUC class prefix
    first, falling back to the nested `patrimonio.<key>` only when the
    accounts list is empty. This avoids the LLM's inconsistent categorization
    between BGs and double-counting of hierarchical levels.

    ``la_data=None`` is supported (Path A from the normative analysis): the
    movement-detail sections fall through empty, the headline initial/final
    balances + utilidad_neta still come from BG + ER.
    """

    la_lines_raw = (la_data or {}).get("lines") or []
    la_lines = la_lines_raw if isinstance(la_lines_raw, list) else []

    leaves_now = _leaf_accounts(bg_data.get("accounts"))
    leaves_prior = _leaf_accounts(prior_bg_data.get("accounts"))

    componentes: list[dict[str, Any]] = []
    utilidad_neta = _dec(er_data.get("utilidad_neta"))

    for label, prefix, nested_key in _EQUITY_COMPONENTS:
        saldo_final = _sum_leaves(leaves_now, prefix)
        saldo_inicial = _sum_leaves(leaves_prior, prefix)
        if saldo_final == 0 and saldo_inicial == 0:
            saldo_final = _nested(bg_data, "patrimonio", nested_key)
            saldo_inicial = _nested(prior_bg_data, "patrimonio", nested_key)

        movimientos: list[dict[str, Any]] = []
        # Utilidad del ejercicio goes directly into resultados_del_ejercicio.
        if label == "resultados_del_ejercicio" and utilidad_neta != 0:
            movimientos.append(
                {"concepto": "utilidad_neta_del_periodo", "valor": float(utilidad_neta)}
            )

        # Pull LA line movements for this component.
        movs_debito = Decimal("0")
        movs_credito = Decimal("0")
        for line in la_lines:
            if not isinstance(line, dict):
                continue
            code = str(line.get("cuenta_puc") or "")
            if code.startswith(prefix):
                movs_debito += _dec(line.get("debito"))
                movs_credito += _dec(line.get("credito"))

        # Net movement: credits add to equity, debits subtract (patrimonio is
        # naturally credit-side).
        net = movs_credito - movs_debito
        if net != 0:
            movimientos.append(
                {
                    "concepto": "movimientos_libro_auxiliar",
                    "valor": float(net),
                    "debitos": float(movs_debito),
                    "creditos": float(movs_credito),
                }
            )

        # Skip the component entirely if both saldos are zero and there are no movements.
        if saldo_final == 0 and saldo_inicial == 0 and not movimientos:
            continue

        componentes.append(
            {
                "concepto_patrimonio": label,
                "saldo_inicial": float(saldo_inicial),
                "movimientos": movimientos,
                "saldo_final": float(saldo_final),
            }
        )

    total_patrimonio_inicio = sum(
        (_dec(c["saldo_inicial"]) for c in componentes), Decimal("0")
    )
    total_patrimonio_fin = sum(
        (_dec(c["saldo_final"]) for c in componentes), Decimal("0")
    )

    # Cross-check against BG.total_patrimonio
    bg_total_patrimonio = _dec(bg_data.get("total_patrimonio"))
    cuadre_patrimonio = abs(total_patrimonio_fin - bg_total_patrimonio) <= max(
        abs(bg_total_patrimonio) * Decimal("0.005"), Decimal("1")
    )

    return {
        "tipo": "cambios_patrimonio",
        "entidad": {"nit": company_nit},
        "periodo_inicio": period_start.date().isoformat(),
        "periodo_fin": period_end.date().isoformat(),
        "moneda": "COP",
        "componentes": componentes,
        "total_patrimonio_inicio": float(total_patrimonio_inicio),
        "total_patrimonio_fin": float(total_patrimonio_fin),
        "informacion_adicional": {
            "rule_version": "v4",
            "source": "leaf_accounts_by_puc_class",
            "bg_total_patrimonio": float(bg_total_patrimonio),
            "cuadre_patrimonio": cuadre_patrimonio,
        },
    }


_NOTE_SPECS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    # (numero, titulo, categoria, prefixes)
    ("4", "Efectivo y equivalentes de efectivo", "activo_corriente", ("11",)),
    (
        "5",
        "Deudores comerciales y otras cuentas por cobrar",
        "activo_corriente",
        ("13",),
    ),
    ("6", "Propiedades, planta y equipo", "activo_no_corriente", ("15", "16", "17")),
    ("7", "Cuentas por pagar comerciales y otras", "pasivo_corriente", ("22", "23")),
    ("8", "Pasivos por impuestos corrientes", "pasivo_corriente", ("24",)),
    ("9", "Obligaciones laborales", "pasivo_corriente", ("25", "26")),
    ("10", "Patrimonio", "patrimonio", ("3",)),
    ("11", "Ingresos operacionales", "ingreso", ("41", "42")),
    ("12", "Gastos operacionales", "gasto", ("51", "52", "53")),
)


def _compute_notes(
    *,
    company_nit: str,
    period_start: datetime,
    period_end: datetime,
    bg_data: dict[str, Any],
    er_data: dict[str, Any],
    la_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a 12-note structure following NIC 1 minimums, skipping notes with no data.

    ``la_data`` is accepted but not currently consumed by the notes derivation
    (kept in the signature for symmetry with flujo and cambios). Passing
    ``None`` is therefore always safe.
    """

    notas: list[dict[str, Any]] = []
    entidad = bg_data.get("entidad") or {}
    marco_normativo = (
        bg_data.get("marco_normativo") or er_data.get("marco_normativo") or "NIIF_pymes"
    )

    # Nota 1: información general
    notas.append(
        {
            "numero_nota": "1",
            "titulo": "Información general de la entidad",
            "categoria": "informacion_general",
            "contenido_resumido": (
                f"NIT: {entidad.get('nit') or company_nit}. "
                f"Razón social: {entidad.get('razon_social') or 'No especificada'}."
            ),
            "cifras_relevantes": [],
        }
    )

    # Nota 2: bases de preparación
    notas.append(
        {
            "numero_nota": "2",
            "titulo": "Bases de preparación de los estados financieros",
            "categoria": "politicas_contables",
            "contenido_resumido": (
                f"Estados financieros preparados bajo {marco_normativo}. "
                "Moneda funcional y de presentación: pesos colombianos (COP). "
                "Período de reporte: "
                f"{period_start.date().isoformat()} a {period_end.date().isoformat()}."
            ),
            "cifras_relevantes": [],
        }
    )

    # Nota 3: políticas contables (template fijo)
    notas.append(
        {
            "numero_nota": "3",
            "titulo": "Políticas contables significativas",
            "categoria": "politicas_contables",
            "contenido_resumido": (
                "Reconocimiento de ingresos por devengo. Inventarios valorados al menor "
                "entre costo y valor neto realizable. PPE registrada al costo menos "
                "depreciación acumulada (método línea recta). Provisiones reconocidas "
                "cuando existe obligación presente derivada de un suceso pasado."
            ),
            "cifras_relevantes": [],
        }
    )

    # Notas 4-12: per-class breakdowns from leaf-level accounts to avoid
    # duplicate rows when the LLM emits hierarchical levels (group + account +
    # sub-account).
    bg_leaves = _leaf_accounts(bg_data.get("accounts"))
    er_leaves = _leaf_accounts(er_data.get("accounts"))

    def _collect(
        leaves: list[dict[str, Any]], prefixes: tuple[str, ...]
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for acc in leaves:
            code = str(acc.get("cuenta_puc") or acc.get("codigo") or "")
            if any(code.startswith(p) for p in prefixes):
                out.append(
                    {
                        "concepto": (
                            f"{code} - {acc.get('nombre') or ''}".strip(" -")
                            if code
                            else acc.get("nombre") or ""
                        ),
                        "valor": float(_dec(acc.get("saldo") or acc.get("valor"))),
                    }
                )
        return out

    for numero, titulo, categoria, prefixes in _NOTE_SPECS:
        # Notes 4-10 come from BG, 11-12 from ER
        source = er_leaves if numero in ("11", "12") else bg_leaves
        cifras = _collect(source, prefixes)
        if not cifras:
            continue
        notas.append(
            {
                "numero_nota": numero,
                "titulo": titulo,
                "categoria": categoria,
                "contenido_resumido": (
                    f"Desglose por cuenta PUC para clases {', '.join(prefixes)}. "
                    f"Total de cuentas reportadas: {len(cifras)}."
                ),
                "cifras_relevantes": cifras,
            }
        )

    return {
        "tipo": "notas_estados_financieros",
        "entidad": {"nit": company_nit},
        "periodo_inicio": period_start.date().isoformat(),
        "periodo_fin": period_end.date().isoformat(),
        "base_presentacion": marco_normativo,
        "moneda_funcional": "COP",
        "hipotesis_negocio_en_marcha": True,
        "notas": notas,
        "informacion_adicional": {
            "derivation_basis": list(_REQUIRED_DERIVATION_INPUTS),
            "rule_version": "v4",
            "source": "leaf_accounts_by_puc_class",
            "notas_count": len(notas),
            "activos": bg_data.get("total_activos"),
            "pasivos": bg_data.get("total_pasivos"),
            "total_patrimonio": bg_data.get("total_patrimonio"),
        },
    }


def derive_financial_statements(
    *,
    company_nit: str,
    period_start: datetime,
    period_end: datetime,
    input_source_mode: str | None = None,
    prior_from_journal: bool = False,
) -> dict[str, Any]:
    """Derive cashflow / equity changes / notes for one company and period.

    Accepts either of two normative input combinations (per Decreto 2650/1993
    + NIC 7):

    * **Path A — BG + ER:** the canonical NIIF set. LA is optional and only
      enriches the equity-changes / cashflow with movement detail.
    * **Path B — comprehensive LA alone:** when the libro auxiliar covers
      PUC classes 1-7, ``synthesize_balance_from_libro_auxiliar`` and
      ``synthesize_income_statement_from_libro_auxiliar`` reconstruct BG
      and ER from movements (Decreto 2649/1993 art. 125+).

    Only **annual** inputs are accepted: monthly closings can't drive NIC 7
    indirect cash flow (which needs working-capital variation across two
    fiscal years). The annual gate refuses mensual / trimestral / custom.

    Args:
        input_source_mode: When set, restricts the BG/ER/LA source lookup to that
            source_mode. Via B passes "direct" (only uploaded statements); Via A
            passes "derived_from_journal" (only journal-built statements). Enforces
            pathway separation: Via B cannot use Via A statements as inputs and
            vice versa.
        prior_from_journal: selects which source_mode the prior-period opening
            balance is loaded from. Via A passes True → the prior is a GENERATED
            first-level close (source_mode='derived_from_journal'); Via B keeps
            False → an uploaded prior balance_general_anterior (source_mode='direct').
            BOTH pathways now REQUIRE the prior period's first-level statement to
            already exist and use it as the opening balance — there is no journal
            recompute. Deriving year Y therefore needs Y-1 generated first
            (order-dependent). The annual gate applies regardless of this flag.

    Raises:
        BusinessRuleError: when neither path is satisfied, when the inputs
            aren't annual, or when no prior period is available for NIC 7
            variation calculation.
    """
    normalized_nit = normalize_nit(company_nit)

    db = SessionLocal()
    try:
        bg_rows = db_service.get_financial_statements(
            db,
            company_nit=normalized_nit,
            statement_type="balance_general",
            period_start=period_start,
            period_end=period_end,
            source_mode=input_source_mode,
        )
        er_rows = db_service.get_financial_statements(
            db,
            company_nit=normalized_nit,
            statement_type="estado_resultados",
            period_start=period_start,
            period_end=period_end,
            source_mode=input_source_mode,
        )
        la_rows = db_service.get_financial_statements(
            db,
            company_nit=normalized_nit,
            statement_type="libro_auxiliar",
            period_start=period_start,
            period_end=period_end,
            source_mode=input_source_mode,
        )

        # ``get_financial_statements`` filters by range and orders by period_end
        # DESC. If a company has both monthly rows and an annual row inside the
        # requested window, prefer the annual one when we later take ``[0]`` so a
        # monthly row can't poison an annual derivation (NIC 7 needs the annual
        # close). Stable sort preserves the period_end ordering within each group.
        bg_rows = sorted(bg_rows, key=lambda r: 0 if is_annual(r) else 1)
        er_rows = sorted(er_rows, key=lambda r: 0 if is_annual(r) else 1)
        la_rows = sorted(la_rows, key=lambda r: 0 if is_annual(r) else 1)

        la_is_comprehensive = bool(la_rows) and _libro_auxiliar_is_comprehensive(
            la_rows[0].data or {}
        )

        # Two acceptable paths — refuse only when neither is satisfied. This
        # follows the normative analysis (NIC 7 § 18 + Decreto 2650/1993).
        path_a_satisfied = bool(bg_rows and er_rows)
        if not path_a_satisfied and not la_is_comprehensive:
            raise BusinessRuleError(
                "Se requiere una de dos combinaciones para derivar:\n"
                "  • Balance General + Estado de Resultados, o\n"
                "  • Libro Auxiliar anual que cubra clases 1-7 del PUC.\n"
                "(NIC 7 método indirecto / Decreto 2650/1993)"
            )

        # Annual gate — applies to BOTH pathways. The NIC 7 indirect method
        # compares two fiscal years, so flujo / cambios / notas only make sense on
        # an annual close. Via B uploads carry an extracted periodicidad; Via A
        # stamps frequency from the period the user chose when generating
        # first-level statements (see build_first_level_from_journal_entries).
        # ``is_annual`` falls back to span inference for legacy NULL-frequency rows.
        inputs_for_check = [r for r in (bg_rows + er_rows + la_rows) if r]
        if not any(is_annual(r) for r in inputs_for_check):
            raise BusinessRuleError(
                "La derivación de flujo, cambios y notas requiere estados ANUALES "
                "(NIC 7). Los estados mensuales sirven para reportes individuales "
                "pero no pueden anclar la derivación NIIF."
            )

        # Dedup guard: skip derived types that already exist
        existing_derived = {
            stmt_type
            for stmt_type in _DERIVED_TARGETS
            if _first_level_type_exists(
                db,
                company_nit=normalized_nit,
                statement_type=stmt_type,
                period_start=period_start,
                period_end=period_end,
            )
        }
        targets_to_create = [t for t in _DERIVED_TARGETS if t not in existing_derived]
        if not targets_to_create:
            return {"status": "already_derived", "skipped": list(existing_derived)}

        # Build BG and ER. When the user uploaded BG+ER directly, use them;
        # otherwise synthesize from the comprehensive LA.
        bg = (
            (bg_rows[0].data or {})
            if bg_rows
            else synthesize_balance_from_libro_auxiliar(la_rows[0].data or {})
        )
        er = (
            (er_rows[0].data or {})
            if er_rows
            else synthesize_income_statement_from_libro_auxiliar(la_rows[0].data or {})
        )
        # LA stays optional. When BG+ER came from uploads and no LA exists,
        # downstream _compute_* helpers handle the None gracefully (see paso 7).
        la = (la_rows[0].data or {}) if la_rows else None

        # NIC 7 indirect method needs a prior-period BG to compute working-capital
        # variations and opening cash. BOTH pathways now require the prior period's
        # first-level statement to already exist — you cannot derive a cash flow
        # from a single period.
        #   Via A: the prior must be a GENERATED first-level close
        #          (source_mode='derived_from_journal'); we use those two balances
        #          rather than recomputing the opening from the raw journal.
        #   Via B: the prior must be an uploaded BG (or LA) — balance_general_anterior.
        prior_bg_row = _load_prior_balance(
            db, normalized_nit, period_start, via_a=prior_from_journal
        )
        prior_la_row = (
            None
            if prior_bg_row
            else _load_prior_libro_auxiliar(
                db, normalized_nit, period_start, via_a=prior_from_journal
            )
        )
        if prior_bg_row is None and prior_la_row is None:
            if prior_from_journal:
                raise BusinessRuleError(
                    "Para derivar el flujo de caja (NIC 7) necesitas el cierre del "
                    "período anterior. Genera primero los estados de primer nivel del "
                    "período anterior y vuelve a intentar."
                )
            raise BusinessRuleError(
                "Para derivar flujo de caja según NIC 7 método indirecto se requiere "
                "el balance general (o libro auxiliar anual) del período anterior. "
                "Sube el cierre del año previo para esta empresa y vuelve a intentar."
            )
        prior_bg = (
            (prior_bg_row.data or {})
            if prior_bg_row
            else synthesize_balance_from_libro_auxiliar(prior_la_row.data or {})
        )

        # Capture which source rows actually back this derivation, so lineage
        # links only point to documents that exist. Synthesized BG/ER don't
        # have their own row — their lineage is the LA they came from.
        source_rows: dict[str, "FinancialStatement"] = {}
        if bg_rows:
            source_rows["balance_general"] = bg_rows[0]
        if er_rows:
            source_rows["estado_resultados"] = er_rows[0]
        if la_rows:
            source_rows["libro_auxiliar"] = la_rows[0]
        if prior_bg_row is not None:
            source_rows["balance_general_anterior"] = prior_bg_row
        elif prior_la_row is not None:
            source_rows["libro_auxiliar_anterior"] = prior_la_row

        flujo_data = _compute_cash_flow_indirect(
            company_nit=normalized_nit,
            period_start=period_start,
            period_end=period_end,
            bg_data=bg,
            prior_bg_data=prior_bg,
            er_data=er,
            la_data=la,
        )
        cambios_data = _compute_equity_changes(
            company_nit=normalized_nit,
            period_start=period_start,
            period_end=period_end,
            bg_data=bg,
            prior_bg_data=prior_bg,
            er_data=er,
            la_data=la,
        )
        notas_data = _compute_notes(
            company_nit=normalized_nit,
            period_start=period_start,
            period_end=period_end,
            bg_data=bg,
            er_data=er,
            la_data=la,
        )

        ingest_job = db_service.create_ingest_job(
            db,
            file_name=f"derived_{normalized_nit}_{period_end.date().isoformat()}",
            file_path="internal://derived_financial_statements",
            commit=False,
        )
        ingest_job.status = IngestStatus.COMPLETED
        ingest_job.document_type = "derived_financial_statements"
        ingest_job.pathway = "work_with_existing"

        all_payloads = {
            "flujo_de_caja": flujo_data,
            "cambios_patrimonio": cambios_data,
            "notas_estados_financieros": notas_data,
        }
        created_rows = {}
        for statement_type, payload in [
            (t, all_payloads[t]) for t in targets_to_create
        ]:
            created_rows[statement_type] = db_service.create_financial_statement(
                db,
                ingest_id=ingest_job.id,
                statement_type=statement_type,
                entity_nit=normalized_nit,
                period_start=period_start,
                period_end=period_end,
                source_mode="derived",
                # Annual gate (paso 6) ensures inputs are annual, so derived
                # rows inherit ``annual`` too — keeps reports filters honest.
                frequency=infer_frequency(period_start, period_end),
                data=payload,
                commit=False,
            )

        for target_type in targets_to_create:
            target = created_rows[target_type]
            # Lineage links only against sources that actually exist — when BG
            # was synthesized from LA, lineage points to the LA, not to a phantom
            # BG row.
            for source in source_rows.values():
                db_service.create_financial_statement_lineage(
                    db,
                    target_statement_id=target.id,
                    source_statement_id=source.id,
                    relation_type="input",
                    commit=False,
                )

        db.commit()

        return {
            "status": "derived",
            "company_nit": normalized_nit,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "created_statement_ids": {k: v.id for k, v in created_rows.items()},
            "source_statement_ids": {k: v.id for k, v in source_rows.items()},
            "derivation_path": (
                "BG+ER" if (bg_rows and er_rows) else "LA_comprehensive"
            ),
            "derived_count": len(created_rows),
            "lineage_links": len(created_rows) * len(source_rows),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
