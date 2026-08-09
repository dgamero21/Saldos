# Saldos — FASE 1: Infraestructura Supabase (DISEÑO)

Estado: **DISEÑO + SQL GENERADO. NO EJECUTADO.**

Este paquete reemplaza el almacenamiento actual (Google Sheets + Google Drive)
por PostgreSQL + Supabase Storage **sin cambiar el comportamiento funcional** del
bot, del webhook ni del workflow.

---

## Distinción de estados (crítica)

| Estado | Descripción | Estado actual |
|---|---|---|
| DISEÑO | Modelo de datos, decisiones, mapeo 1:1 con el código | ✅ COMPLETO |
| SQL GENERADO | `db/schema.sql` + `db/seed.sql` + `db/rollback.sql` + `db/validate.sql` | ✅ COMPLETO |
| SQL EJECUTADO | Aplicado contra el proyecto Supabase `saldos` | ❌ NO (pendiente aprobación) |
| SQL VALIDADO | Resultado de `db/validate.sql` verificado | ❌ NO (requiere ejecución) |

---

## Artefactos

| Archivo | Propósito |
|---|---|---|
| `db/schema.sql` | DDL: tablas, tipos, constraints, índices, RLS, Storage bucket |
| `db/seed.sql` | Datos iniciales de `Config`, `Datos` (reglas) y categorías (idempotente) |
| `db/rollback.sql` | Reversa completa del schema (destructivo) |
| `db/validate.sql` | Consultas de verificación post-aplicación (PASS/FAIL) |
| `db/migracion_10a.sql` | FASE 10A: `consolidado.pagado` (aditivo) |
| `db/rollback_10a.sql` | FASE 10A: revierte `pagado` |
| `db/validate_10a.sql` | FASE 10A: verificación post-aplicación |

---

## Mapeo Sheets → Supabase

| Hoja / recurso actual | Tabla / bucket Supabase |
|---|---|
| `Consolidado` | `consolidado` |
| `Consumos` | `consumos` |
| `Ingresos` | `ingresos` |
| `Datos` (reglas) | `reglas` |
| `Config` (Hora, Last_Telegram_Update_ID, Telegram_State) | `config` (key/value) |
| `Config` (columnas Tipo / Tipo_Ingreso / Monto_Fijo) | `categorias_fijas` |
| Label Gmail `Procesado-Resumen` | `mensajes_procesados` |
| Google Drive (PDFs) | Storage bucket `pdfs` |

---

## Modelo de datos

### `config`
Pares clave/valor operativos. La hoja `Config` es **híbrida**: mezcla la
configuración (Hora_Ejecucion, Last_Telegram_Update_ID, Telegram_State) con las
categorías fijas (Tipo / Tipo_Ingreso / Monto_Fijo). Por eso se divide en
`config` (esto) + `categorias_fijas`.

> ⚠️ **P6 preservado**: `Hora_Ejecucion = '12'` (sin `:`) se migra tal cual.
> El bug de `debe_ejecutar_ahora()` (siempre True) NO se corrige en FASE 1.
> Su corrección es una decisión explícita con tests propios.

### `categorias_fijas`
| Columna | Tipo | Nota |
|---|---|---|
| `id` | BIGSERIAL PK | |
| `es_ingreso` | BOOLEAN NOT NULL DEFAULT FALSE | TRUE = Tipo_Ingreso |
| `tipo` | TEXT NOT NULL | ALQUILER, SUELDO, ... |
| `monto_fijo` | NUMERIC(14,2) NULL | Vacío en la hoja → NULL |
| `pertenece` | TEXT NOT NULL DEFAULT 'David' | |
| `activo` | BOOLEAN NOT NULL DEFAULT TRUE | |
| `creado_en` | TIMESTAMPTZ | |
| **UNIQUE** | `(es_ingreso, tipo)` | |

### `reglas`
Columnas 1:1 con la hoja `Datos`. `Activo/Tiene_Adjunto/Es_Tarjeta_Credito` como
BOOLEAN (en la hoja son `SI`/`NO`). Regex conservadas como TEXT (patrones
Python-RE, compatibles con `re.compile`).

