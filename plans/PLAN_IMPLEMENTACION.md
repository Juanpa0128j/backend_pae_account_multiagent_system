# Plan de Implementación - Backend PAE Account Multiagent System

**Fecha:** Febrero 18, 2026  
**Objetivo:** Completar la implementación del backend siguiendo la arquitectura definida en los documentos de diseño.

---

## 📋 Resumen Ejecutivo

El plan está dividido en **3 fases principales** que suman **15 semanas** de trabajo intenso:

| Fase | Duración | Objetivo Principal |
|------|----------|-------------------|
| **Fase 1: Fundamentos** | Semanas 1-4 | Infraestructura, DB, Schemas |
| **Fase 2: APIs & Agente Piloto** | Semanas 5-9 | Endpoints funcionales + Supervisor básico |
| **Fase 3: Agentes Especializados** | Semanas 10-15 | Contador, Tributario, Auditor |

---

## 📊 Estado Actual vs. Meta

### ✅ Lo que YA existe:
- `main.py` con FastAPI configurado
- Estructura de routers (ingest, process, reports, tax, evaluation)
- Definición básica de schemas en Pydantic
- `pyproject.toml` con dependencias iniciales

### ❌ Lo que FALTA:

**Infraestructura:**
- [ ] Base de datos SQL (SQLite/PostgreSQL)
- [ ] Vector Store (ChromaDB)
- [ ] Modelos de datos (ORM/SQLAlchemy)
- [ ] Migración de base de datos (Alembic)

**Agentes:**
- [ ] LangGraph setup y configuración
- [ ] Agente Supervisor (orquestador)
- [ ] Agente Piloto de Ingesta
- [ ] Agentes Contador, Tributario, Auditor

**APIs Completas:**
- [ ] POST /ingest/upload (implementación real)
- [ ] POST /process/accounting/{ingest_id} (con async job tracking)
- [ ] GET /process/status/{process_id} (monitoreo de progreso)
- [ ] GET /reports/balance, /pnl, /cashflow (implementados)
- [ ] GET /tax/iva, /withholdings (implementados)
- [ ] GET /evaluation/run (implementado)

**Utilities & Config:**
- [ ] Gestión de variables de entorno (.env)
- [ ] Integración con Gemini 2.5 Flash
- [ ] RAG Normativo e Ingesta (básico)
- [ ] Logging estructurado
- [ ] Error handling estándar

---

## 🎯 Fase 1: Fundamentos (Semanas 1-4)

### Objetivo:
Crear la infraestructura base: DB, ORM, schemas completos, y configuración global.

### Tareas:

#### Semana 1: Dependencias & Configuración

**1.1** Actualizar `pyproject.toml` con dependencias necesarias:
```
fastapi, uvicorn, pydantic, sqlalchemy, alembic, chromadb,
langchain, langgraph, google-generativeai, python-dotenv,
pydantic-settings, pytest, httpx
```

**1.2** Crear estructura de configuración:
- `app/core/config.py` (settings con Pydantic)
- `.env.example` con variables necesarias
- `app/core/logger.py` (logging estructurado)

**1.3** Setup de Gemini API:
- Obtener API Key de Google AI Studio
- Validar conexión en `app/core/gemini_client.py`

#### Semana 2: Base de Datos

**2.1** Crear modelos SQLAlchemy en `app/models/database.py`:
```
- TransactionPending (estado PENDING)
- TransactionPosted (estado POSTED)
- ProcessJob (para tracking de procesos async)
- AuditLog (para trazabilidad)
```

**2.2** Implementar Alembic:
- `alembic init alembic`
- Primera migración: crear tablas

**2.3** Crear `app/core/database.py`:
- SessionLocal factory
- Base para ORM
- Helper functions (get_db dependency)

#### Semana 3: Vector DB & RAG setup

**3.1** Crear `app/core/vectordb.py`:
- Inicializar ChromaDB (local)
- Crear colecciones: `normativa_colombia`, `empresa_docs`
- Métodos para insert/search

