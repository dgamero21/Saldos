# AGENT_STATE — Loop de agentes: migración Sheets/Drive → Supabase

> Este archivo es el estado persistente del loop de agentes.
> Baseline oficial: auditoría completa (18 secciones) — no re-auditar.
> Fuente de verdad: GitHub `main` @ `4b39ccf`.

---

## ESTADO ACTUAL

```
FASE ACTUAL:        FASE 6 — Drive -> Supabase Storage (ANÁLISIS)
ESTADO:             EN PROGRESO
AGENTE ACTUAL:      ANALISTA
```

**OBJETIVO DE LA FASE:**
- Reemplazar progresivamente Google Drive por Supabase Storage para los PDFs
  del bot, manteniendo el comportamiento funcional actual.
- Mantener bucket `pdfs` PRIVADO; preferir signed URLs si son compatibles.
- Preservar: PDFs protegidos, límite 10 MB, `application/pdf`, caso especial
  EPEC sin almacenamiento, mismo procesamiento PDF y misma deduplicación.
- NO tocar `api/webhook.js`, NO retirar aún credenciales Google y NO eliminar
  referencias a Drive hasta validar FASE 6.

**CAMBIOS REALIZADOS:**
- GATE FASE 5 aprobado por el usuario; pendiente commitear el baseline y luego
  ejecutar nuevamente la suite completa antes de avanzar con la implementación
  de FASE 6.

**TESTS EJECUTADOS:**
- FASE 5 antes del commit baseline: `pytest tests/` → 103 passed, 5 skipped,
  1 failed (solo smoke de working tree esperado).

**TESTS FALLIDOS:**
- Ninguno confirmado post-commit todavía. Pendiente rerun completo tras cerrar el
  baseline de FASE 5.

**PROBLEMAS ABIERTOS:**
- El label Gmail sigue existiendo como respaldo/observabilidad; su retiro total
  o simplificación se decide más adelante si ya no aporta valor operativo.
- Bucket `pdfs` PRIVADO / signed URLs: decisión FASE 6.
- Drive/Telegram/webhook/secrets: FASE 6-8.

**RIESGOS:**
- Si Gmail etiqueta falla pero Supabase registra bien, el bot no reprocesa, pero
  el label visible en la casilla puede quedar desincronizado.
- Si Supabase no está disponible, el fallback al label Gmail evita reprocesar,
  pero la fuente principal vuelve temporalmente al mecanismo anterior.

**DECISIONES:**
- `mensajes_procesados` es la fuente principal de idempotencia Gmail.
- El filtro Gmail `-label:Procesado-Resumen` solo se usa cuando Supabase no está
  disponible; con Supabase disponible se consulta todo y se decide por DB.
- Registrar el `mensaje_id` ocurre también cuando el resumen resulta duplicado
  por lógica de negocio, para no volver a recorrer ese mismo mail.

**CRITERIO DE PASS (FASE 5):**
- [x] `mensaje_id` se registra con UPSERT idempotente en `mensajes_procesados`
- [x] `mensaje_ya_procesado` consulta Supabase primero
- [x] Fallback explícito al label Gmail si la lectura Supabase falla
- [x] `revisar_mails()` evita volver a extraer MIME/PDF de un mail ya registrado
- [x] Mails duplicados de negocio también quedan marcados por `mensaje_id`
- [x] Concurrencia: N registros simultáneos del mismo `mensaje_id` → 1 fila
- [x] Sin tocar webhook; sin secretos hardcodeados
- [x] Suite completa con DB real: 103 passed / 5 skipped / 1 smoke esperado

**PRÓXIMA ACCIÓN:**
- Commitear baseline de FASE 5, verificar suite 100% verde y ejecutar FASE 6
  completa (análisis -> diseño -> implementación -> tests -> code review).

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

