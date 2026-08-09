# AGENT_STATE — Loop de agentes: migración Sheets/Drive → Supabase

> Este archivo es el estado persistente del loop de agentes.
> Baseline oficial: auditoría completa (18 secciones) — no re-auditar.
> Fuente de verdad: GitHub `main` @ `4b39ccf`.

---

## ESTADO ACTUAL

```
FASE ACTUAL:        FASE 10A — Web App: schema mínimo — **READY TO MIGRATE 2026-08-09**
ESTADO:             PASS local — 92 passed, 60 skipped (pytest) + 13/13 JS (webhook)
AGENTE ACTUAL:      esperando aprobación para ejecutar migración SQL en producción
```

**PROTOCOLO DE LOOP (actualizado 2026-08-09):**
- Sin aprobación manual entre fases. Avanzar automáticamente tras
  TESTER=PASS + CODE REVIEW=PASS, EXCEPTO en FASE 10: cada sub-fase
  (10A–10J) y cualquier cambio que afecte producción (ALTER, cutover,
  borrado Sheets/Drive/secrets) requiere aprobación explícita del usuario.
- Detenerse solo para: operaciones irreversibles sobre producción,
  modificación/eliminación de datos, secretos faltantes, decisiones
  arquitectónicas con múltiples alternativas válidas, tests críticos
  irresolubles, riesgo de pérdida de datos, riesgo de seguridad, o
  ambigüedad de producción.

**OBJETIVO DE LA FASE:**
- Migrar la Web App (dashboard/administración) de Apps Script/Sheets a
  Supabase: Telegram = canal operativo; Web App = dashboard/carga/
  vencimientos/admin; Supabase = única fuente de datos; Google = solo
  Gmail API. Implementar en sub-fases 10A–10J, cerrando cada una con
  implementación/tests/commit/PASS.
- 10A (ESTA): schema mínimo — `consolidado.pagado` (aditivo, backfill
  FALSE, rollback preparado) + corregir que `categorias_fijas.activo`
  sea respetado en alta (bot `obtener_tipos`/`obtener_fijos` y webhook
  `obtenerCategorias`). SQL QUEDA PREPARADO, NO EJECUTADO en producción.

**CAMBIOS REALIZADOS:**
- `db/migracion_10a.sql` — NUEVO: `ALTER TABLE consolidado ADD COLUMN
  IF NOT EXISTS pagado BOOLEAN NOT NULL DEFAULT FALSE` + COMMENT.
  Aditivo, no rompe INSERTs de bot/webhook/import_data (no listan la
  columna; DEFAULT FALSE). Backfill automático de las 112 filas.
- `db/rollback_10a.sql` — NUEVO: `DROP COLUMN IF EXISTS pagado`
  (pierde estado de pago; solo si 10A falla antes de PATCHes).
- `db/validate_10a.sql` — NUEVO: validación post-aplicación 100%
  READ-ONLY (columna existe con default `false`, backfill pagados=0/total
  112, sin escrituras de prueba).
- `db/schema.sql` — ACTUALIZADO: `pagado` en la definición de
  `consolidado` (fuente de verdad); `db/README.md` — tabla actualizada.
- `supabase_client.py` — `_obtener_categorias()` ahora filtra
  `WHERE activo = TRUE` (categorías desactivadas ocultas en alta y sin
  fijos, pero no se borran ni afectan históricos). Corrige bug latente
  detectado en auditoría (el `activo` existía pero no se respetaba).
- `api/webhook.js` — `obtenerCategorias()` filtra `activo=eq.true`
  (teclado Telegram). Corrige el mismo bug en el webhook.
- `api/webhook.test.mjs` — NUEVO test: URL de categorías incluye
  `activo=eq.true`.
- `tests/test_supabase_client.py` — NUEVO test: SQL de
  `_obtener_categorias` filtra `WHERE activo = TRUE`.
- `tests/test_db_connectivity.py` — NUEVOS tests (skip sin DB):
  columna `pagado` existe (BOOLEAN NOT NULL DEFAULT false) y backfill
  pagados=0 con total>0.
