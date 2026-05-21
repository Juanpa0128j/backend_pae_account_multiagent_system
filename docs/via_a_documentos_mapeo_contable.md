# Mapeo Contable de Documentos — Vía A

> **Propósito.** Documento de referencia para revisar la lógica de creación de **Balance General (BG)**, **Estado de Resultados (ER)** y **Libro Auxiliar (LA)** a partir de los documentos fuente que recibe el agente Vía A (`build_from_scratch`). Cada sección describe el documento, los asientos contables que produce, y el impacto esperado sobre cada estado financiero.
>
> **Convenciones.**
>
> - PUC = Plan Único de Cuentas (Decreto 2650/1993).
> - Notación: `D` = débito, `C` = crédito.
> - Cuentas de impuestos según convenciones 2026 vigentes en `CLAUDE.md`: Retefuente por pagar = `2365`, ReteICA por pagar = `2368`, ICA gasto admón = `511505`, ICA gasto ventas = `521505`.
> - Todo asiento debe cumplir `Σ débitos == Σ créditos`.
> - El agente tributario añade retenciones automáticamente — el contador **no** debe duplicarlas.

---

## Índice

1. [Cuenta de Cobro](#1-cuenta-de-cobro-cuenta_cobro)
2. [Conciliación Bancaria](#2-conciliación-bancaria-conciliacion_bancaria)
3. [Extracto Bancario](#3-extracto-bancario-extracto_bancario)
4. [FRA — Formato de Recaudo / Reporte de Aportes](#4-fra--formato-de-recaudo--reporte-de-aportes)
5. [Pago Alianza Fiduciaria (Fidu)](#5-pago-alianza-fiduciaria-fidu)
6. [Cesantías](#6-cesantías-nómina-componente-prestacional)
7. [Planilla de Seguridad Social — PILA](#7-planilla-de-seguridad-social--pila-planilla_seguridad_social)
8. [Recibo de Pago de Impuesto](#8-recibo-de-pago-de-impuesto-recibo_pago_impuesto)
9. [Declaración del Impuesto sobre las Ventas — IVA](#9-declaración-iva-declaracion_iva)
10. [Anexo Autorretención ICA](#10-anexo-autorretención-ica-autorretencion_ica)
11. [Tabla resumen de impacto por estado financiero](#tabla-resumen-de-impacto-por-estado-financiero)

---

## 1. Cuenta de Cobro (`cuenta_cobro`)

**Qué es.** Documento que emite un proveedor **no obligado a facturar** (típicamente persona natural prestadora de servicios sin régimen IVA) para cobrar honorarios o servicios. No causa IVA.

**Campos clave a extraer.**

- Emisor (NIT/CC, nombre).
- Concepto.
- Valor bruto.
- Fecha.
- (Opcional) número de documento, referencia contrato.

**Asiento contable.**

```
D 511505/511525/etc.   Gasto según concepto (honorarios, servicios técnicos…)   valor_bruto
C 220505               Cuentas por pagar — Proveedores                          valor_bruto
```

> El agente tributario añade retenciones después: `D 220505 / C 2365 (retefuente) / C 2368 (reteICA)`.
> Cuando se paga: `D 220505 / C 111005 (banco)`.

**Impacto en estados financieros.**

| Estado | Cuenta(s) | Movimiento |
|--------|-----------|------------|
| **BG** | `220505` Proveedores (pasivo corriente) | Aumenta |
| **ER** | `511xxx`/`521xxx` Gasto operacional | Aumenta gasto → reduce utilidad |
| **LA** | `220505`, `511xxx` | Entrada con débito/crédito y saldo acumulado |

---

## 2. Conciliación Bancaria (`conciliacion_bancaria`)

**Qué es.** Documento que reconcilia el saldo del extracto bancario con el saldo contable de la cuenta `111005`. Identifica partidas conciliatorias: cheques en tránsito, depósitos en tránsito, notas bancarias no contabilizadas, errores.

**Campos clave a extraer.**

- Periodo conciliado (fecha de corte).
- Cuenta bancaria (número y banco).
- Saldo según libros vs saldo según extracto.
- Partidas conciliatorias (cada una con concepto, valor, naturaleza).

**Asientos contables.**

Genera asientos **solo** para partidas conciliatorias que no estén ya registradas en libros. Ejemplos:

- **Nota bancaria de cobro (banco abonó intereses al cliente):**

```
D 111005   Banco                     valor_intereses
C 421005   Intereses ganados         valor_intereses
```

- **Comisiones bancarias no registradas:**

```
D 530505   Gastos bancarios          valor_comision
C 111005   Banco                     valor_comision
```

- **Cheque en tránsito:** NO genera asiento (ya está contabilizado, solo es timing).
- **Error de digitación:** asiento de ajuste según naturaleza del error.

**Impacto en estados financieros.**

| Estado | Cuenta(s) | Movimiento |
|--------|-----------|------------|
| **BG** | `111005` Banco | Aumenta o disminuye según partida |
| **ER** | `421005` Ingresos financieros, `530505` Gastos bancarios | Según corresponda |
| **LA** | `111005`, `42xxxx`/`53xxxx` | Una entrada por partida conciliatoria |

> **Regla crítica:** Si el documento no especifica partidas conciliatorias nuevas, **no se generan asientos**. La conciliación que coincide perfectamente no produce contabilidad.

---

## 3. Extracto Bancario (`extracto_bancario`)

**Qué es.** Reporte del banco con todos los movimientos de una cuenta durante un periodo. Cada línea es un movimiento (débito o crédito desde la perspectiva del cliente — débito = retiro, crédito = consignación).

**Campos clave a extraer.**

- Cuenta bancaria, banco, periodo.
- Saldo inicial y final.
- Por cada movimiento: fecha, descripción, valor débito, valor crédito, saldo.

**Asientos contables.**

Cada movimiento genera **un asiento de 2 líneas**:

- **Consignación recibida (entrada de dinero):**

```
D 111005   Banco                     valor
C 130505/41xxxx/22xxxx   Contraparte según concepto
```

- **Retiro / Pago realizado:**

```
D 5xxxx/22xxxx   Gasto o pago de pasivo
C 111005   Banco                     valor
```

**Reglas específicas (ver `app/core/prompts/contador.py`).**

- El valor del asiento es el campo `debito` o `credito` del movimiento — **no** el saldo acumulado.
- **No** se agregan retenciones desde el contador — las añade el agente tributario.

**Impacto en estados financieros.**

| Estado | Cuenta(s) | Movimiento |
|--------|-----------|------------|
| **BG** | `111005` Banco | Refleja saldo final del periodo |
| **ER** | Variable (ingresos/gastos detectados) | Depende de la contraparte de cada movimiento |
| **LA** | `111005` y cada contraparte | Una entrada por cada movimiento del extracto |

---

## 4. FRA — Formato de Recaudo / Reporte de Aportes

> **Aclaración pendiente.** En contexto colombiano, "FRA" puede significar:
>
> - **Formato de Recaudo de Aportes** (formato cooperativas/asociaciones).
> - **Formato de Reporte de Aportes** parafiscales (SENA, ICBF, Caja).
> - **Factura de Recaudo Anticipado** (recaudo de servicios).
>
> Confirmar con Vanessa cuál corresponde antes de implementar la regla definitiva.

**Hipótesis: FRA = aporte parafiscal / cooperativo recaudado por un tercero**

**Campos clave.**

- Entidad recaudadora.
- Concepto del recaudo.
- Periodo.
- Valor recaudado.
- Detalle por aportante (si aplica).

**Asiento contable (caso recaudo de aportes a pagar).**

```
D 510527/510530/510533   Aportes EPS/ARL/Pensión (gasto empleador)   valor_empleador
D 237005/237006/237010   Aportes empleador por pagar (pasivo)        valor_empleador
C 237005/237006/237010   Aportes por pagar                           valor_total
C 111005                 Banco (si ya se pagó)                       valor_pagado
```

> Si es un **certificado** de aportes ya pagados, solo registra la causación contra la cuenta de pasivo correspondiente y el banco.
> Si es **únicamente reporte/comprobante** de que el aporte se hizo, NO genera asiento nuevo (es soporte).

**Impacto en estados financieros.**

| Estado | Cuenta(s) | Movimiento |
|--------|-----------|------------|
| **BG** | `237xxx` Retenciones y aportes de nómina | Aumenta pasivo (causación) o disminuye (pago) |
| **ER** | `510527`/`510530`/`510533` Aportes empleador | Aumenta gasto operacional |
| **LA** | `237xxx`, `510xxx`, `111005` | Entradas según naturaleza del FRA |

---

## 5. Pago Alianza Fiduciaria (Fidu)

**Qué es.** Pago realizado a o desde un encargo/fondo en una sociedad fiduciaria (e.g., Alianza Fiduciaria). Puede ser:

- Aporte de la empresa al patrimonio autónomo (inversión).
- Cobro de comisión fiduciaria (gasto).
- Rendimientos generados (ingreso).
- Redención / retiro de fondos (recuperación de inversión).

**Campos clave.**

- Tipo de movimiento (aporte / comisión / rendimiento / redención).
- Encargo fiduciario (identificación).
- Valor.
- Fecha.

**Asientos contables (según el caso).**

- **Aporte al fondo:**

```
D 122505   Inversiones en fideicomisos       valor_aporte
C 111005   Banco                             valor_aporte
```

- **Comisión fiduciaria cobrada por la sociedad:**

```
D 530505   Gastos bancarios y financieros    valor_comision
C 111005   Banco                             valor_comision
```

- **Rendimientos abonados al fondo:**

```
D 122505   Inversiones en fideicomisos       valor_rendimiento
C 421005   Rendimientos financieros          valor_rendimiento
```

- **Redención del fondo:**

```
D 111005   Banco                             valor_redencion
C 122505   Inversiones en fideicomisos       valor_redencion
```

**Impacto en estados financieros.**

| Estado | Cuenta(s) | Movimiento |
|--------|-----------|------------|
| **BG** | `122505` Inversiones, `111005` Banco | Reclasificación entre activos |
| **ER** | `530505` Gastos financieros, `421005` Rendimientos | Según naturaleza |
| **LA** | `122505`, `111005`, `421005`/`530505` | Entrada por cada movimiento |

> **Regla crítica:** Distinguir si el pago **aumenta el patrimonio del encargo** (es inversión, va al activo) o si es **gasto** (comisión). Leer cuidadosamente el extracto fiduciario.

---

## 6. Cesantías (nómina, componente prestacional)

**Qué es.** Prestación social anual equivalente a un mes de salario por año trabajado. Se causa mensualmente como provisión y se consigna al fondo de cesantías antes del 14 de febrero del año siguiente.

> Este documento generalmente **no llega como documento fuente independiente**, sino como parte del proceso de nómina mensual o como **certificado de consignación al fondo de cesantías**.

**Asientos contables.**

- **Causación mensual (provisión):**

```
D 510510   Cesantías (gasto)                                            valor_provision
D 510515   Intereses sobre cesantías (gasto)                            valor_intereses
C 252005   Cesantías consolidadas (pasivo prestacional)                 valor_provision
C 252010   Intereses sobre cesantías consolidadas (pasivo prestacional) valor_intereses
```

- **Consignación al fondo (14 feb):**

```
D 252005   Cesantías consolidadas             valor_consignado
C 111005   Banco                              valor_consignado
```

- **Pago directo al empleado (retiro, liquidación):**

```
D 252005   Cesantías consolidadas             valor_pagado
C 111005   Banco                              valor_pagado
```

**Impacto en estados financieros.**

| Estado | Cuenta(s) | Movimiento |
|--------|-----------|------------|
| **BG** | `252005`/`252010` Pasivos laborales | Aumenta con causación, disminuye con pago |
| **ER** | `510510`/`510515` Gastos de personal | Aumenta gasto operacional |
| **LA** | `252005`, `252010`, `510510`, `510515`, `111005` | Entrada según naturaleza |

---

## 7. Planilla de Seguridad Social — PILA (`planilla_seguridad_social`)

**Qué es.** Planilla Integrada de Liquidación de Aportes — documento que liquida y reporta los aportes mensuales al Sistema de Seguridad Social Integral: salud (EPS), pensión, ARL, parafiscales (Caja, SENA, ICBF). Es obligatoria para todo empleador.

**Campos clave a extraer.**

- Periodo de cotización.
- Total aportes salud (empleador + empleado).
- Total aportes pensión (empleador + empleado).
- Total ARL (solo empleador).
- Total parafiscales (Caja, SENA, ICBF — solo empleador, según tipo de cotizante).
- Detalle por empleado (opcional).

**Asiento contable.**

```
D 510527   Aportes EPS empleador (8.5% de IBC)                    aporte_salud_empleador
D 510533   Aportes pensión empleador (12% de IBC)                 aporte_pension_empleador
D 510530   Aportes ARL empleador (0.522% a 6.96% según riesgo)    aporte_arl
D 510568   Aportes Caja de Compensación (4% de IBC)               aporte_caja
D 510569   Aportes SENA (2% de IBC, si aplica)                    aporte_sena
D 510570   Aportes ICBF (3% de IBC, si aplica)                    aporte_icbf

D 237006   Salud empleado por pagar (4% de IBC, retenido en nómina)   aporte_salud_empleado
D 237005   Pensión empleado por pagar (4% de IBC, retenido en nómina) aporte_pension_empleado

C 237006   EPS por pagar                                           aporte_salud_total
C 237005   Pensión por pagar                                       aporte_pension_total
C 237010   ARL por pagar                                           aporte_arl
C 237025   Caja, SENA, ICBF por pagar                              aporte_parafiscales
```

Al pagar (mediante banco / PSE):

```
D 237xxx   Cada cuenta de aportes por pagar      valor_correspondiente
C 111005   Banco                                 valor_total_planilla
```

**Impacto en estados financieros.**

| Estado | Cuenta(s) | Movimiento |
|--------|-----------|------------|
| **BG** | `237005`, `237006`, `237010`, `237025` Pasivos de nómina | Aumenta con causación, se cancela con el pago |
| **ER** | `510527`–`510570` Gastos de personal — aportes empleador | Aumenta gasto operacional |
| **LA** | Todas las cuentas anteriores + `111005` | Entrada por cada concepto |

> **Importante:** Los aportes del **empleado** (salud 4%, pensión 4%) ya fueron retenidos al pagar la nómina y registrados como `237005`/`237006` en ese momento. La PILA solo registra los aportes del **empleador** y consolida los empleados para pagarlos a las entidades.
>
> **Para evitar duplicación:** si la nómina mensual ya registró los aportes empleado, la PILA debe registrar **solo** los aportes empleador. El contador debe leer el contexto (si ya hay un asiento de nómina del mismo periodo).

---

## 8. Recibo de Pago de Impuesto (`recibo_pago_impuesto`)

**Qué es.** Comprobante de pago efectivo de un impuesto previamente causado (IVA, retefuente, ICA, renta, etc.).

**Campos clave a extraer.**

- Tipo de impuesto.
- Periodo gravable.
- Valor pagado.
- Fecha de pago.
- Número de formulario (opcional).

**Asiento contable.**

```
D 240802/2365/2368/240405   Impuesto por pagar (la cuenta del pasivo causado)   valor_pagado
C 111005                    Banco                                                valor_pagado
```

Cuentas según impuesto:

| Impuesto | Cuenta debitada |
|----------|----------------|
| IVA | `240802` |
| Retefuente | `2365` |
| ReteICA | `2368` |
| ICA propio | `2368` |
| Renta | `240405` |
| Autorretención CREE/renta | `236575` |

**Impacto en estados financieros.**

| Estado | Cuenta(s) | Movimiento |
|--------|-----------|------------|
| **BG** | Impuesto por pagar (pasivo), `111005` Banco | Disminuye pasivo, disminuye banco |
| **ER** | — | No impacta resultado (es liquidación de pasivo previamente causado) |
| **LA** | Cuenta del impuesto, `111005` | Entrada de cancelación |

---

## 9. Declaración IVA (`declaracion_iva`)

**Qué es.** Formulario DIAN 300 — declaración bimestral/cuatrimestral del Impuesto sobre las Ventas. Liquida saldo a pagar o saldo a favor del periodo.

**Campos clave.**

- Periodo declarado.
- IVA generado (ventas).
- IVA descontable (compras).
- Saldo a pagar (o a favor).
- Retenciones IVA practicadas.
- Anticipos aplicados.

**Asientos contables.**

- **Si hay saldo a pagar:**

```
D 240802   IVA por pagar                       saldo_a_pagar
C 111005   Banco                               saldo_a_pagar
```

> Si se causa hoy pero se paga después:
>
> ```
> D 240802   IVA por pagar (cierra el saldo del periodo)
> C 240802   IVA por pagar (queda como saldo final a pagar)  # idempotente, omitir si no aplica
> ```

- **Si hay saldo a favor:**

```
D 135505   Saldo a favor IVA (activo)          saldo_a_favor
C 240802   IVA por pagar                       saldo_a_favor
```

**Impacto en estados financieros.**

| Estado | Cuenta(s) | Movimiento |
|--------|-----------|------------|
| **BG** | `240802` IVA por pagar, `135505` Saldo a favor IVA, `111005` Banco | Cierre del IVA del periodo |
| **ER** | — | No impacta resultado |
| **LA** | `240802`, `135505`, `111005` | Asiento de liquidación |

> **Regla crítica:** Este documento **liquida** el IVA del periodo, no causa IVA por operaciones individuales. El IVA generado/descontable ya está contabilizado mes a mes en cada factura.

---

## 10. Anexo Autorretención ICA (`autorretencion_ica`)

**Qué es.** Documento de soporte para la autorretención del Impuesto de Industria y Comercio que algunas empresas (clasificadas como autorretenedoras por el municipio) deben practicarse sobre sus propios ingresos brutos.

**Campos clave.**

- Periodo.
- Base gravable (ingresos brutos del periodo, antes de IVA).
- Tarifa aplicada (por mil, varía por municipio y actividad).
- Valor autorretenido.
- Municipio.

**Asiento contable.**

```
D 521505/511505   ICA (gasto operacional — ventas o admón. según uso)   valor_autorretenido
C 2368            ReteICA por pagar / Autorretención ICA                 valor_autorretenido
```

> Cuando se paga al municipio:
>
> ```
> D 2368   ReteICA por pagar
> C 111005 Banco
> ```

**Impacto en estados financieros.**

| Estado | Cuenta(s) | Movimiento |
|--------|-----------|------------|
| **BG** | `2368` ReteICA por pagar | Aumenta pasivo corriente |
| **ER** | `511505`/`521505` ICA gasto | Aumenta gasto operacional → reduce utilidad |
| **LA** | `2368`, `511505`/`521505` | Entrada de causación |

> **No** duplicar con declaraciones de ICA (`declaracion_ica`): la autorretención se hace durante el periodo, la declaración solo paga el saldo final.

---

## Tabla resumen de impacto por estado financiero

| Documento | BG (cuentas afectadas) | ER (cuentas afectadas) | LA (entradas) |
|-----------|------------------------|------------------------|---------------|
| Cuenta de Cobro | `220505` (+) | `511xxx`/`521xxx` (+) | 2 |
| Conciliación Bancaria | `111005` (±) | `421005`/`530505` (±) según partida | N (1 por partida) |
| Extracto Bancario | `111005` (saldo final) | Variable | N (1 por movimiento) |
| FRA aportes | `237xxx` (±) | `510527`–`510570` (+) | 2–4 |
| Pago Fiduciaria | `122505`/`111005` (±) | `530505`/`421005` (±) | 2 |
| Cesantías (causación) | `252005`/`252010` (+) | `510510`/`510515` (+) | 4 |
| Cesantías (pago) | `252005` (−), `111005` (−) | — | 2 |
| Planilla SS (PILA) | `237xxx` (±) | `510527`–`510570` (+) | 8–10 |
| Recibo Pago Impuesto | Impuesto por pagar (−), `111005` (−) | — | 2 |
| Declaración IVA (saldo a pagar) | `240802` (−), `111005` (−) | — | 2 |
| Declaración IVA (saldo a favor) | `135505` (+), `240802` (cierre) | — | 2 |
| Autorretención ICA | `2368` (+) | `511505`/`521505` (+) | 2 |

**Leyenda:** (+) aumenta, (−) disminuye, (±) puede aumentar o disminuir.

---

## Reglas transversales para revisar la lógica

1. **Validación de partida doble:** todo asiento debe cumplir `Σ débitos == Σ créditos`. El campo `cuadre` del estado financiero derivado debe ser `true`.
2. **No duplicar impuestos:** el contador no añade retenciones ni IVA — el agente tributario maneja eso. Solo los documentos que **liquidan** impuestos (`declaracion_iva`, `recibo_pago_impuesto`, `autorretencion_ica`) tocan directamente cuentas de impuestos.
3. **Idempotencia mensual:** documentos como conciliación bancaria o anexos NO deben re-procesar movimientos ya contabilizados desde el extracto. Solo registran lo nuevo / lo conciliatorio.
4. **Causación vs pago:** distinguir si el documento causa (aumenta pasivo/gasto) o paga (cancela pasivo). Pago contra banco siempre cierra `111005`.
5. **Cuentas de gasto específicas:** evitar `519595` (Diversos) y `5195`. Usar la cuenta del PUC seed que corresponda al concepto. Si no existe, escalar antes de cerrar el periodo.
6. **Periodo:** verificar que `period_start` ≤ fecha del documento ≤ `period_end` del estado que se está derivando.

---

## Pendientes y aclaraciones requeridas

- **FRA** — Confirmar significado exacto con Vanessa antes de cerrar la regla de contabilización.
- **Cesantías** — Coordinar con Jhonsis el formato de entrada (planilla mensual vs certificado consignación).
- **Pago alianza fiduciaria** — Confirmar si los extractos de Alianza incluyen detalle por movimiento o solo saldo neto.
- **Conciliación bancaria** — Pedir el formato correcto (Vanessa anotó que el actual no sirve).