### `consolidado`
| Columna | Tipo | Nota |
|---|---|---|
| `fecha_mail` | DATE NOT NULL | `dd/mm/yyyy` en la hoja → DATE |
| `remitente` | TEXT NOT NULL | |
| `asunto` | TEXT NOT NULL | |
| `monto_total` | NUMERIC(14,2) NOT NULL | |
| `fecha_vencimiento` | DATE NOT NULL | |
| `link_drive` | TEXT | Legacy: URL de Drive |
| `pagado` | BOOLEAN NOT NULL DEFAULT FALSE | **FASE 10A**: estado de pago Web App (no existía campo equivalente; aditivo, no rompe INSERTs) |
| `id_consolidado` | TEXT GENERATED | `lower(remitente)\|vto\|monto` |
| `pertenece` | TEXT NOT NULL DEFAULT 'David' | |
| **UNIQUE INDEX** | `(lower(remitente), fecha_vencimiento, monto_total)` | **dedup 1:1** |

### `consumos`
| Columna | Tipo | Nota |
|---|---|---|
| `fecha_consumo` | DATE NOT NULL | |
| `comprobante` | TEXT NOT NULL | **TEXT a propósito** (raw). Sheets convierte `008452`→`8452`; el ID legacy conserva `008452`. TEXT preserva la forma del parseo. |
| `detalle` | TEXT NOT NULL | |
| `cuota_actual` | INTEGER NOT NULL | |
| `cuota_total` | INTEGER NOT NULL | |
| `pesos` | NUMERIC(14,2) NOT NULL | |
| `dolar` | NUMERIC(14,2) NOT NULL | |
| `fecha_cierre` | DATE NULL | |
| `fecha_vencimiento` | DATE NULL | |
| `remitente` | TEXT NOT NULL | |
| `id_consumo` | TEXT GENERATED | `fecha\|comprobante\|detalle\|cuota_total\|remitente` |
| **UNIQUE INDEX** | `(fecha_consumo, comprobante, detalle, cuota_total, remitente)` | **dedup 1:1** |

> El dedup usa **cuota_total** (no cuota_actual), tal como
> `guardar_o_actualizar_consumos_sheet` construye `id_unico`.

### `ingresos`
| Columna | Tipo | Nota |
|---|---|---|
| `fecha` | DATE NOT NULL | |
| `tipo` | TEXT NOT NULL | categoría |
| `monto` | NUMERIC(14,2) NOT NULL | |
| `origen` | TEXT NOT NULL DEFAULT 'Manual Telegram' | 'Manual Telegram' \| 'Fijo Config' |
| `id_ingreso` | TEXT GENERATED | `fecha\|Ingreso\|tipo\|origen` |
| **UNIQUE INDEX** | `(fecha, tipo, origen)` | **dedup 1:1** |

### `mensajes_procesados`
Registro de idempotencia por `mensaje_id` (UNIQUE). Complementa el label Gmail
`Procesado-Resumen` para prevenir reprocesamientos.

---

## Deduplicación 1:1 (crítica)

Mapeo exacto entre el código actual y las constraints de integridad:

```text
Consolidado:  lower(remitente) | vto | monto         -> UNIQUE(lower(remitente), vto, monto)
Consumos:     fecha | comprobante | detalle | cuota_total | remitente
             -> UNIQUE(fecha_consumo, comprobante, detalle, cuota_total, remitente)
Ingresos:     fecha | Ingreso | categoria | origen   -> UNIQUE(fecha, tipo, origen)
```

Las columnas `id_*` son **TEXT legacy** (lo que el bot escribía en la hoja, para
paridad/migración de datos). La integridad de la deduplicación la garantiza el
**UNIQUE INDEX**, no la columna `id_*`. Se puede insertar con
`INSERT ... ON CONFLICT DO NOTHING` (upsert) — no requiere SELECT+INSERT previo.

> Nota de diseño: no se usan columnas `GENERATED` para `id_*` porque el formato
> legacy lo construye el bot (`normalizar_monto` → float) y `to_char` es STABLE
> (no apto para GENERATED STORED). El dedup real son los UNIQUE INDEX.

