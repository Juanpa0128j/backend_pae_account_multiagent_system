# Tax Module — Follow-ups

Pendientes detectados tras la investigación del módulo de declaraciones y cálculo tributario (2026-05-24). Cada sección describe el estado actual, por qué importa y qué cambios concretos haríamos.

---

## 1. UVT / bases mínimas a DB

### Estado actual
```python
# app/agents/tributario_agent.py:52-58
UVT_2026 = 52374  # hardcoded
BASE_MINIMA_RETEFUENTE_UVT = {"servicios": 4, "bienes": 27, "arrendamiento": 27}
BASE_MINIMA_RETEICA_UVT = 4
```

### Por qué importa
- UVT (Unidad de Valor Tributario) cambia **cada año por decreto DIAN**.
- 2026: $52,374. 2027 será distinto.
- Bases mínimas también cambian por decretos (Decreto 0572 ya cambió bases ReteICA — ver lógica `dual_tax_table` implementada en mayo).
- Hoy: cambiar año = editar código + redeploy.

### Cambios propuestos

**Opción A — tabla dedicada (preferida)**:
```sql
CREATE TABLE uvt_values (
  year INT PRIMARY KEY,
  value NUMERIC(12,2) NOT NULL,
  decreto VARCHAR,
  effective_from DATE,
  effective_to DATE
);

CREATE TABLE tax_base_minima (
  id SERIAL PRIMARY KEY,
  concepto VARCHAR,  -- "retefuente_servicios", "reteica", etc.
  uvt_units NUMERIC(8,2),
  year INT,
  effective_from DATE,
  effective_to DATE
);
```

**Opción B — config genérica**: tabla `tax_constants(key, value, year)` — menos schema, más string-typing.

### Trabajo concreto
- Migration Alembic con creación de tablas.
- Modelos SQLAlchemy `UvtValue`, `TaxBaseMinima`.
- Seed con valores conocidos 2024-2027.
- Helper `get_uvt(year)` y `get_base_minima(concepto, year)` en `app/services/db_service.py`.
- Reemplazar `UVT_2026` y constantes `BASE_MINIMA_*` en `tributario_agent.py` por lookups.
- Endpoint admin (`POST /api/v1/admin/tax-constants`) para actualizar cada diciembre sin redeploy.

### Esfuerzo
Medio-alto. ~1 día.

---

## 2. Period wiring en SummaryPanel

### Estado actual
- `src/app/tax/components/SummaryPanel.tsx` tiene `PeriodSelector` local con `startDate`/`endDate`.
- Hooks `useIVA`, `useWithholdings`, `useICA`, `useRentaProvision` **no aceptan period params**.
- Backend `/api/v1/tax/iva` etc. usa default interno (mes actual o sin filtro).

### Por qué importa
- Bug visible: usuario cambia "Marzo" → "Abril" → números no cambian.
- UI engaña al usuario haciéndole creer que el periodo aplica.

### Cambios propuestos

**Backend** (`app/api/v1/tax.py`):
Añadir query params `period_start`, `period_end` a:
- `GET /api/v1/tax/iva`
- `GET /api/v1/tax/withholdings`
- `GET /api/v1/tax/ica`
- `GET /api/v1/tax/renta-provision`

Pasar al `get_general_ledger(start_date, end_date, company_nit)` existente.

**Frontend** (`src/hooks/useTax.ts`, `src/app/tax/components/SummaryPanel.tsx`):
- Hooks aceptan params:
  ```ts
  useIVA({ periodStart, periodEnd })
  ```
- `queryKey` incluye periodo → re-fetch al cambiar.
- `SummaryPanel` pasa estado local a hooks.
- Skeleton mientras re-carga.

### Esfuerzo
Bajo. ~2-3 horas, simétrico back+front.

---

## 3. Audit log para PATCH draft

### Estado actual
- `PATCH /api/v1/tax/declarations/{id}/fields` sobreescribe `fields_json` directo.
- Sin log de quién cambió qué cuándo.
- Si contador cambia renglón 42 de $10M → $5M, no hay rastro.

### Por qué importa
- DIAN puede auditar declaraciones → requiere trazabilidad.
- Disputa interna (¿quién cambió esto?) sin respuesta.
- Detección de errores tras envío → no se sabe cuándo se introdujo.

### Cambios propuestos

**Tabla nueva**:
```sql
CREATE TABLE tax_declaration_draft_history (
  id SERIAL PRIMARY KEY,
  draft_id UUID REFERENCES tax_declaration_drafts(id),
  renglon VARCHAR,
  field_label VARCHAR,
  old_value NUMERIC(18,2),
  new_value NUMERIC(18,2),
  user_id VARCHAR,         -- from JWT
  user_email VARCHAR,
  changed_at TIMESTAMPTZ DEFAULT NOW(),
  reason TEXT NULL         -- optional justification
);
```

**Endpoints**:
- En `update_draft_field` (backend), antes del commit → insertar row history.
- Nuevo `GET /api/v1/tax/declarations/{id}/history` → lista cambios cronológicos.

**Frontend**:
- Tab "Historial" en `DraftEditor` con timeline visual de cambios.
- Opcionalmente: input "razón del cambio" antes de guardar (popup ligero).

### Esfuerzo
Medio. ~4-6 horas.

---

