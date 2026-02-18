# Estructura de validación y evaluación

Status: Done
Sprint: Sprint 1
Assignee: Jhon Edison Pinto Hincapie
Due Date: February 14, 2026

# ACLARACIÓN

estos temas de contaduría son bastantes delicados, por eso opté por soluciones de cierto modo determinísticas que tengan la capacidad de tener en cuenta la calidad de las diferentes partes del producto. La implementación con un evaluador llm puede ser posible pero no como algo principal, se le puede meter LangSmith para ver lo que hace internamente los agentes.

## 1. Validación Estructural del Sistema

La validación estructural verifica que el sistema produzca salidas **formalmente correctas**, independientemente del contenido semántico de las decisiones contables.

### 1.1 Validación de Esquemas y Tipos de Datos

Todas las salidas generadas por los agentes (Ingesta, Contador, Tributario y Auditor) deben cumplir estrictamente esquemas predefinidos mediante modelos Pydantic.

Esto asegura que:

- No existan respuestas en lenguaje natural no estructurado.
- Todos los campos obligatorios estén presentes.
- Los tipos de datos (fechas, valores numéricos, códigos PUC) sean válidos.

Cualquier salida que no cumpla el esquema es rechazada automáticamente por el Agente Supervisor y reenviada para corrección.

**Métrica asociada:**

- *Schema Compliance Rate*: proporción de salidas que cumplen correctamente el esquema definido.

### 1.2 Validación de Integridad Contable

El sistema valida automáticamente principios contables fundamentales, especialmente el principio de **partida doble**.

El Agente Auditor verifica de forma determinística que la suma de los débitos sea igual a la suma de los créditos para cada transacción registrada. Esta validación no depende del modelo de lenguaje, sino de cálculos numéricos ejecutados en código.

**Métrica asociada:**

- *Double-Entry Error Rate*: porcentaje de transacciones que violan la partida doble.

## 2. Validación Funcional por Agente

Este nivel evalúa si cada agente cumple correctamente su rol especializado dentro de la arquitectura Supervisor–Worker.

### 2.1 Validación del Agente de Ingesta

El Agente de Ingesta es evaluado en su capacidad para extraer información correcta desde documentos contables estructurados y no estructurados.

Se validan los siguientes aspectos:

- Correcta extracción de campos clave (fecha, NIT, totales).
- Clasificación correcta del tipo de documento.
- Detección de documentos duplicados.

Las salidas se comparan con un dataset de referencia previamente validado.

**Métricas asociadas:**

- *Field Extraction Accuracy*
- *Document Classification Accuracy*
- *Duplicate Detection Precision*

### 2.2 Validación del Agente Contador

El Agente Contador es evaluado en su capacidad para clasificar transacciones según el Plan Único de Cuentas (PUC) colombiano.

La cuenta PUC asignada por el sistema se compara con la cuenta definida en el dataset de referencia (ground truth), validado por un contador humano o un sistema contable tradicional.

**Métricas asociadas:**

- *PUC Assignment Accuracy*
- *Top-3 PUC Match Rate*
- *Historical Consistency Score* (coherencia con registros históricos del mismo proveedor).

### 2.3 Validación del Agente Tributario

El Agente Tributario es evaluado en la correcta aplicación de impuestos y retenciones conforme a la normativa colombiana.

Cada cálculo tributario debe estar respaldado por una referencia normativa recuperada mediante el RAG normativo (artículo del Estatuto Tributario, decreto o norma aplicable).

Los valores calculados se comparan numéricamente contra el dataset de referencia.

**Métricas asociadas:**

- *Tax Calculation Accuracy*
- *Legal Reference Coverage*
- *False Tax Application Rate*

### 2.4 Validación del Agente Auditor

El Agente Auditor cumple un rol de control interno y validación final.

Para su evaluación, se introducen intencionalmente errores contables y tributarios en las transacciones, con el fin de verificar su capacidad para detectarlos y rechazarlos correctamente.

**Métricas asociadas:**

- *Error Detection Recall*
- *False Rejection Rate*
- *Audit Pass Rate*

## 3. Evaluación del RAG (Retrieval-Augmented Generation)

### 3.1 Evaluación del RAG Normativo

El RAG normativo se evalúa en términos de precisión y trazabilidad legal.

Se valida que:

- Los artículos legales recuperados correspondan a la consulta realizada.
- Las decisiones tributarias estén respaldadas por normativa vigente.
- No existan respuestas sin soporte documental (alucinaciones).

**Métricas asociadas:**

- *Article Retrieval Accuracy*
- *Context Precision*
- *Hallucination Rate*

### 3.2 Evaluación del RAG Operativo

El RAG operativo se evalúa mediante consultas históricas sobre transacciones y proveedores.

Las respuestas del sistema se comparan con los registros almacenados en la base de datos estructurada.

**Métricas asociadas:**

- *Historical Query Accuracy*
- *Semantic Retrieval Precision*
- *Metadata Filtering Accuracy*

## 4. Evaluación Comparativa Global

Finalmente, se realiza una evaluación end-to-end del sistema completo.

Un conjunto de transacciones reales o simuladas es procesado por:

- El sistema agéntico propuesto.
- Un contador humano o sistema contable de referencia.

Se comparan los resultados finales en términos de asientos contables, cálculos tributarios y reportes generados.

**Métricas globales:**

- *End-to-End Accuracy*
- *Número promedio de iteraciones Supervisor–Worker*
- *Reducción del tiempo de procesamiento*
- *Human Intervention Rate*

# Otras opciones para medir las métricas del sistema

Las métricas propuestas pueden medirse mediante **múltiples enfoques complementarios**, que van desde evaluación determinística clásica hasta evaluación asistida por LLMs y validación humana. A continuación, se describen las principales alternativas.

## 1. Evaluación asistida por LLM (LLM-as-a-Judge)

### ¿Qué es?

Un modelo de lenguaje evalúa la salida de otro modelo, comparándola contra criterios o contra una referencia.

### Ejemplo:

```
Dado el asiento contable generado y el asiento de referencia,
evalúe si la clasificación contable es correcta y explique por qué.
```

### ¿Qué métricas puede cubrir?

- Coherencia contable
- Calidad semántica
- Justificación de decisiones
- Detección de errores sutiles

### Tecnologías:

- GPT-4 / Gemini Pro
- LangChain evaluators como LangSmith
- Prompt-based grading

### Ventajas:

- Evalúa razonamiento, no solo exactitud exacta
- Útil para casos ambiguos (PUC debatible)
- Escalable sin humanos

### Desventajas (críticas del jurado):

- No es determinístico
- Riesgo de sesgo circular
- No es fuente de verdad legal

**Uso recomendado en el proyecto:**

Evaluación **secundaria cualitativa**, nunca métrica principal.

---

## 2. Evaluación Humana (Human-in-the-Loop)

### ¿Qué es?

Un contador humano revisa una muestra de salidas del sistema (NO OLVIDEN QUE GUZMAN TIENE UNA CONOCIDA QUE DEPRONTO NOS AYUDA A VALIDARLO).

### ¿Qué se evalúa?

- Corrección contable general
- Cumplimiento normativo
- Aceptabilidad profesional

### Tecnologías:

- Formularios estructurados (Google Forms)
- Checklist de validación
- Muestreo estratificado

### Ventajas:

- Alta credibilidad académica
- Contexto real del dominio
- Ideal para casos límite

### Desventajas:

- Costoso
- No escalable
- Subjetivo si no se estructura

**Uso recomendado:**

 Validación final o piloto, no evaluación masiva.