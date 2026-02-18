# Estructura de Front-end y de UX

Status: Done
Sprint: Sprint 1
Assignee: Samuel Castano Mira
Due Date: February 14, 2026

# **Estructura de Front-end y de UX — PAE Contable 2026-1**

La interfaz es el punto de contacto directo entre el usuario (contador, administrador) y el sistema agéntico. El objetivo es que sea **funcional, clara y transparente**: el usuario debe entender qué está pasando por debajo, no solo ver resultados mágicos.

## **1. Principios de Diseño**

- **Transparencia sobre magia:** Mostrar siempre el razonamiento del agente, no solo el resultado final.
- **Feedback inmediato:** Toda acción del usuario debe tener una respuesta visual (loading, progreso, confirmación).
- **Mínimos clics:** Flujos lineales. El usuario carga, revisa, aprueba. Sin menús profundos.
- **Mobile-friendly pero desktop-first:** El público objetivo trabaja en escritorio con Excel abierto al lado.

## **2. Stack Tecnológico**

| Componente | Elección | Justificación |
| --- | --- | --- |
| Framework | **Next.js** (App Router) | SSR para SEO de reportes compartidos, file-based routing simple, API routes si se necesitan proxies. |
| UI Components | **MUI (Material UI) v5** | Componentes listos para tablas, formularios, modales. Tema personalizable. |
| State Management | **React Query (TanStack Query)** | Caché automático de datos del backend, manejo de loading/error states sin boilerplate. |
| Charts | **Recharts** | Ligero, declarativo, suficiente para gráficas financieras. |
| Auth | **JWT** (cookie httpOnly) | Consistente con lo definido en el backend (FastAPI + JWT). |
| Drag & Drop | **react-dropzone** | Para la zona de carga de archivos. |
| Formularios | **React Hook Form + Zod** | Validación en cliente rápida y type-safe. |

## **3. Estructura de Carpetas**

```
src/
├── app/                        # Next.js App Router
│   ├── layout.tsx              # Layout global (Sidebar + TopBar)
│   ├── page.tsx                # Dashboard principal
│   ├── upload/
│   │   └── page.tsx            # Vista de carga de documentos
│   ├── transactions/
│   │   ├── page.tsx            # Transacciones pendientes y procesadas
│   │   └── [id]/
│   │       └── page.tsx        # Detalle de transacción individual
│   ├── books/
│   │   ├── page.tsx            # Libros contables (Diario, Mayor, Auxiliares)
│   │   └── [type]/
│   │       └── page.tsx        # Vista de libro específico
│   ├── reports/
│   │   └── page.tsx            # Reportes financieros
│   ├── tax/
│   │   └── page.tsx            # Módulo tributario
│   ├── evaluation/
│   │   └── page.tsx            # Panel de evaluación y métricas del sistema
│   └── settings/
│       └── page.tsx            # Configuración de empresa y usuario
├── components/
│   ├── layout/
│   │   ├── Sidebar.tsx         # Navegación lateral
│   │   ├── TopBar.tsx          # Barra superior (user, notificaciones)
│   │   └── PageHeader.tsx      # Título + breadcrumbs de cada vista
│   ├── upload/
│   │   ├── DropZone.tsx        # Zona de drag & drop
│   │   ├── FilePreview.tsx     # Preview del archivo subido
│   │   └── UploadProgress.tsx  # Barra de progreso de ingesta
│   ├── transactions/
│   │   ├── TransactionTable.tsx
│   │   ├── TransactionDetail.tsx
│   │   └── AgentReasoningPanel.tsx  # Panel que muestra el razonamiento
│   ├── books/
│   │   ├── BookTable.tsx
│   │   └── AccountFilter.tsx
│   ├── reports/
│   │   ├── ReportCard.tsx
│   │   └── FinancialChart.tsx
│   ├── common/
│   │   ├── StatusBadge.tsx     # Chips de estado (PENDING, POSTED, ERROR)
│   │   ├── MoneyDisplay.tsx    # Formato de moneda COP
│   │   └── DataTable.tsx       # Tabla reutilizable con sort/filter/pagination
│   └── agent/
│       ├── AgentTimeline.tsx   # Timeline visual del flujo Supervisor→Workers
│       └── AgentStepCard.tsx   # Tarjeta individual de cada paso del agente
├── hooks/
│   ├── useUpload.ts            # Hook para POST /ingest/upload
│   ├── useTransactions.ts      # Hook para transacciones pendientes/procesadas
│   ├── useReports.ts           # Hook para GET /reports/*
│   └── useTax.ts               # Hook para GET /tax/*
├── lib/
│   ├── api.ts                  # Cliente HTTP (fetch/axios) configurado con base URL
│   ├── auth.ts                 # Funciones de login, logout, refresh token
│   └── formatters.ts           # Formateo de fechas, moneda COP, NITs
├── types/
│   └── index.ts                # Tipos TypeScript alineados con los schemas Pydantic del backend
└── styles/
    └── theme.ts                # Tema MUI personalizado (colores, tipografía)
```

