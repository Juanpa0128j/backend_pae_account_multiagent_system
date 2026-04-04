# Resumen CE y DS - Checklist de Funcionamiento

Fecha de corte: 2026-03-27
Fuente: estados consultados en API (`/api/v1/ingest/{id}` y `/api/v1/process/status/{id}`).

## CE - Comprobantes de Egreso

- [x] CE 1052.jpg
  - ingest_id: ing_1774632514_a38acac1
  - process_id: proc_1774632531_dee91293
  - Ingest: completed
  - Process: completed
  - Observaciones: sin errores de pipeline.

- [x] CE 1058.jpg
  - ingest_id: ing_1774632551_76b53f18
  - process_id: proc_1774632567_46ab7d80
  - Ingest: completed
  - Process: completed
  - Observaciones: sin errores de pipeline.

- [x] CE 1059.jpg
  - ingest_id: ing_1774632584_34f8e45d
  - process_id: proc_1774632612_e750fdcf
  - Ingest: completed
  - Process: completed
  - Observaciones: sin errores de pipeline.

- [x] CE 1060.jpg
  - ingest_id: ing_1774632635_09425980
  - process_id: proc_1774632725_d96e8cb0
  - Ingest: completed
  - Process: completed
  - Observaciones: en una corrida previa hubo timeout del script de polling, pero el proceso en backend terminó en completed.

- [x] CE 1061.jpg
  - ingest_id: ing_1774632846_5d840cb9
  - process_id: proc_1774632864_688880ed
  - Ingest: completed
  - Process: completed
  - Observaciones: revisar calidad de extracción de NIT receptor (`nit_receptor=3A` en raw_transactions).

## DS - Documentos Soporte

- [x] DS 226.jpg
  - ingest_id: ing_1774637919_e308d319
  - process_id: proc_1774637992_139531cf
  - Ingest: completed
  - Process: completed
  - Observaciones: sin errores de pipeline.

- [x] DS 227.jpg
  - ingest_id: ing_1774638145_27fa6ade
  - process_id: proc_1774638162_064f3b1f
  - Ingest: completed
  - Process: completed
  - Observaciones: sin errores de pipeline.

- [x] DS 228.jpg
  - ingest_id: ing_1774638224_3aa82bd8
  - process_id: proc_1774638239_c302fad4
  - Ingest: completed
  - Process: completed
  - Observaciones: revisar calidad de extracción de monto (`total=0.0` en raw_transactions).

## Resumen Ejecutivo

- CE con pipeline OK: 5/5
- DS con pipeline OK: 3/3
- Pendientes por calidad de datos (no bloqueantes de pipeline):
  - CE 1061 (NIT receptor)
  - DS 228 (total en 0)