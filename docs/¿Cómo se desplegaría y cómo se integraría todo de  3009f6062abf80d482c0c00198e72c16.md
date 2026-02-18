# ¿Cómo se desplegaría y cómo se integraría todo de forma correcta?

Status: Done
Sprint: Sprint 1
Assignee: Juan Pablo Mejia Gomez
Due Date: February 14, 2026

## 1. Infraestructura general

### Principios guía

- **Simplicidad > hiper-optimización**
- **Reproducibilidad** (cualquiera puede correr el sistema)
- **Trazabilidad** (fundamental en contabilidad)
- **Despliegue sencillo** (1–2 servicios principales)

### Propuesta general

- **Arquitectura en capas**
- **Backend monolítico modular** (no microservicios)
- **Agentes como módulos desacoplados**
- **Despliegue en cloud académico o local**
- **Infraestructura “container-first”**

## 2. Backend: arquitectura lógica

### Stack recomendado

| Componente | Elección |
| --- | --- |
| Lenguaje | **Python** |
| Framework API | **FastAPI** |
| Orquestación agéntica | LangGraph / LangChain |
| Procesamiento datos | Pandas, PyArrow |
| PDFs | pdfplumber / PyMuPDF |
| Excel | openpyxl / pandas |
| LLM | OpenAI / HF / local |
| Auth simple | JWT |
| Contenedores | Docker |

### Capas del backend

```
┌─────────────────────────┐
│        API Layer        │  ← FastAPI
├─────────────────────────┤
│     Orquestador IA      │  ← Agente coordinador
├─────────────────────────┤
│     Agentes IA          │
│  (clasificación, IVA…)  │
├─────────────────────────┤
│     Lógica contable     │  ← Reglas + normativa
├─────────────────────────┤
│     ETL / Ingesta       │
├─────────────────────────┤
│     Persistencia        │
└─────────────────────────┘
```

### 4. APIs (cómo se integra todo)

### API principal (FastAPI)

### Ingesta

```
POST /ingest/upload
```

- Excel / PDF
- Retorna ID del procesamiento

### Procesamiento contable

Este endpoint inicia el procesamiento de una serie de documentos

```
POST /process/accounting/{ingest_id}
```

### Generación de reportes

```
GET /reports/balance
GET /reports/pnl
GET /reports/cashflow
```

### Tributario

```
GET /tax/iva
GET /tax/withholdings
```

### Evaluación

```
GET /evaluation/run
```

### Status

```
GET /health
```

### API interna entre agentes

Comunicación vía:

- Python calls
- Scratchpad
- Mds temporales
- Estado compartido (context object)
- LangGraph state machine

## 5. Frontend

### Stack recomendado

| Componente | Elección |
| --- | --- |
| Framework | **React o Next.js** |
| UI | MUI |
| Charts | Recharts |
| Auth | JWT simple |

### Vistas clave

1. **Carga de documentos**
2. **Estado de procesamiento**
3. **Libros contables**
4. **Reportes financieros**
5. **Alertas / Evaluación**
6. **Explicaciones del agente**

Sería bueno **mostrar el razonamiento**, no solo los resultados.

## 6. Despliegue

1. AWS / GCP / Azure (free tier) (no sé si los profes de nube nos regalen alguito ahí)
2. Render / Railway / Nano / DigitalOcean (tenemos unos créditos gratis usando el Github Education)
- Backend + DB

Se tendría que crear un docker compose para contenerizar todo y que sea fácil desplegar

## 7. Seguridad

- JWT
- Roles simples (usuario / admin)
- Logs inmutables

## 8. Testing

Unos cúantos unitario para la lógica contable, de integración para agentes y base de datos, y unos cuántos end-to-end para el flujo completo

## 9. Prácticas de desarrollo

1. Establecer con anterioridad cuáles van a ser los contratos que vamos a usar para comunicarnos entre backend y frontend
2. Usar devcontainer, linters, formatters y LSP para desarrollo.
3. Usar diferentes branches para desarrollar features o realizar fixes específicos, luego se abre pr para hacer merge a main. Lo ideal es que todos podamos revisar las PRs de todos
4. Usar .env y añadirlo al .gitignore
5. Realizar comentarios cuando haga falta, poner nombres dicientes a variables
6. REVISAR BIEN LO QUE HACE LA IA
7. Establecer bien como evaluar y testear
8. Escribir documentación corta y clara
9. Simplificar y dividir