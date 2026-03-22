# Investigación: APIs para datos normativos y tributarios colombianos

> Fecha de investigación: 2026-03-22

## Contexto del sistema

El sistema PAE usa tres archivos JSON como base del RAG normativo y varios valores hardcodeados para los cálculos tributarios:

**Archivos JSON (RAG normativo):**
- `data/ley_43_1990.json` – 13 artículos de la Ley 43 de 1990
- `data/normativa_tributaria.json` – 20+ artículos del Estatuto Tributario
- `data/puc_accounts.json` – ~40 cuentas del Plan Único de Cuentas (PUC)

**Valores hardcodeados en el codebase** (archivos principales: `app/agents/tributario_agent.py`, `app/api/v1/settings.py`, `alembic/versions/8fb1b0855393_initial_schema.py`):

| Concepto | Valores | Referencia legal |
|----------|---------|-----------------|
| Retefuente Servicios | 11% | Art. 383/392 ET |
| Retefuente Bienes | 3% | Art. 401 ET |
| Retefuente Arrendamiento | 10% | Art. 401 ET |
| ReteICA default nacional | 0.69‰ | Decreto 2048/1992 |
| ReteICA Bogotá | 4.14‰ – 13.8‰ por sector CIIU | Acuerdo 050/2024 |
| ReteICA Medellín | 2‰ flat | Acuerdo 093/2023 |
| ReteICA Cali | 4.14‰ – 11.04‰ por sector | Acuerdo 0294/2014 |
| ReteICA Barranquilla | 5.4‰ – 20‰ por sector | Acuerdo 006/2023 |
| ReteICA Bucaramanga | 5‰ flat | Municipal |
| ReteICA (16 ciudades más) | 6.9‰ – 9.66‰ referencia | Varios acuerdos |
| IVA general | 19% | Art. 468 ET |
| IVA reducido | 5% | Art. 468-1 ET |
| IVA exento | 0% | Art. 477 ET |

---

## Veredicto general: NO existe una API pública, gratuita y completa

Colombia **no tiene un equivalente a TaxJar** (EE.UU.) ni a plataformas similares para impuestos. La información tributaria está dispersa entre la DIAN, 1.100+ municipios y el Congreso. Ninguna entidad oficial expone REST APIs para consulta de tarifas.

---

## Análisis detallado por categoría

### 1. Estatuto Tributario y Ley 43 (los JSON normativos)

| Fuente | Tipo acceso | Costo | ¿API REST? |
|--------|-------------|-------|-----------|
| DIAN Normograma (`normograma.dian.gov.co`) | Portal web HTML | Gratis | ❌ No |
| Secretaría del Senado (`secretariasenado.gov.co`) | HTML / PDF descargable | Gratis | ❌ No |
| SUIN-JURISCOL (`suin-juriscol.gov.co`) | Portal web HTML | Gratis | ❌ No |

**Conclusión**: Los textos legales solo están disponibles como páginas HTML o PDFs en portales oficiales. Se podría hacer scraping pero es frágil y costoso de mantener.

**Frecuencia de cambio**: El Estatuto Tributario cambia con cada Ley de Financiamiento (típicamente 1 vez por año). La Ley 43 de 1990 es estructural y cambia muy rara vez.

**Recomendación**: Mantener los JSON manualmente y revisar después de cada reforma tributaria anual.

---

### 2. PUC – Plan Único de Cuentas

| Fuente | Tipo acceso | Costo | ¿API REST? |
|--------|-------------|-------|-----------|
| Contaduría General de la Nación | PDF/Word descargable | Gratis | ❌ No |
| `puc.com.co` / `elpuc.com` | Sitios web de referencia | Gratis | ❌ No |
| **Alegra API** (`developer.alegra.com`) | REST API `/cuentas-contables` | Freemium | ✅ Sí* |
| **Siigo API** (`siigoapi.docs.apiary.io`) | REST API `/v1/account-groups` | Pagado enterprise | ✅ Sí* |
| **Helisa API** (`helisa.com/api`) | REST API `GET /accountList` | Pagado | ✅ Sí* |

*\* Importante: Alegra, Siigo y Helisa devuelven las cuentas contables **de la empresa registrada en ese software**, no el catálogo PUC nacional completo. No son un reemplazo directo del JSON.*

**Conclusión**: El PUC oficial (Decreto 2650 de 1993) no tiene API y cambia muy poco. El JSON actual es suficiente y bien estructurado.

---

### 3. IVA (Impuesto al Valor Agregado)