**3.2** Crear `app/services/rag.py`:
- `SearchNormativo(query: str)` → artículos del ET, leyes
- `SearchHistorico(proveedor: str)` → transacciones pasadas
- Implementar hybrid search (BM25 + Vector)

**3.3** Poblar data inicial (RAG Normativo):
- Artículos básicos del Estatuto Tributario (por ahora, dataset simulado en JSON)
- Información de PUC base

#### Semana 4: Schemas & Validaciones

**4.1** Expandir `app/models/schemas.py` con:
- `RawTransaction` (salida de ingesta)
- `Transaction` (con PUC asignado)
- `TransactionWithTax` (con impuestos)
- `JournalEntry` (asiento contable)
- `ProcessStatus` (para polling de jobs)
- `AgentState` (estado compartido de LangGraph)

**4.2** Crear validadores customizados:
- Validación de NIT (Pydantic validators)
- Validación de fechas
- Validación de montos positivos

**4.3** Crear archivo `app/core/exceptions.py`:
- `InvalidNITException`
- `ProcessNotFoundException`
- `ValidationException`
- Error handlers en main.py

---

## 🤖 Fase 2: APIs & Agente Piloto (Semanas 5-9)

### Objetivo:
Implementar las APIs principales y crear el Supervisor + Agente de Ingesta básico.

#### Semana 5: Agente Supervisor & Setup LangGraph

**5.1** Crear `app/agents/graph.py`:
```python
# Estructura base LangGraph
StateGraph con nodos:
- supervisor (router logic)
- ingest_worker (placeholder)
- contador_worker (placeholder)
- tributario_worker (placeholder)
- auditor_worker (placeholder)
```

**5.2** Crear `app/agents/state.py`:
- Definir `AgentState` TypedDict
- Incluir: raw_data, transactions, errors, audit_log

**5.3** Crear `app/agents/supervisor.py`:
- Lógica de ruteo basada en estado
- Estados posibles: INGEST → PROCESS → AUDIT → POSTED

#### Semana 6: Agente Piloto de Ingesta

**6.1** Crear `app/agents/ingest_agent.py`:
- Tool: `read_pdf` (usando pdfplumber)
- Tool: `read_excel` (usando pandas)
- Prompt simple para extraer: fecha, nit_emisor, nit_receptor, total, items
- Output: Lista de `RawTransaction`

**6.2** Crear `app/services/file_handler.py`:
- Guardar archivos subidos (temporal)
- Detectar tipo de archivo
- Limpiar caracteres especiales

**6.3** Integración con Gemini:
- Usar `gemini-2.5-flash` para procesar imágenes/OCR
- Setup de vision capabilities

#### Semana 7: API de Ingesta Completa

**7.1** Implementar `POST /ingest/upload`:
```
- Recibir file (PDF/Excel)
- Guardar en storage temporal
- Invocar AgentGraph (Ingest Worker)
- Guardar RawTransactions en DB (estado PENDING)
- Retornar ingest_id + metadata
```

**7.2** Crear `GET /ingest/{ingest_id}`:
```
- Retornar estado e info del documento
- Mostrar transacciones extraídas
```

**7.3** Tests en `tests/test_ingest.py`:
- Test upload PDF simple
- Test extraction correctness
- Test error handling (file inválido)

#### Semana 8: API de Procesamiento & Job Tracking

**8.1** Implementar Job Queue:
- `ProcessJob` modelo en DB
- Estados: queued, running, completed, failed
- Tabla de auditoría

**8.2** Implementar `POST /process/accounting/{ingest_id}`:
```
- Validar que ingest_id existe
- Crear ProcessJob en DB (status=queued)
- Iniciar agente (async con asyncio.create_task)
- Retornar 202 Accepted + process_id
```

