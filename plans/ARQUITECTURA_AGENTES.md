# 🤖 Arquitectura de Agentes - Detalle Técnico

**Objetivo:** Explicar exactamente cómo funcionan los agentes con LangGraph + Gemini 2.5 Flash

---

## 1. Conceptos Clave

### StateGraph (LangGraph)
```
╔═══════════════════════════════════════════════════════════╗
║              StateGraph (Máquina de Estados)              ║
╠═══════════════════════════════════════════════════════════╣
║                                                           ║
║  Nodo 1: supervisor              Nodo 2: ingest          ║
║  ┌──────────────────┐            ┌──────────────────┐    ║
║  │ Estado: INIT     │ ──route──► │ Estado: INGEST   │    ║
║  │ Acción: decidir  │            │ Acción: OCR      │    ║
║  └──────────────────┘            └──────────────────┘    ║
║          ▲                               │                ║
║          │                               ├──route──┐      ║
║          │                               │         │      ║
║  Nodo 5: auditor◄─────────────────────────┘      Nodo 3: contador
║  ┌──────────────────┐            ┌──────────────────┐    ║
║  │ Estado: AUDIT    │            │ Estado: CONTADOR │    ║
║  │ Acción: validar  │            │ Acción: PUC      │    ║
║  └──────────────────┘            └──────────────────┘    ║
║          │                               │                ║
║          │             Nodo 4: tributario│                ║
║          │             ┌──────────────────┐              ║
║          └──route─────►│ Estado: TRIBUTARIO│              ║
║                        │ Acción: impuestos │              ║
║                        └──────────────────┘              ║
║                                                           ║
╚═══════════════════════════════════════════════════════════╝
```

### AgentState (TypedDict)
```python
# Compartida entre TODOS los nodos

class AgentState(TypedDict):
    # Entrada
    ingest_id: str
    file_path: str
    raw_transactions: List[RawTransaction]
    
    # Procesamiento
    classified_txns: List[Transaction]  # Con PUC
    taxed_txns: List[TransactionWithTax]  # Con impuestos
    journal_entries: List[JournalEntry]  # Asiento final
    
    # Control
    current_stage: str  # "init" → "ingest" → "contador" → "tributario" → "auditor" → "posted"
    errors: List[str]
    agent_log: List[Dict]  # Timeline de agentes
```

---

## 2. Flujo de Ejecución Detallado

### Usuario sube PDF → Entrada en StateGraph

```
┌─────────────────────────────────────────────────────────┐
│ 1. POST /ingest/upload → Guardar archivo temporal      │
│ 2. Crear AgentState inicial:                           │
│    - ingest_id: "ing_123"                              │
│    - file_path: "storage/uploads/ing_123.pdf"          │
│    - current_stage: "init"                             │
│    - raw_transactions: []                              │
│    - agent_log: []                                     │
│                                                        │
│ 3. graph.invoke(state) → Inicia en Supervisor         │
└─────────────────────────────────────────────────────────┘
```

### Paso 1: Supervisor Decide

```
┌─────────────────────────────────────────────────────────┐
│ NODO: supervisor                                        │
├─────────────────────────────────────────────────────────┤
│                                                        │
│ Lógica:                                                │
│  if state["current_stage"] == "init":                  │
│      logger.info("Init → Ingest")                      │
│      state["current_stage"] = "ingest"                 │
│      return {"next_agent": "ingest"}                   │
│                                                        │
│ Estado salida:                                         │
│  ✓ current_stage = "ingest"                           │
│  ✓ agent_log += [{agent: supervisor, action: route}]  │
│                                                        │
└─────────────────────────────────────────────────────────┘
```

### Paso 2: Agente Ingesta (Ocurre en Background)