## **4. Vistas Principales y Flujos de Usuario**

### **4.1. Dashboard (`/`)**

Vista de resumen ejecutivo. El usuario aterriza aquí al iniciar sesión.

**Contenido:**

- **Tarjetas de resumen:** Documentos pendientes, transacciones del mes, alertas activas.
- **Gráfica rápida:** Ingresos vs Gastos del período actual (Recharts - BarChart).
- **Tabla corta:** Últimas 5 transacciones procesadas con su estado.
- **Acceso directo:** Botón prominente "Cargar documentos".

```
┌──────────────────────────────────────────────────────────┐
│  Sidebar  │            Dashboard                         │
│           │                                              │
│  📊 Home  │  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐       │
│  📁 Upload│  │Pend: │ │Proc: │ │Alert:│ │Total:│       │
│  📋 Trans.│  │  12  │ │  84  │ │  3   │ │$45M  │       │
│  📖 Books │  └──────┘ └──────┘ └──────┘ └──────┘       │
│  📈 Report│                                              │
│  💰 Tax   │  ┌────────────────────────────────┐         │
│  🔍 Eval  │  │   Ingresos vs Gastos (Chart)   │         │
│  ⚙️ Config│  └────────────────────────────────┘         │
│           │                                              │
│           │  ┌────────────────────────────────┐         │
│           │  │  Últimas transacciones (Table)  │         │
│           │  └────────────────────────────────┘         │
└──────────────────────────────────────────────────────────┘
```

### **4.2. Carga de Documentos (`/upload`)**

Es la puerta de entrada al Pipeline 1 (Ingestión). El usuario sube archivos y ve el progreso en tiempo real.

**Flujo:**

1. El usuario arrastra archivos (PDF, Excel, XML) a la zona de drop.
2. Se muestra una preview con nombre, tipo y tamaño.
3. Al confirmar, se hace `POST /ingest/upload` por cada archivo.
4. Se muestra un progreso por archivo: `Subiendo → Procesando OCR → Extrayendo datos → Listo`.
5. Al finalizar, se muestra un resumen de lo extraído (fecha, NIT, valor) para que el usuario valide visualmente.

**Componentes clave:**

- `DropZone`: Zona de arrastrar y soltar con validación de tipos permitidos.
- `UploadProgress`: Barra de progreso con estados del Agente de Ingesta.
- `FilePreview`: Tabla con los datos extraídos del documento para verificación rápida.

**Conexión con la arquitectura:**

- Llama a `POST /ingest/upload` (Pipeline 1).
- Los datos extraídos quedan en `transacciones_pendientes` con estado `PENDING`.

### **4.3. Transacciones (`/transactions`)**

Vista central del sistema. Muestra las transacciones en sus diferentes estados y permite disparar el procesamiento contable.

**Tabs:**

1. **Pendientes:** Transacciones extraídas pero no contabilizadas (`PENDING`).
2. **Procesadas:** Transacciones ya con asiento contable definitivo (`POSTED`).
3. **Rechazadas:** Transacciones que el Auditor devolvió (`REJECTED`).

**Acciones:**

- **"Contabilizar seleccionadas":** Dispara `POST /process/accounting/{ingest_id}` (Pipeline 2). Inicia el loop Contador → Tributario → Auditor.
- **Ver detalle:** Abre `/transactions/[id]` con el desglose completo.

**Vista de Detalle (`/transactions/[id]`):**

Aquí es donde se aplica el principio de **transparencia**. Se muestra:

| Sección | Contenido |
| --- | --- |
| **Datos originales** | Lo que el Agente de Ingesta extrajo del documento (JSON crudo). |
| **Clasificación contable** | Cuenta PUC asignada por el Agente Contador, con la justificación del RAG (qué artículo o historial consultó). |
| **Cálculos tributarios** | Tabla con Retefuente, ReteICA, IVA. Cada valor con referencia normativa (Art. X del ET). |
| **Asiento contable** | Tabla débito/crédito final. Resaltado verde si la partida doble cuadra. |
| **Timeline del agente** | Componente `AgentTimeline`: muestra visualmente el recorrido Supervisor → Contador → Tributario → Auditor, con tiempos y resultado de cada paso. |

