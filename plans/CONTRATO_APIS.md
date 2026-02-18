# Especificación de Contrato de APIs - Backend

**Estado:** Fase 2  
**Objetivo:** Definir exactamente qué endpoint, qué recibe, qué retorna  
**Importante:** Estos contratos fueron definidos en coordinación con el equipo de frontend (ver documentación de UX)

---

## 1. INGESTA (Pipeline 1)

### 1.1 `POST /api/v1/ingest/upload`

**Propósito:** Subir un documento (PDF/Excel) para ser digitalizado por el Agente de Ingesta.

**Request:**
```
Content-Type: multipart/form-data

file: UploadFile (PDF o Excel)
```

**Response (202 Accepted):**
```json
{
  "message": "File uploaded successfully and queued for processing",
  "ingest_id": "ing_1739875432_abc123",
  "status": "pending_processing",
  "file_name": "factura_proveedor_xyz_2026_01_15.pdf",
  "created_at": "2026-01-15T10:30:45.123Z",
  "extracted_transactions": 0,
  "raw_preview": null
}
```

**Error (422 Unprocessable Entity):**
```json
{
  "detail": "Unsupported file type. Accepted: PDF, Excel",
  "error_code": "INVALID_FILE_TYPE"
}
```

**Nota:** Esta API retorna inmediatamente después de guardar el archivo. El procesamiento real ocurre en background.

---

### 1.2 `GET /api/v1/ingest/{ingest_id}`

**Propósito:** Consultar el estado e información extraída de una ingesta.

**Response:**
```json
{
  "ingest_id": "ing_1739875432_abc123",
  "file_name": "factura_proveedor_xyz.pdf",
  "status": "completed",
  "created_at": "2026-01-15T10:30:45.123Z",
  "completed_at": "2026-01-15T10:35:12.456Z",
  "extraction_errors": [],
  "raw_transactions": [
    {
      "fecha": "2026-01-15",
      "nit_emisor": "800.123.456",
      "nit_receptor": "900.987.654",
      "total": 1500000,
      "descripcion": "Servicio de consultoría",
      "items": [
        {
          "descripcion": "Consultoría contable",
          "valor": 1500000
        }
      ]
    }
  ]
}
```

**Status posibles:**
- `pending_processing` - En cola de espera
- `processing` - Siendo procesado por Agente de Ingesta
- `completed` - Listo para contabilizar
- `failed` - Error en extracción

---

## 2. PROCESAMIENTO CONTABLE (Pipeline 2)

### 2.1 `POST /api/v1/process/accounting/{ingest_id}`

**Propósito:** Iniciar el procesamiento contable completo: Contador → Tributario → Auditor.

**Request:**
```json
{
  "ingest_id": "ing_1739875432_abc123"
}
```

(O simplemente en la URL)

**Response (202 Accepted):**
```json
{
  "message": "Accounting process started",
  "process_id": "proc_1739875445_xyz789",
  "ingest_id": "ing_1739875432_abc123",
  "status": "queued",
  "created_at": "2026-01-15T10:35:15.789Z"
}
```

**Error (404 Not Found):**
```json
{
  "detail": "Ingest ID not found",
  "error_code": "INGEST_NOT_FOUND"
}
```

**Nota:** Retorna inmediatamente. Frontend debe polling a `/process/status/{process_id}`.

---

### 2.2 `GET /api/v1/process/status/{process_id}`

**Propósito:** Consultar el progreso del procesamiento en tiempo real.

**Response:**
```json
{
  "process_id": "proc_1739875445_xyz789",
  "ingest_id": "ing_1739875432_abc123",
  "status": "running",
  "progress": 65,
  "current_stage": "tributario",
  "current_agent": "TributarioAgent",
  "started_at": "2026-01-15T10:35:16.123Z",
  "error_message": null,
  "agent_log": [
    {
      "timestamp": "2026-01-15T10:35:16.150Z",
      "agent": "supervisor",
      "action": "started_process",
      "details": {}
    },
    {
      "timestamp": "2026-01-15T10:35:18.200Z",
      "agent": "contador",
      "action": "classified_transaction",
      "details": {
        "transaction_id": "txn_1",
        "cuenta_puc": "5195",
        "puc_description": "Gastos diversos",
        "reasoning": "Consultó RAG histórico. Transacciones previas del mismo proveedor clasificadas como 5195."
      }
    },
    {
      "timestamp": "2026-01-15T10:35:22.400Z",
      "agent": "tributario",
      "action": "calculated_taxes",
      "details": {
        "transaction_id": "txn_1",
        "retefuente": 52500,
        "reteica": 10350,
        "iva": 285000,
        "references": ["Art. 383 ET", "Art. 10 Decreto 2048/1992"]
      }
    }
  ]
}
```

