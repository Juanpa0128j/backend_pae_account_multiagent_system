# 🏗️ Agente Piloto - Referencia Técnica Rápida

## Estructura del Código

```
app/agents/
├── state.py
│   └── AgentState (TypedDict)
│       ├── file_path: str
│       ├── raw_text: str
│       ├── interpreted_data: dict
│       ├── result: dict
│       └── error: Optional[str]
│
├── graph.py
│   ├── create_agent_graph() → StateGraph
│   │   ├── Supervisor → Ingesta → END
│   │   └── compile()
│   │
│   └── invoke_agent(file_path) → dict
│
├── supervisor.py
│   └── supervisor_node(state) → state
│       ├── Valida file_path existe
│       ├── Valida que sea .pdf
│       └── Pasa a ingesta o error
│
└── ingest_agent.py
    └── ingest_node(state) → state
        ├── extract_text_from_pdf(file_path)
        ├── gemini_client.extract_receipt_data(text)
        ├── Formatea resultado final
        └── Retorna state con result lleno

app/core/
└── gemini_client.py
    └── GeminiClient
        ├── __init__(api_key, model)
        └── extract_receipt_data(text) → dict
            ├── Envía prompt a Gemini
            ├── Parsea respuesta JSON
            └── Retorna: {fecha, monto, concepto, ...}

app/services/
└── pdf_processor.py
    ├── extract_text_from_pdf(file_path) → str
    └── save_uploaded_file(content, dest) → str

app/api/v1/
└── ingest.py
    ├── save_temp_file(content, name) → str
    └── upload_file(file) → IngestResponse
        ├── Valida PDF
        ├── Guarda en /tmp/
        ├── invoke_agent(path)
        └── Limpia /tmp/
```

## Flujo de Datos

```
┌─────────────────────────────────────────────────────────────┐
│ Client                                                      │
└───────────────────┬─────────────────────────────────────────┘
                    │ POST /api/v1/ingest/upload
                    │ [PDF file]
                    ▼
        ┌───────────────────────┐
        │ ingest.py/upload_file │
        │                       │
        │ 1. Valida PDF         │
        │ 2. Guarda temp        │
        │ 3. invoke_agent()     │
        │ 4. Limpia temp        │
        │ 5. Return respuesta   │
        └───────────┬───────────┘
                    │
                    ▼
        ┌───────────────────────────┐
        │ graph.py/invoke_agent()   │
        │                           │
        │ AgentState inicial:       │
        │ {file_path, raw_text="",  │
        │  interpreted_data={},     │
        │  result={}, error=None}   │
        └───────────┬───────────────┘
                    │
        ┌───────────▼──────────────┐
        │ Supervisor Node          │
        │                          │
        │ ✓ file_path existe?      │
        │ ✓ es .pdf?               │
        │ → Pasa a Ingesta         │
        └───────────┬──────────────┘
                    │
        ┌───────────▼──────────────────────┐
        │ Ingest Node                      │
        │                                  │
        │ 1. extract_text_from_pdf()       │
        │    ↓ PyPDF2 reads PDF            │
        │    ↓ raw_text = "..."            │
        │                                  │
        │ 2. Gemini extraction             │
        │    ↓ GeminiClient.extract()      │
        │    ↓ Prompt: "Extract fecha..." │
        │    ↓ Response: JSON              │
        │    ↓ interpreted_data filled     │
        │                                  │
        │ 3. Format result                 │
        │    ↓ result = {                  │
        │         process_id: uuid4(),     │
        │         status: "completed",     │
        │         data: interpreted_data,  │
        │         message: "..."           │
        │       }                          │
        │                                  │
        │ 4. Return state                  │
        └───────────┬──────────────────────┘
                    │
        ┌───────────▼─────────────┐
        │ Graph returns final_state│
        │                         │
        │ final_state["result"]   │
        └───────────┬─────────────┘
                    │
        ┌───────────▼──────────────────┐
        │ ingest.py maps to Response   │
        │                              │
        │ IngestResponse(              │
        │   message=result["message"], │
        │   ingest_id=result["id"],    │
        │   status=result["status"]    │
        │ )                            │
        └───────────┬──────────────────┘
                    │ 200 OK + JSON
                    ▼
        ┌───────────────────────┐
        │ Client receives       │
        │ {message, ingest_id,  │
        │  status}              │
        └───────────────────────┘
```

## Prompt para Gemini

```
Eres un experto en lectura de recibos y facturas.

Texto extraído del documento:
---
{raw_text}
---

Extrae la siguiente información en JSON válido 
(responde SOLO JSON, sin explicación):

{
  "fecha": "YYYY-MM-DD o null",
  "monto": 0.00 (número),
  "concepto": "descripción del pago",
  "beneficiario": "quien recibe",
  "empresa": "empresa/banco emisor",
  "referencia": "número de transacción o null",
  "tipo_documento": "recibo|factura|extracto|otro"
}
```

## Configuración Gemini

- **Model:** gemini-2.5-flash
- **Temperature:** 0.0 (deterministic)
- **Max tokens:** 512
- **Rate limit:** 15 RPM (free tier)

## Variables de Entorno

```
# .env
GEMINI_API_KEY=sk_live_... 
GEMINI_MODEL=gemini-2.5-flash
```

## Testing

### Unit: Gemini Client
```python
from app.core.gemini_client import GeminiClient

client = GeminiClient()
result = client.extract_receipt_data("Fecha: 2026-02-18...")
assert "fecha" in result
assert "monto" in result
```

### Integration: Full Agent
```python
from app.agents.graph import invoke_agent

result = invoke_agent("test_recibo.pdf")
assert result["status"] == "completed"
assert "data" in result
```

### E2E: API
```bash
curl -X POST http://localhost:8000/api/v1/ingest/upload \
  -F "file=@recibo.pdf"
```

## Error Handling

| Situación | Error | HTTP | Acción |
|-----------|-------|------|--------|
| No PDF | ValueError | 400 | Rechazar upload |
| PDF no existe | FileNotFoundError | 400 | Reject |
| PDF vacío | ValueError | 400 | Reject |
| Gemini fail | Exception | 500 | Log + return error |
| JSON inválido | JSONDecodeError | 500 | Log + return error |

## Performance

- **PDF extraction:** ~1-2 seg (pequeño)
- **Gemini call:** ~3-5 seg
- **Total por recibo:** ~5-7 seg
- **Rate limit:** 15 req/min (Gemini free)

## Ejemplos de Respuesta

### Success 200
```json
{
  "message": "Receipt/invoice successfully processed",
  "ingest_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed"
}
```

### Error 400
```json
{
  "detail": "Only PDF files are accepted"
}
```

### Error 500
```json
{
  "detail": "Error processing file: Invalid JSON from Gemini"
}
```

## Dependencias

```
langraph>=0.0.27          # StateGraph
google-generativeai>=0.3.0 # Gemini API
pypdf>=6.7.0              # PDF reading
pydantic>=2.0             # Validation
python-dotenv>=1.0        # .env loading
```

## Próximas Features (Post-MVP)

- [ ] Excel support (app/services/excel_processor.py)
- [ ] DB persistence (app/models/database.py + sqlalchemy)
- [ ] Job tracking (app/services/jobs.py)
- [ ] Async processing (Celery/RQ)
- [ ] RAG search (app/services/rag.py + ChromaDB)
- [ ] Additional agents (Contador, Tributario, Auditor)

---

**Agente Piloto v0.1.0** ✨
