# Roadmap Ejecutable - 15 Semanas de Implementación

**Objetivo:** Guía semanal con tareas definidas, archivos a crear, y criterios de aceptación.

---

## FASE 1: FUNDAMENTOS (Semanas 1-4)

### 📅 Semana 1: Dependencias & Configuración

**Objetivo:** Setup básico del proyecto con todas las dependencias correctas.

**Tareas:**

| # | Tarea | Archivo | CheckList |
|---|-------|---------|-----------|
| 1.1 | Actualizar pyproject.toml | `pyproject.toml` | ✅ Todas dependencias listadas |
| 1.2 | Instalar dependencias | Terminal | ✅ `pip install -e ".[dev]"` sin errores |
| 1.3 | Crear .env.example | `.env.example` | ✅ Todas variables de env listadas |
| 1.4 | Crear .env local | `.env` | ✅ API_KEY de Gemini configurada |
| 1.5 | Crear app/core/config.py | `app/core/config.py` | ✅ Settings con Pydantic |
| 1.6 | Crear app/core/logger.py | `app/core/logger.py` | ✅ JSON logging funcional |
| 1.7 | Crear .gitignore | `.gitignore` | ✅ Incluye .env, __pycache__, .db |

**Criterios de Éxito:**
- [ ] `from app.core.config import settings` funciona
- [ ] `get_logger("test")` genera logs en JSON
- [ ] `pip list` muestra todas las dependencias

**Duración:** 1 día

---

### 📅 Semana 2: Base de Datos (SQLAlchemy + Alembic)

**Objetivo:** Crear el esquema de BD y migrations automáticas.

**Tareas:**

| # | Tarea | Archivo | CheckList |
|---|-------|---------|-----------|
| 2.1 | Crear app/core/database.py | `app/core/database.py` | ✅ SessionLocal factory |
| 2.2 | Crear app/models/database.py | `app/models/database.py` | ✅ 5 tablas ORM definidas |
| 2.3 | Crear app/core/exceptions.py | `app/core/exceptions.py` | ✅ 6 custom exceptions |
| 2.4 | Inicializar Alembic | `alembic/` | ✅ `alembic init alembic` |
| 2.5 | Configurar Alembic | `alembic/env.py` | ✅ Auto-detect modelos |
| 2.6 | Primera migración | `alembic/versions/` | ✅ `alembic revision --autogenerate -m "Initial"` |
| 2.7 | Aplicar migración | Terminal | ✅ `alembic upgrade head` crea test.db |
| 2.8 | Crear storage folder | `storage/uploads/` | ✅ Carpeta existe |

**Criterios de Éxito:**
- [ ] `test.db` existe y contiene 5 tablas
- [ ] `from app.core.database import SessionLocal` funciona
- [ ] `Session.query(TransactionPending).count()` retorna 0 (sin errores)

**Duración:** 3 días

---

### 📅 Semana 3: Vector DB & RAG Setup

**Objetivo:** Configurar ChromaDB y poblarlo con datos iniciales.

**Tareas:**

| # | Tarea | Archivo | CheckList |
|---|-------|---------|-----------|
| 3.1 | Crear app/core/vectordb.py | `app/core/vectordb.py` | ✅ ChromaDB client |
| 3.2 | Crear app/services/rag.py | `app/services/rag.py` | ✅ search_normativo, search_empresa |
| 3.3 | Crear data/puc_base.json | `data/puc_base.json` | ✅ 30 cuentas PUC principales |
| 3.4 | Crear data/estatuto_tributario.json | `data/estatuto_tributario.json` | ✅ 10 artículos key (ET, tributación) |
| 3.5 | Script poblador de RAG | `scripts/populate_rag.py` | ✅ Inserta datos iniciales |
| 3.6 | Test RAG searches | `tests/test_rag.py` | ✅ 3 test cases |
| 3.7 | Crear storage/chromadb/ | Carpeta | ✅ Datos persistidos |

**Criterios de Éxito:**
- [ ] `vectordb.search_normativo("retención en la fuente")` retorna resultados
- [ ] `storage/chromadb/` existe y tiene datos
- [ ] Tests en `test_rag.py` pasan (3/3)