### FASE 4 — Bot: escritura (Supabase, sin doble escritura) — **PASS 2026-08-08**
- **ANALISTA**: relevadas las rutas de escritura de Sheets (guardar_en_sheet, guardar_o_actualizar_consumos_sheet, es_registro_duplicado, fijos mensuales, Telegram manual MANUAL|/INGRESO|); mapeo de dedup 1:1 con los UNIQUE INDEX (test_dedup_schema).
- **ARQUITECTO**: UPSERT idempotente con ON CONFLICT sobre los UNIQUE dedup (integridad en PostgreSQL bajo concurrencia); transacción + reintento por UniqueViolation; sin fallback de escritura a Sheets (error explícito `[SUPABASE WRITE ERROR]`); cuota nunca retrocede; comprobante raw.
- **IMPLEMENTADOR**: capa de escritura en `supabase_client.py` (guardar_consolidado, guardar_o_actualizar_consumos, guardar_ingreso, existe_consolidado, _write_in_transaction); migradas las escrituras de `bot_Saldo.py` a Supabase (ws ignorado); fijos y Telegram manual sin tocar la hoja.
- **TESTER**: `test_fase4_write.py` 22/22 (mocks, Sheets no escribe); `test_fase4_integracion.py` 12/12 (DB real: dedup, cuotas, fijos, concurrencia 6 hilos → 1 fila, regresión vs lógica anterior, sentinels limpiados); validación EXPLAIN de conflict targets 3/3; suite completa con DB real 89 passed / 5 skipped / 1 smoke esperado (working_tree por no commitear aún).
- **CODE REVIEW**: PASS. Verificado: sin doble escritura (ws ignorado), secretos por env, webhook intacto, GOOGLE_*/SHEET_ID intactos, residuos 0 en DB.
- **GATE**: APROBADO por el usuario el 2026-08-08 → se procede a FASE 5. Baseline commiteado (`532e762` + ajuste smoke) y suite 100% verde: **90 passed, 5 skipped**. El guard de head se relajó a "descendiente del baseline" (`262a215`) porque todo commit legítimo mueve HEAD (la igualdad estricta rompía tras cada commit).

### FASE 5 — Gmail (mensajes_procesados) — **IMPLEMENTADO + VALIDADO (PENDIENTE GATE) 2026-08-08**
- **ANALISTA**: relevado el flujo actual de Gmail (`buscar_mails_nuevos`, `extraer_datos_mensaje_mime`, `marcar_procesado`, `revisar_mails`); identificado que la idempotencia dependía del label `Procesado-Resumen` y que el schema ya disponía de `mensajes_procesados (UNIQUE mensaje_id)` para migrarla.
- **ARQUITECTO**: Supabase pasa a ser la fuente principal de idempotencia por `mensaje_id`; el label Gmail queda como respaldo / señal visual. Con Supabase disponible, la búsqueda NO excluye por label y el skip se decide por DB; si la lectura falla, fallback explícito al label. La escritura del registro del mensaje es obligatoria y sin fallback silencioso.
- **IMPLEMENTADOR**: añadidos `mensaje_ya_procesado()` y `registrar_mensaje_procesado()` en `supabase_client.py`; en `bot_Saldo.py` se agregaron `mensaje_tiene_label()`, `mensaje_ya_procesado()`, `registrar_y_marcar_mensaje_procesado()` y la integración en `revisar_mails()` para saltar mails ya procesados antes de extraer MIME/PDF y registrar también los duplicados de negocio.
- **TESTER**: `test_fase5_gmail.py` (unit) + `test_fase5_integracion.py` (DB real) + ampliación de `test_supabase_client.py` → **38/38 PASS**; suite completa con DB real: **103 passed, 5 skipped, 1 smoke esperado** (`working_tree`). `py_compile bot_Saldo.py supabase_client.py` PASS.
- **CODE REVIEW**: PASS preliminar. Verificado: webhook intacto, sin secretos hardcodeados, fallback al label solo en lectura, DB idempotente bajo concurrencia 6 hilos → 1 fila.

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
