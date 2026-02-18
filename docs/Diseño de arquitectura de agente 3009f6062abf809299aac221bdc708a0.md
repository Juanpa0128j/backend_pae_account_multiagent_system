# Diseño de arquitectura de agente

Status: Done
Sprint: Sprint 1
Assignee: Mateo Builes Duque
Due Date: February 14, 2026

# Plan de Arquitectura Técnica Detallada - PAE Contable 2026-1

Este documento complementa el diseño de alto nivel, detallando la estrategia de orquestación de agentes y la arquitectura de RAG para el cumplimiento normativo colombiano.

## 1. Estrategia de Sub-agentes: Arquitectura "Supervisor-Worker" (Jerárquica)

### ¿Por qué no "Skills" (Un solo agente con muchas herramientas)?

En contabilidad, el contexto es masivo. Si le das a un solo agente 50 herramientas (leer pdf, buscar en estatuto tributario, calcular retención, escribir en excel, validar PUC), el agente se confundirá ("Catastrophic Forgetting") o alucinará reglas.

### ¿Por qué "Supervisor-Worker"?

Esta arquitectura aísla responsabilidades.

1. **Aislamiento de Prompts:** El "Agente Tributario" tiene un System Prompt experto en impuestos y NO sabe leer PDFs. El "Agente de Ingesta" sabe de OCR y estructuras de datos, pero NO sabe qué es una retención en la fuente.
2. **Robustez:** Si el agente tributario falla, el Supervisor lo detecta y puede reintentar o pedir ayuda humana sin romper el flujo de lectura de archivos.
3. **Implementación en LangGraph:**
    - El **Supervisor** es un nodo que enruta (router) basándose en la salida estructurada de los workers.
    - Los **Workers** (Contador, Auditor, etc.) son nodos que retornan al Supervisor una actualización del estado global.

### 1.1. Definición de Roles Agénticos (Nodos del Grafo)

A continuación, se define la responsabilidad técnica y funcional de cada nodo dentro de la arquitectura LangGraph:

### A. Agente Supervisor (Orquestador)

- **Rol:** Es el cerebro administrativo del sistema. No realiza tareas operativas; su única función es examinar el `AgentState` y decidir **quién actúa a continuación**.
- **Lógica:** Máquina de estados finitos. Si `estado == "nuevos_docs"`, llama a Ingesta. Si `estado == "audit_failed"`, llama a Contador.
- **Salida:** Un string con el nombre del siguiente nodo (ej: `"Tributario"`).

### B. Agente de Ingesta (Data Engineer)

- **Rol:** Responsable de la entrada de datos (Input). Transforma archivos no estructurados (imágenes, PDFs) en datos estructurados (JSON).
- **Capabilities:** Visión por computador (multimodal), OCR, limpieza de strings.
- **Entrada:** Rutas de archivos (`.pdf`, `.xml`, `.xlsx`).
- **Salida:** Lista de diccionarios `RawTransaction` (fecha, nit, valor, concepto).

### C. Agente Contador (Bookkeeper)

- **Rol:** El experto contable. Recibe la data cruda y la traduce al lenguaje contable colombiano (PUC). Es quien decide si una compra es un "Gasto" (5) o un "Activo" (1).
- **Herramientas Clave:**
    - `search_puc`: Busca códigos en el Plan Único de Cuentas.
    - `search_history`: Busca cómo se contabilizó a este proveedor en el pasado.
- **Salida:** Objeto `Transaction` con cuenta PUC asignada, pero sin impuestos finales.

### D. Agente Tributario (Tax Specialist)

- **Rol:** El experto fiscal. Recibe una transacción clasificada y calcula las obligaciones tributarias accesorias (Retefuente, ReteICA, IVA descontable vs generado).
- **Herramientas Clave:**
    - `search_tax_law`: Consulta el Estatuto Tributario en la base vectorial.
    - `tax_calculator`: Función determinística (código Python) para aplicar porcentajes. **Nunca** calcula mentalmente.
- **Salida:** Objeto `Transaction` enriquecido con valores de impuestos y cuentas de pasivo (2365, 2408).

### E. Agente Auditor (Internal Control / Critic)

- **Rol:** El filtro de calidad. Valida la integridad de la información antes de guardarla.
- **Validaciones:**
    1. **Partida Doble:** Suma de Débitos == Suma de Créditos.
    2. **Lógica de Negocio:** Detecta anomalías (ej: "Compra de licor" clasificada como "Gastos de Representación" sin justificación, o facturas duplicadas).
