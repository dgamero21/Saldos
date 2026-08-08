# AGENT_STATE — Loop de agentes: migración Sheets/Drive → Supabase

> Este archivo es el estado persistente del loop de agentes.
> Baseline oficial: auditoría completa (18 secciones) — no re-auditar.
> Fuente de verdad: GitHub `main` @ `4b39ccf`.

---

## ESTADO ACTUAL

```
FASE ACTUAL:        FASE 4 — Bot: escritura (IMPLEMENTADO + VALIDADO)
ESTADO:             PENDIENTE GATE — código listo, tests verdes, falta aprobación
AGENTE ACTUAL:      CODE REVIEW
```

**OBJETIVO DE LA FASE:**
- Migrar las ESCRITURAS del bot (Consolidado, Consumos, Ingresos y fijos
  mensuales) desde Google Sheets a Supabase como persistencia PRINCIPAL.
- SIN doble escritura: Supabase escribe; Sheets NO recibe escrituras (ni
  accidentales). Si Supabase falla → error explícito `[SUPABASE WRITE ERROR]`,
  nunca insertar en Sheets en silencio.
- Preservar la semántica de dedup 1:1 (garantizada por los UNIQUE INDEX de la
  DB: `lower(remitente)|vto|monto`, `fecha|comprobante|detalle|cuota_total|
  remitente`, `fecha|tipo|origen`) y la lógica de cuotas (nunca retroceden).
- NO eliminar `gspread`/`GOOGLE_*`/`SHEET_ID` (respaldo de LECTURA). NO tocar
  `api/webhook.js` (FASE 7).

**CAMBIOS REALIZADOS:**
- `supabase_client.py` — MODIFICADO: capa de ESCRITURA (FASE 4):
  - `SupabaseWriteError`; `_get_conn(for_write=True)` (errores de escritura);
    helpers `_a_fecha`/`_a_monto`/`_a_int`; `_write_in_transaction(fn)`
    (transacción + reintento por UniqueViolation en carreras concurrentes).
  - `guardar_consolidado()` → UPSERT idempotente `ON CONFLICT
    (lower(remitente), fecha_vencimiento, monto_total) DO NOTHING`;
    devuelve `'insertado' | 'existente'` (equivale a guardar_en_sheet +
    es_registro_duplicado).
  - `guardar_o_actualizar_consumos()` → insert / update solo si
    `cuota_actual >= existente` (nunca retrocede); comprobante RAW;
    devuelve estados por ítem `'insertado'|'actualizado'|'sin_cambios'`.
  - `guardar_ingreso()` → `ON CONFLICT (fecha, tipo, origen) DO NOTHING`,
    ID legacy reproducible (`fecha|Ingreso|tipo|origen`).
  - `existe_consolidado()` → lectura de dedup (1:1 con es_registro_duplicado).
- `bot_Saldo.py` — MODIFICADO (escrituras → Supabase, sin tocar Sheets):
  - `guardar_en_sheet()` → delega en `guardar_consolidado()` (ws ignorado).
  - `guardar_o_actualizar_consumos_sheet()` → delega en
    `guardar_o_actualizar_consumos()`.
  - `es_registro_duplicado()` → Supabase primero (lectura) + fallback Sheets.
  - `procesar_fijos_mensuales()` → fijos gastos/ingresos se escriben en
    Supabase (mensajes Telegram SOLO para los insertados).
  - Telegram manual (`MANUAL|`/`INGRESO|`) → `guardar_o_actualizar_consumos`
    / `guardar_ingreso`; ya no pide la hoja Consumos/Ingresos.
- `tests/test_fase4_write.py` — NUEVO (unit con mocks: upsert, cuota no
  retrocede, IDs legacy, errores explícitos, Sheets NO recibe escrituras,
  reintento UniqueViolation, es_registro_duplicado Supabase-first).
- `tests/test_fase4_integracion.py` — NUEVO (DB real con sentinels + cleanup:
  dedup consolidado/consumos/ingresos, fijos y cambio de mes, concurrencia
  6 hilos → 1 fila, regresión vs lógica anterior).
- `tests/test_fase3_integracion.py` — ACTUALIZADO: tests de fijos validan la
  nueva ruta de escritura (Supabase) y que sin credenciales la escritura falla
  explícito sin tocar Sheets.