| Tarifa | Valor | Fuente legal | ¿API disponible? |
|--------|-------|-------------|-----------------|
| General | **19%** | Art. 468 ET | ❌ No oficial |
| Diferencial | **5%** | Art. 468-1 ET | ❌ No oficial |
| Exento | **0%** | Art. 477 ET | ❌ No oficial |

**APIs de terceros con soporte Colombia:**

| API | Colombia IVA | Costo | Notas |
|-----|-------------|-------|-------|
| **Avalara AvaTax** (`developer.avalara.com`) | ✅ Sí | Enterprise / custom | Cubre 19%, no Retefuente ni ReteICA |
| **TaxJar** | ❌ No | – | No soporta Colombia para nuevos clientes |

**Conclusión**: La tasa de IVA cambia raramente (última modificación en 2017, de 16% a 19%). Mantener hardcodeado es razonable. Si se quisiera automatización: solo Avalara, pero con costo enterprise.

---

### 4. Retefuente (Retención en la Fuente)

| Tipo | Tasa | Art. ET | ¿API disponible? |
|------|------|---------|-----------------|
| Servicios | **11%** | Art. 383 / 392 | ❌ No existe |
| Bienes | **3%** | Art. 401 | ❌ No existe |
| Arrendamiento | **10%** | Art. 401 | ❌ No existe |

**Conclusión**: No existe ninguna API oficial ni de terceros para tarifas de Retefuente colombiana. Las tasas las publica DIAN en el Estatuto Tributario y solo cambian con reformas tributarias.

---

### 5. ReteICA (Retención de Industria y Comercio municipal)

Este es el caso más complejo: es un **impuesto municipal** — cada alcaldía fija sus propias tarifas por acuerdo municipal.

| Ciudad | Tarifa vigente | Fuente normativa | ¿API? |
|--------|---------------|-----------------|-------|
| Bogotá | 2‰ unificado (2024) | Acuerdo 050/2024, Secretaría Hacienda Bogotá | ❌ No |
| Medellín | Variable por sector | Acuerdo 093/2023, Alcaldía Medellín | ❌ No |
| Cali | Variable por CIIU | Acuerdo 0294/2014, Alcaldía Cali | ❌ No |
| Barranquilla | Variable por sector | Acuerdo 006/2023, Alcaldía Barranquilla | ❌ No |
| Bucaramanga | 5‰ flat | Acuerdo municipal | ❌ No |

**Conclusión**: **No existe ninguna API nacional ni municipal para ReteICA.** Cada municipio publica sus acuerdos como PDFs. El equipo debe monitorear cambios cuando los municipios actualizan sus acuerdos (generalmente cada 2-4 años).

---

### 6. UVT (Unidad de Valor Tributario)

| Año | Valor COP | Fuente |
|-----|-----------|--------|
| **2026** | **$52,374** | DIAN Resolución 000238 (diciembre 2025) |
| 2025 | $49,799 | DIAN Resolución nov 2024 |
| 2024 | $47,065 | DIAN Resolución |

**API disponible**: No existe API oficial. DIAN publica el valor anualmente en noviembre/diciembre via resolución. Portales como `uvt.com.co` y el blog de Siigo lo replican informalmente.

**Acción identificada**: El UVT 2026 es **$52,374**. Actualmente no está centralizado en el codebase como constante configurable — está disperso o no presente. Debería agregarse como variable de configuración.

---

### 7. APIs de DIAN (facturación electrónica y validación)

Estas APIs existen pero son para **operaciones**, no para consultar normativa ni tarifas:

| API | Qué hace | Costo | Documentación |
|-----|---------|-------|---------------|
| **MATIAS API** (`matias-api.com`) | Generación facturas electrónicas DIAN (UBL 2.1) | Pagado | `docs.matias-api.com` |
| **Invopop** (`docs.invopop.com/guides/countries/co-dian`) | Envío facturas a DIAN via Plemsi | Pagado | `docs.invopop.com` |
| **Apitude** (`apitude.co`) | Validación RUT/NIT, consulta declaraciones DIAN Formato 110/210 | Pagado | `apitude.co/en/docs` |
| **Plemsi** (`plemsi.com`) | Proveedor autorizado DIAN facturación | Pagado | `plemsi.com` |

---

### 8. APIs de software contable colombiano

Estas APIs son para **integración con el ERP del cliente** (crear facturas, registrar movimientos), no para reemplazar datos normativos:

| API | Qué ofrece relevante | Free tier | Auth | Docs |
|-----|---------------------|-----------|------|------|
| **Alegra** | Cuentas contables, impuestos, retenciones, facturas | ✅ Freemium | Basic Auth (email:token) | `developer.alegra.com` |
| **Siigo** | Facturas, grupos de cuentas, asientos, terceros | ❌ Enterprise | API key | `siigoapi.docs.apiary.io` |
| **Helisa** | PUC, balance, P&G, terceros, documentos | ❌ Pagado (~$193k-$397k/mes COP) | Company code | `helisa.com/api` |
| **Nominapp** | Nómina electrónica, PILA | ✅ Freemium | N/A | `nominapp.com` |
| **World Office Cloud** | Contabilidad, ventas, inventario | ❌ Pagado | REST Auth | `devapidoc.worldoffice.cloud` |

---

### 9. Datos abiertos del gobierno colombiano

| Fuente | Qué tiene | Costo | Útil para nuestro caso |
|--------|----------|-------|----------------------|
| **datos.gov.co** | Estadísticas de recaudo DIAN por tipo de impuesto (2005-2019+) | Gratis | ❌ Solo datos macro, no tarifas |
| **World Bank** (`data.worldbank.org`) | Indicadores: tax revenue % GDP, impuestos renta Colombia | Gratis | ❌ Solo datos macro |
| **CEPALSTAT** | Revenue Statistics América Latina (OECD classification) | Gratis | ❌ Solo datos macro |

---

## Resumen ejecutivo

### Lo que NO existe en Colombia:
1. ❌ API oficial DIAN para consultar tarifas de impuestos
2. ❌ API para el texto del Estatuto Tributario o la Ley 43
3. ❌ API nacional para tarifas ReteICA municipales
4. ❌ API oficial para el catálogo PUC completo
5. ❌ API para el valor de la UVT
6. ❌ TaxJar / herramienta similar con soporte Colombia

### Lo que SÍ existe (pero con limitaciones):
1. ✅ **Alegra API** (freemium) — cuentas contables, impuestos y retenciones, pero de empresas registradas en Alegra, no catálogo nacional
2. ✅ **Siigo/Helisa** (pagado enterprise) — ídem, integración ERP
3. ✅ **Avalara AvaTax** (enterprise/caro) — cálculo IVA Colombia, pero no Retefuente ni ReteICA
4. ✅ **MATIAS API / Invopop / Apitude** (pagado) — facturación electrónica y validación DIAN, no tarifas

---

## Recomendación de estrategia

**Los JSON actuales son la mejor opción disponible.** La alternativa realista no es reemplazarlos con APIs externas (no existen para este propósito), sino establecer un proceso claro de actualización manual:

### Mejoras recomendadas al sistema actual

1. **Centralizar la UVT** como constante configurable en `app/core/config.py`. Valor vigente 2026: **$52,374**.

2. **Agregar campo `vigencia_desde`** a los registros JSON para rastrear cuándo fue la última verificación/actualización.

3. **Establecer calendario de revisión**:
   - Diciembre/enero: UVT nueva (DIAN publica en noviembre)
   - Después de cada Ley de Financiamiento: Estatuto Tributario
   - Cuando municipios actualicen sus acuerdos: tarifas ReteICA

4. **Agregar un script de verificación** en `scripts/` que imprima un resumen de cuándo se actualizó cada archivo JSON y qué fuente verificar.

### Si en el futuro hay presupuesto
- **Avalara** para IVA automático (enterprise, precio custom)
- **Alegra API** (freemium) como fuente auxiliar para verificar PUC de clientes reales vs. nuestro catálogo

---

## Archivos del codebase con datos hardcodeados

| Archivo | Qué contiene |
|---------|-------------|
| [app/agents/tributario_agent.py](../app/agents/tributario_agent.py) | Tasas Retefuente, ReteICA default, IVA, cuentas PUC (236540, 240802, 240808, 240815) |
| [app/api/v1/settings.py](../app/api/v1/settings.py) | Defaults nacionales de tarifas como parámetros de configuración |
| [app/models/schemas.py](../app/models/schemas.py) | Defaults en los schemas Pydantic |
| [app/models/database.py](../app/models/database.py) | Defaults en columnas de base de datos |
| [alembic/versions/8fb1b0855393_initial_schema.py](../alembic/versions/8fb1b0855393_initial_schema.py) | Seed de tarifas ReteICA municipales (20 ciudades, líneas 246-352) |
| [data/normativa_tributaria.json](../data/normativa_tributaria.json) | Artículos ET para el RAG normativo |
| [data/puc_accounts.json](../data/puc_accounts.json) | Cuentas PUC para el RAG normativo |
| [data/ley_43_1990.json](../data/ley_43_1990.json) | Ley 43 de 1990 para el RAG normativo |
