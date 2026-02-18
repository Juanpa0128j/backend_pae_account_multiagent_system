# 🚀 Agente Piloto - Guía de Ejecución

## ✅ Qué se implementó

Se creó un **agente funcional completo** que:

1. ✅ **Acepta PDFs** vía API (`POST /api/v1/ingest/upload`)
2. ✅ **Extrae texto** con PyPDF
3. ✅ **Interpreta datos** con Gemini 2.5 Flash
4. ✅ **Retorna JSON** con campos estructurados

### Archivos creados:

```
app/
├── agents/
│   ├── state.py          ← AgentState TypedDict
│   ├── supervisor.py     ← Nodo Supervisor (valida)
│   ├── ingest_agent.py   ← Nodo Ingesta (procesa)
│   └── graph.py          ← StateGraph (orquestación)
├── core/
│   └── gemini_client.py  ← Wrapper de Gemini API
├── services/
│   └── pdf_processor.py  ← PyPDF utilities
└── api/v1/
    └── ingest.py        ← Endpoint real (integrado)
```

**Actualizaciones:**
- ✅ `pyproject.toml` - Dependencies añadidas
- ✅ `main.py` - Logging configurado
- ✅ `.env.example` - GEMINI_API_KEY variable añadida

---

## 🔧 Setup (5 minutos)

### 1. Instalar dependencias

```bash
pip install -e ".[dev]"
```

O instalar manualmente:

```bash
pip install langraph google-generativeai pypdf pydantic python-dotenv
```

### 2. Obtener API Key de Gemini

1. Ir a: https://ai.google.dev/
2. Click "Get API Key"
3. Crear nuevo proyecto Google Cloud
4. Copiar la API key

### 3. Crear `.env` (copiar de `.env.example`)

```bash
cp .env.example .env
```

Editar `.env` y pegar tu API key:

```
GEMINI_API_KEY=tu_api_key_aqui
GEMINI_MODEL=gemini-2.5-flash
```

---

## ▶️ Ejecutar el agente

### Opción A: Vía API (Recomendado)

**1. Arrancar servidor:**
```bash
python main.py
```

Output esperado:
```
INFO:     Uvicorn running on http://0.0.0.0:8000
```

**2. Subir un PDF (en otra terminal):**

```bash
# Crear test recibo primero (ver abajo)
curl -X POST http://localhost:8000/api/v1/ingest/upload \
  -F "file=@test_recibo.pdf"
```

**3. Respuesta esperada:**
```json
{
  "message": "Receipt/invoice successfully processed",
  "ingest_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed"
}
```

---

### Opción B: Directamente en Python (Para debugging)

```python
from app.agents.graph import invoke_agent

# Procesar archivo
result = invoke_agent("path/to/recibo.pdf")

print(result)
# Output:
# {
#   "process_id": "...",
#   "status": "completed",
#   "data": {
#     "fecha": "2026-02-18",
#     "monto": 150000.0,
#     "concepto": "Pago de servicios",
#     ...
#   }
# }
```

---

## 📄 Crear un Test Recibo PDF

Opción 1: **Usar reportlab** (genera PDF desde código)

```python
from reportlab.pdfgen import canvas

pdf_path = "test_recibo.pdf"
c = canvas.Canvas(pdf_path)

# Encabezado
c.setFont("Helvetica-Bold", 14)
c.drawString(50, 750, "RECIBO DE PAGO")

# Datos
c.setFont("Helvetica", 11)
c.drawString(50, 700, "Fecha: 18 de febrero de 2026")
c.drawString(50, 670, "Banco: Bancolombia S.A.")
c.drawString(50, 640, "Concepto: Transacción y servicios")
c.drawString(50, 610, "Beneficiario: Empresa XYZ SAS")
c.drawString(50, 580, "Monto: $150.000")
c.drawString(50, 550, "Referencia: REF-2026-001234")
c.drawString(50, 520, "Tipo: Recibo")

c.showPage()
c.save()

print(f"✅ PDF creado: {pdf_path}")
```

Opción 2: **Usar un PDF real** 
- Descarga un recibo/factura de tu banco
- O genera uno online (https://www.pdf.io/)

---

## 🧪 Testing

### Test 1: Validación de API

```bash
# Debe retornar 400 si no es PDF
curl -X POST http://localhost:8000/api/v1/ingest/upload \
  -F "file=@test.txt"

# Debe retornar 404 si archivo no existe
curl -X POST http://localhost:8000/api/v1/ingest/upload \
  -F "file=@no_existe.pdf"
```

### Test 2: Validación de Gemini

```python
from app.core.gemini_client import GeminiClient

client = GeminiClient()

# Test extracción
text = """
RECIBO DE PAGO
Fecha: 18/02/2026
Monto: $150.000
Concepto: Pago de servicios
Beneficiario: Empresa ABC
"""

result = client.extract_receipt_data(text)
print(result)
# Debe retornar dict con fecha, monto, concepto, etc.
```

---

## 🔍 Debugging

### Issue: "GEMINI_API_KEY not set"

**Solución:**
```bash
# Verificar que .env existe
cat .env

# O setear variable directo (PowerShell):
$env:GEMINI_API_KEY="tu_key"

# O (bash):
export GEMINI_API_KEY="tu_key"
```

### Issue: "No readable text found in PDF"

**Causa:** PDF tiene solo imágenes, sin texto extractable
**Solución:** Usar OCR (future enhancement, ahora solo PDFs con texto)

### Issue: "Invalid JSON from Gemini"

**Causa:** Gemini no retornó JSON válido
**Solución:** Revisar prompt en `gemini_client.py`, temperature está en 0.0 para determinismo

---

## 📊 Flujo Completo (Paso a Paso)

```
1. Cliente hace POST /api/v1/ingest/upload con PDF
   ↓
2. ingest.py recibe archivo
   ↓
3. Guarda en temp dir
   ↓
4. Llamar invoke_agent(temp_file_path)
   ↓
5. graph.py: StateGraph inicia con Supervisor
   ↓
6. Supervisor valida:
   - ¿Archivo existe? ✓
   - ¿Es PDF? ✓
   ↓
7. Pasa a Ingest node
   ↓
8. Ingest node:
   a) PyPDF extrae texto del PDF
   b) Envía texto a Gemini
   c) Gemini retorna JSON interpretado
   d) Formatea respuesta final
   ↓
9. Graph retorna result dict
   ↓
10. ingest.py mapea a IngestResponse
   ↓
11. API retorna 200 + JSON al cliente
```

---

## 📈 Próximos pasos (Post-MVP)

- [ ] Persistir en DB (TransactionPending)
- [ ] Job tracking async
- [ ] Procesar Excel además de PDF
- [ ] Agregar Contador agent (clasificar PUC)
- [ ] Agregar Tributario agent (calcular impuestos)
- [ ] Agregar Auditor agent (validar doble entrada)

---

## 🆘 Soporte

Si hay error:
1. Revisar logs en terminal (nivel INFO+)
2. Revisar `.env` tiene GEMINI_API_KEY válida
3. Revisar PyPDF puede leer el PDF (test manual)
4. Revisar Gemini responde (test gemini_client.py directo)

---

**Agente Piloto está VIVO ✨**