- `tests/test_smoke.py` — ACTUALIZADO: baseline de blob de `bot_Saldo.py` al
  estado FASE 4 (hash nuevo, comentado).

**TESTS EJECUTADOS:**
- Con `SUPABASE_DBPW` (DB real): `pytest tests/` → 89 passed, 5 skipped,
  1 failed (solo `test_smoke.py::test_working_tree_limpio_para_produccion`,
  esperado: `bot_Saldo.py` modificado sin commitear; se resuelve al commitear
  el baseline tras el gate).
- Sin credenciales: 70 passed, 24 skipped, 1 failed (mismo smoke).
- Suite FASE 4 específica: `test_fase4_write.py` 22/22 +
  `test_fase4_integracion.py` 12/12 PASS (dedup real, concurrencia, regresión).
- Validación de conflict targets contra DB real (EXPLAIN): OK en las 3 tablas.
- Verificado: sin residuos de pruebas en la DB (sentinels limpiados; fijos
  `Fijo Config`/`SUELDO` de una corrida fallida purgados).

**TESTS FALLIDOS:**
- `tests/test_smoke.py::test_working_tree_limpio_para_produccion` — POR DISEÑO:
  FASE 4 modifica `bot_Saldo.py` (trackeado) de forma intencional; el guard de
  FASE 0 se actualiza al commitear el nuevo baseline (post-gate).

**PROBLEMAS ABIERTOS:**
- Escrituras a Sheets desactivadas (FASE 4): si Supabase no está configurado,
  cualquier escritura falla explícito (`SupabaseNotConfiguredError` /
  `[SUPABASE WRITE ERROR]`) — comportamiento intencional (sin doble escritura).
- Bucket `pdfs` PRIVADO / signed URLs: decisión FASE 6.
- Gmail/Drive/Telegram/webhook/secrets: FASE 5-8.
- `es_registro_duplicado` conserva fallback de LECTURA a Sheets (respaldo).

**RIESGOS:**
- Mientras la hoja siga existiendo, escrituras nuevas van SOLO a Supabase: la
  hoja queda como histórico/lectura. Migración definitiva = FASE 10.
- `comprobante` en DB es TEXT raw; en Sheets quedó normalizado por
  `USER_ENTERED`. La dedup usa el raw (como el ID_Consumo legacy).

**DECISIONES:**
- UPSERT idempotente apoyado en los UNIQUE INDEX (integridad en PostgreSQL,
  no SELECT+INSERT) → seguro bajo concurrencia cron + dispatch.
- Sin fallback de escritura a Sheets (solo lecturas tienen fallback).
- Mensajes de fijos solo para registros realmente insertados.

**CRITERIO DE PASS (FASE 4):**
- [x] Consolidado: inserta / dedup / monto distinto / vto distinto / case
- [x] Consumos: insert / update por cuota / cuota menor NO retrocede /
      pesos/dólares/cierre/vto preservados / comprobante legacy raw
- [x] Ingresos: insert / dedup / ID reproducible
- [x] Fijos: gasto / ingreso / dup / cambio de mes
- [x] Concurrencia: N escrituras simultáneas → 1 fila (UNIQUE de la DB)
- [x] Regresión: estado final DB == lógica anterior (consumos y consolidado)
- [x] Sheets NO recibe escrituras accidentales (tests con mocks)
- [x] Sin hardcodear secretos; `GOOGLE_*`/`SHEET_ID` intactos; webhook intacto
- [x] Suite completa con DB real: 89 passed / 5 skipped / 1 smoke esperado

**PRÓXIMA ACCIÓN:**
- Reportar GATE FASE 4 y esperar aprobación del usuario antes de FASE 5.
  No avanzar de fase sin gate. Tras aprobación: commitear baseline (resuelve
  el smoke) y arrancar FASE 5 (Gmail).

---

## LOG POR FASE

### FASE 0 — Sincronización y base del loop — **PASS 2026-08-08**
- **ANALISTA**: merge-base = local → fast-forward limpio; verificado HEAD y blobs; guard `__main__` presente; detectado env-var a nivel de módulo (líneas 26-28).
- **ARQUITECTO**: diseño base: `.gitignore`, `tests/conftest.py`, `tests/test_smoke.py`, `requirements-dev.txt`, venv `.venv`; env dummies para import del bot.
- **IMPLEMENTADOR**: sincronización ff-only a `4b39ccf`; creados los archivos base; venv + deps instalados; sin tocar producción.
- **TESTER**: `pytest` 7/7 PASS.
- **CODE REVIEW**: PASS. Verificación post-review: tests verdes tras ediciones de AGENT_STATE (sin impacto en código).

