# Manual Transaction CRUD — Design Spec

**Date:** 2026-06-01  
**Branch:** `feature/manual-transactions` (backend + frontend)  
**Approach:** Synthetic IngestJob per Manual Transaction (Approach 1)

---

## 1. Goal

Enable users to create, edit, and delete manual accounting transactions in the PAE system without uploading a document. Manual transactions flow through the same agentic pipeline (Contador → Tributario → Auditor → Persist) as uploaded documents, preserving quality guarantees and audit trails.

## 2. Architecture

### 2.1 Data Flow

```
User Form → POST /api/v1/transactions
    → Synthetic IngestJob (file_name="manual_entry", status=COMPLETED)
    → TransactionPending (status=PENDING, raw_data shaped like Gemini extraction)
    → User can PATCH while PENDING
    → User triggers POST /api/v1/process/accounting/{ingest_id}
    → Full agent pipeline runs unchanged
    → TransactionPosted + JournalEntryLines created
```

### 2.2 Edit POSTED Transactions

```
POST /api/v1/transactions/{id}/reprocess
    → _delete_transaction_cascade(old_id)
    → _resync_derived_statements(company_nit)
    → Create new synthetic IngestJob + TransactionPending with updated data
    → Return { new_transaction_id, new_ingest_id }
    → User triggers process on new ingest_id
```

## 3. Backend API

### 3.1 New Endpoints

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `POST /api/v1/transactions` | POST | Bearer | Create manual transaction |
| `PATCH /api/v1/transactions/{id}` | PATCH | Bearer | Edit pending transaction |
| `POST /api/v1/transactions/{id}/reprocess` | POST | Bearer | Re-create a posted transaction for editing |

### 3.2 Existing Endpoints (No Change)

| Endpoint | Reused For |
|---|---|
| `POST /api/v1/process/accounting/{ingest_id}` | Trigger pipeline for manual transaction |
| `GET /api/v1/process/status/{process_id}` | Polling during pipeline execution |
| `DELETE /api/v1/transactions/{id}` | Already implemented |

### 3.3 Request / Response Contracts

#### `POST /api/v1/transactions`

**Body (`CreateTransactionPayload`):**
```python
class TransactionItem(BaseModel):
    descripcion: str
    subtotal: float
    iva: float = 0.0

class CreateTransactionPayload(BaseModel):
    fecha: str  # ISO 8601 or DD/MM/YYYY
    concepto: str
    total: float
    nit_emisor: str
    nit_receptor: str
    tipo_documento: str  # factura | extracto | nota_credito | cuenta_cobro | recibo_caja | otro
    items: List[TransactionItem] = []
    company_nit: str
```

**Validation:**
- `total` must equal `sum(items.subtotal) + sum(items.iva)` within 1 COP tolerance.
- `company_nit` must have existing `CompanySettings`.
- `tipo_documento` must be in `DocumentType` enum.

**Response 201:**
```json
{
  "transaction_id": "txn_...",
  "ingest_id": "ing_...",
  "status": "PENDING"
}
```

**Response 409 (missing company settings):**
```json
{
  "error_category": "business_precondition",
  "error_code": "MISSING_COMPANY_SETTINGS",
  "message": "No se encontró configuración tributaria para la empresa con NIT ...",
  "remediation": "Configure el perfil tributario de su empresa en /settings y vuelva a intentarlo."
}
```

**Response 422 (total mismatch):**
```json
{"detail": "El total (1190000.00) no coincide con la suma de items + IVA (1000000.00 + 190000.00 = 1190000.00)."}
```

#### `PATCH /api/v1/transactions/{id}`

**Body (`UpdateTransactionPayload`):**
Same fields as `CreateTransactionPayload`, all optional. Only fields provided are updated.

**Guard:** If `status != PENDING` → 409.

**Response 200:**
```json
{
  "id": "txn_...",
  "fecha": "2024-03-15",
  "concepto": "Updated concept",
  "total": 1190000.00,
  "status": "PENDING"
}
```

#### `POST /api/v1/transactions/{id}/reprocess`

**Body:** None (or optional `updated_data: CreateTransactionPayload` if user wants to edit at the same time).

**Guard:** If `status != POSTED` → 409.

**Response 201:**
```json
{
  "old_transaction_id": "txn_old",
  "new_transaction_id": "txn_new",
  "new_ingest_id": "ing_new"
}
```

## 4. Synthetic IngestJob

### 4.1 Implementation

New function `db_service.create_manual_ingest_job()`:

```python
def create_manual_ingest_job(
    db: Session,
    company_nit: str,
    created_by: str | None = None,
) -> IngestJob:
    job = IngestJob(
        id=_generate_id("ing_"),
        file_name="manual_entry",
        file_path=None,
        file_names=None,
        multi_file_mode="pages",
        status=IngestStatus.COMPLETED,  # nothing to parse
        document_type="manual_entry",
        pathway="build_from_scratch",
        classification_confirmed=True,
        company_nit=normalize_nit(company_nit),
        parser_mode="fast",
    )
    db.add(job)
    create_audit_log(db, "manual_ingest_created", job.id, "ingest", {"company_nit": company_nit}, commit=False, created_by=created_by)
    db.commit()
    db.refresh(job)
    return job
```

### 4.2 TransactionPending `raw_data` Shape

Compatible with existing extraction schemas so Contador/Tributario prompts work without modification:

```json
{
  "fecha": "2024-03-15",
  "nit_emisor": "800123456",
  "nit_receptor": "900654321",
  "totales": {
    "subtotal": 1000000.00,
    "iva": 190000.00,
    "total": 1190000.00
  },
  "items": [
    {
      "descripcion": "Servicios de consultoría",
      "subtotal": 1000000.00,
      "iva": 190000.00
    }
  ],
  "tipo_documento": "factura",
  "concepto": "Factura servicios marzo"
}
```

