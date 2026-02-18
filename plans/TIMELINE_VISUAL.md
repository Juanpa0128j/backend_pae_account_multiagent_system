# 📈 Timeline Visual del Plan

**Objetivo:** Ver el plan de 15 semanas de un vistazo.

---

## 🎯 Overview de 15 Semanas

```
SEMANA   1  2  3  4  |  5  6  7  8  9  | 10 11 12 13 14 15
         ===== FASE 1 (Fundamentos) ==== | ===== FASE 2 (APIs) ===== | =========== FASE 3 (Agentes) ===========
         
Config   ████                           |                          |
DB/ORM      ████                        |                          |
Vector DB      ████                     |                          |
Schemas         ████                    |                          |
                   |   LangGraph        |                          |
                   |   ████             |                          |
                   |      Ingest Agent  |                          |
                   |      ████          |                          |
                   |         Ingest API |                          |
                   |         ████       |                          |
                   |            Job Track                          |
                   |            ████    |                          |
                   |               Supervisor                      |
                   |               ████ |                          |
                   |                    | Contador Agent           |
                   |                    | ████                     |
                   |                    |    RAG Expanded          |
                   |                    |    ████                  |
                   |                    |       Tributario Agent   |
                   |                    |       ████               |
                   |                    |          Auditor Agent   |
                   |                    |          ████            |
                   |                    |             Integration  |
                   |                    |             ████         |
                   |                    |                Reports & Evaluation
                   |                    |                ████████ |
```

---

## 📅 Vista Semanal Detallada

### FASE 1: FUNDAMENTOS (4 semanas)

```
┌─────────────────────────────────┐
│ SEMANA 1: Setup & Config (1d)   │
├─────────────────────────────────┤
│ ✓ pyproject.toml                │
│ ✓ .env setup                    │
│ ✓ app/core/config.py            │
│ ✓ app/core/logger.py            │
│ Duration: 1 día                 │
└─────────────────────────────────┘

┌─────────────────────────────────┐
│ SEMANA 2: Database (2-3d)       │
├─────────────────────────────────┤
│ ✓ app/core/database.py          │
│ ✓ ORM Models (5 tablas)         │
│ ✓ Alembic setup                 │
│ ✓ test.db created               │
│ Duration: 3 días                │
└─────────────────────────────────┘

┌─────────────────────────────────┐
│ SEMANA 3: Vector DB & RAG (3-4d)│
├─────────────────────────────────┤
│ ✓ ChromaDB setup                │
│ ✓ PUC base data                 │
│ ✓ RAG search functions          │
│ ✓ Tests RAG                     │
│ Duration: 4 días                │
└─────────────────────────────────┘

┌─────────────────────────────────┐
│ SEMANA 4: Schemas (2d)          │
├─────────────────────────────────┤
│ ✓ Pydantic models (15+)         │
│ ✓ Validadores custom            │
│ ✓ Error handlers                │
│ ✓ Tests schemas                 │
│ Duration: 2 días                │
└─────────────────────────────────┘
```

**META SEMANA 4:** Database funcional + Vector DB poblado + Schemas validados

---

### FASE 2: APIs & AGENTE PILOTO (5 semanas)

```
┌─────────────────────────────────┐
│ SEMANA 5: LangGraph (3d)        │
├─────────────────────────────────┤
│ ✓ app/agents/state.py           │
│ ✓ app/agents/graph.py           │
│ ✓ Supervisor logic              │
│ ✓ Gemini client                 │
│ Duration: 3 días                │
└─────────────────────────────────┘

┌─────────────────────────────────┐
│ SEMANA 6: Ingest Agent (4d)     │
├─────────────────────────────────┤
│ ✓ PDF/Excel parsing             │
│ ✓ Gemini vision prompt          │
│ ✓ RawTransaction extraction     │
│ ✓ Tests ingest                  │
│ Duration: 4 días                │
└─────────────────────────────────┘

┌─────────────────────────────────┐
│ SEMANA 7: Ingest API (3d)       │
├─────────────────────────────────┤
│ ✓ POST /ingest/upload           │
│ ✓ GET /ingest/{id}              │
│ ✓ File storage                  │
│ ✓ E2E test upload               │
│ Duration: 3 días                │
└─────────────────────────────────┘

┌─────────────────────────────────┐
│ SEMANA 8: Job Tracking (4d)     │
├─────────────────────────────────┤
│ ✓ POST /process/accounting      │
│ ✓ GET /process/status           │
│ ✓ GET /process/result           │
│ ✓ Async job execution           │
│ Duration: 4 días                │
└─────────────────────────────────┘

┌─────────────────────────────────┐
│ SEMANA 9: Full Pipeline (4d)    │
├─────────────────────────────────┤
│ ✓ Supervisor enrutamiento       │
│ ✓ Error handling & retries      │
│ ✓ agent_log tracking            │
│ ✓ E2E pipeline test             │
│ Duration: 4 días                │
└─────────────────────────────────┘
```