**Status posibles:**
- `queued` - En cola de espera
- `running` - En procesamiento (ver `current_stage`)
- `completed` - Proceso exitoso
- `failed` - Error en algún agente
- `rejected` - Auditor rechazó la transacción

**Progress:** 0-100%

**Nota:** Frontend debe hacer polling cada 2 segundos hasta que `status` sea `completed` o `failed`.

---

### 2.3 `GET /api/v1/process/result/{process_id}`

**Propósito:** Obtener el resultado completo del procesamiento después de completarse.

**Response (200 OK si completado):**
```json
{
  "process_id": "proc_1739875445_xyz789",
  "ingest_id": "ing_1739875432_abc123",
  "status": "completed",
  "transactions": [
    {
      "id": "txn_1",
      "fecha": "2026-01-15",
      "nit_emisor": "800.123.456",
      "nit_receptor": "900.987.654",
      "total": 1500000,
      "cuenta_puc": "5195",
      "puc_description": "Gastos diversos",
      "retefuente": 52500,
      "reteica": 10350,
      "iva": 285000,
      "journal_entries": [
        {
          "cuenta": "5195",
          "descripcion": "Gastos diversos",
          "debito": 1500000,
          "credito": 0
        },
        {
          "cuenta": "2408",
          "descripcion": "Retención en la fuente",
          "debito": 0,
          "credito": 52500
        },
        {
          "cuenta": "2365",
          "descripcion": "Retención en ICA",
          "debito": 0,
          "credito": 10350
        },
        {
          "cuenta": "1110",
          "descripcion": "Bancos",
          "debito": 0,
          "credito": 1437150
        }
      ],
      "agent_reasoning": {
        "contador": "Clasificación basada en transacciones históricas similares",
        "tributario": "Se aplicaron retenciones según Art. 383 ET",
        "auditor": "Validado: partida doble cuadra (1.5M = 52.5K+10.35K+1.437M)"
      }
    }
  ],
  "completed_at": "2026-01-15T10:36:05.890Z"
}
```

**Response (202 Accepted si aún procesando):**
```json
{
  "status": "running",
  "message": "Process still running. Current stage: auditor"
}
```

**Response (404 Not Found):**
```json
{
  "detail": "Process not found",
  "error_code": "PROCESS_NOT_FOUND"
}
```

---

## 3. LIBROS CONTABLES (Reportes)

### 3.1 `GET /api/v1/reports/balance`

**Query Parameters (opcionales):**
```
start_date: YYYY-MM-DD (default: inicio del año actual)
end_date: YYYY-MM-DD (default: hoy)
```

**Response:**
```json
{
  "report_type": "balance_sheet",
  "period": "2026-01-01 to 2026-01-31",
  "generated_at": "2026-01-15T10:40:00.123Z",
  "data": {
    "assets": {
      "nombre": "ACTIVOS",
      "cuentas": [
        {
          "codigo": "1110",
          "nombre": "Bancos",
          "saldo": 5234500
        },
        {
          "codigo": "1210",
          "nombre": "Clientes",
          "saldo": 12500000
        }
      ],
      "total": 17734500
    },
    "liabilities": {
      "nombre": "PASIVOS",
      "cuentas": [
        {
          "codigo": "2408",
          "nombre": "Retención en la fuente",
          "saldo": 152500
        },
        {
          "codigo": "2365",
          "nombre": "Retención en ICA",
          "saldo": 30350
        }
      ],
      "total": 182850
    },
    "equity": {
      "nombre": "PATRIMONIO",
      "cuentas": [
        {
          "codigo": "3110",
          "nombre": "Capital",
          "saldo": 1000000
        }
      ],
      "total": 1000000
    }
  }
}
```