**Duración:** 4 días

---

### 📅 Semana 4: Schemas & Validaciones

**Objetivo:** Definir todos los modelos Pydantic y validadores.

**Tareas:**

| # | Tarea | Archivo | CheckList |
|---|-------|---------|-----------|
| 4.1 | Expandir app/models/schemas.py | `app/models/schemas.py` | ✅ 15+ clases Pydantic |
| 4.2 | Crear validadores custom | `app/models/schemas.py` | ✅ Validadores para NIT, total |
| 4.3 | Crear error handlers | `main.py` | ✅ 3 exception handlers |
| 4.4 | Tests de schemas | `tests/test_schemas.py` | ✅ 5 test cases |
| 4.5 | Documentación en docstrings | Todos los archivos | ✅ Cada clase tiene descripción |

**Criterios de Éxito:**
- [ ] `from app.models.schemas import *` funciona
- [ ] Validación: NIT inválido → ValidationException
- [ ] Tests en `test_schemas.py` pasan (5/5)

**Duración:** 2 días

---

## FASE 2: APIs & Agente Piloto (Semanas 5-9)

### 📅 Semana 5: LangGraph & Supervisor

**Objetivo:** Setup del framework de agentes y lógica de orquestación.

**Tareas:**

| # | Tarea | Archivo | CheckList |
|---|-------|---------|-----------|
| 5.1 | Crear app/agents/state.py | `app/agents/state.py` | ✅ AgentState TypedDict |
| 5.2 | Crear app/agents/graph.py | `app/agents/graph.py` | ✅ StateGraph con 5 nodos |
| 5.3 | Crear app/agents/supervisor.py | `app/agents/supervisor.py` | ✅ Router logic |
| 5.4 | Crear app/core/gemini_client.py | `app/core/gemini_client.py` | ✅ Gemini wrapper |
| 5.5 | Test Gemini connection | `tests/test_gemini.py` | ✅ 1 test (simple prompt) |
| 5.6 | Test LangGraph structure | `tests/test_graph.py` | ✅ Nodos existen, transiciones OK |

**Criterios de Éxito:**
- [ ] `from app.agents.graph import build_workflow` funciona
- [ ] `call_gemini("Hola")` retorna texto
- [ ] Graph tiene 5 nodos: supervisor, ingest, contador, tributario, auditor

**Duración:** 3 días

---

### 📅 Semana 6: Agente de Ingesta Piloto

**Objetivo:** Implementar el primer agente worker funcional.

**Tareas:**

| # | Tarea | Archivo | CheckList |
|---|-------|---------|-----------|
| 6.1 | Crear app/agents/ingest_agent.py | `app/agents/ingest_agent.py` | ✅ Prompts + tools |
| 6.2 | Crear app/services/file_handler.py | `app/services/file_handler.py` | ✅ read_pdf, read_excel |
| 6.3 | Crear test PDF y Excel | `tests/fixtures/` | ✅ 2 archivos de prueba |
| 6.4 | Test extracción básica | `tests/test_ingest_agent.py` | ✅ 3 test cases |
| 6.5 | Actualizar graph | `app/agents/graph.py` | ✅ Integrar ingest node |

**Criterios de Éxito:**
- [ ] Test PDF → `RawTransaction` list (correcto)
- [ ] Test Excel → campos fecha, nit_emisor, total extraídos
- [ ] Agente retorna JSON válido según schema

**Duración:** 4 días

---

### 📅 Semana 7: API de Ingesta Completa

**Objetivo:** Primer endpoint funcional de punta a punta.

**Tareas:**

| # | Tarea | Archivo | CheckList |
|---|-------|---------|-----------|
| 7.1 | Implementar POST /ingest/upload | `app/api/v1/ingest.py` | ✅ Guardar file + llamar agente |
| 7.2 | Implementar GET /ingest/{ingest_id} | `app/api/v1/ingest.py` | ✅ Consultar estado |
| 7.3 | Crear app/services/jobs.py | `app/services/jobs.py` | ✅ Crear/update ProcessJob |
| 7.4 | Test E2E ingesta | `tests/test_ingest_api.py` | ✅ Upload + get status |
| 7.5 | Update main.py | `main.py` | ✅ Tests correr sin advertencias |

