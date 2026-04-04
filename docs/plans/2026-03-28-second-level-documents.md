# Second-Level Financial Documents Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Generate and persist all 7 financial documents (4 first-level from JournalEntryLines + 3 derived second-level) automatically at the end of the Via A pipeline, and trigger auto-derivation after Via B uploads complete.

**Architecture:** After `db_persist` marks `ProcessJob → COMPLETED` (process mode), synchronously call `build_first_level_from_journal_entries()` then `derive_financial_statements()`. For Via B, after each `_persist_financial_statement()`, check if all 3 source docs exist and call `derive_financial_statements()` if so. Expose stored statements via new API endpoints. Update `simulate_frontend_full_pipeline.py` to validate both pathways.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, LangGraph, Supabase/pgvector. Package manager: `uv`. Tests: `pytest`.

---

## Task 1: Add DB helpers to `db_service.py`

**Files:**
- Modify: `app/services/db_service.py`
- Test: `tests/test_db_service_statements.py`

**Context:** We need two helpers: one to check if specific statement types already exist for a company+period (for dedup), and one to get the JournalEntryLine date range for a company (to infer the period for Via A first-level statements).

**Step 1: Write failing tests**

Create `tests/test_db_service_statements.py`:
```python
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

def test_financial_statements_exist_returns_true_when_present():
    from app.services import db_service
    db = MagicMock()
    mock_query = db.query.return_value.filter.return_value.filter.return_value.filter.return_value
    mock_query.count.return_value = 3
    result = db_service.financial_statements_exist(
        db,
        company_nit="800999888",
        period_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        period_end=datetime(2024, 12, 31, tzinfo=timezone.utc),
        types=["balance_general", "estado_resultados", "libro_auxiliar"],
    )
    assert result is True

def test_financial_statements_exist_returns_false_when_missing():
    from app.services import db_service
    db = MagicMock()
    mock_query = db.query.return_value.filter.return_value.filter.return_value.filter.return_value
    mock_query.count.return_value = 1  # Only 1 of 3 present
    result = db_service.financial_statements_exist(
        db,
        company_nit="800999888",
        period_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        period_end=datetime(2024, 12, 31, tzinfo=timezone.utc),
        types=["balance_general", "estado_resultados", "libro_auxiliar"],
    )
    assert result is False

def test_get_journal_entry_period_returns_none_when_no_entries():
    from app.services import db_service
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    result = db_service.get_journal_entry_period(db, company_nit="800999888")
    assert result is None
```

**Step 2: Run tests to verify they fail**

```bash
cd d:/Code/Github/backend_pae_account_multiagent_system
uv run pytest tests/test_db_service_statements.py -v
```
Expected: `ImportError` or `AttributeError` — functions don't exist yet.

**Step 3: Implement in `db_service.py`**

Find the end of the file (around line 1015) and add before the last blank line:

```python
def financial_statements_exist(
    db: Session,
    *,
    company_nit: str,
    period_start: datetime,
    period_end: datetime,
    types: list[str],
) -> bool:
    """Return True if all requested statement types exist for this company and period window."""
    from app.models.database import FinancialStatement
    count = (
        db.query(FinancialStatement)
        .filter(FinancialStatement.entity_nit == company_nit)
        .filter(FinancialStatement.period_end >= period_start)
        .filter(FinancialStatement.period_end <= period_end + timedelta(days=1))
        .filter(FinancialStatement.statement_type.in_(types))
        .count()
    )
    return count >= len(types)


def get_journal_entry_period(
    db: Session,
    *,
    company_nit: str,
) -> tuple[datetime, datetime] | None:
    """Return (min_fecha, max_fecha) from JournalEntryLine for the company, or None."""
    from app.models.database import JournalEntryLine
    from sqlalchemy import func as sqlfunc

    row = (
        db.query(
            sqlfunc.min(JournalEntryLine.fecha).label("min_fecha"),
            sqlfunc.max(JournalEntryLine.fecha).label("max_fecha"),
        )
        .filter(JournalEntryLine.company_nit == company_nit)
        .first()
    )
    if row is None or row.min_fecha is None:
        return None
    return (row.min_fecha, row.max_fecha)
```

Check imports at top of `db_service.py` — add `timedelta` to the `datetime` import if missing:
```python
from datetime import datetime, timedelta
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_db_service_statements.py -v
```
Expected: 3 PASS.

**Step 5: Commit**

```bash
git add app/services/db_service.py tests/test_db_service_statements.py
git commit -m "feat: add financial_statements_exist and get_journal_entry_period helpers"
```

---

## Task 2: Add `build_first_level_from_journal_entries()` to `financial_statement_service.py`

**Files:**
- Modify: `app/services/financial_statement_service.py`
- Test: `tests/test_financial_statement_service.py`

