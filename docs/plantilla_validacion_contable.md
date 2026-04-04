# Plantilla de Validacion Contable por Documento

Objetivo: validar rapido si el asiento generado por el pipeline representa correctamente el hecho economico del documento fuente.

## 1) Datos Base

- Fecha de validacion:
- Validador:
- Documento (nombre/ID):
- Tipo documental esperado (ej. comprobante de egreso, factura de venta, etc.):
- ingest_id:
- process_id:
- company_nit:

## 2) Resultado Tecnico del Pipeline

- Ingest status: [completed | failed]
- Process status: [completed | failed | skipped]
- extraction_errors:
- error_code / remediation:

Criterio:
- Si Ingest o Process no estan en completed, el documento queda NO APROBADO (tecnico).

## 3) Validacion de Partida Doble

- Total debitos:
- Total creditos:
- Cuadra (debitos == creditos): [si | no]

Criterio:
- Si no cuadra, NO APROBADO (contable).

## 4) Validacion de Naturaleza por Cuenta PUC

Lista de lineas del asiento:
- Cuenta PUC:
  - Nombre:
  - Movimiento: [debito | credito]
  - Valor:
  - Naturaleza esperada para este hecho economico:
  - Coincide: [si | no]

Criterio:
- Revisar que el sentido del movimiento corresponda al evento:
  - Egreso/pago: suele acreditar caja-bancos y debitar gasto o pasivo.
  - Ingreso/venta: suele acreditar ingreso y debitar caja/clientes.

## 5) Validacion Tributaria Basica

- IVA calculado coherente con base gravable: [si | no | n/a]
- Retefuente aplicable segun tipo de operacion: [si | no | n/a]
- ReteICA aplicable segun actividad/municipio: [si | no | n/a]
- Referencias legales incluidas: [si | no]

## 6) Consistencia con Documento Fuente

- Tercero (NIT) correcto:
- Fecha correcta:
- Valor total correcto:
- Descripcion/concepto correcto:
- Soporte documental suficiente:

Criterio:
- Si hay discrepancias materiales (fecha, valor, tercero), NO APROBADO.

## 7) Decision Final

- Estado: [APROBADO | APROBADO CON OBSERVACIONES | NO APROBADO]
- Hallazgos clave:
- Ajuste sugerido al agente/regla:
- Evidencia (JSON de process result / libro auxiliar):

## 8) Checklist Rapido (Semaforo)

- Tecnico (ingest+process): [verde | amarillo | rojo]
- Partida doble: [verde | rojo]
- Naturaleza PUC: [verde | amarillo | rojo]
- Tributario: [verde | amarillo | rojo]
- Calidad global: [verde | amarillo | rojo]