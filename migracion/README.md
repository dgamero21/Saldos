# FASE 2 — Migración de datos: CSVs → Supabase

Migra el estado actual de las hojas (export CSV) hacia las tablas Supabase
creadas en FASE 1. Reproducible e idempotente (`ON CONFLICT DO NOTHING`).

## Fuentes

| Hoja | Archivo CSV | Registros (datos) |
|---|---|---|
| Config | `Resumenes_bot - Config.csv` | 6 filas (3 config + 6 categorías gasto + 2 ingreso) |
| Datos | `Resumenes_bot - Datos.csv` | 7 |
| Consolidado | `Resumenes_bot - Consolidado.csv` | 112 |
| Consumos | `Resumenes_bot - Consumos.csv` | 416 |
| Ingresos | `Resumenes_bot - Ingresos.csv` | 1 |

> Nota: el `Consolidado=113`/`Consumos=417` del informe de auditoría **incluye
> el header**. Conteos reales de datos: **112** y **416**.

## Cómo ejecutar

```pwsh
$env:SUPABASE_DB_URL = "postgresql://postgres.zargsvnssplbwkkixjos:<PW>@aws-0-sa-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
python migracion/import_data.py --dry-run    # validar sin insertar
python migracion/import_data.py              # carga real (idempotente)
```

## Mapeo hoja → tabla (por columna)

### Consolidado → `consolidado`

| Origen (hoja) | Destino | Tipo origen | Tipo destino | Transformación | Nullable | Default |
|---|---|---|---|---|---|---|
| Fecha Mail | `fecha_mail` | text `dd/mm/yyyy` | DATE | parse; año 2 díg → 20xx | no | — |
| Remitente | `remitente` | text | TEXT | trim | no | — |
| Asunto | `asunto` | text | TEXT | trim | no | — |
| Monto Total | `monto_total` | text `"27110,4"` | NUMERIC(14,2) | `parse_monto` (`,`→`.`; `.` miles; `$`) | no | — |
| Fecha Vencimiento | `fecha_vencimiento` | text | DATE | parse | no | — |
| Link Drive | `link_drive` | text URL | TEXT | trim; vacío→NULL | sí | NULL |
| ID_Consolidado | `id_consolidado` | text legacy | TEXT | **raw preservado** | sí | NULL |
| Pertenece | `pertenece` | text | TEXT | trim; vacío→'David' | no | 'David' |
| — | `id` | — | BIGSERIAL | auto | no | gen |

### Consumos → `consumos`

| Origen (hoja) | Destino | Tipo origen | Tipo destino | Transformación | Nullable | Default |
|---|---|---|---|---|---|---|
| Fecha Consumo | `fecha_consumo` | text | DATE | parse | no | — |
| Comprobante | `comprobante` | text | TEXT | **VER `_comprobante_legacy`** | no | — |
| Detalle | `detalle` | text | TEXT | trim | no | — |
| Cuota Actual | `cuota_actual` | text int | INTEGER | int | no | 1 |
| Cuota Total | `cuota_total` | text int | INTEGER | int | no | 1 |
| Pesos | `pesos` | text `"$999.999,99"` | NUMERIC(14,2) | `parse_monto` | no | 0 |
| Dolar | `dolar` | text `"137,51"` / `PAGADO` | NUMERIC(14,2) | `parse_monto`; `PAGADO`→0 | no | 0 |
| Fecha Cierre | `fecha_cierre` | text | DATE | parse; vacío→NULL | sí | NULL |
| Fecha Vencimiento | `fecha_vencimiento` | text | DATE | parse; vacío→NULL | sí | NULL |
| Remitente | `remitente` | text | TEXT | trim | no | — |
| ID_Consumo | `id_consumo` | text legacy | TEXT | **raw preservado** | sí | NULL |
| Pertenece | `pertenece` | text | TEXT | trim; vacío→'David' | no | 'David' |
| — | `id` | — | BIGSERIAL | auto | no | gen |

### Ingresos → `ingresos`

| Origen (hoja) | Destino | Tipo origen | Tipo destino | Transformación | Nullable | Default |
|---|---|---|---|---|---|---|
| Fecha | `fecha` | text | DATE | parse | no | — |
| Tipo | `tipo` | text | TEXT | trim | no | — |
| Monto | `monto` | text `"$1.700.000"` | NUMERIC(14,2) | `parse_monto` | no | — |
| Origen | `origen` | text | TEXT | trim | no | 'Manual Telegram' |
| ID_Ingreso | `id_ingreso` | text legacy | TEXT | **raw preservado** | sí | NULL |
| Pertenece | `pertenece` | text | TEXT | trim | no | 'David' |
| — | `id` | — | BIGSERIAL | auto | no | gen |

### Datos → `reglas`

| Origen | Destino | Transformación |
|---|---|---|
| Remitente | `remitente` | trim; vacío→'Manual Telegram' |
| Asunto_Contiene | `asunto_contiene` | trim; vacío→NULL |
| Clave | `clave` | trim; vacío→NULL |
| Activo / Tiene_Adjunto / Es_Tarjeta_Credito | `activo` / `tiene_adjunto` / `es_tarjeta_credito` | `SI`→TRUE, resto→FALSE |
| Regex_* | `regex_*` | trim; vacío→NULL |
| Pertenece | `pertenece` | trim; vacío→'David' |
| Entidad | `entidad` | trim; vacío→NULL |

### Config → `config` + `categorias_fijas`