**Context:** This function reads the `JournalEntryLine` aggregates that `db_service.get_balance_sheet()` and `db_service.get_general_ledger()` already compute, formats them as `FinancialStatement` records, and persists all 4 first-level docs (balance_general, estado_resultados, libro_auxiliar, libro_diario). It is idempotent: skips types that already exist.

**Step 1: Write failing test**

Create `tests/test_financial_statement_service.py`:
```python
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

def test_build_first_level_skips_existing_types():
    """If statements already exist for company+period, they should not be re-created."""
    from app.services import financial_statement_service as fss
    db = MagicMock()

    with patch.object(fss, "_first_level_type_exists", return_value=True) as mock_exists, \
         patch.object(fss.db_service, "create_financial_statement") as mock_create:
        result = fss.build_first_level_from_journal_entries(
            db,
            company_nit="800999888",
            period_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            period_end=datetime(2024, 12, 31, tzinfo=timezone.utc),
        )
    mock_create.assert_not_called()
    assert result["skipped"] == 4
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_financial_statement_service.py::test_build_first_level_skips_existing_types -v
```
Expected: `AttributeError` — function doesn't exist.

**Step 3: Implement in `financial_statement_service.py`**

Add after line 16 (after `_DERIVED_TARGETS = ...`):

```python
_FIRST_LEVEL_TYPES = ("balance_general", "estado_resultados", "libro_auxiliar", "libro_diario")


def _first_level_type_exists(
    db,
    *,
    company_nit: str,
    statement_type: str,
    period_start: datetime | None,
    period_end: datetime | None,
) -> bool:
    """Check if a first-level statement of this type already exists for company+period."""
    rows = db_service.get_financial_statements(
        db,
        company_nit=company_nit,
        statement_type=statement_type,
        period_start=period_start,
        period_end=period_end,
    )
    return len(rows) > 0


def build_first_level_from_journal_entries(
    db,
    *,
    company_nit: str,
    period_start: datetime,
    period_end: datetime,
) -> dict[str, Any]:
    """Build and persist first-level FinancialStatement records from JournalEntryLines.

    Reads balance sheet, PnL, general ledger, and libro diario data from the
    JournalEntryLine table (already populated by the accounting pipeline), then
    persists four FinancialStatement records with source_mode='derived_from_journal'.

    Idempotent: skips any type that already has a record for company+period.
    """
    normalized_nit = normalize_nit(company_nit)
    created: dict[str, str] = {}
    skipped: list[str] = []

    # --- Balance General ---
    if not _first_level_type_exists(db, company_nit=normalized_nit, statement_type="balance_general",
                                    period_start=period_start, period_end=period_end):
        bg_raw = db_service.get_balance_sheet(db, company_nit=normalized_nit,
                                              start_date=period_start, end_date=period_end)
        bg_data = {
            "tipo": "balance_general",
            "entidad": {"nit": normalized_nit},
            "periodo_inicio": period_start.date().isoformat(),
            "periodo_fin": period_end.date().isoformat(),
            "total_activos": bg_raw.get("total_activos", 0),
            "total_pasivos": bg_raw.get("total_pasivos", 0),
            "total_patrimonio": bg_raw.get("total_patrimonio", 0),
            "activos": bg_raw.get("activos", []),
            "pasivos": bg_raw.get("pasivos", []),
            "patrimonio": bg_raw.get("patrimonio", []),
            "moneda": "COP",
            "source": "derived_from_journal",
        }
        ingest_job = _create_derivation_ingest_job(db, normalized_nit, period_end, "balance_general")
        stmt = db_service.create_financial_statement(
            db, ingest_id=ingest_job.id, statement_type="balance_general",
            entity_nit=normalized_nit, period_start=period_start, period_end=period_end,
            source_mode="derived_from_journal", data=bg_data, commit=True,
        )
        created["balance_general"] = stmt.id
    else:
        skipped.append("balance_general")

    # --- Estado de Resultados ---
    if not _first_level_type_exists(db, company_nit=normalized_nit, statement_type="estado_resultados",
                                    period_start=period_start, period_end=period_end):
        er_raw = db_service.get_pnl(db, company_nit=normalized_nit,
                                    start_date=period_start, end_date=period_end)
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
        }
        ingest_job = _create_derivation_ingest_job(db, normalized_nit, period_end, "estado_resultados")
        stmt = db_service.create_financial_statement(
            db, ingest_id=ingest_job.id, statement_type="estado_resultados",
            entity_nit=normalized_nit, period_start=period_start, period_end=period_end,
            source_mode="derived_from_journal", data=er_data, commit=True,
        )
        created["estado_resultados"] = stmt.id
    else:
        skipped.append("estado_resultados")

    # --- Libro Auxiliar ---
    if not _first_level_type_exists(db, company_nit=normalized_nit, statement_type="libro_auxiliar",
                                    period_start=period_start, period_end=period_end):
        ledger = db_service.get_general_ledger(db, company_nit=normalized_nit,
                                               start_date=period_start, end_date=period_end)
        la_data = {
            "tipo": "libro_auxiliar",
            "entidad": {"nit": normalized_nit},
            "periodo_inicio": period_start.date().isoformat(),
            "periodo_fin": period_end.date().isoformat(),
            "accounts": ledger if isinstance(ledger, list) else ledger.get("entries", []),
            "moneda": "COP",
            "source": "derived_from_journal",
        }
        ingest_job = _create_derivation_ingest_job(db, normalized_nit, period_end, "libro_auxiliar")
        stmt = db_service.create_financial_statement(
            db, ingest_id=ingest_job.id, statement_type="libro_auxiliar",
            entity_nit=normalized_nit, period_start=period_start, period_end=period_end,
            source_mode="derived_from_journal", data=la_data, commit=True,
        )
        created["libro_auxiliar"] = stmt.id
    else:
        skipped.append("libro_auxiliar")

    # --- Libro Diario ---
    if not _first_level_type_exists(db, company_nit=normalized_nit, statement_type="libro_diario",
                                    period_start=period_start, period_end=period_end):
        journal_lines = db_service.get_journal_entry_lines(
            db, company_nit=normalized_nit, start_date=period_start, end_date=period_end
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
        ingest_job = _create_derivation_ingest_job(db, normalized_nit, period_end, "libro_diario")
        stmt = db_service.create_financial_statement(
            db, ingest_id=ingest_job.id, statement_type="libro_diario",
            entity_nit=normalized_nit, period_start=period_start, period_end=period_end,
            source_mode="derived_from_journal", data=ld_data, commit=True,
        )
        created["libro_diario"] = stmt.id
    else:
        skipped.append("libro_diario")

    return {
        "status": "built",
        "company_nit": normalized_nit,
        "created": created,
        "skipped": len(skipped),
        "skipped_types": skipped,
    }


def _create_derivation_ingest_job(db, company_nit: str, period_end: datetime, doc_type: str):
    """Create a synthetic IngestJob to satisfy the FK constraint on FinancialStatement."""
    ingest_job = db_service.create_ingest_job(
        db,
        file_name=f"journal_derived_{company_nit}_{period_end.date().isoformat()}_{doc_type}",
        file_path="internal://journal_derived",
        commit=False,
    )
    ingest_job.status = IngestStatus.COMPLETED
    ingest_job.document_type = doc_type
    ingest_job.pathway = "build_from_scratch"
    return ingest_job
```