- `tests/test_smoke.py` — blob hash de `api/webhook.js` actualizado a
  FASE 10A (`fe7139e...`).
- `.github/workflows/revisar-mails.yml` — FIX: indentación YAML rota en
  main (la versión de HEAD era YAML INVALIDO, fallaba en línea 24); el
  working tree la corrige. Verificado: estructura parseada idéntica
  (sin cambios funcionales). Mantenimiento de CI incluido en 10A.

**TESTS EJECUTADOS:**
- `pytest tests/` → **92 passed, 60 skipped** (post-commit, tree limpio;
  los skipped son los DB/REST que requieren secrets, no ejecutados por
  decisión del usuario).
- `node --test api/webhook.test.mjs` → **13/13 PASS**.
- SQL validado sintácticamente con sqlglot (dialect postgres): los 3
  nuevos + schema/seed/rollback/validate existentes → OK.
- YAML del workflow: parseado con PyYAML → VÁLIDO; estructura idéntica
  a la intención original (verificado contra HEAD).

**CODE REVIEW:**
- PASS. Cambio aditivo y reversible (rollback preparado). No toca
  producción (SQL solo preparado). `pagado` con DEFAULT FALSE no rompe
  los INSERTs existentes. Filtro `activo` corrige bug latente sin
  alterar datos. Sin secretos involucrados. `cleanup_old.py` (script
  DELETE contra producción) permanece SIN trackear y NO se ejecuta.

**RIESGOS:**
- MIGRACIÓN NO EJECUTADA: `db/migracion_10a.sql` debe aplicarse en
  producción con aprobación explícita (comando en PRÓXIMA ACCIÓN).
- El rollback pierde el estado de pago marcado en la Web App si 10A se
  revierte después de que existan PATCHes (10C). Riesgo bajo en 10A.
- Filtro `activo`: si en el futuro se quiere listar categorías
  inactivas en admin (10E), habrá que agregar un query sin el filtro
  (no rompe nada actual: las 8 categorías están todas activas).

**PRÓXIMA ACCIÓN:**
- [x] SQL generado (migración + rollback + validate).
- [x] SQL validado sintácticamente (sqlglot).
- [x] Tests locales PASS (92 pytest + 13 JS).
- [x] Compatibilidad de schema validada (columna aditiva, DEFAULT FALSE,
      INSERTs existentes sin columna no se rompen).
- [x] Rollback preparado (`db/rollback_10a.sql`).
- [x] Code Review PASS.
- [x] Commit `6bfab73` + push a `main`.
- [x] AGENT_STATE actualizado.
- [ ] EJECUTAR MIGRACIÓN en producción (requiere aprobación + credenciales):
      `psql "$SUPABASE_DB_URL" -f db/migracion_10a.sql`
      Validar con: `psql "$SUPABASE_DB_URL" -f db/validate_10a.sql`
      (esperado: columna boolean NOT NULL default false; pagados=0, total=112)
      Rollback si falla: `psql "$SUPABASE_DB_URL" -f db/rollback_10a.sql`
- [ ] Después de aplicar y validar: `pytest tests/ -k "db"` con credenciales
      → los tests nuevos de `pagado` deben pasar. Luego avanzar a 10B
      (API de lectura) SOLO con aprobación.
- NOTA: al momento de redactar esto NO hay credenciales disponibles en el
  entorno; no se pidió ni se pegó `SUPABASE_DB_URL` por instrucción del
  usuario.
- `pytest tests/test_fase8_secrets.py` con `SUPABASE_DBPW`:
  **11/13 PASS**, 2 fallos esperados (REST/Storage requieren key real).
- `pytest tests/` sin secrets: **91 passed, 41 skipped**.
- En CI (con los 3 secrets reales), se esperan los 13/13 PASS.

**CODE REVIEW:**
- PASS. Secrets nunca se imprimen, ni se escriben en archivos, ni en logs.
- Tests con skipif separados para evitar FAIL cuando falta un secret.
- Storage cleanup siempre se ejecuta (try/except alrededor del upload
  para eliminar antes de propagar errores).