```
┌─────────────────────────────────────────────────────────┐
│ NODO: ingest                                            │
├─────────────────────────────────────────────────────────┤
│                                                        │
│ def ingest_node(state: AgentState) → AgentState:     │
│                                                        │
│   file_path = state["file_path"]  # "storage/...pdf"  │
│   file_type = detect_file_type(file_path)             │
│                                                        │
│   if file_type == "pdf":                              │
│     raw_text = read_pdf(file_path)                    │
│   elif file_type == "excel":                          │
│     raw_text = read_excel(file_path)                  │
│                                                        │
│   # USAR GEMINI con Vision                            │
│   prompt = f"""                                       │
│   Extrae de este documento:                           │
│   - Fecha                                              │
│   - NIT emisor                                         │
│   - NIT receptor                                       │
│   - Total                                              │
│   - Items (descripción, valor)                         │
│                                                        │
│   Retorna JSON: {fecha, nit_emisor, nit_receptor,     │
│                  total, items}                         │
│                                                        │
│   Documento: {raw_text}                               │
│   """                                                  │
│                                                        │
│   response = call_gemini(prompt)                      │
│   extracted_json = parse_json(response.text)          │
│                                                        │
│   # VALIDAR con Pydantic                              │
│   raw_txn = RawTransaction(**extracted_json)          │
│                                                        │
│   # ACTUALIZAR estado                                 │
│   state["raw_transactions"].append(raw_txn)           │
│   state["current_stage"] = "ingest"  # Listo          │
│   state["agent_log"].append({                         │
│       "agent": "ingest",                              │
│       "fecha": raw_txn.fecha,                         │
│       "total": raw_txn.total,                         │
│       "status": "success"                             │
│   })                                                  │
│                                                        │
│   # GUARDAR en BD                                      │
│   db.create(TransactionPending(                        │
│       ingest_id=state["ingest_id"],                    │
│       raw_data=raw_txn.dict()                          │
│   ))                                                  │
│                                                        │
│   return state  # ← Devuelve estado actualizado        │
│                                                        │
└─────────────────────────────────────────────────────────┘
```

### Paso 3: Supervisor Enruta a Contador

```
┌─────────────────────────────────────────────────────────┐
│ NODO: supervisor (2ª vez)                              │
├─────────────────────────────────────────────────────────┤
│                                                        │
│ Lógica:                                                │
│  if state["current_stage"] == "ingest" and            │
│     len(state["raw_transactions"]) > 0:               │
│      state["current_stage"] = "proceso"               │
│      return {"next_agent": "contador"}                │
│                                                        │
└─────────────────────────────────────────────────────────┘
```

### Paso 4: Agente Contador (Assigns PUC)

```
┌─────────────────────────────────────────────────────────┐
│ NODO: contador                                          │
├─────────────────────────────────────────────────────────┤
│                                                        │
│ def contador_node(state: AgentState) → AgentState:   │
│                                                        │
│   txn = state["raw_transactions"][0]  # Primera txn    │
│                                                        │
│   # Tool 1: Search PUC                                 │
│   puc_results = vectordb.search_normativo(            │
│       f"Gasto: {txn.descripcion}"                     │
│   )                                                   │
│   # Retorna: [{cuenta: "5195", desc: "Gastos div"}]  │
│                                                        │
│   # Tool 2: Search History                            │
│   history = db.query(TransactionPosted).filter(       │
│       nit_emisor=txn.nit_emisor                       │
│   ).limit(5)                                          │
│   # Retorna: [prev_txn1, prev_txn2, ...]             │
│                                                        │
│   # LLAMAR GEMINI CON CONTEXTO                        │
│   context = f"""                                      │
│   RAG PUC: {puc_results}                              │
│   Histórico del proveedor: {history}                  │
│   """                                                  │
│                                                        │
│   prompt = f"""                                       │
│   Eres contador colombiano experto.                    │
│                                                        │
│   Classifica esta transacción según PUC:              │
│   Tipo: {txn.descripcion}                              │
│   Monto: ${txn.total}                                 │
│   Proveedor: {txn.nit_emisor}                         │
│                                                        │
│   {context}                                            │
│                                                        │
│   Retorna JSON: {{                                    │
│     "cuenta_puc": "XXXX",                             │
│     "descripcion": "...",                             │
│     "confianza": 0.95,                                │
│     "razonamiento": "..."                             │
│   }}                                                  │
│   """                                                  │
│                                                        │
│   response = call_gemini(prompt, system_prompt=...)   │
│   classification = parse_json(response.text)          │
│                                                        │
│   # VALIDACIÓN DETERMINÍSTICA                         │
│   validated_puc = validate_puc(classification["cuen-  │
│       ta_puc"])  # Busca en tabla PUC válidos         │
│   if not validated_puc:                               │
│       state["errors"].append(f"PUC inválido: ...")    │
│       return state  # Rechazar y subir a supervisor   │
│                                                        │
│   # ACTUALIZAR estado                                 │
│   classified_txn = Transaction(                       │
│       **txn.dict(),                                   │
│       cuenta_puc=validated_puc.codigo,                │
│       puc_descripcion=validated_puc.descripcion       │
│   )                                                   │
│   state["classified_txns"].append(classified_txn)     │
│   state["agent_log"].append({                         │
│       "agent": "contador",                            │
│       "cuenta": validated_puc.codigo,                 │
│       "razonamiento": classification["razonamiento"], │
│       "status": "success"                             │
│   })                                                  │
│                                                        │
│   return state                                        │
│                                                        │
└─────────────────────────────────────────────────────────┘
```