**Important:** Before writing, check `db_service.py` for the exact signatures of:
- `get_balance_sheet(db, ...)` — note parameter names (may use `start_date`/`end_date` or `period_start`/`period_end`)
- `get_pnl(db, ...)` — same
- `get_general_ledger(db, ...)` — same
- `get_journal_entry_lines(db, ...)` — if this function doesn't exist, use `get_general_ledger` with raw=True or query JournalEntryLine directly

If `get_journal_entry_lines` doesn't exist in `db_service.py`, add it (see Task 1 pattern):
```python
def get_journal_entry_lines(db, *, company_nit, start_date=None, end_date=None):
    from app.models.database import JournalEntryLine
    q = db.query(JournalEntryLine).filter(JournalEntryLine.company_nit == company_nit)
    if start_date:
        q = q.filter(JournalEntryLine.fecha >= start_date)
    if end_date:
        q = q.filter(JournalEntryLine.fecha <= end_date)
    rows = q.order_by(JournalEntryLine.fecha).all()
    return [
        {
            "fecha": r.fecha.isoformat() if r.fecha else None,
            "comprobante": r.comprobante,
            "cuenta_puc": r.cuenta_puc,
            "tercero_nit": r.tercero_nit,
            "descripcion": r.descripcion,
            "debito": str(r.debito),
            "credito": str(r.credito),
        }
        for r in rows
    ]
```

**Step 4: Add dedup guard to `derive_financial_statements()`**

In `financial_statement_service.py`, at line 193 (before the loop that creates derived statements), add:
```python
        # Dedup guard: skip derived types that already exist
        existing_derived = {
            stmt_type
            for stmt_type in _DERIVED_TARGETS
            if _first_level_type_exists(db, company_nit=normalized_nit,
                                        statement_type=stmt_type,
                                        period_start=period_start,
                                        period_end=period_end)
        }
        targets_to_create = [t for t in _DERIVED_TARGETS if t not in existing_derived]
        if not targets_to_create:
            db.close()
            return {"status": "already_derived", "skipped": list(existing_derived)}
```
And update the loop on line 193 to iterate `targets_to_create` instead of hardcoded list.