**Validación:** ACTIVOS == PASIVOS + PATRIMONIO

---

### 3.2 `GET /api/v1/reports/pnl`

**Query Parameters:**
```
start_date: YYYY-MM-DD
end_date: YYYY-MM-DD
```

**Response:**
```json
{
  "report_type": "profit_and_loss",
  "period": "2026-01-01 to 2026-01-31",
  "generated_at": "2026-01-15T10:40:00.123Z",
  "data": {
    "ingresos": {
      "ventas": 5000000,
      "servicios": 3000000,
      "otros_ingresos": 500000,
      "total": 8500000
    },
    "costos": {
      "costo_venta": 2500000,
      "total": 2500000
    },
    "gastos": {
      "gastos_administrativos": 1200000,
      "gastos_ventas": 800000,
      "gastos_diversos": 500000,
      "total": 2500000
    },
    "impuestos": {
      "impuesto_renta": 800000,
      "total": 800000
    },
    "resultado_neto": 1700000
  }
}
```

---

### 3.3 `GET /api/v1/reports/cashflow`

**Query Parameters:**
```
start_date: YYYY-MM-DD
end_date: YYYY-MM-DD
```

**Response:**
```json
{
  "report_type": "cash_flow",
  "period": "2026-01-01 to 2026-01-31",
  "generated_at": "2026-01-15T10:40:00.123Z",
  "data": {
    "actividades_operacion": {
      "utilidad_neta": 1700000,
      "cuentas_cobrar_aumento": -2000000,
      "cuentas_pagar_aumento": 500000,
      "flujo_operacion": 200000
    },
    "actividades_inversion": {
      "compra_activos": -1000000,
      "flujo_inversion": -1000000
    },
    "actividades_financiamiento": {
      "prestamos": 500000,
      "flujo_financiamiento": 500000
    },
    "cambio_neto_efectivo": -300000,
    "efectivo_inicial": 5500000,
    "efectivo_final": 5200000
  }
}
```

---

## 4. MÓDULO TRIBUTARIO

### 4.1 `GET /api/v1/tax/iva`

**Query Parameters:**
```
start_date: YYYY-MM-DD
end_date: YYYY-MM-DD
```

**Response:**
```json
{
  "report_type": "iva_report",
  "period": "2026-01-01 to 2026-01-31",
  "generated_at": "2026-01-15T10:40:00.123Z",
  "data": {
    "iva_generado": {
      "ventas_tarifa_19": 850000,
      "servicios_tarifa_5": 50000,
      "total_generado": 900000
    },
    "iva_descontable": {
      "compras_tarifa_19": 285000,
      "servicios_tarifa_5": 15000,
      "total_descontable": 300000
    },
    "iva_a_pagar": 600000,
    "referencias": ["Art. 477 ET", "Art. 24 ET - Renta bruta"]
  }
}
```

---

### 4.2 `GET /api/v1/tax/withholdings`

**Query Parameters:**
```
start_date: YYYY-MM-DD
end_date: YYYY-MM-DD
```

**Response:**
```json
{
  "report_type": "withholdings_report",
  "period": "2026-01-01 to 2026-01-31",
  "generated_at": "2026-01-15T10:40:00.123Z",
  "data": {
    "retencion_en_la_fuente": {
      "servicios": 150000,
      "honorarios": 85000,
      "total_retenido": 235000,
      "tasa_promedio": 0.11
    },
    "retencion_ica": {
      "total_retenido": 65000,
      "tasa": 0.0069
    },
    "referencias": ["Art. 383 ET", "Decreto 2048/1992"]
  }
}
```

---

## 5. EVALUACIÓN Y MÉTRICAS

### 5.1 `GET /api/v1/evaluation/run`

**Propósito:** Ejecutar evaluación de calidad del sistema sobre las transacciones procesadas.

**Query Parameters (opcionales):**
```
sample_size: int (default: 50, máx: 500) - Cuántas transacciones evaluar
```

