# AGENT_STATE — Loop de agentes: migración Sheets/Drive → Supabase

> Este archivo es el estado persistente del loop de agentes.
> Baseline oficial: auditoría completa (18 secciones) — no re-auditar.
> Fuente de verdad: GitHub `main` @ `4b39ccf`.

---

## ESTADO ACTUAL

```
FASE ACTUAL:        FASE 7 — Webhook -> Supabase (ANÁLISIS)
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
- `supabase_client.py` — MODIFICADO: capa de Storage privado:
  - `supabase_storage_disponible()` / `_storage_config()` / headers de REST.
  - `subir_pdf_storage()` → upload a bucket privado `pdfs`, validación previa
    (PDF real por magic bytes, <= 10 MB), `x-upsert=true`, path estable por
    SHA256 y remitente.
  - `crear_signed_url_pdf()` → signed URL temporal para Telegram.
  - `link_drive` pasa a aceptar referencia estable `storage://pdfs/<path>`.
- `bot_Saldo.py` — MODIFICADO: migración progresiva Drive -> Storage:
  - nuevo helper `subir_pdf()` → Storage como principal, Drive como fallback
    temporal y explícito.
  - `revisar_mails()` ya no sube el PDF antes del dedup; primero parsea y
    decide, luego sube solo si el mail no es duplicado.
  - la rama Telegram PDF manual también usa `subir_pdf()`.
  - EPEC conserva el caso especial: no almacena archivo y deja link vacío.
  - Telegram recibe signed URL; `consolidado.link_drive` guarda una referencia
    estable (`storage://pdfs/...`) cuando se usa Storage.
- `tests/test_fase6_storage.py` — NUEVO (unit: upload helper, signed URL,
  fallback Drive, flujo Gmail, flujo Telegram manual, EPEC).
- `tests/test_fase6_storage_integracion.py` — NUEVO (integración opcional con
  Storage real; hoy queda SKIP si faltan `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY`).
- `tests/test_smoke.py` — ACTUALIZADO: blob hash de `bot_Saldo.py` al estado
  FASE 6 en desarrollo.

**TESTS EJECUTADOS:**
- Post-commit FASE 5: `pytest tests/` → **104 passed, 5 skipped** (baseline
  limpio confirmado antes de arrancar FASE 6).
- Suite FASE 6 específica: `test_fase6_storage.py` → **8/8 PASS**;
  `test_fase6_storage_integracion.py` → **1 skipped** (faltan credenciales REST
  de Storage en este entorno local).
- Suite completa con DB real: `pytest tests/` → **111 passed, 6 skipped,
  1 failed** (solo smoke de working tree esperado mientras FASE 6 siga sin
  commitear/gate).
- `py_compile bot_Saldo.py supabase_client.py` PASS.

**TESTS FALLIDOS:**
- Ninguno confirmado post-commit todavía. Pendiente rerun completo tras cerrar el
  baseline de FASE 5.

**PROBLEMAS ABIERTOS:**
- El workflow de GitHub Actions aún no inyecta `SUPABASE_URL` ni
  `SUPABASE_SERVICE_ROLE_KEY`; por eso Drive sigue como fallback temporal hasta
  FASE 8 (secrets/deploy).
- `consolidado.link_drive` mezcla histórico Drive URL con nueva referencia
  `storage://pdfs/...`; es intencional para transición sin schema nuevo.
- El label Gmail sigue existiendo como respaldo/observabilidad; su retiro total
  o simplificación se decide más adelante si ya no aporta valor operativo.
- Webhook y secrets siguen pendientes para FASE 7/8.

**VALIDACIÓN PENDIENTE E2E (FASE 8/9):**
- La integración real de Storage (upload + signed URL contra el bucket privado)
  NO bloquea FASE 6, pero queda pendiente de ejecución en un entorno con
  `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY`. Debe validarse explícitamente en
  deploy durante FASE 8/9.

**RIESGOS:**
- Mientras el fallback a Drive siga activo, una falla de Storage puede dejar
  links nuevos en Drive y referencias más viejas en `storage://...`; es el costo
  temporal de la migración progresiva sin romper producción antes de FASE 8.
- Las signed URLs son temporales por diseño; la referencia persistida permite
  re-firmar luego, pero la URL enviada por Telegram expira.
- Si Gmail etiqueta falla pero Supabase registra bien, el bot no reprocesa, pero
  el label visible en la casilla puede quedar desincronizado.

**DECISIONES:**
- `mensajes_procesados` es la fuente principal de idempotencia Gmail.
- El filtro Gmail `-label:Procesado-Resumen` solo se usa cuando Supabase no está
  disponible; con Supabase disponible se consulta todo y se decide por DB.
- Registrar el `mensaje_id` ocurre también cuando el resumen resulta duplicado
  por lógica de negocio, para no volver a recorrer ese mismo mail.
- Bucket `pdfs` permanece PRIVADO; no se convierte en público.
- `consolidado.link_drive` guarda una referencia estable `storage://pdfs/<path>`
  cuando el archivo vive en Storage; Telegram usa signed URL temporal.
- EPEC mantiene link vacío y no sube archivo.
- Hasta FASE 8, Drive se conserva como fallback explícito para no romper el
  workflow actual mientras faltan las credenciales REST de Storage.