### Paso 5: Agente Tributario (Calculates Taxes)

```
┌─────────────────────────────────────────────────────────┐
│ NODO: tributario                                        │
├─────────────────────────────────────────────────────────┤
│                                                        │
│ def tributario_node(state: AgentState) → AgentState: │
│                                                        │
│   txn = state["classified_txns"][0]                   │
│                                                        │
│   # Tool 1: Funciones DETERMINÍSTICAS (NO LLM)        │
│   retefuente = calc_retefuente(                       │
│       valor=txn.total,                                │
│       tipo_proveedor=detect_type(txn.nit_emisor)     │
│   )                                                   │
│   # Ejemplo: 1500000 * 0.11 = 165000                 │
│                                                        │
│   reteica = calc_reteica(                             │
│       valor=txn.total,                                │
│       actividad=get_actividad(txn.nit_emisor)        │
│   )                                                   │
│   # Ejemplo: 1500000 * 0.0069 = 10350                │
│                                                        │
│   iva = calc_iva(                                     │
│       valor=txn.total,                                │
│       tipo_bien=infer_tipo(txn.descripcion)          │
│   )                                                   │
│   # Ejemplo: 1500000 * 0.19 = 285000                 │
│                                                        │
│   # Tool 2: Search Tax Law                            │
│   tax_references = vectordb.search_normativo(         │
│       "retención en la fuente servicios"              │
│   )                                                   │
│   # Retorna: [Art. 383 ET, Decreto 2048/1992, ...]  │
│                                                        │
│   # USAR GEMINI PARA JUSTIFICAR (NO CALCULAR)        │
│   prompt = f"""                                       │
│   Eres experto tributario colombiano.                 │
│                                                        │
│   Esta transacción requiere:                          │
│   - Retefuente: ${retefuente}  (11% servicios)       │
│   - ReteICA: ${reteica}  (0.69% cali)                │
│   - IVA: ${iva}  (19% tarifa general)                │
│                                                        │
│   Normativa aplicable:                                │
│   {tax_references}                                    │
│                                                        │
│   Cita los artículos que justifican estas tasas.      │
│   Retorna: {{                                         │
│     "referencias": ["Art. 383 ET", ...],              │
│     "justificacion": "..."                            │
│   }}                                                  │
│   """                                                  │
│                                                        │
│   response = call_gemini(prompt)                      │
│   justification = parse_json(response.text)           │
│                                                        │
│   # ACTUALIZAR estado                                 │
│   taxed_txn = TransactionWithTax(                     │
│       **txn.dict(),                                   │
│       retefuente=retefuente,                          │
│       reteica=reteica,                                │
│       iva=iva,                                        │
│       tax_references=justification["referencias"]    │
│   )                                                   │
│   state["taxed_txns"].append(taxed_txn)              │
│   state["agent_log"].append({                         │
│       "agent": "tributario",                          │
│       "retefuente": retefuente,                       │
│       "reteica": reteica,                             │
│       "iva": iva,                                     │
│       "referencias": justification["referencias"],    │
│       "status": "success"                             │
│   })                                                  │
│                                                        │
│   return state                                        │
│                                                        │
└─────────────────────────────────────────────────────────┘
```