**Criterios de Éxito:**
- [ ] `curl -X POST /ingest/upload -F file=@test.pdf` retorna 202 + ingest_id
- [ ] `GET /ingest/{id}` retorna transacciones extraídas
- [ ] E2E test pasa sin errores

**Duración:** 3 días

---

### 📅 Semana 8: Job Tracking & Async Processing

**Objetivo:** Implementar procesamiento async y monitoreo de progreso.

**Tareas:**

| # | Tarea | Archivo | CheckList |
|---|-------|---------|-----------|
| 8.1 | Implementar POST /process/accounting/ | `app/api/v1/process.py` | ✅ Crear job + disparar async |
| 8.2 | Implementar GET /process/status/ | `app/api/v1/process.py` | ✅ Polling endpoint |
| 8.3 | Implementar GET /process/result/ | `app/api/v1/process.py` | ✅ Retornar transacciones finales |
| 8.4 | Integrar graph execution | `app/services/jobs.py` | ✅ Ejecutar workflow |
| 8.5 | Tests de status polling | `tests/test_process_api.py` | ✅ 4 test cases |
| 8.6 | Timeout handling | `app/services/jobs.py` | ✅ Max 5 min por job |

**Criterios de Éxito:**
- [ ] `POST /process/accounting` retorna process_id
- [ ] `GET /process/status/{id}` muestra progreso actual
- [ ] Job se marca como completed/failed automáticamente
- [ ] Tests pasan (4/4)

**Duración:** 4 días

---

### 📅 Semana 9: Supervisor Funcional & Testing

**Objetivo:** Cerrar Fase 2 con supervisión completa del pipeline.

**Tareas:**

| # | Tarea | Archivo | CheckList |
|---|-------|---------|-----------|
| 9.1 | Actualizar supervisor.py | `app/agents/supervisor.py` | ✅ Full routing logic |
| 9.2 | Integración completa | `app/agents/graph.py` | ✅ Supervisor controla flujo |
| 9.3 | Error handling en agentes | Todos agentes | ✅ Retry logic (max 3) |
| 9.4 | Logging estructurado | `app/agents/supervisor.py` | ✅ agent_log actualizándose |
| 9.5 | Tests E2E | `tests/test_e2e_phase2.py` | ✅ Upload → clasif → resultado |
| 9.6 | Documentation update | `docs/IMPLEMENTATION_STATUS.md` | ✅ Fase 2 completa |

**Criterios de Éxito:**
- [ ] Flujo completo: upload PDF → recibe resultado con asiento contable
- [ ] agent_log muestra todos los pasos
- [ ] E2E test pasa
- [ ] 0 errores en logs

**Duración:** 4 días

---

## FASE 3: Agentes Especializados (Semanas 10-15)

### 📅 Semana 10: Agente Contador (PUC)

**Objetivo:** Implementar lógica de clasificación contable según PUC colombiano.

**Tareas:**

| # | Tarea | Archivo | CheckList |
|---|-------|---------|-----------|
| 10.1 | Crear app/agents/contador_agent.py | `app/agents/contador_agent.py` | ✅ Prompts en español |
| 10.2 | Crear herramientas | `app/agents/contador_agent.py` | ✅ search_puc, search_hist |
| 10.3 | Expandir RAG (PUC completo) | `data/puc_base.json` | ✅ 200+ cuentas |
| 10.4 | Tests de PUC assignment | `tests/test_contador_agent.py` | ✅ 5 casos de prueba |
| 10.5 | Integración en graph | `app/agents/graph.py` | ✅ Nodo contador |
| 10.6 | Validación determinística | `app/services/puc_validator.py` | ✅ PUC existe en tabla |

**Criterios de Éxito:**
- [ ] Agente clasifica transacción: "gasto" → encuentra PUC 5195
- [ ] RAG retorna histórico del proveedor
- [ ] Tests pasan (5/5)
- [ ] Salida en JSON con PUC validado