### 4.3 Document Type Registration

`manual_entry` must be added to `DocumentType` enum in `app/models/document_types.py` with:
- `PATHWAY_MAP["manual_entry"] = "build_from_scratch"`
- Guidance in Contador prompt: treat as generic invoice / generic expense document based on `totales` sign.

## 5. Frontend Changes

### 5.1 New Components

| Component | Path | Purpose |
|---|---|---|
| `TransactionFormModal` | `src/components/transactions/TransactionFormModal.tsx` | Brutalist modal for create/edit |
| `TransactionItemTable` | `src/components/transactions/TransactionItemTable.tsx` | Dynamic items subtotal/iva/total table |

### 5.2 Modified Components

| Component | Change |
|---|---|
| `src/app/transactions/page.tsx` | Add `+ NUEVA TRANSACCIÓN` button, wire modal |
| `TransactionTable` | Add edit icon (PENDING) / reprocess icon (POSTED) per row |
| `src/hooks/useTransactions.ts` | Add `useCreateTransaction`, `useUpdateTransaction`, `useReprocessTransaction` |
| `src/lib/api/clients/reportApiClient.ts` | Add `createTransaction`, `updateTransaction`, `reprocessTransaction` |
| `src/types/index.ts` | Add `CreateTransactionPayload`, `UpdateTransactionPayload`, `TransactionItem` |

### 5.3 Brutalist Form Design

- **Modal width:** `maxWidth: 'md'` (900px)
- **Accent:** `moduleAccents.transactions` (pink)
- **Hero label:** `// NUEVA_TRANSACCIÓN` in JetBrains Mono
- **Fields:** fecha (MUI DatePicker), concepto (multiline), total (formatted COP), nit_emisor / nit_receptor (with `// NIT` label), tipo_documento (Select with `// TIPO` label)
- **Items table:** Brutalist stripped table with `// ITEMS` header, add/remove rows, live total calculation
- **Submit button:** `CHARTREUSE` accent, `// GUARDAR` label
- **Cancel:** ghost button with `palette.paperGhost`

### 5.4 Validation (Frontend)

- `total === sum(items.subtotal) + sum(items.iva)` → inline error under total field
- `nit_emisor` and `nit_receptor` normalized on blur (strip `.` and spaces)
- `concepto` required, max 500 chars
- `fecha` not in future
- Disable submit while `total_mismatch` or `required_fields_empty`

## 6. Error Handling

| Scenario | Backend | Frontend |
|---|---|---|
| Company settings missing | 409 + structured error | `Alert` with remediation link to `/settings` |
| Total mismatch | 422 | Inline red text under total field |
| PATCH on POSTED | 409 | Disable edit button for POSTED; show "Reprocess to edit" tooltip |
| Delete fails (existing) | 404/500 | `Alert` with Spanish message, dismissible |
| Network failure | 5xx | Generic `Alert` + retry button |

## 7. Testing Strategy (TDD)

### 7.1 Backend Tests

File: `tests/api/v1/test_transactions_manual.py`

1. `test_create_manual_transaction_returns_201`
2. `test_create_manual_transaction_stores_raw_data_shape`
3. `test_create_manual_transaction_requires_company_settings`
4. `test_create_manual_transaction_validates_total`
5. `test_patch_pending_updates_fields`
6. `test_patch_posted_returns_409`
7. `test_reprocess_posted_creates_new_pending`
8. `test_reprocess_non_posted_returns_409`
9. `test_reprocess_triggers_resync`
10. `test_create_manual_transaction_triggers_pipeline`

### 7.2 Frontend Tests

File: `src/test/transactions/createTransaction.test.ts`

1. `renders create button on transactions page`
2. `opens modal and validates required fields`
3. `calculates total from items automatically`
4. `submits create mutation and invalidates queries`
5. `shows error alert on API failure`

File: `src/test/transactions/transactionFormModal.test.ts`

1. `pre-fills fields when editing`
2. `disables submit on validation errors`
3. `calls reprocess for posted transactions`

## 8. Migration

**None required.** `IngestJob` already supports `file_path=None`, `file_names=None`, and `status=COMPLETED`. `TransactionPending` `ingest_id` stays non-nullable. Only a new enum value `manual_entry` in `DocumentType`.

## 9. Rollout Order

1. **Backend:**
   - Add `manual_entry` to `DocumentType`
   - Implement `create_manual_ingest_job()` in `db_service.py`
   - Implement `POST /api/v1/transactions`
   - Implement `PATCH /api/v1/transactions/{id}`
   - Implement `POST /api/v1/transactions/{id}/reprocess`
   - Add `document_type` handling in Contador prompt for `manual_entry`
   - Write tests
   - Run `make lint`, `make format`, `make test`

2. **Frontend:**
   - Add types to `src/types/index.ts`
   - Add API methods to `reportApiClient.ts`
   - Add hooks to `useTransactions.ts`
   - Build `TransactionFormModal` + `TransactionItemTable`
   - Wire into `transactions/page.tsx`
   - Write tests
   - Run `pnpm tsc --noEmit`, `pnpm test`, `pnpm format:check`

## 10. Open Questions (None)

All decisions resolved:
- Create flow queues as PENDING, user triggers process later (Answer B)
- Edit POSTED = delete + recreate + reprocess (Answer B)
- Approach = Synthetic IngestJob (Approach 1)
- Branches = `feature/manual-transactions` on both repos

---

**Approved by:** User (verbal LGTM 2026-06-01)
