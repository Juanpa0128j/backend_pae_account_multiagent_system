# Design: Second-Level Financial Document Generation

**Date:** 2026-03-28
**Branch:** asegurar-creacion-docs

## Context

The pipeline currently handles two pathways:

- **Vía A (build_from_scratch):** Source documents (facturas, extractos, etc.) → Ingest → Contador/Tributario/Auditor → DB persist → JournalEntryLines in DB → Reportero generates balance/pnl/cashflow on demand.
- **Vía B (work_with_existing):** Upload first-level financial statements → stored directly in `FinancialStatement` table.

First-level documents (balance_general, estado_resultados, libro_auxiliar) are already produced end-to-end. This design covers producing the four second-level documents (libro_diario, flujo_de_caja, cambios_patrimonio, notas_estados_financieros) for both pathways and exposing them via API.

## Requirements

1. **Vía A:** After the accounting pipeline completes (ProcessJob → COMPLETED), automatically build first-level FinancialStatement records from JournalEntryLines, then derive second-level documents.
2. **Vía B:** After each first-level upload, check if all three source statements are present; if so, auto-derive second-level documents.
3. All seven documents stored correctly in `FinancialStatement` table.
4. New API endpoints to retrieve stored statements.
5. `simulate_frontend_full_pipeline.py` updated to validate both pathways end-to-end.

## Architecture

```
VÍA A:
  source docs → ingest → contador/tributario/auditor → db_persist (process mode)
                                                           ↓ [NEW]
                                              build_first_level_from_journal_entries()
                                                    → FinancialStatement: balance_general
                                                    → FinancialStatement: estado_resultados
                                                    → FinancialStatement: libro_auxiliar
                                                    → FinancialStatement: libro_diario
                                                           ↓ [EXISTING]
                                              derive_financial_statements()
                                                    → FinancialStatement: flujo_de_caja
                                                    → FinancialStatement: cambios_patrimonio
                                                    → FinancialStatement: notas_estados_financieros

VÍA B:
  upload balance_general    → FinancialStatement (direct) \
  upload estado_resultados  → FinancialStatement (direct)  → 3 present? → derive()
  upload libro_auxiliar     → FinancialStatement (direct) /
```

## Files to Modify

| File | Change |
|------|--------|
| `app/services/financial_statement_service.py` | Add `build_first_level_from_journal_entries()` + dedup guard in `derive_financial_statements()` |
| `app/agents/persist_node.py` | Call derivation at end of process mode; trigger Vía B auto-derive after `_persist_financial_statement()` |
| `app/services/db_service.py` | Add `get_financial_statements_by_company()` and `financial_statements_exist()` |
| `app/api/v1/reports.py` | Add GET `/reports/statements` and GET `/reports/statements/{id}` |
| `simulate_frontend_full_pipeline.py` | Fetch second-level docs after Vía A; add `--source-mode via-b` |

## Detailed Design

### 1. `financial_statement_service.py`

**New function:** `build_first_level_from_journal_entries(db, company_nit, period_start, period_end)`
- Queries `db_service.get_balance_sheet(db, company_nit, period_start, period_end)` (already exists via reportero)
- Queries `db_service.get_pnl(db, company_nit, period_start, period_end)`
- Queries `db_service.get_general_ledger(db, company_nit, period_start, period_end)`
- Builds four FinancialStatementContent dicts (balance_general, estado_resultados, libro_auxiliar, libro_diario)
- Persists four `FinancialStatement` records with `source_mode="derived_from_journal"`
- Returns dict of created statement IDs
- Idempotent: skips creation if same type + company + period already exists

**Modify:** `derive_financial_statements()` — add existence check before inserting each derived statement to prevent duplicates.

### 2. `persist_node.py`

**End of process mode** (after line ~600 where ProcessJob → COMPLETED):
```python
try:
    period_start, period_end = _get_period_from_journal_entries(db, company_nit)
    build_first_level_from_journal_entries(db, company_nit, period_start, period_end)
    derive_financial_statements(db, company_nit, period_start, period_end)
except Exception as e:
    logger.warning(f"[persist] Second-level derivation failed (non-fatal): {e}")
```

**End of `_persist_financial_statement()`** (Vía B, after creating FinancialStatement record):
```python
if _all_source_statements_present(db, company_nit, period_start, period_end):
    try:
        derive_financial_statements(db, company_nit, period_start, period_end)
    except Exception as e:
        logger.warning(f"[persist] Via B auto-derivation failed (non-fatal): {e}")
```

**New helper:** `_get_period_from_journal_entries(db, company_nit)` — returns (min_fecha, max_fecha) from JournalEntryLine for the company.

### 3. `db_service.py`

**New functions:**
- `get_financial_statements_by_company(db, company_nit, types=None)` → `List[FinancialStatement]`
- `financial_statements_exist(db, company_nit, period_start, period_end, types: List[str])` → `bool`

### 4. `reports.py`

**New endpoints:**
- `GET /reports/statements?company_nit=X&type=flujo_de_caja` → list of statement summaries (id, type, period, source_mode)
- `GET /reports/statements/{statement_id}` → full statement data JSON

### 5. `simulate_frontend_full_pipeline.py`

**Vía A mode (`--source-mode demo`):**
- After polling process complete, call `GET /reports/statements?company_nit=X`
- Display all seven statements with their source_mode and period

**New Vía B mode (`--source-mode via-b`):**
- Generate three synthetic PDFs (balance_general, estado_resultados, libro_auxiliar) using reportlab
- Upload all three via `POST /api/v1/ingest/upload`
- Poll each ingest until COMPLETED
- Poll `GET /reports/statements?company_nit=X` until flujo_de_caja appears (max 60s)
- Display all seven derived statements

## Verification

```bash
# Start backend
uvicorn main:app --reload --port 8016

# Vía A end-to-end (should show all 7 documents)
uv run python simulate_frontend_full_pipeline.py \
  --base-url http://127.0.0.1:8016 --source-mode demo \
  --company-nit 800999888-2 --city Bogota --ciiu 6920 \
  --timeout-seconds 240 --poll-seconds 2 --report-timeout-seconds 420

# Vía B end-to-end (should show all 7 documents after 3 uploads)
uv run python simulate_frontend_full_pipeline.py \
  --base-url http://127.0.0.1:8016 --source-mode via-b \
  --company-nit 800999888-2 --city Bogota --ciiu 6920

# Verify DB
# SELECT statement_type, source_mode, entity_nit FROM financial_statements;
# Should show 7 rows: 4 derived_from_journal (Vía A) or 3 direct + 3 derived (Vía B) + libro_diario
```

Expected final state in `financial_statements` table:
| statement_type | source_mode |
|---|---|
| balance_general | derived_from_journal (A) or direct (B) |
| estado_resultados | derived_from_journal (A) or direct (B) |
| libro_auxiliar | derived_from_journal (A) or direct (B) |
| libro_diario | derived_from_journal |
| flujo_de_caja | derived |
| cambios_patrimonio | derived |
| notas_estados_financieros | derived |