**Duración:** 5 días

---

### 📅 Semana 11: RAG Normativo Expandido

**Objetivo:** Poblar knowl edgebase con normativa colombiana clave.

**Tareas:**

| # | Tarea | Archivo | CheckList |
|---|-------|---------|-----------|
| 11.1 | Documento: Estatuto Tributario | `data/estatuto_tributario.json` | ✅ 50 artículos clave |
| 11.2 | Documento: Ley 43/1990 | `data/ley_43_1990.json` | ✅ Principios contables |
| 11.3 | Documento: PUC explicado | `data/puc_guia.json` | ✅ Descripciones por cuenta |
| 11.4 | Script poblador mejorado | `scripts/populate_rag.py` | ✅ Insert all docs |
| 11.5 | Hybrid search implementation | `app/services/rag.py` | ✅ BM25 + Vector |
| 11.6 | Tests de RAG | `tests/test_rag_expanded.py` | ✅ 10 searches validadas |

**Criterios de Éxito:**
- [ ] Buscar "Art. 383" → retorna artículo completo
- [ ] Buscar "retención en la fuente" → artículos relevantes
- [ ] ChromaDB contiene 300+ items indexados
- [ ] Tests pasan (10/10)

**Duración:** 5 días

---

### 📅 Semana 12: Agente Tributario (Impuestos)

**Objetivo:** Calcular impuestos correctamente según normativa colombiana.

**Tareas:**

| # | Tarea | Archivo | CheckList |
|---|-------|---------|-----------|
| 12.1 | Crear app/services/tax_calculator.py | Archivo | ✅ Funciones determinísticas |
| 12.2 | Crear app/agents/tributario_agent.py | Archivo | ✅ Prompts + tools |
| 12.3 | Validación de tasas | `app/services/tax_calculator.py` | ✅ Retefuente, ReteICA, IVA |
| 12.4 | Tests de cálculo | `tests/test_tax_calculator.py` | ✅ 8 casos de prueba |
| 12.5 | Tests de agente | `tests/test_tributario_agent.py` | ✅ 4 integración |
| 12.6 | Integración en graph | `app/agents/graph.py` | ✅ Después contador |

**Criterios de Éxito:**
- [ ] calc_retefuente(1500000, "servicios") → 165000 (11%)
- [ ] Agente cita artículos del ET en salida
- [ ] Journal entries incluyen cuentas de pasivo (2408, 2365)
- [ ] Tests pasan (12 total)

**Duración:** 5 días

---

### 📅 Semana 13: Agente Auditor (Control)

**Objetivo:** Validar integridad contable y detectar anomalías.

**Tareas:**

| # | Tarea | Archivo | CheckList |
|---|-------|---------|-----------|
| 13.1 | Crear app/agents/auditor_agent.py | Archivo | ✅ Lógica de validación |
| 13.2 | Validaciones determinísticas | `app/services/audit_validator.py` | ✅ Partida doble, duplicados |
| 13.3 | Tests de auditoría | `tests/test_auditor_agent.py` | ✅ 6 casos (aprobado/rechazado) |
| 13.4 | Pruebas con datos erróneos | `tests/fixtures/invalid_txn.json` | ✅ Agente rechaza |
| 13.5 | Integration tests | `tests/test_full_pipeline.py` | ✅ Flujo completo |
| 13.6 | Retry logic | `app/agents/supervisor.py` | ✅ Reintenta si rechaza |

**Criterios de Éxito:**
- [ ] Partida doble valida correctamente
- [ ] Detecta duplicados (mismo prov, monto, fecha ±3d)
- [ ] Auditor rechaza transacción inválida
- [ ] Sistema reintenta hasta 3 veces
- [ ] Tests pasan (6/6)

**Duración:** 5 días

---

### 📅 Semana 14: Integración Completa & Refinamiento

**Objetivo:** Pipeline end-to-end funcionando, limpieza de código.

**Tareas:**