### Paso 6: Agente Auditor (Valida Integridad)

```
┌─────────────────────────────────────────────────────────┐
│ NODO: auditor                                           │
├─────────────────────────────────────────────────────────┤
│                                                        │
│ def auditor_node(state: AgentState) → AgentState:    │
│                                                        │
│   txn = state["taxed_txns"][0]                        │
│                                                        │
│   # GENERAR ASIENTO CONTABLE (determinístico)        │
│   journal_entries = [                                 │
│       JournalEntry(cuenta="5195", debito=txn.total), │
│       JournalEntry(cuenta="2408", credito=txn.ret... │
│       JournalEntry(cuenta="2365", credito=txn.ret... │
│       JournalEntry(cuenta="1110", credito=(txn.tot-  │
│           retefuente-reteica))                        │
│   ]                                                   │
│                                                        │
│   # VALIDACIÓN 1: Partida Doble (DETERMINÍSTICO)    │
│   total_debito = sum(e.debito for e in journal_...)  │
│   total_credito = sum(e.credito for e in journal_...) │
│   assert total_debito == total_credito, "NO cuadra"  │
│   # Ejemplo: 1.5M débito == (285K + 52K + 10K + 1.1M) │
│                                                        │
│   # VALIDACIÓN 2: Duplicados (DETERMINÍSTICO)         │
│   duplicates = db.query(TransactionPosted).filter(   │
│       nit_emisor=txn.nit_emisor,                      │
│       total=txn.total,                                │
│       fecha >= date(txn.fecha) - timedelta(days=3)   │
│   ).limit(1)                                          │
│   if duplicates:                                      │
│       state["should_reject"] = True                   │
│       state["rejection_reason"] = "Duplicado probable"│
│       return state                                    │
│                                                        │
│   # VALIDACIÓN 3: Lógica de Negocio (LLM-assisted)  │
│   if txn.total > 10_000_000:  # Monto inusual        │
│       prompt = f"""                                   │
│       Analiza esta transacción inusual:              │
│       - {txn.descripcion}                             │
│       - Total: ${txn.total}                           │
│       - Proveedor: {txn.nit_emisor}                   │
│                                                        │
│       ¿Hay algo sospechoso? Retorna:                  │
│       {{                                              │
│         "es_anomalia": true|false,                    │
│         "razon": "..."                                │
│       }}                                              │
│       """                                              │
│       response = call_gemini(prompt)                  │
│       result = parse_json(response.text)              │
│       if result["es_anomalia"]:                       │
│           state["should_reject"] = True              │
│           state["rejection_reason"] = result["razon"]│
│           return state                               │
│                                                        │
│   # SI PASÓ TODO: Aprobar                            │
│   state["journal_entries"] = journal_entries         │
│   state["current_stage"] = "posted"                  │
│   state["should_reject"] = False                     │
│   state["agent_log"].append({                         │
│       "agent": "auditor",                            │
│       "validations": {                                │
│           "double_entry": "✓",                       │
│           "duplicates": "✓",                         │
│           "business_logic": "✓"                      │
│       },                                              │
│       "status": "approved"                            │
│   })                                                  │
│                                                        │
│   # GUARDAR RESULTADO en BD                           │
│   db.create(TransactionPosted(                        │
│       transaction_pending_id=state["ingest_id"],      │
│       cuenta_puc=txn.cuenta_puc,                      │
│       retefuente=txn.retefuente,                      │
│       reteica=txn.reteica,                            │
│       iva=txn.iva,                                    │
│       journal_entries=journal_entries,                │
│       status="posted",                                │
│       agent_log=state["agent_log"]                    │
│   ))                                                  │
│                                                        │
│   return state                                        │
│                                                        │
└─────────────────────────────────────────────────────────┘
```

---

## 3. Uso de Gemini 2.5 Flash

### Por qué Gemini?