- **Salida:** `Approved` (pasa a guardar) o `Rejected` (devuelve al Contador con feedback de error).

### F. Agente Reportero (Analyst)

- **Rol:** Generador de entregables. Se activa solo cuando el usuario solicita informes.
- **Acción:** Consulta la base de datos SQL (Libro Mayor) y genera documentos finales.
- **Salida:** Archivos PDF (Balance General) o Excel (Auxiliares).

## 2. Arquitectura de Doble Memoria (Dual-RAG Architecture)

Para que el sistema sea funcional, debemos separar estrictamente los datos públicos (normativa) de los datos privados (documentos de la empresa). Implementaremos dos pipelines de ingesta distintos.

### 2.1. RAG Normativo (Base de Conocimiento Global)

*Objetivo:* Proporcionar contexto legal y técnico. Es de solo lectura para los agentes.

### A. Ingesta de Conocimiento

Necesitas crear una base de datos vectorial con los documentos mencionados en tu bibliografía (Estatuto Tributario, Ley 43 de 1990, NIIF).

1. **Chunking (Fragmentación):**
    - **Estrategia:** *Parent Document Retriever*.
    - *Razón:* Un artículo de ley (ej. Art 383 del ET) no debe cortarse a la mitad. Se indexan fragmentos pequeños para búsqueda, pero se recupera el artículo completo (Parent) para el contexto del LLM.
2. **Almacenamiento (Vector DB):**
    - Colección: `normativa_colombia_v1`
    - Tecnología: **ChromaDB (Local)**.
3. **Recuperación:**
    - **Hybrid Search (BM25 + Vector):** Esencial para leyes. Si el agente busca "Artículo 383", la búsqueda por palabras clave (BM25) es superior a la semántica.

### 2.2. RAG Operativo (Base Documental de la Empresa)

*Objetivo:* Almacenar facturas, extractos bancarios y RUTs de terceros para que el sistema pueda "recordar" transacciones pasadas o consultar soportes.

### A. Arquitectura de la Base de Datos de la Empresa

A diferencia de la normativa, aquí necesitamos almacenar tanto vectores (para buscar) como datos estructurados (para sumar).

1. **Componente Estructurado (SQL - SQLite/PostgreSQL):**
    - *Función:* Almacena el "Libro Diario". Aquí van las transacciones ya procesadas (Fecha, Tercero, Valor, Cuenta PUC).
    - *Por qué:* Los vectores no sirven para hacer balances; necesitas SQL para garantizar integridad transaccional.
2. **Componente No Estructurado (Vector Store - Privado):**
    - *Función:* Almacena el contenido crudo de las facturas PDF ingeridas.
    - *Colección:* `empresa_{nit}_docs` (Aislamiento por cliente).
    - *Uso:* Permite al agente responder preguntas como: "¿Cuánto pagamos a Claro el mes pasado?" o "¿Cuál es la actividad económica en el RUT de este proveedor?".

### B. Pipeline de Ingesta de Documentos de la Empresa (ETL)

El "Agente de Ingesta" ejecutará este flujo cada vez que se sube un archivo:

1. **Clasificación Inicial:**
    - El modelo determina si el archivo es: Factura Electrónica (XML/PDF), Extracto Bancario (Excel/PDF) o Documento Legal (RUT/Cámara de Comercio).
2. **Extracción (Vision-to-Text):**
    - Usar modelo multimodal de HF para leer el documento.
    - *Prompt:* "Extrae la fecha, NIT emisor, NIT receptor, items y totales en formato JSON".
3. **Vectorización (Embedding):**
    - Se guarda el texto completo del documento en la colección `empresa_{nit}_docs`.
    - *Metadata:* Se etiqueta con `{tipo: "factura", fecha: "2026-01-15", proveedor: "X"}` para permitir filtrado híbrido (ej: "Busca facturas de Enero" -> Filtro de metadatos + Búsqueda vectorial).

## 3. Stack Tecnológico Sugerido (Costo $0 - Freemium)

