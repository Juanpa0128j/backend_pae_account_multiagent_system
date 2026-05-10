# PAE Contable — Backend Context

Multi-agent FastAPI backend for Colombian accounting automation. Processes financial documents through a LangGraph-orchestrated pipeline.

## Language

**Ingesta**:
The pipeline phase that receives source documents (facturas, extractos bancarios, declaraciones) and extracts structured accounting data from them.
_Avoid_: upload, ingestión

**Procesamiento**:
The pipeline phase that takes pending transactions, classifies them by PUC, applies tax rules (IVA, retención, ICA), validates double-entry, and persists them.
_Avoid_: processing, contabilización

**Reportería**:
The pipeline phase that generates financial statements (balance general, estado de resultados, flujo de caja, etc.) from posted journal entries.
_Avoid_: reporting, reportes

**Proceso Contable**:
The seam responsible for making accounting data durable: building journal entries, deriving financial statements, and persisting both to the database. This is the boundary between the agentic pipeline and storage.
_Avoid_: persistencia, db_persist_node

**Outputs de agente**:
The work product produced by each worker node in the pipeline. `contador_output` contains PUC classifications; `tributario_output` contains tax calculations; `auditor_output` contains validation findings.
_Avoid_: resultados, respuestas

**Vía A** (`build_from_scratch`):
Ingest pathway for source documents that must pass through the full accounting pipeline (extraction → PUC → tax → audit → persist).
_Avoid_: camino A, modo A

**Vía B** (`work_with_existing`):
Ingest pathway for pre-existing financial statements that are stored directly without running the full accounting pipeline.
_Avoid_: camino B, modo B

**Asiento contable**:
A double-entry journal entry with debit and credit lines, tied to a specific PUC account.
_Avoid_: journal entry (in Spanish docs), transacción

**Estado financiero**:
A financial statement row (balance general, estado de resultados, etc.) derived from posted journal entries.
_Avoid_: reporte financiero, statement

**NIT**:
Número de Identificación Tributaria — Colombian tax ID. Must be normalized (strip `.` and spaces) before storage.
_Avoid_: tax ID, RUT

**PUC**:
Plan Único de Cuentas — Colombian chart of accounts. Each account has a numeric code (e.g., `110505` caja general).
_Avoid_: chart of accounts, COA

**Contador**:
The worker agent that assigns PUC account codes to transactions.
_Avoid_: accountant agent, classifier

**Tributario**:
The worker agent that calculates IVA, retención en la fuente, and ICA for classified transactions.
_Avoid_: tax agent, fiscal

**Auditor**:
The worker agent that validates double-entry balance and emits internal control findings.
_Avoid_: audit agent, validator

**Reportero**:
The worker agent that generates financial statements and narrative analysis from posted data.
_Avoid_: report agent, reporter

**Supervisor**:
The entry node that validates files, classifies documents, resolves pathway (Vía A / Vía B), and sets `mode`.
_Avoid_: router, entry point

## Relationships

- An **Ingesta** produces one or more pending transactions (Vía A) or financial statements (Vía B)
- **Procesamiento** consumes pending transactions and produces **Asientos contables**
- **Proceso Contable** is the seam where **Asientos contables** become durable and **Estados financieros** are derived
- **Reportería** consumes posted **Asientos contables** to produce **Estados financieros**
- Each worker node (Contador, Tributario, Auditor, Reportero) produces **Outputs de agente**
- A **NIT** identifies exactly one empresa; all data is scoped by NIT

## Example dialogue

> **Dev:** "When the Contador produces its output, does the Proceso Contable immediately persist it?"
> **Domain expert:** "No — the Tributario and Auditor must also run. The Proceso Contable only executes after all three agents have produced their outputs and the Auditor has approved."

## Flagged ambiguities

- "Transaction" was used to mean both pending source lines (`TransactionPending`) and posted journal lines (`JournalEntryLine`). Resolved: these are distinct concepts at different lifecycle stages.
- "Process" was overloaded between `ProcessJob` (DB entity), `process` pipeline mode, and generic "business process." Resolved: `Procesamiento` refers to the pipeline phase; `ProcessJob` is the DB record.