| Característica | Importancia | Gemini Score |
|----------------|-------------|--------------|
| Context Window | 🟥🟥🟥 Crítica | 1M tokens (mejor) |
| Costo | 🟥🟥 Alto | Gratis tier (gen. 2) |
| OCR | 🟥 Importante | Excelente (vision) |
| Velocidad | 🟥 Importante | Flash = 2x rápido |
| Multimodal | 🟥 Importante | Pdf + imagen + texto |

### Patrones de Uso

**Pattern 1: Vision for PDF Reading**
```python
# Gemini puede leer PDFs completos (hasta 1M tokens)
# No necesitamos OCR externo
with open("factura.pdf", "rb") as f:
    file_content = f.read()

model = genai.GenerativeModel("gemini-2.5-flash")
response = model.generate_content([
    "Extract all fields from this invoice in JSON format",
    {"mime_type": "application/pdf", "data": file_content}
])
```

**Pattern 2: Retrieval-Augmented (RAG)**
```python
# Gemini recibe contexto del RAG
tax_law = vectordb.search_normativo("retención en la fuente")
previous_transactions = db.query_similar(nit=X)

prompt = f"""
Contexto legal: {tax_law}
Histórico: {previous_transactions}

Clasifica: {transaction}
"""

response = model.generate_content(prompt)
```

**Pattern 3: Deterministic + LLM Hybrid**
```python
# Cálculos determinísticos (Python)
retefuente = 1500000 * 0.11  # Siempre 165000

# Justificación del agente (Gemini)
prompt = f"Justifica por qué aplicamos ${retefuente} de retefuente"
response = model.generate_content(prompt)
```

---

## 4. Manejo de Errores y Retries

### Si Auditor Rechaza (Rejected)

```
┌─────────────────────────────────────────────────────────┐
│ AUDITOR rechaza por anomalía                            │
├─────────────────────────────────────────────────────────┤
│                                                        │
│ state["should_reject"] = True                          │
│ state["rejection_reason"] = "Monto inusual"            │
│                                                        │
│ → Supervisor vuelve a evaluar:                        │
│                                                        │
│ if state["should_reject"]:                             │
│     retry_count += 1                                   │
│     if retry_count < 3:                                │
│         state["current_stage"] = "proceso"             │
│         # Volver al Contador con feedback              │
│         state["agent_log"].append({                    │
│             "agent": "supervisor",                     │
│             "action": "retry_from_contador",           │
│             "reason": state["rejection_reason"]        │
│         })                                             │
│         return {"next": "contador"}  # Reintentar      │
│     else:                                              │
│         state["current_stage"] = "rejected"            │
│         return {"next": "END"}  # Humano revisa        │
│                                                        │
└─────────────────────────────────────────────────────────┘
```

---

## 5. Flujo Completo en Seudocódigo

```python
# Pseudo-código del flujo completo

state = AgentState(
    ingest_id="ing_123",
    file_path="storage/uploads/ing_123.pdf",
    current_stage="init",
    raw_transactions=[],
    classified_txns=[],
    taxed_txns=[],
    journal_entries=[],
    errors=[],
    agent_log=[]
)

# Ejecutar workflow
workflow = build_workflow()  # StateGraph con 5 nodos + supervisor

retry_count = 0
while state["current_stage"] != "posted" and retry_count < 3:
    state = workflow.invoke(state)
    
    if state["errors"]:
        retry_count += 1
        # Retry logic
    else:
        break

if state["current_stage"] == "posted":
    # ✅ Éxito
    response = {
        "process_id": "proc_456",
        "status": "completed",
        "journal_entries": state["journal_entries"],
        "agent_log": state["agent_log"]
    }
else:
    # ❌ Fallo - Requiere revisión humana
    response = {
        "process_id": "proc_456",
        "status": "failed",
        "rejection_reason": state["rejection_reason"],
        "agent_log": state["agent_log"]
    }
```

---

## 6. Monitoreo y Trazabilidad

### agent_log Completo (Ejemplo)