| Componente | Tecnología Recomendada | Razón |
| --- | --- | --- |
| **Orquestación** | **LangGraph** (Python) | Requisito del proyecto y estándar actual para multi-agentes cíclicos. |
| **LLM (Cerebro)** | **Gemini 2.5 Flash** (vía Google AI Studio) / HF si ratelimits muy bajos | **Context Window masivo (1M tokens)**. Puede leer el PDF completo de una factura o un balance sin RAG para la etapa de ingesta. Tiene un tier gratuito muy generoso. |
| **Embeddings** | **HuggingFace** (`multilingual-MiniLM`) | Gratuito, local, bueno en español. |
| **Vector DB** | **ChromaDB** | Local, permite múltiples colecciones (`normativa` y `empresa_docs`) sin costo. |
| **DB Estructurada** | **SQLite** (Dev) / **PostgreSQL** (Prod) | Para el libro contable real (Transacciones, Saldos). |
| **Validación** | **Pydantic** | Para obligar a los agentes a responder en JSON estricto (no texto libre). |
| **Herramientas** | **Pandas** (Dataframes) | Para manejo de libros contables (Excel/CSV). El LLM genera código Pandas, no hace sumas mentales. |

## 4. Diagramas de Flujo de Datos Diferenciados (Pipelines)

Para garantizar modularidad y eficiencia, se separan los procesos de entrada de datos (Ingestión) del razonamiento contable (Procesamiento).

### 4.1. Pipeline 1: Ingestión y Digitalización (Data Entry)

*Objetivo:* Transformar archivos físicos/digitales en datos estructurados y almacenarlos en cola de espera. **En este flujo NO participan los agentes contables ni auditores.**

1. **Usuario** -> Carga de Archivos (PDF/XML/Imágenes) en la interfaz.
2. **Supervisor** -> Detecta evento `FILE_UPLOAD` -> Enruta a **Agente de Ingesta**.
3. **Agente de Ingesta (Worker Único)**:
    - **Paso A (OCR/Vision):** Utiliza Gemini Flash o modelo de visión de HF para leer el documento completo.
    - **Paso B (Extracción Estructurada):** Extrae campos clave (Fecha, NIT, Subtotal, IVA, Total) a formato JSON estandarizado.
    - **Paso C (Persistencia Operativa - Vectorial):** Guarda el contenido del documento en ChromaDB (`empresa_docs`) para memoria a largo plazo (RAG).
    - **Paso D (Persistencia Transaccional - Staging):** Guarda el JSON extraído en una tabla SQL temporal (`transacciones_pendientes`) con estado `PENDING`.
4. **Supervisor** -> Confirma recepción y finaliza el flujo A.

### 4.2. Pipeline 2: Procesamiento Contable y Auditoría (Accounting Loop)

*Objetivo:* Tomar datos estructurados de la cola, aplicar normativa, calcular impuestos y generar el asiento contable definitivo. **En este flujo NO participa el agente de ingesta.**

1. **Trigger** -> Usuario solicita "Contabilizar pendientes" o proceso batch automático.
2. **Supervisor** -> Lee tabla `transacciones_pendientes` -> Enruta a **Agente Contador**.
3. **Agente Contador**:
    - *Input:* JSON crudo (ej: "Compra Éxito - Suministros - $50.000").
    - *Acción RAG 1 (Histórico):* Consulta `empresa_docs` para ver antecedentes con este proveedor.
    - *Acción RAG 2 (Normativo):* Consulta `normativa` para asignar cuenta PUC (ej: 5195).
    - *Output:* Objeto `Transaction` clasificado preliminarmente. -> Retorna a Supervisor.
4. **Supervisor** -> Enruta a **Agente Tributario**.
5. **Agente Tributario**:
    - *Input:* Transacción con cuenta PUC.
    - *Acción RAG (Fiscal):* Consulta `normativa` (Estatuto Tributario) para validar tarifas de retención vigentes.
    - *Output:* Calcula Retefuente, ReteICA, IVA generado/descontable y actualiza el objeto `Transaction`. -> Retorna a Supervisor.
6. **Supervisor** -> Enruta a **Agente Auditor**.
7. **Agente Auditor**:
    - *Validación:* Verifica ecuación patrimonial y consistencia lógica.
    - *Decisión:*
        - **Si OK:** Cambia estado a `POSTED`, mueve datos al Libro Diario (SQL Definitivo) y marca fin del proceso.
        - **Si Error:** Agrega feedback de error al estado y devuelve el control al **Agente Contador** para corrección.