**META SEMANA 9:** Upload PDF → Recibir resultado contabilizado

---

### FASE 3: AGENTES ESPECIALIZADOS (6 semanas)

```
┌─────────────────────────────────┐
│ SEMANA 10: Contador (5d)        │
├─────────────────────────────────┤
│ ✓ PUC assignment logic          │
│ ✓ RAG search_puc + history      │
│ ✓ Pydantic validation           │
│ ✓ Tests PUC assignment          │
│ Duration: 5 días                │
└─────────────────────────────────┘

┌─────────────────────────────────┐
│ SEMANA 11: RAG Expandido (5d)   │
├─────────────────────────────────┤
│ ✓ Estatuto Tributario (50 arts) │
│ ✓ Ley 43/1990 (principios)      │
│ ✓ Hybrid search (BM25 + VEC)    │
│ ✓ Tests RAG completo            │
│ Duration: 5 días                │
└─────────────────────────────────┘

┌─────────────────────────────────┐
│ SEMANA 12: Tributario (5d)      │
├─────────────────────────────────┤
│ ✓ Tax calculators (determ.)     │
│ ✓ Retefuente, ReteICA, IVA      │
│ ✓ Normative references          │
│ ✓ Tests tax calculation         │
│ Duration: 5 días                │
└─────────────────────────────────┘

┌─────────────────────────────────┐
│ SEMANA 13: Auditor (5d)         │
├─────────────────────────────────┤
│ ✓ Double-entry validation       │
│ ✓ Duplicate detection           │
│ ✓ Business logic checks         │
│ ✓ Retry logic on reject         │
│ Duration: 5 días                │
└─────────────────────────────────┘

┌─────────────────────────────────┐
│ SEMANA 14: Integración (5d)     │
├─────────────────────────────────┤
│ ✓ E2E tests completos           │
│ ✓ Performance testing           │
│ ✓ Error scenarios               │
│ ✓ Code review & refactoring     │
│ Duration: 5 días                │
└─────────────────────────────────┘

┌─────────────────────────────────┐
│ SEMANA 15: Reportes (5d)        │
├─────────────────────────────────┤
│ ✓ GET /reports/* (Balance P&L)  │
│ ✓ GET /tax/* (IVA, retenciones) │
│ ✓ GET /evaluation/run (métricas)│
│ ✓ Deploy checklist              │
│ Duration: 5 días                │
└─────────────────────────────────┘
```

**META SEMANA 15:** Sistema 100% funcional, listo para MVP

---

## 💎 Milestones Criticos

```
SEMANA 4 ✓
    ├─ Database funcional
    ├─ ChromaDB con datos
    └─ Schemas validados
    
SEMANA 7 ✓
    ├─ Primer endpoint funcional
    ├─ PDFs pueden subirse
    └─ Datos extraídos

SEMANA 9 ✓
    ├─ Pipeline básico E2E
    ├─ Upload → Resultado contabilizado
    └─ Agent_log muestra razonamiento

SEMANA 12 ✓
    ├─ Impuestos calculados
    ├─ Normativa consultada
    └─ Asiento contable generado

SEMANA 15 ✓ 🎉
    ├─ Todas APIs funcionan
    ├─ Evaluación completa
    └─ MVP lista para usuario final
```

---

## 📊 Carga de Trabajo Estimada (Horas)

```
SEMANA 1   [████░░░░░░] 12h  Setup basado
SEMANA 2   [██████░░░░] 20h  Schemas complejos
SEMANA 3   [██████░░░░] 18h  RAG, ChromaDB
SEMANA 4   [████░░░░░░] 14h  Validaciones

SEMANA 5   [██████░░░░] 18h  LangGraph learning curve
SEMANA 6   [██████░░░░] 20h  Primer agente (OCR)
SEMANA 7   [████░░░░░░] 16h  API básica
SEMANA 8   [██████░░░░] 18h  Job tracking, async
SEMANA 9   [████░░░░░░] 14h  Integration

SEMANA 10  [██████░░░░] 20h  Contador (PUC logic)
SEMANA 11  [██████░░░░] 18h  RAG expandido
SEMANA 12  [██████░░░░] 18h  Tributario (taxes)
SEMANA 13  [██████░░░░] 18h  Auditor (validation)
SEMANA 14  [████░░░░░░] 14h  Integration, tests
SEMANA 15  [████░░░░░░] 12h  Reportes (read-only)

TOTAL: ≈ 260 horas ≈ 6.5 semanas full-time (1 dev)
       ≈ 3.25 meses con 1 dev dedicado
```