**CRITERIO DE PASS (FASE 6):**
- [x] `subir_a_drive()` relevado y reemplazado por flujo Storage-first
- [x] identificados todos los usos del link: Gmail, Telegram manual y
      `consolidado.link_drive`
- [x] bucket privado preservado; signed URLs temporales definidas
- [x] EPEC mantiene excepción sin almacenamiento
- [x] compatibilidad con `consolidado` preservada (ref estable `storage://...`)
- [x] no se toca `consumos` (no guarda links)
- [x] manejo de errores: fallback explícito a Drive mientras faltan secrets
- [x] upload solo después del dedup para evitar archivos huérfanos por duplicado
- [x] tests unitarios de flujo y helper PASS; integración Storage real preparada
- [x] suite completa con DB real: 111 passed / 6 skipped / 1 smoke esperado

**PRÓXIMA ACCIÓN:**
- GATE FASE 6 aprobado por el usuario. Commitear baseline, rerun completo y
  arrancar FASE 7 (Webhook -> Supabase), separando migración de persistencia de
  cualquier mejora de seguridad del secret token.

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

### FASE 5 — Gmail (mensajes_procesados) — **PASS 2026-08-08**
- **ANALISTA**: relevado el flujo actual de Gmail (`buscar_mails_nuevos`, `extraer_datos_mensaje_mime`, `marcar_procesado`, `revisar_mails`); identificado que la idempotencia dependía del label `Procesado-Resumen` y que el schema ya disponía de `mensajes_procesados (UNIQUE mensaje_id)` para migrarla.
- **ARQUITECTO**: Supabase pasa a ser la fuente principal de idempotencia por `mensaje_id`; el label Gmail queda como respaldo / señal visual. Con Supabase disponible, la búsqueda NO excluye por label y el skip se decide por DB; si la lectura falla, fallback explícito al label. La escritura del registro del mensaje es obligatoria y sin fallback silencioso.
- **IMPLEMENTADOR**: añadidos `mensaje_ya_procesado()` y `registrar_mensaje_procesado()` en `supabase_client.py`; en `bot_Saldo.py` se agregaron `mensaje_tiene_label()`, `mensaje_ya_procesado()`, `registrar_y_marcar_mensaje_procesado()` y la integración en `revisar_mails()` para saltar mails ya procesados antes de extraer MIME/PDF y registrar también los duplicados de negocio.
- **TESTER**: `test_fase5_gmail.py` (unit) + `test_fase5_integracion.py` (DB real) + ampliación de `test_supabase_client.py` → **38/38 PASS**; suite completa con DB real: **103 passed, 5 skipped, 1 smoke esperado** (`working_tree`). `py_compile bot_Saldo.py supabase_client.py` PASS.
- **CODE REVIEW**: PASS preliminar. Verificado: webhook intacto, sin secretos hardcodeados, fallback al label solo en lectura, DB idempotente bajo concurrencia 6 hilos → 1 fila.
- **GATE**: APROBADO por el usuario el 2026-08-08. Baseline commiteado (`1a4c3f1`) y suite post-commit: **104 passed, 5 skipped**; working tree limpio confirmado antes de FASE 6.

### FASE 6 — Drive -> Supabase Storage — **PASS 2026-08-08**
- **ANALISTA**: relevado `subir_a_drive()` y sus 2 usos reales (Gmail + Telegram PDF manual); identificado que `consolidado.link_drive` es el único campo persistido con link y que `consumos` no guarda PDFs. Confirmado el caso especial EPEC: no almacenar archivo, link vacío.
- **ARQUITECTO**: diseño Storage-first con bucket `pdfs` PRIVADO y signed URLs temporales para Telegram; persistencia estable en `consolidado.link_drive` como `storage://pdfs/<path>`; Drive queda como fallback explícito y temporal mientras el workflow aún no expone `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` (FASE 8). Upload movido después del dedup para evitar huérfanos por duplicado.
- **IMPLEMENTADOR**: añadidos helpers de Storage REST en `supabase_client.py` (`subir_pdf_storage`, `crear_signed_url_pdf`, parsing de refs `storage://...`); añadido `subir_pdf()` en `bot_Saldo.py`; migradas las ramas Gmail y Telegram manual a Storage-first; EPEC preservado sin almacenamiento.
- **TESTER**: `test_fase6_storage.py` **8/8 PASS**; `test_fase6_storage_integracion.py` preparado y **1 skip** por falta de credenciales REST en este entorno local; suite completa con DB real: **111 passed, 6 skipped, 1 smoke esperado** (`working_tree`).
- **CODE REVIEW**: PASS preliminar. Verificado: bucket sigue privado, webhook intacto, sin secretos hardcodeados, compatibilidad transicional con `consolidado`, Drive solo como respaldo temporal explícito.
- **GATE**: APROBADO por el usuario el 2026-08-08. Queda documentado que la validación E2E real de Storage (upload + signed URL con `SUPABASE_URL` y `SUPABASE_SERVICE_ROLE_KEY`) NO bloquea FASE 6 pero debe ejecutarse explícitamente en FASE 8/9.

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
