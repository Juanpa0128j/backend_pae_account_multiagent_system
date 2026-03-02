# 🤖 Agente Piloto - Diseño Minimal

## Objetivo
**Procesar un recibo/factura PDF → Extraer datos bancarios/transaccionales → JSON**

## 1. Flujo del Agente

```
PDF Input
   ↓
[Supervisor] recibe archivo
   ↓
[Ingesta Agent] extrae texto
   ↓
[Gemini] interpreta campos
   ↓
JSON Output
```

## 2. Estructura Mínima

```
app/
├── agents/
│   ├── __init__.py
│   ├── state.py           ← AgentState (TypedDict)
│   ├── graph.py           ← StateGraph (Supervisor → Ingesta)
│   ├── supervisor.py      ← Nodo Supervisor
│   └── ingest_agent.py    ← Nodo Ingesta
├── services/
│   ├── __init__.py
│   └── pdf_processor.py   ← PyPDF utilities
├── core/
│   ├── __init__.py
│   └── gemini_client.py   ← Wrapper de Gemini
└── api/
    └── v1/
        └── ingest.py      ← Endpoint (existente, aplicaremos lógica real)
```

## 3. Definiciones

### 3.1 AgentState (state.py)
```python
class AgentState(TypedDict):
    # Input
    file_path: str                    # Ruta del PDF
    
    # Intermediate
    raw_text: str                     # Texto extraído por PyPDF
    interpreted_data: dict            # Datos interpretados por Gemini
    
    # Output
    result: dict                       # JSON final
    error: Optional[str]              # Mensaje de error
```

### 3.2 Nodes

**Supervisor Node**
```
Input: file_path
Logic:
  1. Valida que el PDF exista
  2. Envía a Ingesta con instrucción
Output: Pasa estado a Ingesta
```

**Ingesta Node**
```
Input: file_path, raw_text (vacío)
Logic:
  1. Abre PDF con PyPDF
  2. Extrae texto de todas las páginas
  3. Llama a Gemini con:
     - raw_text
     - prompt: "Extrae fecha, monto, concepto, beneficiario del recibo"
  4. Parsea respuesta JSON de Gemini
Output: 
  - raw_text relleno
  - interpreted_data con datos estructurados
  - Termina (end)
```

### 3.3 Prompt para Gemini (Ingesta)
```
Eres un experto en lectura de recibos y facturas.

Texto extraído del PDF:
---
{raw_text}
---

Extrae la siguiente información en JSON:
{
  "fecha": "YYYY-MM-DD o null",
  "monto": 0.00 (float),
  "concepto": "descripción del pago",
  "beneficiario": "quien recibe",
  "empresa": "empresa/banco emisor",
  "referencia": "número de transacción o null",
  "tipo_documento": "recibo|factura|extracto|otro"
}

Responde SOLO JSON válido, sin explicación.
```

## 4. Secuencia en Código

### 4.1 Llamada a la API
```bash
curl -X POST http://localhost:8000/api/v1/ingest/upload \
  -F "file=@recibo.pdf"

Response:
{
  "process_id": "uuid",
  "status": "completed",
  "data": {
    "fecha": "2026-02-18",
    "monto": 150000.00,
    "concepto": "Pago de servicios",
    ...
  }
}
```

### 4.2 Flujo Interno
```python
# main.py or ingest.py
from app.agents.graph import create_agent_graph

graph = create_agent_graph()

# Ejecutar
result = graph.invoke({
    "file_path": "/tmp/recibo.pdf",
    "raw_text": "",
    "interpreted_data": {},
    "result": {},
    "error": None
})

# Devolver result
return result["result"]
```

## 5. Dependencias Necesarias (Mínimas)

```toml
langraph = "^0.0.27"          # StateGraph
langchain-google-genai = "^0.1.0" # Gemini API (via LangChain)
pypdf = "^6.7.0"              # PDF reading (ya existe)
pydantic = "^2.0"             # Validation
python-dotenv = "^1.0"        # .env
```

## 6. Configuración Mínima (.env)

```
GEMINI_API_KEY=xxx
GEMINI_MODEL=gemini-2.5-flash
```

## 7. Puntos de Decisión

| Aspecto | Opción | Razón |
|--------|--------|-------|
| **Errores en PyPDF** | Try/except + reraise | JSON roto → fácil de debuggear |
| **Gemini timeout** | 30 segundos | Recibo simple < 5s normalmente |
| **Validación JSON** | Pydantic model | Rechaza respuestas inválidas |
| **Storage** | No persistir PDF | POC, no necesita DB |
| **Async** | No async aún | Sincrónico más simple para MVP |

## 8. Ahora vs. Después

### MVP Piloto (Ahora)
- ✅ PDF → JSON
- ✅ Sin base de datos
- ✅ Bez RAG
- ✅ Sin doble entrada
- ✅ 1 endpoint real

### Post-MVP (Plan semanas 2-4)
- ➕ Base de datos (TransactionPending)
- ➕ Job tracking (async)
- ➕ Persistencia de PDFs
- ➕ Multiple document types

## 9. Testing Manual

```bash
# 1. Crear recibo simple (PDF)
# Generar PDF de prueba con:
from reportlab.pdfgen import canvas
pdf = canvas.Canvas("test_recibo.pdf")
pdf.drawString(100, 750, "RECIBO DE PAGO")
pdf.drawString(100, 700, "Fecha: 2026-02-18")
pdf.drawString(100, 650, "Monto: $150.000")
pdf.showPage()
pdf.save()

# 2. Hacer request
curl -X POST http://localhost:8000/api/v1/ingest/upload \
  -F "file=@test_recibo.pdf"

# 3. Validar JSON
# Resultado debe tener: fecha, monto, concepto, etc.
```

## 10. Next Steps

1. **Now:** Crea app/agents/state.py + graph.py
2. **Next:** Crea app/services/pdf_processor.py
3. **Next:** Crea app/core/gemini_client.py
4. **Next:** Crea app/agents/supervisor.py + ingest_agent.py
5. **Finally:** Integra en app/api/v1/ingest.py endpoint
6. **Test:** Sube un PDF real y valida salida JSON

---

**Tiempo estimado:** 2 horas para código + testing