**8.3** Implementar `GET /process/status/{process_id}`:
```
- Retornar estado actual del job
- Mostrar progreso: qué agente ejecutándose, errores
- Para debug: show agent reasoning (agent_log)
```

**8.4** Tests en `tests/test_process.py`:
- Test crear job
- Test status polling
- Test timeout handling

#### Semana 9: Agente Supervisor Funcional

**9.1** Implementar Supervisor con lógica simple:
```python
@node
def supervisor(state: AgentState):
    if state.current_stage == "INGEST":
        return {"next": "contador"}
    elif state.current_stage == "PROCESS":
        return {"next": "auditor"}
    # etc
```

**9.2** Integración con ProcessJob:
- Antes de cada nodo: check si hay cancelación
- Después de cada nodo: update DB con progreso

**9.3** Error handling:
- Si nodo falla, log error y mark job as failed
- Retry logic (máx 3 intentos)

---

## 🎓 Fase 3: Agentes Especializados (Semanas 10-15)

### Objetivo:
Implementar los agentes Contador, Tributario y Auditor con toda su lógica contable.

#### Semana 10: Agente Contador (Clasificación PUC)

**10.1** Crear `app/agents/contador_agent.py`:
- Tool: `search_puc(query: str)` → buscar en RAG normativo
- Tool: `search_history(proveedor: str)` → buscar transacciones pasadas
- Prompt: Instrucciones en español para clasificación contable
- Output: `Transaction` con PUC asignado

**10.2** Setup de PUC base:
- Crear archivo `data/puc_base.json` con cuentas principales
- Indexar en ChromaDB

**10.3** Validación:
- PUC debe existir en tabla de validación
- Si no, rechazar y pedir revisión humana

#### Semana 11: RAG Normativo Mejorado

**11.1** Expandir base de normativa:
- Artículos del Estatuto Tributario (ET) - temas clave
- Art. 24 (Renta bruta)
- Art. 383 (Retención en la fuente)
- Art. 477 (Impuesto a las ventas)
- Ley 43/1990 (Principios contables)

**11.2** Implementar Hybrid Search:
- BM25 para búsqueda por palabras clave
- Vector para búsqueda semántica
- Parent Document Retriever (artículos completos)

**11.3** Testing del RAG:
- Test searches en `tests/test_rag.py`
- Verificar que artículos correctos se retornan

#### Semana 12: Agente Tributario (Impuestos)

**12.1** Crear `app/agents/tributario_agent.py`:
- Tool: `calculate_retefuente(valor: float, tipo_prov: str)` → determinístico
- Tool: `calculate_reteica(valor: float, actividad: str)` → determinístico
- Tool: `calculate_iva(valor: float, tipo_bien: str)` → determinístico
- Tool: `search_tax_law(query: str)` → RAG
- Prompt: En español, aplicar normativa tributaria colombiana
- Output: `TransactionWithTax` con impuestos y referencias legales

**12.2** Lógica tributaria determinística:
```python
# NO dejar que el LLM adivine porcentajes
# Usar funciones Python determinísticas

def calc_retefuente(valor, tipo_proveedor):
    rates = {
        "servicios": 0.11,
        "bienes": 0.03,
        "arrendamiento": 0.10,
        # ...
    }
    return valor * rates.get(tipo_proveedor, 0)

def calc_iva(valor, tipo_bien):
    rates = {
        "exento": 0.0,
        "bienes": 0.19,
        "servicios": 0.19,
        # ...
    }
    return valor * rates.get(tipo_bien, 0.19)
```

**12.3** Validación tributaria:
- Asegurar que el agente siempre cite un art. del ET
- Si no, rechazar salida

#### Semana 13: Agente Auditor (Control Interno)

**13.1** Crear `app/agents/auditor_agent.py`:
- Tool: `validate_double_entry(entries: List[JournalEntry])` → determinístico
- Tool: `detect_duplicates(transaction: Transaction)` → DB query
- Tool: `validate_business_logic(transaction: Transaction)` → reglas
- Prompt: Rol de crítica, buscar anomalías
- Output: `Approved` o `Rejected` con feedback