- PostgreSQL solo lectura (sin UPDATE/DELETE/INSERT).
- PDF de prueba no es real (generado en runtime con magic bytes %PDF).

**RIESGOS:**
- Vercel: no puedo verificar programáticamente que tenga los 2 secrets.
  Requiere confirmación del usuario (Dashboard Vercel → Settings →
  Environment Variables).
- Los tests de REST/Storage no se pueden validar localmente sin la
  service_role key real. La validación completa es en CI.
- Si GitHub Actions secrets no están correctamente configurados, el
  workflow `fase8-validacion.yml` fallará con skip en lugar de pass.

**PRÓXIMA ACCIÓN:**
- Commitear baseline FASE 8, avanzar automáticamente a FASE 9 (Integración
  / Regresión). El usuario debe verificar Vercel en paralelo.

**CAMBIOS REALIZADOS:**
- `api/webhook.js` — MODIFICADO: migración Sheets API -> Supabase REST:
  - helpers: `supabaseFetch`, `supabaseQuery`, `supabaseInsert`,
    `obtenerCategorias`, `toArDate`, `monthYearAr`, `todayAr`, `todayIso`.
  - consultas migradas a PostgREST (`/rest/v1/<tabla>?select=...`):
    vencimientos (`consolidado`), balance (`consumos`+`ingresos`),
    cuotas (`consumos`), gasto categoría (`consumos`).
  - callbacks migrados a `supabaseInsert` con `onConflict`:
    `MANUAL|` → `consumos`, `INGRESO|` → `ingresos`, `CANCELAR` sin DB.
  - `obtenerCategorias()` reemplaza `fetchSheetValues("Config!E2:J100")`.
  - `fmt()`, `sendTelegram()`, `editTelegram()`, PDF dispatch: intactos.
  - `TELEGRAM_CHAT_ID` validación: intacta.
  - comentario documentado: `update_id` no se persiste ni se usa para dedup.
  - `GOOGLE_*`/`SHEET_ID` conservadas (no retiradas, transicional).
- `api/webhook.test.mjs` — NUEVO: 12 tests con `node:test` (categorías,
  vencimientos, balance, cuotas, gasto categoría, MANUAL, INGRESO, CANCELAR,
  TELEGRAM_CHAT_ID, error Supabase, PDF dispatch, payload inválido).
- `tests/test_fase7_webhook.py` — NUEVO: wrapper Python que ejecuta
  `node --test api/webhook.test.mjs` con `encoding="utf-8"`.
- `tests/test_smoke.py` — ACTUALIZADO: hash blob `api/webhook.js` FASE 7.

**TESTS EJECUTADOS:**
- `node --test api/webhook.test.mjs` → **12/12 PASS**.
- `pytest tests/test_fase7_webhook.py` → **1/1 PASS**.
- Suite completa con DB real (pre-commit): **113 passed, 6 skipped,
  1 failed** (solo smoke `working_tree` esperado por `api/webhook.js`
  modificado sin commitear).

**CODE REVIEW:**
- PASS. UX Telegram intacta (mensajes, callbacks, teclados, validación chat).
- PDF dispatch intacto (`repository_dispatch` + `client_payload`).
- Sin secretos hardcodeados; `SUPABASE_SERVICE_ROLE_KEY` desde `process.env`.
- `GOOGLE_*`/`SHEET_ID` no retiradas (transicional).
- `update_id`/`last_update_id`: NO corregido (decisión explícita documentada).
- Secret token: NO agregado (cambio independiente, documentado).

**RIESGOS:**
- El webhook requiere `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` en Vercel;
  si no están configuradas, `ensureSupabase()` lanza error explícito.
  Incorporación de secrets en Vercel = FASE 8.
- `GOOGLE_*`/`SHEET_ID` siguen en Vercel pero no se usan tras FASE 7;
  retirarlas definitivamente = FASE 10.
- Comportamiento de parsing de montos del webhook (ej: `200mil` → 200M)
  preservado sin corregir (decisión explícita, no parte de FASE 7).