### FASE 1 — Infraestructura Supabase (DISEÑO) — **PASS de diseño 2026-08-08 (NO EJECUTADO)**
- **ANALISTA**: relevado modelo real (funciones de escritura de bot_Saldo.py + CSVs reales de Config/Datos/Consolidado/Consumos/Ingresos). Detectado: comprobante padded en ID_Consumo legacy; hoja Config híbrida; Pertenece (David/Nayla).
- **ARQUITECTO**: diseñado schema (7 tablas, dedup UNIQUE 1:1, RLS sin políticas públicas, bucket `pdfs` PRIVADO), mapeo Sheets→DB, estrategia storage (signed URLs vs público — riesgo documentado).
- **IMPLEMENTADOR**: generados `db/schema.sql`, `db/seed.sql`, `db/rollback.sql`, `db/validate.sql`, `db/README.md`; `tests/test_db_connectivity.py`, `tests/test_dedup_schema.py`; actualizado `requirements-dev.txt`.
- **TESTER**: 13 passed + 5 skipped; SQL parses OK con sqlglot; cobertura de columnas 7/7 OK. Detectó y se corrigieron: `CREATE POLICY IF NOT EXISTS` (inválido en PG) y `to_char` en GENERATED (STABLE → columna inválida; se cambió a TEXT legacy).
- **CODE REVIEW**: PASS. Revisado RLS (revoke de schema revertido por riesgo PostgREST), grants de secuencias (sin config_id_seq), producción intacta, rollback restaurado.

### FASE 1 — Infraestructura Supabase (EJECUCIÓN) — **PASS 2026-08-08**
- **IMPLEMENTADOR**: conectividad vía pooler (Postgres 17.6) OK; aplicado `db/schema.sql` (7 tablas, 3 UNIQUE dedup, índices, RLS, bucket `pdfs` + 3 policies service_role); aplicado `db/seed.sql` (config 3, categorias 8 — NIÑERA U+00D1 verificado, reglas 7); verificado estado real por consulta directa.
- **TESTER**: `db/validate.sql` 10/10 PASS; `pytest tests/` → 18 passed 0 skipped; manuales: dedup 5/5 (UniqueViolation en las 4 tablas), RLS 5/5 (service_role OK, anon/authenticated bloqueados), Storage 10/10 (PDF ok, no-PDF 415, >10MB 413, anon 403/404/0 filas, cleanup ok). Residuos 0.
- **CODE REVIEW**: PASS. Verificado por consulta: grants solo service_role, anon/authenticated sin privilegios, RLS activo, bucket privado. Estado final: EJECUTADO + VALIDADO.

### FASE 2 — Migración de datos (CSV → Supabase) — **PASS 2026-08-08**
- **ANALISTA**: inventario completo de los 5 CSVs (conteos reales: Consolidado 112, Consumos 416, Ingresos 1, Datos 7, Config 6); análisis P1-P8 de comprobantes legacy (178 IDs con padding; la columna fue normalizada por Sheets; el raw vive en el ID_Consumo); simulación UNIQUE 0 colisiones.
- **ARQUITECTO**: decisión de NO normalizar comprobantes; se preserva el raw del ID_Consumo legacy como `comprobante`; mapeo columna a columna documentado en `migracion/README.md`; `valor_original`/`valor_normalizado` documentados.
- **IMPLEMENTADOR**: creados `migracion/import_data.py` (idempotente, dry-run, parseadores), `migracion/validate_migracion.py`, `migracion/rollback_data.sql`, `migracion/README.md`. Corregido transform_config (3 config rows vía header, no valores). Detectado y corregido: `reglas` sin UNIQUE duplicaba en re-import → purgado + `uq_reglas_dedup`.
- **TESTER**: migración real OK (112/416/1); `validate_migracion.py` → DIFERENCIA 0, 6/6 tablas 1:1; dedup 4/4 sin duplicados; idempotencia re-run → 0 insertados; rollback+re-import OK; `pytest` 18/18; `validate.sql` 10/10 + 8b.
- **CODE REVIEW**: PASS. Producción intacta. Estado final: EJECUTADO + VALIDADO.