```
┌──────────────────────────────────────────────────────────┐
│  Transacción #1042 — Factura Proveedor XYZ               │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  📄 Datos Originales          🏷️ Clasificación           │
│  ┌────────────────────┐      ┌────────────────────┐     │
│  │ Fecha: 2026-01-15  │      │ Cuenta: 5195       │     │
│  │ NIT: 900.123.456   │      │ "Gastos diversos"  │     │
│  │ Total: $1.500.000  │      │ Fuente: Histórico  │     │
│  └────────────────────┘      └────────────────────┘     │
│                                                          │
│  💰 Impuestos                 📊 Asiento Final           │
│  ┌────────────────────┐      ┌──────────────────────┐   │
│  │ ReteFte: $52.500   │      │ Cuenta  │ Déb │ Cré  │   │
│  │ ReteICA: $10.350   │      │ 5195    │ 1.5M│      │   │
│  │ IVA: $285.000      │      │ 2408    │     │ 285K │   │
│  │ Ref: Art.383 ET    │      │ 2365    │     │ 52K  │   │
│  └────────────────────┘      │ ...     │     │ ...  │   │
│                               │ ✅ Cuadra          │   │
│                               └──────────────────────┘   │
│                                                          │
│  🤖 Razonamiento del Agente (Timeline)                   │
│  ──●── Ingesta (0.8s) ✅                                 │
│  ──●── Contador (1.2s) ✅ "Clasifiqué como 5195..."     │
│  ──●── Tributario (0.9s) ✅ "Apliqué Art.383 ET..."     │
│  ──●── Auditor (0.3s) ✅ "Partida doble OK"             │
└──────────────────────────────────────────────────────────┘
```

### **4.4. Libros Contables (`/books`)**

Vista de consulta de los libros contables generados por el sistema.

**Sub-vistas:**

- **Libro Diario:** Todas las transacciones en orden cronológico.
- **Libro Mayor:** Agrupado por cuenta PUC.
- **Auxiliares:** Filtrado por tercero o cuenta específica.

**Funcionalidades:**

- Filtros por rango de fechas, cuenta PUC, tercero (NIT).
- Exportar a Excel (`openpyxl` en backend, descarga directa).
- Búsqueda rápida por concepto o valor.

**Datos:** Se consultan directamente desde la base SQL (Libro Diario definitivo) vía los endpoints del backend.

### **4.5. Reportes Financieros (`/reports`)**

Panel para generar y visualizar los estados financieros principales.

**Reportes disponibles:**

| Reporte | Endpoint | Formato |
| --- | --- | --- |
| Balance General | `GET /reports/balance` | Tabla + PDF exportable |
| Estado de Resultados (P&L) | `GET /reports/pnl` | Tabla + gráfica |
| Flujo de Caja | `GET /reports/cashflow` | Tabla + gráfica |

**UX del flujo:**

1. El usuario selecciona el período (mes, trimestre, año).
2. Clic en "Generar reporte".
3. Se muestra una preview en pantalla (tabla formateada con MUI DataGrid).
4. Botón "Descargar PDF" / "Descargar Excel".

**Gráficas (Recharts):**

- Balance General: BarChart horizontal (Activos vs Pasivos + Patrimonio).
- P&L: LineChart de tendencia mensual (Ingresos, Costos, Utilidad).
- Flujo de Caja: AreaChart (Entradas vs Salidas).

### **4.6. Módulo Tributario (`/tax`)**

Vista para consultar el estado de las obligaciones fiscales.

**Contenido:**

- **Resumen de IVA:** IVA generado vs IVA descontable, saldo a pagar/favor. (`GET /tax/iva`)
- **Retenciones:** Tabla de retenciones practicadas por tipo (Retefuente, ReteICA). (`GET /tax/withholdings`)
- **Alertas fiscales:** Próximos vencimientos, montos cercanos a umbrales de responsabilidad.

### **4.7. Evaluación y Métricas (`/evaluation`)**

Panel interno (probablemente solo para el rol `admin`) que muestra la salud del sistema agéntico.

**Contenido:**

- Botón "Ejecutar evaluación" → `GET /evaluation/run`.
- Tabla con métricas clave:
    - Schema Compliance Rate
    - Double-Entry Error Rate
    - PUC Assignment Accuracy
    - Tax Calculation Accuracy
    - Audit Pass Rate
- Gráfica de tendencia histórica de las métricas.

**Conexión:** Se alinea directamente con las métricas definidas en el documento de Validación y Evaluación.

### **4.8. Configuración (`/settings`)**

- Datos de la empresa (NIT, razón social, régimen tributario).
- Gestión de usuarios (solo admin).
- Preferencias de visualización.

## **5. Contratos Frontend ↔ Backend (Tipos Compartidos)**

Para que la comunicación sea predecible, los tipos en TypeScript deben espejear los schemas Pydantic del backend. Ejemplo de los tipos principales:

```tsx
// types/index.ts

type TransactionStatus = "PENDING" | "PROCESSING" | "POSTED" | "REJECTED";

interface RawTransaction {
  id: string;
  fecha: string;            // ISO 8601
  nit_emisor: string;
  nit_receptor: string;
  concepto: string;
  subtotal: number;
  iva: number;
  total: number;
  tipo_documento: "factura" | "extracto" | "nota_credito" | "otro";
  archivo_origen: string;
  status: TransactionStatus;
  created_at: string;
}

interface AsientoContable {
  cuenta_puc: string;       // Ej: "5195"
  nombre_cuenta: string;    // Ej: "Gastos diversos"
  debito: number;
  credito: number;
  tercero_nit: string;
}

interface TransactionDetail {
  id: string;
  raw: RawTransaction;
  clasificacion: {
    cuenta_puc: string;
    nombre_cuenta: string;
    justificacion: string;  // Explicación del agente contador
    fuente: "historico" | "normativa" | "manual";
  };
  impuestos: {
    retefuente: number;
    reteica: number;
    iva_generado: number;
    iva_descontable: number;
    referencia_normativa: string;  // Ej: "Art. 383 ET"
  };
  asiento: AsientoContable[];
  partida_doble_ok: boolean;
  agent_trace: AgentStep[];
}

interface AgentStep {
  agente: "Supervisor" | "Ingesta" | "Contador" | "Tributario" | "Auditor";
  accion: string;
  resultado: "success" | "error" | "retry";
  duracion_ms: number;
  detalle: string;           // Explicación en lenguaje natural
}

interface ReportRequest {
  tipo: "balance" | "pnl" | "cashflow";
  fecha_inicio: string;
  fecha_fin: string;
}
```

## **6. Manejo de Estados Asíncronos**

El procesamiento contable no es instantáneo (puede tomar varios segundos por transacción). La UX debe manejar esto correctamente:

### **Estrategia: Polling + Optimistic UI**

1. **Al disparar procesamiento:** Se muestra la transacción con estado `PROCESSING` inmediatamente (optimistic update).
2. **Polling:** React Query hace polling cada 3 segundos al endpoint de estado hasta que cambie a `POSTED` o `REJECTED`.
3. **Notificación:** Al completarse, se muestra un toast (MUI Snackbar) con el resultado.

```tsx
// hooks/useProcessTransaction.ts (pseudo-código)
const { mutate } = useMutation({
  mutationFn: (id: string) => api.post(`/process/accounting/${id}`),
  onSuccess: () => {
    // Inicia polling automático vía React Query refetch interval
    queryClient.invalidateQueries(["transactions"]);
  }
});
```

**No usamos WebSockets** para mantener la simplicidad. El polling con React Query es suficiente para este volumen de datos y evita complejidad en el backend.

## **7. Consideraciones de UX**

### **7.1. Accesibilidad y Usabilidad**

- **Colores con significado consistente:** Verde = cuadra/aprobado, Rojo = error/rechazado, Amarillo = pendiente/en proceso.
- **Tablas responsivas:** MUI DataGrid con paginación server-side para libros con miles de registros.
- **Feedback de errores claro:** Si el Auditor rechaza una transacción, mostrar el motivo exacto (no un "Error genérico").

### **7.2. Navegación**

```
Sidebar (siempre visible):
├── Dashboard
├── Cargar documentos
├── Transacciones
├── Libros contables
├── Reportes financieros
├── Tributario
├── Evaluación (solo admin)
└── Configuración
```

La navegación es plana (un nivel). No hay submenús desplegables. Cada ítem lleva a una vista completa. Los sub-niveles (ej: tipo de libro) se manejan con tabs dentro de la vista.

### **7.3. Responsive Design**

- **Desktop (>1024px):** Sidebar expandido + contenido completo.
- **Tablet (768-1024px):** Sidebar colapsado (solo íconos) + contenido completo.
- **Mobile (<768px):** Sidebar como drawer (hamburger menu) + tablas en modo scroll horizontal.

## **8. Roadmap de Implementación**

El frontend se puede construir de forma incremental siguiendo las dependencias con el backend:

| Fase | Vistas | Depende de |
| --- | --- | --- |
| **1. Base** | Layout, Sidebar, Dashboard (estático), Login | Backend desplegado con `/health` |
| **2. Ingesta** | Upload, UploadProgress | `POST /ingest/upload` |
| **3. Procesamiento** | Transactions (lista + detalle), AgentTimeline | `POST /process/accounting`, endpoints de transacciones |
| **4. Consultas** | Books, filtros, exportación | Endpoints de libros contables |
| **5. Reportes** | Reports, Charts, exportación PDF/Excel | `GET /reports/*` |
| **6. Tributario** | Tax module | `GET /tax/*` |
| **7. Evaluación** | Evaluation panel | `GET /evaluation/run` |

Este orden asegura que cada fase sea funcional y demostrable antes de avanzar a la siguiente.