```json
{
  "process_id": "proc_456",
  "ingest_id": "ing_123",
  "agent_log": [
    {
      "timestamp": "2026-01-15T10:35:16Z",
      "agent": "supervisor",
      "action": "route",
      "decision": "ingest",
      "details": {}
    },
    {
      "timestamp": "2026-01-15T10:35:18Z",
      "agent": "ingest",
      "action": "extracted",
      "fecha": "2026-01-15",
      "nit_emisor": "800.123.456",
      "total": 1500000,
      "items_count": 1,
      "status": "success"
    },
    {
      "timestamp": "2026-01-15T10:35:22Z",
      "agent": "contador",
      "action": "classified",
      "cuenta_puc": "5195",
      "puc_desc": "Gastos diversos",
      "confidence": 0.95,
      "razonamiento": "Basado en histórico del proveedor",
      "status": "success"
    },
    {
      "timestamp": "2026-01-15T10:35:26Z",
      "agent": "tributario",
      "action": "calculated",
      "retefuente": 165000,
      "reteica": 10350,
      "iva": 285000,
      "referencias": ["Art. 383 ET", "Decreto 2048/1992"],
      "status": "success"
    },
    {
      "timestamp": "2026-01-15T10:35:30Z",
      "agent": "auditor",
      "action": "validated",
      "double_entry": "✓",
      "duplicates": "✓",
      "business_logic": "✓",
      "status": "approved"
    }
  ]
}
```

---

## 7. Optimizaciones Críticas

### ✅ DO's

- ✅ Separar LLM calls de cálculos determinísticos
- ✅ Usar herramientas (tools) para acceso a BD/RAG
- ✅ Retornar JSON estructurado (Pydantic parser)
- ✅ Logging en CADA paso (debug importante)
- ✅ Timeout por agente (5 min máx)

### ❌ DON'Ts

- ❌ Dejar que LLM calcule porcentajes
- ❌ Llamar Gemini sin contexto (RAG)
- ❌ Ignorar Pydantic validations
- ❌ Retries infinitos
- ❌ Prompts sin instrucciones claras (JSON, etc)

---

## 8. Ejemplo Real: Transacción Completa

### Input
```json
{
  "file": "factura_2026-01-15.pdf",
  "content": "ABC SAS, NIT 800.123.456... Total: $1,500,000.00"
}
```

### Process (Paso a Paso)

```
[Supervisor]
  ↓ "Init → Ingest"
[Ingesta]  (Gemini Vision)
  Raw: {fecha: 2026-01-15, nit: 800.123.456, total: 1500000}
  ↓
[Supervisor]
  ↓ "Ingest → Contador"
[Contador]  (Gemini LLM + RAG)
  Search PUC: "gastos, servicios"
  Search history: proveedor 800.123.456 → prev PUC: 5195
  Decision: cuenta_puc = 5195
  ↓
[Tributario]  (Python + Gemini)
  calc_retefuente(1500000, "servicios") = 165000
  calc_reteica(1500000, "cali") = 10350
  calc_iva(1500000, "bienes") = 285000
  Search law: "Art. 383 ET"
  ↓
[Auditor]  (Python + Gemini)
  Journal entries OK? ✓ (1.5M = 165K + 10.35K + 1.137M)
  Duplicados? ✓ (No hay)
  Anomalías? ✓ (Monto normal)
  → APPROVE
  ↓
[Database]
  TransactionPosted created
  Status: POSTED
  agent_log saved
```

### Output
```json
{
  "status": "completed",
  "cuenta_puc": "5195",
  "retefuente": 165000,
  "reteica": 10350,
  "iva": 285000,
  "journal_entries": [
    {"cuenta": "5195", "debito": 1500000, "credito": 0},
    {"cuenta": "2408", "debito": 0, "credito": 165000},
    {"cuenta": "2365", "debito": 0, "credito": 10350},
    {"cuenta": "1110", "debito": 0, "credito": 1324650}
  ],
  "agent_log": [...]
}
```

---

**Documento Técnico Completo.** Para implementación, ve a [GUIA_TECNICA_FASE1.md](GUIA_TECNICA_FASE1.md)

Última actualización: 2026-02-18