**Step 5: Run tests**

```bash
uv run pytest tests/test_financial_statement_service.py -v
```
Expected: PASS.

**Step 6: Commit**

```bash
git add app/services/financial_statement_service.py tests/test_financial_statement_service.py
git commit -m "feat: add build_first_level_from_journal_entries and derive dedup guard"
```

---

## Task 3: Trigger derivation at end of Via A process in `persist_node.py`

**Files:**
- Modify: `app/agents/persist_node.py`

**Context:** After `ProcessJob → COMPLETED` (line 604), synchronously build first-level statements from JournalEntryLines and derive the 3 second-level documents. This is non-fatal: if derivation fails, the process is still marked complete.

**Step 1: Add imports at top of `persist_node.py`**

Find the existing imports section. Add:
```python
from app.services.financial_statement_service import (
    build_first_level_from_journal_entries,
    derive_financial_statements,
    BusinessRuleError,
)
from app.services.db_service import get_journal_entry_period
```

**Step 2: Add helper to get company NIT and period for process mode**

Before the `_run_persist` function, add:
```python
def _derive_all_statements_for_company(db, company_nit: str) -> None:
    """Build first-level statements from JournalEntryLines and derive second-level.

    Non-fatal: logs warnings but never raises so the process pipeline stays COMPLETED.
    """
    import logging
    logger_local = logging.getLogger(__name__)

    period = get_journal_entry_period(db, company_nit=company_nit)
    if period is None:
        logger_local.warning(
            "[persist] No JournalEntryLines found for %s — skipping statement derivation",
            company_nit,
        )
        return

    period_start, period_end = period
    logger_local.info(
        "[persist] Building first-level statements for %s (%s → %s)",
        company_nit, period_start.date(), period_end.date(),
    )

    try:
        build_first_level_from_journal_entries(
            db,
            company_nit=company_nit,
            period_start=period_start,
            period_end=period_end,
        )
    except Exception as exc:
        logger_local.warning("[persist] build_first_level failed (non-fatal): %s", exc)
        return

    try:
        derive_financial_statements(
            company_nit=company_nit,
            period_start=period_start,
            period_end=period_end,
        )
    except BusinessRuleError as exc:
        logger_local.warning("[persist] derive skipped (missing inputs): %s", exc)
    except Exception as exc:
        logger_local.warning("[persist] derive failed (non-fatal): %s", exc)
```

**Step 3: Call `_derive_all_statements_for_company` after ProcessJob COMPLETED**

In `_run_persist()`, locate the block (around line 604):
```python
                )

        state["db_result"] = {
```

Insert the derivation call **between** the `update_process_job(... COMPLETED ...)` block and `state["db_result"] = {`:
```python
        # After marking process complete, derive all financial statements (non-fatal)
        if mode == "process" and company_nit:
            _derive_all_statements_for_company(db, company_nit)
```

**Step 4: Run existing tests to ensure nothing breaks**

```bash
uv run pytest tests/ -v -x
```
Expected: all tests that were passing before still pass.

**Step 5: Commit**

```bash
git add app/agents/persist_node.py
git commit -m "feat: auto-derive financial statements at end of Via A process pipeline"
```

---

## Task 4: Trigger auto-derivation after Via B upload in `persist_node.py`

**Files:**
- Modify: `app/agents/persist_node.py`

**Context:** After `_persist_financial_statement()` stores a Via B first-level document (line 784 `db.commit()`), check if all 3 source types (balance_general, estado_resultados, libro_auxiliar) are now present for the same company+period. If yes, call `derive_financial_statements()`.

**Step 1: Add import for `financial_statements_exist`**

In the imports added in Task 3, also add:
```python
from app.services.db_service import get_journal_entry_period, financial_statements_exist
```

**Step 2: Add check after `db.commit()` in `_persist_financial_statement()`**

After line 784 (`db.commit()`), before the `state["db_result"] = {` block, insert:
```python
        # Via B auto-derivation: if all 3 source statements are now present, derive second-level
        if doc_type in ("balance_general", "estado_resultados", "libro_auxiliar") and company_nit:
            _try_via_b_auto_derive(db, company_nit=company_nit,
                                   period_start=period_start, period_end=period_end)
```

**Step 3: Add `_try_via_b_auto_derive` helper**