**Response:**
```json
{
  "status": "completed",
  "sample_size": 50,
  "evaluated_at": "2026-01-15T10:45:00.123Z",
  "metrics": {
    "schema_compliance_rate": 1.0,
    "double_entry_error_rate": 0.0,
    "field_extraction_accuracy": 0.98,
    "puc_assignment_accuracy": 0.96,
    "tax_calculation_accuracy": 1.0,
    "audit_pass_rate": 0.94,
    "process_completion_rate": 0.92
  },
  "detailed_metrics": {
    "schema_compliance": {
      "passed": 50,
      "failed": 0,
      "description": "All extracted transactions comply with Pydantic schema"
    },
    "double_entry": {
      "passed": 50,
      "failed": 0,
      "description": "All journal entries balance correctly"
    },
    "field_extraction": {
      "passed": 49,
      "failed": 1,
      "description": "One transaction had missing item description"
    },
    "puc_assignment": {
      "passed": 48,
      "failed": 2,
      "description": "Two transactions assigned questionable PUC codes (manual review recommended)"
    },
    "tax_calculation": {
      "passed": 50,
      "failed": 0,
      "description": "All tax calculations verified against Colombian tax rates"
    },
    "audit_pass": {
      "passed": 47,
      "failed": 3,
      "description": "Three transactions flagged for potential duplicates or anomalies"
    }
  },
  "recommendations": [
    "Retrain contador agent: PUC accuracy below 98%",
    "Review 3 flagged transactions manually (IDs: txn_12, txn_34, txn_56)",
    "Overall system is performing well. No critical issues."
  ]
}
```

---

## 6. HEALTH & STATUS

### 6.1 `GET /health`

**Response (200 OK):**
```json
{
  "status": "healthy",
  "timestamp": "2026-01-15T10:45:30.123Z",
  "version": "0.1.0",
  "services": {
    "api": "ok",
    "database": "ok",
    "vectordb": "ok",
    "gemini": "ok"
  }
}
```

**Response (503 Service Unavailable):**
```json
{
  "status": "unhealthy",
  "timestamp": "2026-01-15T10:45:30.123Z",
  "services": {
    "api": "ok",
    "database": "error",
    "vectordb": "ok",
    "gemini": "error"
  },
  "message": "Database and Gemini API are unreachable"
}
```

---

## 7. ERRORES COMUNES (HTTP Status Codes)

| Código | Significado | Ejemplo |
|--------|-------------|---------|
| `200` | OK | GET /reports/balance ✅ |
| `202` | Accepted (async) | POST /ingest/upload, POST /process/accounting |
| `400` | Bad Request | Parámetros inválidos |
| `404` | Not Found | ingest_id o process_id no existe |
| `422` | Unprocessable Entity | Validación Pydantic fallida |
| `500` | Internal Server Error | Error en agente o BD |
| `503` | Service Unavailable | Gemini API down, BD desconectada |

**Formato de error estándar:**
```json
{
  "detail": "Descripción del error",
  "error_code": "ERROR_CODE",
  "timestamp": "2026-01-15T10:45:30.123Z"
}
```

---

## 8. NOTAS IMPORTANTES

### Polling Pattern
Frontend NO debe hacer llamadas síncrones a `/process/accounting/{ingest_id}`. Debe:
1. `POST /process/accounting/{ingest_id}` → obtiene `process_id`
2. Loop: `GET /process/status/{process_id}` cada 2 segundos
3. Cuando `status == completed` o `failed` → `GET /process/result/{process_id}`

### Timestamps
- Todos en **ISO 8601 format**: `2026-01-15T10:45:30.123Z`
- Timezone: **UTC**

### Currency
- Moneda: **COP** (Pesos colombianos)
- Decimales: **sin decimales** (ej: 1500000, no 1500000.00)
- Excepción: tasas impositivas en formato decimal (0.11 = 11%)

### Página siguiente: Implementación de Agents y RAG

Ver `GUIA_TECNICA_FASE1.md` para detalles de cómo implementar cada agente.

---

**Última actualización:** 2026-02-18  
**Responsable:** Equipo Backend  
**Validado por:** Equipo Frontend (contrato alineado con UX)