| Origen | Destino | Transformación |
|---|---|---|
| Hora_Ejecucion / Last_Telegram_Update_ID / Telegram_State | `config(clave, valor)` | `valor` trim; **Hora sin `:` preservada** (riesgo P6, no se corrige en FASE 2) |
| Tipo (col E) | `categorias_fijas(es_ingreso=FALSE, tipo)` | trim |
| Tipo_Ingreso (col I) | `categorias_fijas(es_ingreso=TRUE, tipo)` | trim |
| Pertenece | `categorias_fijas.pertenece` | trim; vacío→'David' |

> Nota: la hoja Config es **híbrida** (config + categorías). `import_data.py`
> separa ambas tablas de la misma forma que `db/seed.sql`.

## COMPROBANTES LEGACY — análisis (preguntas de aprobación)

**P1 — ¿Cuántos registros tienen comprobantes con ceros a la izquierda?**
- Columna `Comprobante` del CSV: **0** (Sheets ya los normalizó).
- `ID_Consumo` legacy: **178** (149 con `00` + 17 con `000` + 12 con `0`).

**P2 — ¿Cuántos podrían corresponder al mismo comprobante?**
- 344 registros: el comprobante de la columna coincide con el del ID tras
  quitar ceros a la izquierda (misma serie lógica).

**P3 — ¿`008452` y `8452` aparecen en distintos registros?**
- **NO.** Solo hay 1 registro (24/10/2023, ARGENTINA COLOR): columna `8452`,
  ID `008452`. Es el **mismo** registro; Sheets normalizó la columna, el ID
  conservó el original. No hay pares `008452`/`8452` en filas distintas.

**P4 — ¿Afecta la deduplicación actual?**
- En Sheets: no (el bot compara contra la columna ID, que conserva el raw).
- En Supabase: **sí importa**. El UNIQUE `uq_consumos_dedup` usa `comprobante`.
  Si se migrara el valor normalizado (`8452`), un re-procesamiento del bot
  (que genera `008452` desde el PDF) **no matchearía** → falso duplicado.

**P5 — ¿El código usa el comprobante como identificador o como dato?**
- **Identificador.** `id_unico = fecha|comprobante|detalle|cuota_total|remitente`
  (bot_Saldo.py:611) y el UNIQUE de Supabase. No es solo descriptivo.

**P6 — ¿Modificarlo podría generar falsos duplicados?**
- Normalizar a `8452` rompería el match futuro (ver P4). **0 colisiones**
  detectadas si se preserva el raw; la normalización no aporta ni elimina
  duplicados reales.

**P7 — ¿Normalizar antes o después de migrar?**
- **No se normaliza.** El valor original es el del ID legacy (raw del parseo
  del bot). La columna fue normalizada por Sheets como efecto colateral de
  `USER_ENTERED`, no por el bot.

**P8 — ¿Es realmente necesario normalizar?**
- **No.** `comprobante` es TEXT; preservar `008452` conserva la trazabilidad y
  la dedup 1:1. Se migra el valor raw del `ID_Consumo` (columna K), no el de
  la columna B. `valor_original` = raw del ID; `valor_normalizado` = ninguno.

## Decisiones y normalizaciones

1. **Comprobante**: se toma del `ID_Consumo` legacy (raw, ceros preservados).
   Documentado arriba.
2. **`Dolar='PAGADO'`** (6 filas, registros manuales Telegram 29/7/2026):
   NO es un monto USD; es un marcador manual de la hoja. `dolar` es
   NUMERIC(14,2) → se migra `0.00`. El valor original `PAGADO` queda
   documentado aquí (no hay columna de marcador en el schema).
3. **`ID_Consolidado`**: 108 vacíos, 3 `PAGADO`, 1 `naranja|...` — se preservan
   raw en `id_consolidado` (informativo; la integridad la da el UNIQUE).
4. **Fechas sin padding** (`18/6/2024`, `29/7/2026`): normalizadas a DATE ISO.
5. **`Hora_Ejecucion='12'`** sin `:`: preservada (riesgo P6, decisión futura).
6. **NIÑERA**: UTF-8 correcto (U+00D1) en fuente y destino (el `�` era solo
   display de consola en algunas salidas).
7. **`pertenece` en categorias_fijas**: la hoja dice `DAvid` (typo, v minúscula);
   el seed (aprobado en FASE 1) usa `David`. El bot **no lee** `pertenece` de
   categorías (solo `Tipo`/`Monto_Fijo`, fila[4]/[5]/[8]/[9]), así que es
   irrelevante para el comportamiento. Se preserva el valor del seed (`David`).
8. **`reglas` duplicadas**: `reglas` no tenía UNIQUE (solo PK), por lo que
   `ON CONFLICT DO NOTHING` del seed no la protegía y el import inicial creó
   duplicados. FIX aplicado: se purgaron los 7 duplicados y se agregó
   `uq_reglas_dedup (remitente, COALESCE(asunto_contiene,''), COALESCE(clave,''))`
   — mismo patrón que las demás tablas. La re-ejecución del import ya es
   idempotente para reglas.

## Integridad / dedup

- La migración **no** fusiona ni elimina registros: `ON CONFLICT DO NOTHING`.
- Validación 1:1 (post-carga) en `db/validate.sql` + `tests/`:
  counts fuente vs Supabase, fechas, montos, remitentes, cuotas, comprobantes.
- 0 colisiones en las claves UNIQUE de las 3 tablas de datos (verificado).

## Rollback (solo datos de FASE 2)

`migracion/rollback_data.sql` elimina **solo** los datos de las tablas de
negocio (consolidado, consumos, ingresos). No toca el schema ni config/
categorias_fijas/reglas (semilla de FASE 1). No usar `db/rollback.sql`
(corresponde a la infraestructura completa).