Add near `_derive_all_statements_for_company`:
```python
def _try_via_b_auto_derive(db, *, company_nit: str, period_start, period_end) -> None:
    """After a Via B upload, check if all 3 source docs are present and derive if so."""
    import logging
    logger_local = logging.getLogger(__name__)

    if period_start is None or period_end is None:
        return

    required = ["balance_general", "estado_resultados", "libro_auxiliar"]
    if not financial_statements_exist(
        db,
        company_nit=company_nit,
        period_start=period_start,
        period_end=period_end,
        types=required,
    ):
        logger_local.info(
            "[persist] Via B: not all 3 source docs present yet for %s — skipping auto-derive",
            company_nit,
        )
        return

    logger_local.info(
        "[persist] Via B: all 3 source docs present for %s — triggering derivation", company_nit
    )
    try:
        derive_financial_statements(
            company_nit=company_nit,
            period_start=period_start,
            period_end=period_end,
        )
    except BusinessRuleError as exc:
        logger_local.warning("[persist] Via B derive skipped: %s", exc)
    except Exception as exc:
        logger_local.warning("[persist] Via B derive failed (non-fatal): %s", exc)
```

**Step 4: Run tests**

```bash
uv run pytest tests/ -v -x
```

**Step 5: Commit**

```bash
git add app/agents/persist_node.py
git commit -m "feat: auto-derive second-level docs after Via B first-level uploads complete"
```

---

## Task 5: Add `GET /reports/statements` endpoints to `reports.py`

**Files:**
- Modify: `app/api/v1/reports.py`
- Test: `tests/test_reports_statements.py`

**Context:** Expose stored `FinancialStatement` records via two REST endpoints so the frontend and simulate script can retrieve all generated documents. Reuse the existing `list_financial_statements()` from `financial_statement_service.py`.

**Step 1: Write failing tests**

Create `tests/test_reports_statements.py`:
```python
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

def test_get_statements_returns_list(client: TestClient):
    with patch("app.api.v1.reports.list_financial_statements", return_value=[
        {"id": "stmt-1", "statement_type": "balance_general", "source_mode": "derived_from_journal",
         "period_start": "2024-01-01T00:00:00+00:00", "period_end": "2024-12-31T00:00:00+00:00",
         "entity_nit": "800999888", "data": {}, "ingest_id": "ingest-1", "created_at": "2024-01-01T00:00:00+00:00"},
    ]) as mock_list:
        response = client.get("/api/v1/reports/statements?company_nit=800999888-2")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert data[0]["statement_type"] == "balance_general"

def test_get_statements_filters_by_type(client: TestClient):
    with patch("app.api.v1.reports.list_financial_statements", return_value=[]) as mock_list:
        response = client.get("/api/v1/reports/statements?company_nit=800999888-2&statement_type=flujo_de_caja")
    assert response.status_code == 200
    mock_list.assert_called_once()
    call_kwargs = mock_list.call_args.kwargs
    assert call_kwargs["statement_type"] == "flujo_de_caja"
```

**Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_reports_statements.py -v
```
Expected: FAIL — endpoints don't exist yet.

**Step 3: Add endpoints to `reports.py`**

At the top, add import:
```python
from app.services.financial_statement_service import list_financial_statements
```

At the bottom of the file (after the cashflow endpoint), add:
```python
@router.get("/statements")
async def get_financial_statements(
    company_nit: str = Query(..., description="Company NIT"),
    statement_type: Optional[str] = Query(None, description="Filter by type (e.g. flujo_de_caja)"),
    start_date: Optional[date] = Query(None, description="Period start YYYY-MM-DD"),
    end_date: Optional[date] = Query(None, description="Period end YYYY-MM-DD"),
    source_mode: Optional[str] = Query(None, description="Filter: direct | derived | derived_from_journal"),
):
    """List stored FinancialStatement records for a company."""
    try:
        normalized_nit = normalize_nit(company_nit)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"Invalid company_nit: {e}")

    from datetime import datetime, timezone
    period_start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc) if start_date else None
    period_end = datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59, tzinfo=timezone.utc) if end_date else None

    statements = list_financial_statements(
        company_nit=normalized_nit,
        period_start=period_start,
        period_end=period_end,
        statement_type=statement_type,
        source_mode=source_mode,
    )
    return statements


@router.get("/statements/{statement_id}")
async def get_financial_statement_by_id(statement_id: str):
    """Get a specific FinancialStatement by ID."""
    statements = list_financial_statements(company_nit="")  # will need to search by ID
    # Find by ID (list_financial_statements doesn't filter by ID yet — use db directly)
    from app.core.database import SessionLocal
    from app.services import db_service
    db = SessionLocal()
    try:
        from app.models.database import FinancialStatement
        stmt = db.query(FinancialStatement).filter(FinancialStatement.id == statement_id).first()
        if stmt is None:
            raise HTTPException(status_code=404, detail=f"Statement {statement_id} not found")
        return {
            "id": stmt.id,
            "ingest_id": stmt.ingest_id,
            "statement_type": stmt.statement_type,
            "period_start": stmt.period_start.isoformat() if stmt.period_start else None,
            "period_end": stmt.period_end.isoformat() if stmt.period_end else None,
            "entity_nit": stmt.entity_nit,
            "source_mode": stmt.source_mode,
            "data": stmt.data,
            "created_at": stmt.created_at.isoformat() if stmt.created_at else None,
        }
    finally:
        db.close()