**PRÓXIMA ACCIÓN:**
- Commitear baseline FASE 7, rerun suite completa, confirmar working tree
  limpio, y avanzar automáticamente a FASE 8 (Secrets / Deploy).

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
- Commitear baseline FASE 7, rerun suite completa, confirmar working tree
  limpio, y avanzar automáticamente a FASE 8 (Secrets / Deploy).

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

### FASE 7 — Webhook -> Supabase — **PASS 2026-08-08**
- **ANALISTA**: relevado `api/webhook.js` completo (377 líneas): 4 consultas (vencimientos, balance, cuotas, gasto categoría), 3 callbacks (MANUAL|, INGRESO|, CANCELAR), config categorías (Config!E2:J100), PDF dispatch (`repository_dispatch`), validación `TELEGRAM_CHAT_ID`, env vars (`SHEET_ID`, `GOOGLE_*`, `GH_PAT`, `TELEGRAM_*`). Detectado: `update_id` validado pero no persistido ni usado para dedup de retries.
- **ARQUITECTO**: migración a PostgREST (Supabase) con helpers `supabaseFetch`/`supabaseQuery`/`supabaseInsert`; `GOOGLE_*`/`SHEET_ID` conservadas (no retirar); secret token NO mezclado con migración de persistencia (cambio independiente); `update_id` no corregido (decisión explícita); mes actual sin padding preservado.
- **IMPLEMENTADOR**: `api/webhook.js` migrado: consultas→PostgREST, callbacks→`supabaseInsert` con `onConflict`, `obtenerCategorias()`→`categorias_fijas`; `api/webhook.test.mjs` (12 tests `node:test`); `tests/test_fase7_webhook.py` (wrapper Python); smoke actualizado.
- **TESTER**: `node --test` 12/12 PASS; `pytest tests/test_fase7_webhook.py` 1/1 PASS; suite completa pre-commit: 113 passed, 6 skipped, 1 failed (smoke `working_tree` esperado).
- **CODE REVIEW**: PASS. UX Telegram intacta; PDF dispatch intacto; sin secretos hardcodeados; `update_id` y secret token documentados como decisiones explícitas; `GOOGLE_*` no retiradas.
- **PROTOCOLO**: auto-avance a FASE 8 (protocolo nuevo: sin gate manual entre fases).