---

## 👥 Recursos Necesarios

### Recomendado
- **1 Backend Developer** (dedicado 100%, semanas 1-15)
- **0.5 Backend Developer** (ayuda en semanas 10-15 con agentes)
- **1 Frontend Developer** (inicia semana 5, consume APIs)
- **1 Product Manager / Scrum Master** (tracking)

### Mínimo viable
- **1 Backend Developer** (dedicado 100%)
- **1 Frontend Developer** (part-time, semana 5+)

### Ideal
- **2 Backend Developers** (principal + secundario)
- **1 Frontend Developer**
- **1 QA/Tester**
- **1 PM/Scrum Master**

---

## 🎯 Dependencias Entre Fases

```
INICIO
  │
Fase 1 ──────────────────┐
  │                      │
  └─────────────────────────► Fase 2 START
                         │
  (Config, DB, RAG         ├─► Fase 2 (APIs, Ingest)
   deben estar listos)     │
                           └─ Fase 1 MUST complete antes Fase 2
                           
                          Fase 2 ──────────────────┐
                            │                      │
                            └─────────────────────────► Fase 3 START
                           
                          (APIs y Job tracking
                           deben estar listos)
                           
                                              Fase 3
                                              (Agentes)
                                              
                                              Fase 3 ──► MVP LISTA
```

---

## 📈 Progreso Esperado por Semana

### Gráfico de Funcionalidad

```
        FASE 1        |FASE 2         |     FASE 3
100%┐               /│               /
    │              / │              /
 75%│             /  │             /
    │            /   │       /\   /
 50%│           /    │      /  \ /
    │          /     │     /    X
 25%│         /      │    /    / \
    │        /       │   /    /   \
  0%└───────────────────────────────────
    1  2  3  4  5  6  7  8  9 10 11 12 13 14 15
                    SEMANA

    ████ = Implementación
    /──\ = Testing
    ──X─ = Integration
```

---

## ✅ Go/No-Go Criteria

### End of Semana 4 (Fase 1)
```
✓ Database system working
✓ Vector DB with 300+ items indexed
✓ Schemas pydantic validated
✓ No critical bugs
✓ 90%+ test coverage
→ DECISION: Go to Fase 2 (expected: GO)
```

### End of Semana 9 (Fase 2)
```
✓ upload PDF → extracted data (accuracy 80%+)
✓ All async jobs complete in <5min
✓ Agent_log shows reasoning
✓ E2E pipeline test passes
→ DECISION: Go to Fase 3 (expected: GO)
```

### End of Semana 15 (Fase 3)
```
✓ All 10+ endpoints functional
✓ 7 evaluation metrics > 90%
✓ No security issues
✓ Zero uncaught exceptions
→ DECISION: Release MVP (expected: GO)
```

---

## 🔄 Iteración vs Waterfall

This plan is **sequential** (Fase 1 → 2 → 3) but within each phase you can have **sprints**:

```
Fase 1:  4 semanas = 1 "sprint"
         Deliverable: Infrastructure ready

Fase 2:  5 semanas = 5 "sprints" (1 semanal)
         Sprint 1: APIs setup
         Sprint 2: Ingest Agent
         Sprint 3: Ingest API
         Sprint 4: Job tracking
         Sprint 5: Integration
         
Fase 3:  6 semanas = 6 "sprints" (1 semanal)
         Sprint 1: Contador
         Sprint 2: RAG
         Sprint 3: Tributario
         Sprint 4: Auditor
         Sprint 5: Integration
         Sprint 6: Reportes
```

---

## 🎯 Success = 15 Semanas

**Si empiezas hoy (Semana 18 de Feb 2026):**
- Semana 4: 18 Marzo
- Semana 9: 22 Abril
- Semana 15: 3 Junio 2026

**Para el verano tú ya tendrás:**
- ✅ Sistema funcionando
- ✅ Usuarios usando la plataforma
- ✅ Datos reales siendo procesados
- ✅ Histórico de decisiones disponible

---

**¿Listo para empezar?** → [Ir a PLAN_IMPLEMENTACION.md](PLAN_IMPLEMENTACION.md)

**¿No claro el timeline?** → [Ver INDICE.md](INDICE.md)