| # | Tarea | Archivo | CheckList |
|---|-------|---------|-----------|
| 14.1 | Tests end-to-end | `tests/test_full_e2e.py` | ✅ 5 escenarios reales |
| 14.2 | Performance testing | `tests/test_performance.py` | ✅ Job < 5min |
| 14.3 | Error scenarios | `tests/test_error_handling.py` | ✅ 8 casos de error |
| 14.4 | Refactoring | Todos archivos | ✅ Code review |
| 14.5 | Logging optimization | `app/core/logger.py` | ✅ Logs útiles, sin spam |
| 14.6 | Documentation | `/docs` | ✅ README agentes |

**Criterios de Éxito:**
- [ ] E2E tests pasan (5/5)
- [ ] Todos los jobs completan en < 5 min
- [ ] Error handling sin excepciones no capturadas
- [ ] Code sin warnings/linting

**Duración:** 5 días

---

### 📅 Semana 15: Reportes, Evaluación & Cierre

**Objetivo:** Cerrar Fase 3 con reportes y sistema de evaluación.

**Tareas:**

| # | Tarea | Archivo | CheckList |
|---|-------|---------|-----------|
| 15.1 | Implementar GET /reports/* | `app/api/v1/reports.py` | ✅ Balance, P&L, Cashflow |
| 15.2 | Implementar GET /tax/* | `app/api/v1/tax.py` | ✅ IVA, Withholdings |
| 15.3 | Implementar GET /evaluation/run | `app/api/v1/evaluation.py` | ✅ Métricas completas |
| 15.4 | Tests de reportes | `tests/test_reports.py` | ✅ 6 casos |
| 15.5 | Tests de evaluación | `tests/test_evaluation.py` | ✅ Validar métricas |
| 15.6 | Documentation final | `/docs` | ✅ README completo |
| 15.7 | Deploy checklist | `DEPLOY_CHECKLIST.md` | ✅ Listo para prod |

**Criterios de Éxito:**
- [ ] GET /reports/balance retorna Balance Sheet correcto (ACTIVOS == PASIVOS + PATRIMONIO)
- [ ] GET /tax/iva muestra IVA a pagar
- [ ] GET /evaluation/run ejecuta 7 métricas
- [ ] Todos tests pasan (20+)
- [ ] 0 security issues
- [ ] Doc completa y clara

**Duración:** 5 días

---

## 📊 Resumen de Progreso

### Estado Actual (Semana 0)
```
✅ Main.py
✅ Routers estructura
❌ Implementaciones reales
❌ Agentes
❌ Base de datos
❌ RAG
```

### Después de Semana 4 (Fin Fase 1)
```
✅ Config + Logger
✅ DB + ORM
✅ Vector DB
✅ Schemas
❌ APIs
❌ Agentes
```

### Después de Semana 9 (Fin Fase 2)
```
✅ Config + Logger
✅ DB + ORM
✅ Vector DB
✅ Schemas
✅ APIs (ingest + process)
✅ Supervisor + Ingest Agent
❌ Contador, Tributario, Auditor
```

### Después de Semana 15 (Fin Fase 3)
```
✅ TODO ✅
Sistema 100% funcional
Listo para MVP
```

---

## 🎯 Key Milestones

| Hito | Semana | Criterio |
|------|--------|----------|
| "Hello World" DB | 2 | Tables creadas |
| First API endpoint | 7 | POST /ingest/upload funciona |
| Agent pipeline | 9 | Upload → Resultado con asiento |
| Full accounting | 12 | Impuestos calculados correctamente |
| MVP ready | 15 | Todos endpoints, evaluación funciona |

---

## 📝 Checklist de Buenas Prácticas

Por cada semana:

- [ ] **Tests:** Al menos 1 test por tarea principal
- [ ] **Commits:** Commit por tarea, mensaje descriptivo (`[Phase X] Tarea: descripción`)
- [ ] **Docs:** Docstring en funciones públicas
- [ ] **Logs:** Info level + error handling
- [ ] **Review:** Code limpio, sin `TODO` sin resolver
- [ ] **Merge:** Merge a `main` solo con tests verdes

---

**Status:** 🔴 No iniciado  
**Próxima acción:** Ejecutar Semana 1  
**Responsable:** Equipo Backend  
**Última actualización:** 2026-02-18