**13.2** Validaciones determinísticas:
```python
def validate_double_entry(entries: List[JournalEntry]):
    total_debit = sum(e.debit for e in entries)
    total_credit = sum(e.credit for e in entries)
    return total_debit == total_credit

def detect_duplicates(transaction: Transaction):
    # Buscar en DB: mismo proveedor, mismo monto, fecha cercana (±3 días)
    # Si 2+ matches, flag como duplicado
    pass
```

**13.3** Lógica de rechazo:
- Si Auditor rechaza, guardar feedback
- Supervisor re-enruta a Contador con contexto de error
- Máx 3 intentos, luego → human_review

#### Semana 14: Integración del Pipeline Completo

**14.1** Prueba end-to-end:
- Usuario sube PDF → Ingesta extrae datos → Contador clasifica → Tributario calcula → Auditor aprueba → Generan asiento

**14.2** Manejo de errores:
- Si algún agente falla: log error, mark job, permitir retry
- Timeout por agente: 5 min máx

**14.3** Logging & Observabilidad:
- Cada paso del agente → log estructurado (JSON)
- Timeline visible para debugging (agent_log en response)

#### Semana 15: APIs de Reportes & Evaluación

**15.1** Implementar reporting:
- `GET /reports/balance` → generar Balance desde Libro Mayor
- `GET /reports/pnl` → Ingresos - Gastos (desde categorías contables)
- `GET /reports/cashflow` → Flujos de efectivo

**15.2** Implementar tax reporting:
- `GET /tax/iva` → Resumen de IVA generado vs descontable
- `GET /tax/withholdings` → Retenciones aplicadas

**15.3** Implementar evaluation:
```
GET /evaluation/run
- Schema Compliance Rate
- Double Entry Error Rate
- PUC Assignment Accuracy (si hay ground truth)
- Process Completion Rate
```

**15.4** Tests finales:
- Tests unitarios para cada agente
- Tests de integración (pipeline completo)
- Tests de boundary cases

---

## 🏗️ Estructura de Carpetas Final

```
backend_pae_account_multiagent_system/
├── main.py
├── pyproject.toml
├── .env.example
├── .gitignore
├── PLAN_IMPLEMENTACION.md          ← Tú lo lees aquí
├── README.md
├── alembic/                         ← DB migrations
│   ├── versions/
│   └── env.py
├── app/
│   ├── __init__.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py               ← Settings globales
│   │   ├── database.py             ← SQLAlchemy setup
│   │   ├── logger.py               ← Logging
│   │   ├── exceptions.py           ← Custom exceptions
│   │   ├── gemini_client.py        ← Gemini integration
│   │   └── vectordb.py             ← ChromaDB setup
│   ├── models/
│   │   ├── __init__.py
│   │   ├── schemas.py              ← Pydantic request/response
│   │   └── database.py             ← SQLAlchemy ORM models
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── state.py                ← AgentState TypedDict
│   │   ├── graph.py                ← LangGraph setup
│   │   ├── supervisor.py           ← Supervisor node
│   │   ├── ingest_agent.py         ← Ingesta worker
│   │   ├── contador_agent.py       ← Contador worker
│   │   ├── tributario_agent.py     ← Tributario worker
│   │   └── auditor_agent.py        ← Auditor worker
│   ├── services/
│   │   ├── __init__.py
│   │   ├── rag.py                  ← RAG search
│   │   ├── file_handler.py         ← File management
│   │   ├── tax_calculator.py       ← Deterministic tax calc
│   │   └── jobs.py                 ← Job tracking
│   ├── api/
│   │   ├── __init__.py
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── ingest.py           ← Endpoints de ingesta
│   │       ├── process.py          ← Endpoints de procesamiento
│   │       ├── reports.py          ← Endpoints de reportes
│   │       ├── tax.py              ← Endpoints tributarios
│   │       └── evaluation.py       ← Endpoints de evaluación
│   └── __init__.py
├── data/
│   ├── puc_base.json               ← PUC inicial
│   └── estatuto_tributario.json    ← Normativa inicial
├── tests/
│   ├── __init__.py
│   ├── test_ingest.py
│   ├── test_process.py
│   ├── test_contador.py
│   ├── test_tributario.py
│   ├── test_rag.py
│   └── test_audit.py
├── storage/                         ← Archivos subidos (local)
│   └── uploads/
└── docs/
    └── *.md                         ← Documentación del proyecto
```