### FASE 3 — Bot: lectura (Supabase con fallback Sheets) — **PASS 2026-08-08**
- **ANALISTA**: relevadas las 4 lecturas de Sheets en bot_Saldo.py (obtener_reglas, debe_ejecutar_ahora/Hora_Ejecucion, leer_config_completo, procesar_fijos_mensuales); confirmado que el resto de lecturas pertenecen a rutas de escritura (FASE 4).
- **ARQUITECTO**: capa de acceso `supabase_client.py` SOLO LECTURA (errores explícitos, `_read_only` bloquea DDL/DML, DSN desde entorno); helper `_leer_con_supabase` con fallback documentado; decisión de no normalizar `Hora_Ejecucion='12'`.
- **IMPLEMENTADOR**: creado `supabase_client.py`; integrado en bot_Saldo.py (import + 4 lecturas con fallback Sheets explícito y `[SUPABASE READ ERROR]`); añadido `tzdata` al venv (ZoneInfo en Windows).
- **TESTER**: `test_supabase_client.py` 12/12, `test_fase3_integracion.py` 12/12, `test_fase3_equivalencia.py` 7/7 (DB real + CSV); suite completa 58 passed (2 fallos solo en test_smoke de FASE 0, esperados por modificación intencional de la working tree).
- **CODE REVIEW**: PASS. Verificado: solo 4 lecturas migradas (resto intacto), formato de reglas 1:1, fallback por función, secretos solo por env, GOOGLE_*/SHEET_ID intactos.
- **GATE**: APROBADO por el usuario el 2026-08-08 (los 2 fallos de test_smoke aceptados como esperados; no bloquean FASE 4).

### FASE 4 — Bot: escritura (Supabase, sin doble escritura) — **IMPLEMENTADO + VALIDADO (PENDIENTE GATE) 2026-08-08**
- **ANALISTA**: relevadas las rutas de escritura de Sheets (guardar_en_sheet, guardar_o_actualizar_consumos_sheet, es_registro_duplicado, fijos mensuales, Telegram manual MANUAL|/INGRESO|); mapeo de dedup 1:1 con los UNIQUE INDEX (test_dedup_schema).
- **ARQUITECTO**: UPSERT idempotente con ON CONFLICT sobre los UNIQUE dedup (integridad en PostgreSQL bajo concurrencia); transacción + reintento por UniqueViolation; sin fallback de escritura a Sheets (error explícito `[SUPABASE WRITE ERROR]`); cuota nunca retrocede; comprobante raw.
- **IMPLEMENTADOR**: capa de escritura en `supabase_client.py` (guardar_consolidado, guardar_o_actualizar_consumos, guardar_ingreso, existe_consolidado, _write_in_transaction); migradas las escrituras de `bot_Saldo.py` a Supabase (ws ignorado); fijos y Telegram manual sin tocar la hoja.
- **TESTER**: `test_fase4_write.py` 22/22 (mocks, Sheets no escribe); `test_fase4_integracion.py` 12/12 (DB real: dedup, cuotas, fijos, concurrencia 6 hilos → 1 fila, regresión vs lógica anterior, sentinels limpiados); validación EXPLAIN de conflict targets 3/3; suite completa con DB real 89 passed / 5 skipped / 1 smoke esperado (working_tree por no commitear aún).
- **CODE REVIEW**: PASS. Verificado: sin doble escritura (ws ignorado), secretos por env, webhook intacto, GOOGLE_*/SHEET_ID intactos, residuos 0 en DB.

---

## ORDEN DE FASES (del baseline, no alterar sin razón técnica)

```text
FASE 0  → Sincronización
FASE 1  → Infraestructura Supabase
FASE 2  → Migración de datos
FASE 3  → Bot: lectura
FASE 4  → Bot: escritura
FASE 5  → Gmail
FASE 6  → Drive → Supabase Storage
FASE 7  → Webhook → Supabase
FASE 8  → Secrets / Deploy
FASE 9  → Integración / Regresión
FASE 10 → Migración definitiva
```

## DEDUPLICACIÓN A PRESERVAR (1:1)

```text
Consolidado:  remitente|vto|monto
Consumos:     fecha|comprobante|detalle|cuota|remitente
Ingresos:     fecha|Ingreso|categoria|Manual Telegram
```