```

**Step 4: Run tests**

```bash
uv run pytest tests/test_reports_statements.py -v
```
Expected: PASS.

Also smoke-test with the server running:
```bash
curl "http://127.0.0.1:8016/api/v1/reports/statements?company_nit=800999888-2"
```

**Step 5: Commit**

```bash
git add app/api/v1/reports.py tests/test_reports_statements.py
git commit -m "feat: add GET /reports/statements and /reports/statements/{id} endpoints"
```

---

## Task 6: Update `simulate_frontend_full_pipeline.py` — Via A second-level doc display

**Files:**
- Modify: `simulate_frontend_full_pipeline.py`

**Context:** After Via A process completes and the first 3 reports are fetched, call `GET /reports/statements` and display all stored financial statements (should be 7: 4 first-level + 3 derived).

**Step 1: Add helper `fetch_all_statements()` to the simulate script**

Find the `fetch_and_print_reports` function. After the existing report fetch calls, add:

```python
def fetch_all_statements(base_url: str, company_nit: str, timeout: float = 60.0) -> list:
    """Fetch all stored FinancialStatements for the company."""
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = requests.get(
                f"{base_url}/api/v1/reports/statements",
                params={"company_nit": company_nit},
                timeout=30,
            )
            if resp.status_code == 200:
                stmts = resp.json()
                # Wait until at least the 3 derived second-level docs appear
                second_level = [s for s in stmts if s["statement_type"] in
                                ("flujo_de_caja", "cambios_patrimonio", "notas_estados_financieros")]
                if len(second_level) >= 3:
                    return stmts
        except Exception:
            pass
        time.sleep(2)
    # Return whatever we have even if not complete
    resp = requests.get(f"{base_url}/api/v1/reports/statements",
                        params={"company_nit": company_nit}, timeout=30)
    return resp.json() if resp.status_code == 200 else []
```

**Step 2: Call `fetch_all_statements()` in the main reporting section**

Find the section that calls `fetch_and_print_reports(...)` in the `main()` function (around lines 546-585). After it, add:

```python
    # Fetch and display all 7 financial statements (first-level + second-level)
    print("\n" + "="*60)
    print("SECOND-LEVEL FINANCIAL DOCUMENTS")
    print("="*60)
    all_stmts = fetch_all_statements(args.base_url, company_nit, timeout=60)
    print(f"Total stored statements: {len(all_stmts)}")
    for stmt in sorted(all_stmts, key=lambda s: s["statement_type"]):
        print(f"  [{stmt['source_mode']:25s}] {stmt['statement_type']}")
    second_level_types = {"flujo_de_caja", "cambios_patrimonio", "notas_estados_financieros"}
    found_second = {s["statement_type"] for s in all_stmts} & second_level_types
    if len(found_second) == 3:
        print("\n✅ All 3 second-level documents generated successfully")
    else:
        missing = second_level_types - found_second
        print(f"\n⚠️  Missing second-level documents: {missing}")
```

**Step 3: Run Via A simulation and verify output**

```bash
uvicorn main:app --reload --port 8016  # in a separate terminal

uv run python simulate_frontend_full_pipeline.py \
  --base-url http://127.0.0.1:8016 --source-mode demo \
  --company-nit 800999888-2 --city Bogota --ciiu 6920 \
  --timeout-seconds 240 --poll-seconds 2 --report-timeout-seconds 420
```

Expected output includes:
```
SECOND-LEVEL FINANCIAL DOCUMENTS
============================
Total stored statements: 7
  [derived               ] cambios_patrimonio
  [derived               ] flujo_de_caja
  ...