---

## 📝 Checklist de Implementación

### Fase 1:
- [ ] Actualizar pyproject.toml con todas dependencias
- [ ] Crear app/core/config.py
- [ ] Crear .env.example
- [ ] Crear SQLAlchemy models en app/models/database.py
- [ ] Inicializar Alembic y primera migración
- [ ] Crear app/core/database.py con SessionLocal
- [ ] Crear app/core/vectordb.py con ChromaDB
- [ ] Crear app/services/rag.py con search methods
- [ ] Expandir app/models/schemas.py
- [ ] Crear app/core/exceptions.py

### Fase 2:
- [ ] Crear app/agents/state.py
- [ ] Crear app/agents/graph.py (estructura base)
- [ ] Crear app/agents/supervisor.py
- [ ] Crear app/agents/ingest_agent.py
- [ ] Crear app/services/file_handler.py
- [ ] Crear app/core/gemini_client.py
- [ ] Implementar POST /ingest/upload
- [ ] Implementar GET /ingest/{ingest_id}
- [ ] Implementar POST /process/accounting/{ingest_id}
- [ ] Implementar GET /process/status/{process_id}
- [ ] Crear tests para Phase 2

### Fase 3:
- [ ] Crear app/agents/contador_agent.py
- [ ] Expandir RAG normativo
- [ ] Crear app/agents/tributario_agent.py
- [ ] Crear app/services/tax_calculator.py
- [ ] Crear app/agents/auditor_agent.py
- [ ] Integración completa del pipeline
- [ ] Implementar GET /reports/*
- [ ] Implementar GET /tax/*
- [ ] Implementar GET /evaluation/run
- [ ] Tests finales y debugging

---

## 🚀 Próximas Acciones Inmediatas

1. **Esta semana:**
   - [ ] Actualizar `pyproject.toml` con dependencias (Fase 1)
   - [ ] Crear estructura de carpetas en `app/core/`
   - [ ] Setup Gemini API key

2. **Siguiente semana:**
   - [ ] Implementar `app/core/config.py` y `.env.example`
   - [ ] Crear modelos SQLAlchemy
   - [ ] Inicializar Alembic

---

## 📚 Referencias de Documentos de Diseño

- **¿Cómo se desplegaría...?** → Arquitectura, APIsAPIs y stack tecnológico
- **Diagrama de Arquitectura...** → Flujo de datos y nodos del grafo
- **Diseño de arquitectura de agente...** → Roles, RAG dual, pipelines
- **Estructura de Front-end...** → Contratos de APIs (importante para schemas)
- **Estructura de validación...** → Métricas y evaluación

---

## ⚠️ Notas Importantes

1. **Gemini 2.5 Flash:** API key gratuita en https://ai.google.dev/ - 15 RPM, 1M tokens/min
2. **ChromaDB:** Local, sin infra externa necesaria
3. **SQLite para dev:** Perfecto para prototipo. Cambiar a PostgreSQL en prod.
4. **Agentes:** Empezar simple (1-2 tools por agente), expandir después
5. **Testing:** Crucial para validación. Crear tests JUNTO con código, no al final.
6. **LangSmith:** Opcional pero recomendado para debug de agentes (https://smith.langchain.com)

---

**Status:** 🟡 Pendiente ejecución  
**Última actualización:** 2026-02-18
