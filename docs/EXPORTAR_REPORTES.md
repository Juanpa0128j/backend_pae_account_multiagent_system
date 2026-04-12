# Guía de Descarga de Reportes Contables

## Descripción General

El sistema PAE ahora proporciona endpoints para descargar reportes financieros en dos formatos:
- **PDF**: Presentación profesional formateada según estándares contables colombianos
- **Excel**: Hojas de cálculo compatibles con Microsoft Excel para análisis adicional

## Endpoints Disponibles

### 1. Balance General (Balance Sheet)

#### Descargar PDF
```http
GET /api/v1/reports/balance/download/pdf
```

**Parámetros de Query:**
- `start_date` (opcional): Fecha de inicio (YYYY-MM-DD)
- `end_date` (opcional): Fecha de fin (YYYY-MM-DD, por defecto: hoy)
- `company_nit` (opcional): NIT de la empresa para filtrar
- `company_name` (opcional, default: "Empresa"): Nombre de la empresa en el PDF

**Ejemplo cURL:**
```bash
curl -o balance_general.pdf \
  "http://localhost:8000/api/v1/reports/balance/download/pdf?company_nit=800999888-2&company_name=Acme%20S.A.S."
```

#### Descargar Excel
```http
GET /api/v1/reports/balance/download/excel
```

**Parámetros idénticos a PDF, devuelve XLSX.**

---

### 2. Estado de Resultados (Profit & Loss)

#### Descargar PDF
```http
GET /api/v1/reports/pnl/download/pdf
```

**Parámetros:**
- `start_date` (opcional)
- `end_date` (opcional)
- `company_nit` (opcional)
- `company_name` (opcional)

**Ejemplo cURL:**
```bash
curl -o estado_resultados.pdf \
  "http://localhost:8000/api/v1/reports/pnl/download/pdf?start_date=2026-01-01&end_date=2026-03-31"
```

#### Descargar Excel
```http
GET /api/v1/reports/pnl/download/excel
```

---

### 3. Flujo de Caja (Cash Flow)

#### Descargar PDF
```http
GET /api/v1/reports/cashflow/download/pdf
```

#### Descargar Excel
```http
GET /api/v1/reports/cashflow/download/excel
```

Parámetros idénticos a los anteriores.

---

## Características de los Reportes