✅ All 3 second-level documents generated successfully
```

**Step 4: Commit**

```bash
git add simulate_frontend_full_pipeline.py
git commit -m "feat: display second-level documents after Via A pipeline completes"
```

---

## Task 7: Add `--source-mode via-b` to `simulate_frontend_full_pipeline.py`

**Files:**
- Modify: `simulate_frontend_full_pipeline.py`

**Context:** Add a new mode that generates 3 synthetic first-level PDFs (balance_general, estado_resultados, libro_auxiliar), uploads them, waits for auto-derivation, and displays all 7 statements.

**Step 1: Add PDF generators for first-level documents**

Find the `build_demo_documents()` function (around line 108). Add a new function:

```python
def build_via_b_documents(output_dir: str) -> list[dict]:
    """Generate synthetic first-level financial statement PDFs for Via B testing."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    import os

    os.makedirs(output_dir, exist_ok=True)
    docs = []

    # Balance General
    bg_path = os.path.join(output_dir, "balance_general_2024.pdf")
    c = canvas.Canvas(bg_path, pagesize=letter)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, 720, "BALANCE GENERAL")
    c.setFont("Helvetica", 12)
    c.drawString(72, 690, "Empresa: Test S.A.S. | NIT: 800999888-2")
    c.drawString(72, 670, "Período: 01/01/2024 - 31/12/2024")
    c.drawString(72, 640, "ACTIVOS")
    c.drawString(100, 620, "Activos Corrientes: $150,000,000")
    c.drawString(100, 600, "Activos No Corrientes: $80,000,000")
    c.drawString(72, 580, "TOTAL ACTIVOS: $230,000,000")
    c.drawString(72, 550, "PASIVOS")
    c.drawString(100, 530, "Pasivos Corrientes: $60,000,000")
    c.drawString(72, 510, "TOTAL PASIVOS: $60,000,000")
    c.drawString(72, 480, "PATRIMONIO")
    c.drawString(100, 460, "Capital Social: $120,000,000")
    c.drawString(100, 440, "Utilidad del Ejercicio: $50,000,000")
    c.drawString(72, 420, "TOTAL PATRIMONIO: $170,000,000")
    c.save()
    docs.append({"path": bg_path, "type": "balance_general"})

    # Estado de Resultados
    er_path = os.path.join(output_dir, "estado_resultados_2024.pdf")
    c = canvas.Canvas(er_path, pagesize=letter)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, 720, "ESTADO DE RESULTADOS")
    c.setFont("Helvetica", 12)
    c.drawString(72, 690, "Empresa: Test S.A.S. | NIT: 800999888-2")
    c.drawString(72, 670, "Período: 01/01/2024 - 31/12/2024")
    c.drawString(72, 640, "INGRESOS OPERACIONALES: $200,000,000")
    c.drawString(72, 610, "COSTO DE VENTAS: $80,000,000")
    c.drawString(72, 580, "UTILIDAD BRUTA: $120,000,000")
    c.drawString(72, 550, "GASTOS OPERACIONALES: $70,000,000")
    c.drawString(72, 520, "UTILIDAD OPERACIONAL: $50,000,000")
    c.drawString(72, 490, "UTILIDAD NETA: $50,000,000")
    c.save()
    docs.append({"path": er_path, "type": "estado_resultados"})

    # Libro Auxiliar
    la_path = os.path.join(output_dir, "libro_auxiliar_2024.pdf")
    c = canvas.Canvas(la_path, pagesize=letter)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, 720, "LIBRO AUXILIAR")
    c.setFont("Helvetica", 12)
    c.drawString(72, 690, "Empresa: Test S.A.S. | NIT: 800999888-2")
    c.drawString(72, 670, "Período: 01/01/2024 - 31/12/2024")
    c.drawString(72, 640, "Cuenta 1105 - Caja")
    c.drawString(100, 620, "2024-01-15 | Ingreso ventas | Débito: $5,000,000 | Saldo: $5,000,000")
    c.drawString(100, 600, "2024-02-01 | Pago proveedor | Crédito: $2,000,000 | Saldo: $3,000,000")
    c.drawString(72, 570, "Cuenta 1110 - Bancos")
    c.drawString(100, 550, "2024-01-20 | Transferencia | Débito: $10,000,000 | Saldo: $10,000,000")
    c.save()
    docs.append({"path": la_path, "type": "libro_auxiliar"})

    return docs
```

**Step 2: Add `run_via_b_pipeline()` function**

```python
def run_via_b_pipeline(args) -> None:
    """Upload 3 first-level documents and wait for auto-derivation of second-level docs."""
    import os

    output_dir = "storage/uploads/frontend_sim/via_b"
    print("\n" + "="*60)
    print("VÍA B: Uploading first-level financial statements")
    print("="*60)

    docs = build_via_b_documents(output_dir)
    company_nit = args.company_nit
    base_url = args.base_url

    # Ensure company settings
    ensure_company_settings(base_url, company_nit, args.city, args.ciiu)

    for doc in docs:
        print(f"\n→ Uploading {doc['type']} from {os.path.basename(doc['path'])}")
        with open(doc["path"], "rb") as f:
            resp = requests.post(
                f"{base_url}/api/v1/ingest/upload",
                files={"file": (os.path.basename(doc["path"]), f, "application/pdf")},
                data={"company_nit": company_nit},
                timeout=30,
            )
        if resp.status_code not in (200, 201, 202):
            print(f"  ❌ Upload failed: {resp.status_code} {resp.text[:200]}")
            continue

        ingest_id = resp.json().get("ingest_id", "")
        print(f"  Ingest ID: {ingest_id}")

        # Poll until completed
        deadline = time.time() + args.timeout_seconds
        while time.time() < deadline:
            status_resp = requests.get(f"{base_url}/api/v1/ingest/{ingest_id}", timeout=10)
            if status_resp.status_code == 200:
                status = status_resp.json().get("status", "")
                if status in ("completed", "COMPLETED"):
                    print(f"  ✅ Ingested as {status_resp.json().get('document_type', '?')}")
                    break
                elif status in ("failed", "FAILED"):
                    print(f"  ❌ Ingest failed: {status_resp.json().get('extraction_errors', '')}")
                    break
            time.sleep(args.poll_seconds)
        else:
            print(f"  ⚠️  Ingest timed out")

    # Wait for auto-derivation (poll for second-level docs)
    print("\nWaiting for auto-derivation of second-level documents...")
    all_stmts = fetch_all_statements(base_url, company_nit, timeout=120)
    print(f"\nTotal stored statements: {len(all_stmts)}")
    for stmt in sorted(all_stmts, key=lambda s: s["statement_type"]):
        print(f"  [{stmt['source_mode']:25s}] {stmt['statement_type']}")
    second_level_types = {"flujo_de_caja", "cambios_patrimonio", "notas_estados_financieros"}
    found_second = {s["statement_type"] for s in all_stmts} & second_level_types
    if len(found_second) == 3:
        print("\n✅ Via B: All 3 second-level documents derived successfully")
    else:
        missing = second_level_types - found_second
        print(f"\n⚠️  Via B: Missing second-level documents: {missing}")
```

**Step 3: Wire `via-b` into the `main()` function**

Find where `args.source_mode` is checked (around line 520). Add:
```python
    if args.source_mode == "via-b":
        run_via_b_pipeline(args)
        return
```

This should go BEFORE the existing source_mode checks (`demo`, `existing`).

**Step 4: Run Via B simulation**

```bash
uv run python simulate_frontend_full_pipeline.py \
  --base-url http://127.0.0.1:8016 --source-mode via-b \
  --company-nit 800999888-2 --city Bogota --ciiu 6920 \
  --timeout-seconds 120 --poll-seconds 2
```

Expected:
```
VÍA B: Uploading first-level financial statements
→ Uploading balance_general ...  ✅
→ Uploading estado_resultados ... ✅
→ Uploading libro_auxiliar ...   ✅
Waiting for auto-derivation...
Total stored statements: 6
  [direct                   ] balance_general
  [derived                  ] cambios_patrimonio
  [derived                  ] flujo_de_caja
  [direct                   ] estado_resultados
  [direct                   ] libro_auxiliar
  [derived                  ] notas_estados_financieros
✅ Via B: All 3 second-level documents derived successfully
```

**Step 5: Commit**

```bash
git add simulate_frontend_full_pipeline.py
git commit -m "feat: add --source-mode via-b to simulate Via B pipeline end-to-end"
```

---

## Task 8: End-to-end verification

**Step 1: Full Via A run**

```bash
# Fresh DB recommended (or truncate financial_statements for clean test)
uv run python simulate_frontend_full_pipeline.py \
  --base-url http://127.0.0.1:8016 --source-mode demo \
  --company-nit 800999888-2 --city Bogota --ciiu 6920 \
  --timeout-seconds 240 --poll-seconds 2 --report-timeout-seconds 420
```

**Expected:** 7 documents in `/reports/statements` output.

**Step 2: Full Via B run** (use a different NIT to avoid conflicts with Via A)

```bash
uv run python simulate_frontend_full_pipeline.py \
  --base-url http://127.0.0.1:8016 --source-mode via-b \
  --company-nit 900111222-1 --city Medellin --ciiu 6910 \
  --timeout-seconds 120 --poll-seconds 2
```

**Expected:** 6 documents (3 direct + 3 derived, no libro_diario in Via B).

**Step 3: Run all tests**

```bash
uv run pytest tests/ -v
```
Expected: all pass.

**Step 4: Final commit**

```bash
git add .
git commit -m "feat: complete second-level financial document generation for Via A and Via B"
```

---

## Verification Checklist

- [ ] `db_service.financial_statements_exist()` returns True only when all specified types exist
- [ ] `db_service.get_journal_entry_period()` returns min/max fecha from JournalEntryLine
- [ ] `build_first_level_from_journal_entries()` creates 4 FinancialStatement records with `source_mode="derived_from_journal"`
- [ ] `derive_financial_statements()` creates 3 records with `source_mode="derived"` and lineage links
- [ ] Both functions are idempotent (run twice → same result, no duplicates)
- [ ] Via A: derivation runs automatically after process pipeline completes
- [ ] Via B: derivation runs automatically after 3rd first-level document is uploaded
- [ ] GET `/reports/statements?company_nit=X` returns all stored statements
- [ ] Simulate script Via A shows 7 documents at the end
- [ ] Simulate script Via B shows 6 documents at the end (no libro_diario)
- [ ] All tests pass