### FASE 8 — Secrets / Deploy — **IMPLEMENTADO (PENDIENTE VALIDACIÓN E2E EN CI) 2026-08-08**
- **ANALISTA**: confirmados 3 secrets en GitHub Repository Secrets (`SUPABASE_DB_URL`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`). Identificado que `SUPABASE_DBPW` ya funciona localmente (pooler) y `SUPABASE_SERVICE_ROLE_KEY` no está disponible en entorno local (solo en CI). Vercel requiere configuración manual por el usuario (Dashboard Vercel → Settings → Environment Variables).
- **ARQUITECTO**: tests separados por dependencia: `skipif_no_db` (PostgreSQL, `SUPABASE_DB_URL`/`SUPABASE_DBPW`) y `skipif_no_rest` (REST/Storage, `SUPABASE_URL`+`SUPABASE_SERVICE_ROLE_KEY`). Workflow `fase8-validacion.yml` ejecuta tests FASE 8 + suite completa con secrets. Validación E2E Storage real pendiente (requiere `SERVICE_ROLE_KEY` real en CI). Secrets nunca en logs/archivos.
- **IMPLEMENTADOR**: `tests/test_fase8_secrets.py` (13 tests: presencia secrets, PostgreSQL 5 tests, REST 1, Storage 3, seguridad 3). `.github/workflows/fase8-validacion.yml` (workflow_dispatch con secrets inyectados). `AGENT_STATE.md` actualizado. Suite sin secrets: 91 passed, 41 skipped. Suite con `SUPABASE_DBPW`: 11/13 PASS (2 fallos esperados por key dummy).
- **TESTER**: `pytest tests/test_fase8_secrets.py` con `SUPABASE_DBPW` → 11/13 PASS (2 fallos esperados por `SERVICE_ROLE_KEY` dummy). Suite completa sin secrets: 91 passed, 41 skipped. En CI con 3 secrets reales se esperan 13/13 PASS.
- **CODE REVIEW**: PASS. Secrets nunca impresos/escritos/logueados. Tests con skipif separados. Storage cleanup garantizado. PostgreSQL solo lectura. PDF de prueba sintético.
- **RIESGOS**: Vercel no verificado programáticamente — requiere confirmación del usuario. `SERVICE_ROLE_KEY` real solo en CI. E2E Storage real pendiente CI. `GOOGLE_*`/`SHEET_ID` no retiradas (FASE 10).
- **PRÓXIMA ACCIÓN**: Commitear baseline FASE 8, auto-avanzar a FASE 9 (Integración / Regresión). Usuario debe confirmar Vercel en paralelo.

### FASE 9 — Integración / Regresión E2E — **PASS 2026-08-08**
- **ANALISTA**: definido alcance E2E (Gmail PDF, webhook callbacks, dedup, EPEC, fijos, concurrencia, regresión, security, Storage). Identificados 10 escenarios críticos.
- **ARQUITECTO**: tests con mocks para flujo Gmail y webhook, DB real para dedup/concurrencia/regresión; skipif para Storage real; cleanup automático por tag.
- **IMPLEMENTADOR**: `tests/test_fase9_e2e.py` (16 tests E2E), `.github/workflows/fase9-e2e.yml` (workflow_dispatch); fix mocks `leer_config_completo` en `test_fase4_write.py`.
- **TESTER**: `pytest tests/test_fase9_e2e.py` → **16 passed, 1 skipped**; suite completa: **137 passed, 12 skipped**; smoke 7/7 PASS.
- **CODE REVIEW**: PASS. Flujo Gmail→bot→Supabase→Telegram cubierto. Webhook callbacks, dedup, concurrencia, regresión consumos, EPEC, security, RLS, bucket privado validados.
- **RIESGOS**: Validación Storage real pendiente CI (requiere `SERVICE_ROLE_KEY`). Vercel requiere configuración manual.
- **AUTO-AVANCE**: FASE 10 (Migración definitiva) — ahora sub-fases 10A–10J con gate manual por fase.

### FASE 10A — Web App: schema mínimo — **READY TO MIGRATE 2026-08-09**
- **ANALISTA**: auditoría DB 10A confirmó que `consolidado` NO tiene campo de
  estado (solo `fecha_vencimiento`) → `pagado` necesario. Confirmado bug
  latente: `categorias_fijas.activo` existía pero NO se respetaba en
  `obtener_tipos`/`obtener_fijos` (supabase_client) ni `obtenerCategorias`
  (webhook).
- **ARQUITECTO**: `pagado BOOLEAN NOT NULL DEFAULT FALSE` aditivo (no rompe
  INSERTs existentes; backfill por DEFAULT). Filtro `activo=TRUE` en la capa
  de categorías (alta y fijos) sin borrar ni afectar históricos. SQL con
  migración + rollback + validate (100% read-only).
- **IMPLEMENTADOR**: `db/migracion_10a.sql`, `db/rollback_10a.sql`,
  `db/validate_10a.sql`; `schema.sql`/`README.md` actualizados;
  `_obtener_categorias()` y `obtenerCategorias()` filtran activas; tests
  nuevos (SQL filtra activo, columna `pagado`, webhook `activo=eq.true`);
  smoke blob actualizado; fix YAML de `revisar-mails.yml` (main tenía YAML
  inválido).
- **TESTER**: 92 passed, 60 skipped (pytest) + 13/13 JS; SQL validado con
  sqlglot; YAML validado con PyYAML (estructura idéntica a HEAD).
- **CODE REVIEW**: PASS. Aditivo y reversible; producción NO tocada (SQL
  solo preparado); sin secretos; `cleanup_old.py` sin trackear.
- **GATE**: migración NO ejecutada por instrucción explícita del usuario
  (no pedir/pegar `SUPABASE_DB_URL`, no psql contra producción). Queda
  **READY TO MIGRATE** con comandos documentados en ESTADO ACTUAL.

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