---

## RLS / Seguridad

- RLS habilitado en **todas** las tablas, **sin políticas** para
  `anon`/`authenticated` → los datos financieros no se exponen por REST público.
- `service_role` (credencial de servidor que usará la app) tiene `BYPASSRLS` y
  privilegios explícitos `GRANT SELECT/INSERT/UPDATE/DELETE`.
- `REVOKE ALL ON TABLE` para `anon`/`authenticated` (defensa en profundidad).
  El webhook Vercel y el bot usan `service_role`.
- No se revoca `USAGE` del schema public: rompería la introspección de
  PostgREST; la protección real es RLS sin políticas.

---

## Storage (bucket `pdfs`)

- Bucket **PRIVADO** (`public = FALSE`).
- Solo `service_role` puede insert/read/delete (políticas explícitas).
- Los PDFs son facturas (datos financieros) → **no exponer como público**.

### Riesgo documentado (compatibilidad con comportamiento actual)
Hoy el bot usa `webViewLink` **público** de Drive (`role: reader, type: anyone`)
para adjuntar el link en el mensaje de Telegram. Con bucket privado, el acceso
debe hacerse mediante **signed URLs** (firma con `service_role` y expiración).
Alternativa NO recomendada: bucket público (reproduce el comportamiento actual
pero deja facturas accesibles con solo conocer la URL). Decisión a tomar en
FASE 6 (Drive → Storage) con tests propios.

---

## Tipos de datos

- Montos: `NUMERIC(14,2)` (máx. 999.999.999.999,99 — holgado para los valores
  del dominio; `normalizar_monto` descarta > 10M).
- Fechas: `DATE` (la hoja las guarda como texto `dd/mm/yyyy`; la conversión
  `to_char(..., 'DD/MM/YYYY')` preserva el formato en los `id_*` GENERATED).
- Comprobante: `TEXT` (conserva ceros a la izquierda — ver nota).
- Cuotas: `INTEGER`.

---

## Aplicación (cuando se apruebe)

1. Conectar a la DB de Supabase (`db.zargsvnssplbwkkixjos.supabase.co`) o usar
   `supabase db` / SQL editor con rol `postgres` o `service_role`.
2. Aplicar en orden: `db/schema.sql` → `db/seed.sql`.
3. Ejecutar `db/validate.sql` y confirmar PASS.
4. Si algo falla: `db/rollback.sql` (destructivo, devuelve a estado vacío).

## Rollback

`db/rollback.sql` elimina en orden inverso: políticas storage → bucket → grants →
tablas. **Destruye los datos**; solo para volver a un estado previo limpio.

---

## Qué NO hace FASE 1

- No modifica `bot_Saldo.py`, `api/webhook.js`, workflow ni `vercel.json`.
- No migra datos históricos (CSV → DB): eso es FASE 2.
- No corrige P1-P6 incidentalmente: quedan documentados como riesgos con
  decisión explícita por fase.

---

## Notas de aplicación / migración (FASE 2)

- **Encoding**: `seed.sql` contiene `NIÑERA` (UTF-8). Aplicar con client
  encoding UTF-8 (psql: `SET client_encoding TO 'UTF8';` o usar SQL editor).
- **Cadenas vacías → NULL**: en consumos, las filas fijas escriben
  `fecha_cierre`/`fecha_vencimiento` como `""` y `link_drive` vacío. En la
  migración, `""` → `NULL` (columnas DATE son nullable).
- **Comprobante padded**: el ID legacy de consumos conserva `008452` mientras
  la columna de datos (tras `USER_ENTERED` en Sheets) muestra `8452`. La
  migración debe normalizar `comprobante` (TEXT raw del parseo) para que el
  `uq_consumos_dedup` quede consistente. Decisión explícita de FASE 2.
- **Fechas**: el bot maneja `dd/mm/yyyy` como texto; la columna DATE requiere
  conversión explícita al insertar (`to_date(fecha, 'DD/MM/YYYY')`).