## 4. Renta provision real (F110)

### Estado actual
```python
# app/agents/tributario_agent.py:313 + app/services/tax_declaration_service.py
provision = max(0, utilidad_antes_impuestos) * 0.35
```

### Por qué importa
- F110 (Renta) es la declaración más compleja del régimen colombiano.
- Cálculo actual es una multiplicación. Real involucra:
  - Conciliación fiscal (F2516 — diferencias contables vs fiscales).
  - **Compensación de pérdidas** años anteriores (Art. 147 ET, 12 años).
  - **Rentas exentas** (Art. 235-2 ET).
  - **Descuentos tributarios** (donaciones, CREE, etc.).
  - **Retenciones del año** (deducir del impuesto).
  - **Anticipo año siguiente**.
- Hoy: número estimado, **NO sirve para presentar a DIAN**.

### Cambios propuestos

**Fase 1 — exponer campos editables**:
Añadir a `tax_declaration_drafts.fields_json` los renglones F110 completos:
- Renta líquida ordinaria.
- Pérdidas fiscales por compensar (input manual).
- Rentas exentas (input manual).
- Renta líquida gravable.
- Impuesto (× 35%).
- Descuentos tributarios (input).
- Impuesto neto.
- Retenciones del año (sumar de PUC 1355xx).
- Saldo a pagar / favor.

Marcar manual fields como `requires_review=true`.

**Fase 2 — automatización**:
- Calcular retenciones del año desde `journal_entry_lines` (PUC 135515, 135518).
- Lookup tabla `perdidas_fiscales_acumuladas` (por empresa, por año).
- Si F2516 está `reviewed` → tomar valores de ahí en lugar de pedirlos.

### Esfuerzo
Alto. Fase 1 ~1 día. Fase 2 ~3-5 días.

---

## 5. Draft → reviewed → filed workflow

### Estado actual
- Tabla `tax_declaration_drafts` tiene columna `status: draft | reviewed | filed`.
- Status siempre queda en `draft`.
- Botón "Marcar como revisado" en `src/app/tax/components/DraftEditor.tsx:474-496` **siempre disabled**.
- Sin flujo de aprobación.

### Por qué importa
- Contador genera draft → asistente edita → contador revisa → presenta.
- Hoy: cualquiera edita en cualquier momento, sin firma de aprobación.
- No se distingue draft tentativo vs declaración finalizada.

### Cambios propuestos

**Schema**:
```sql
ALTER TABLE tax_declaration_drafts ADD COLUMN reviewed_by VARCHAR;
ALTER TABLE tax_declaration_drafts ADD COLUMN reviewed_at TIMESTAMPTZ;
ALTER TABLE tax_declaration_drafts ADD COLUMN filed_by VARCHAR;
ALTER TABLE tax_declaration_drafts ADD COLUMN filed_at TIMESTAMPTZ;
ALTER TABLE tax_declaration_drafts ADD COLUMN dian_acknowledgment VARCHAR;  -- número de radicado MUISCA
```

**Endpoints**:
- `POST /api/v1/tax/declarations/{id}/review` → valida `requires_review === false` en todos los campos, setea `status=reviewed`.
- `POST /api/v1/tax/declarations/{id}/file` → solo desde `reviewed`, registra fecha + radicado opcional.
- `POST /api/v1/tax/declarations/{id}/reopen` → `filed → reviewed` o `reviewed → draft` (con auditoría).

**Frontend**:
- Habilitar botón "Marcar como revisado" (condicional a `fieldsRequiringReview === 0`).
- Nuevo botón "Marcar como presentada" + modal con input de número de radicado MUISCA.
- Badge de status en lista de drafts.
- `reviewed` / `filed` → bloquear PATCH (cambios solo desde `draft`).

### Esfuerzo
Medio. ~1 día completo back+front.

---

## Resumen de impacto vs esfuerzo

| # | Item | Esfuerzo | Impacto | Bloquea producción real |
|---|------|----------|---------|---|
| 1 | UVT / bases a DB | Medio-alto | Alto cada Enero | No (parche manual sirve) |
| 2 | Period wiring | Bajo | Alto (UX rota) | No |
| 3 | Audit log | Medio | Alto (compliance) | Sí, eventual |
| 4 | Renta real | Alto | Crítico | Sí (F110 no se puede presentar) |
| 5 | Workflow status | Medio | Medio | No (workaround manual) |

### Orden recomendado
1. **Period wiring** — barato, fix UX bug visible.
2. **Workflow status** — habilita flujo correcto draft → reviewed → filed.
3. **Audit log** — compliance, necesario antes de salir a producción.
4. **UVT a DB** — antes de Diciembre 2026.
5. **Renta real** — proyecto grande, planificar como fase aparte (probablemente Fase 1 y Fase 2 en sprints distintos).

---

## Contexto: fixes ya aplicados (2026-05-24)

Ver PRs:
- Backend: corrección `240808 → 240805` en `tax_declaration_service.py`, conexión `get_reteica_tarifa` en `tributario_agent.py`, error codes estructurados en `/declarations/generate`.
- Frontend: removido `F260` de `FORM_TYPES`, agregado `F2516`, handler estructurado de errores en `DeclarationPanel.tsx` con CTAs por código.

Estos follow-ups asumen ese baseline.