### PDF
✅ **Diseño profesional** en colores corporativos (azul #1F4788)  
✅ **Encabezados estructurados** con datos de la empresa  
✅ **Tablas formateadas** con separadores y sombreado alternado  
✅ **Validación contable** (cuadre, balance verification)  
✅ **Símbolos de moneda** ($ COP)  
✅ **Notas normativas** de NIIF y PCGA (cuando disponibles)  

### Excel
✅ **Celdas formateadas** con moneda ($)  
✅ **Encabezados destacados** con relleno de color  
✅ **Filas de totales** en negrita  
✅ **Ancho de columnas optimizado**  
✅ **Bordes profesionales**  
✅ **Múltiples pestañas** por tipo de reporte  

---

## Uso desde el Script de Simulación

El script `simulate_frontend_full_pipeline.py` ya incluye la funcionalidad de descarga automática:

```bash
uv run python scripts/simulate_frontend_full_pipeline.py \
  --base-url http://127.0.0.1:8000 \
  --source-mode demo
```

Los reportes descargados se guardan en:
```
storage/downloads/reports/
├── balance_800999888-2.pdf
├── balance_800999888-2.xlsx
├── pnl_800999888-2.pdf
├── pnl_800999888-2.xlsx
├── cashflow_800999888-2.pdf
└── cashflow_800999888-2.xlsx
```

---

## Ejemplo: Cliente Python (requests)

```python
import requests
from pathlib import Path

BASE_URL = "http://localhost:8000"
COMPANY_NIT = "800999888-2"
COMPANY_NAME = "Acme S.A.S."
OUTPUT_DIR = Path("reportes")

OUTPUT_DIR.mkdir(exist_ok=True)

# Descargar Balance General PDF
resp = requests.get(
    f"{BASE_URL}/api/v1/reports/balance/download/pdf",
    params={
        "company_nit": COMPANY_NIT,
        "company_name": COMPANY_NAME,
    }
)
if resp.status_code == 200:
    with open(OUTPUT_DIR / "balance.pdf", "wb") as f:
        f.write(resp.content)
    print("✓ Balance PDF descargado")
else:
    print(f"✗ Error: {resp.status_code}")

# Descargar Estado de Resultados Excel
resp = requests.get(
    f"{BASE_URL}/api/v1/reports/pnl/download/excel",
    params={
        "company_nit": COMPANY_NIT,
        "company_name": COMPANY_NAME,
        "start_date": "2026-01-01",
        "end_date": "2026-03-31",
    }
)
if resp.status_code == 200:
    with open(OUTPUT_DIR / "pnl.xlsx", "wb") as f:
        f.write(resp.content)
    print("✓ P&L Excel descargado")
```

---

## Ejemplo: Cliente JavaScript/Node.js

```javascript
const BASE_URL = "http://localhost:8000";
const COMPANY_NIT = "800999888-2";
const COMPANY_NAME = "Acme S.A.S.";

async function downloadReport(reportType, format) {
  const url = new URL(
    `${BASE_URL}/api/v1/reports/${reportType}/download/${format}`
  );
  url.searchParams.append("company_nit", COMPANY_NIT);
  url.searchParams.append("company_name", COMPANY_NAME);

  try {
    const response = await fetch(url.toString());
    if (!response.ok) {
      throw new Error(`Error ${response.status}`);
    }

    const blob = await response.blob();
    const downloadUrl = window.URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = downloadUrl;
    link.download = `${reportType}_${format}`;
    link.click();

    console.log(`✓ ${reportType} ${format} descargado`);
  } catch (error) {
    console.error(`✗ Error descargando ${reportType}:`, error);
  }
}

// Uso
await downloadReport("balance", "pdf");    // balance.pdf
await downloadReport("balance", "excel");  // balance.xlsx
await downloadReport("pnl", "pdf");        // pnl.pdf
await downloadReport("cashflow", "excel"); // cashflow.xlsx
```

---

## Estructura Técnica

Los exportadores están implementados en:
- **Archivo:** `app/services/report_export_service.py`
- **Clases:**
  - `BalanceSheetExporter` → Balance General
  - `PnLExporter` → Estado de Resultados
  - `CashFlowExporter` → Flujo de Caja

Cada clase tiene dos métodos estáticos:
- `.to_pdf(report: dict, company_name: str) → bytes`
- `.to_excel(report: dict, company_name: str) → bytes`

---

## Notas Importantes

1. **Datos JSON → Reportes:** Los endpoints de descarga primero obtienen los datos JSON de los reportes (via `/api/v1/reports/{type}`), luego los formatean a PDF/Excel.

2. **Campos obligatorios:** Los reportes requieren datos válidos en la base de datos. Si la empresa no tiene transacciones contabilizadas, los reportes estarán vacíos.

3. **Validación de cuadre:** El Balance General incluye automáticamente validación de cuadre (Activos = Pasivos + Patrimonio). Si hay discrepancia, se muestra en el PDF/Excel.

4. **Moneda:** Todos los valores están expresados en **pesos colombianos (COP)** y formateados con separador de miles.

5. **Períodos:** Usar `start_date` y `end_date` para filtrar por período. Si se omite `end_date`, usa la fecha actual.

---

## Roadmap Futuro

- [ ] Exportar a Word (.docx)
- [ ] Agregar firma digital / QR (auditoría)
- [ ] Reportes combinados (Balance + P&L + Cashflow en un PDF)
- [ ] Formato XML para intercambio (XBRL, UBL)
- [ ] Generación de reportes por lote (batch)
- [ ] Plantillas personalizables por empresa

---

## Troubleshooting

| Problema | Causa Probable | Solución |
|----------|---|---|
| **404 Not Found** | Endpoint no existe | Verificar URL y parámetros |
| **500 Internal Server** | Error al generar reporte | Revisar logs del backend; asegurar datos válidos en DB |
| **PDF vacío** | Reportlab no instalado | `uv sync` para reinstalar dependencias |
| **Excel con formato incorrecto** | Openpyxl incompatible | Actualizar con `uv add --upgrade openpyxl` |
| **Archivo muy grande** | Demasiadas transacciones | Filtrar con `start_date`/`end_date` |

---

Documentación última actualización: **April 2026**  
Versión PAE: **0.2.0**
