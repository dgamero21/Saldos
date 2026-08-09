-- =====================================================================
-- Saldos — Infraestructura Supabase
-- FASE 1 (DISEÑO + SQL GENERADO — NO EJECUTADO)
-- Fuente de verdad: auditoría (baseline) + análisis de bot_Saldo.py
-- + hojas actuales (CSV export).
--
-- Objetivo: reemplazar Google Sheets + Google Drive por tablas
-- PostgreSQL + Supabase Storage preservando el comportamiento 1:1
-- (incluida la deduplicación).
--
-- Formato de fechas del bot: 'dd/mm/yyyy' (texto en Sheets).
-- Se almacenan como DATE con conversión explícita.
-- =====================================================================

-- ---------------------------------------------------------------------
-- SECCIÓN 0 — Extensiones (idempotente)
-- ---------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pg_trgm;    -- búsqueda aproximada (reservado)

-- ---------------------------------------------------------------------
-- SECCIÓN 1 — Tabla: config (pares clave/valor operativos)
-- Mapea la hoja 'Config' (Hora_Ejecucion, Last_Telegram_Update_ID,
-- Telegram_State). La hoja es híbrida: mezcla config + categorías.
-- Las categorías van a categorias_fijas.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS config (
    clave        TEXT PRIMARY KEY,
    valor        TEXT NOT NULL,
    descripcion  TEXT,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- SECCIÓN 2 — Tabla: categorias_fijas
-- Mapea columnas Tipo / Tipo_Ingreso (+ Monto_Fijo) de la hoja Config.
-- es_ingreso = TRUE -> ingreso fijo (Tipo_Ingreso)
-- es_ingreso = FALSE -> gasto fijo (Tipo)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS categorias_fijas (
    id          BIGSERIAL PRIMARY KEY,
    es_ingreso  BOOLEAN NOT NULL DEFAULT FALSE,
    tipo        TEXT NOT NULL,
    monto_fijo  NUMERIC(14,2),
    pertenece   TEXT NOT NULL DEFAULT 'David',
    activo      BOOLEAN NOT NULL DEFAULT TRUE,
    creado_en   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (es_ingreso, tipo)
);

-- ---------------------------------------------------------------------
-- SECCIÓN 3 — Tabla: reglas (hoja 'Datos')
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reglas (
    id                   BIGSERIAL PRIMARY KEY,
    remitente            TEXT NOT NULL,
    asunto_contiene      TEXT,
    clave                TEXT,
    activo               BOOLEAN NOT NULL DEFAULT FALSE,
    tiene_adjunto        BOOLEAN NOT NULL DEFAULT FALSE,
    es_tarjeta_credito   BOOLEAN NOT NULL DEFAULT FALSE,
    regex_consumo        TEXT,
    regex_cierre         TEXT,
    regex_vencimiento    TEXT,
    regex_monto          TEXT,
    pertenece            TEXT NOT NULL DEFAULT 'David',
    entidad              TEXT,
    creado_en            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Índice para búsquedas por remitente / asunto (flujo identificar_regla_por_pdf)
CREATE INDEX IF NOT EXISTS idx_reglas_remitente ON reglas (remitente);
CREATE INDEX IF NOT EXISTS idx_reglas_asunto ON reglas (asunto_contiene);

-- Deduplicación 1:1 (agregado en FASE 2: evita reglas duplicadas al re-importar
-- el CSV de la hoja 'Datos', igual patrón que las demás tablas de negocio).
CREATE UNIQUE INDEX IF NOT EXISTS uq_reglas_dedup
    ON reglas (remitente, COALESCE(asunto_contiene, ''), COALESCE(clave, ''));

-- ---------------------------------------------------------------------
-- SECCIÓN 4 — Tabla: consolidado (hoja 'Consolidado')
-- Dedup 1:1 con bot_Saldo.es_registro_duplicado / guardar_en_sheet:
--   id_consolidado = lower(remitente)|vto|monto
-- pagado: FASE 10A (Web App) — estado de pago del vencimiento.
--   NO existía campo equivalente (auditoría 10A); aditivo con DEFAULT FALSE
--   para no romper INSERTs existentes (bot/webhook/import_data).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS consolidado (
    id                BIGSERIAL PRIMARY KEY,
    fecha_mail        DATE NOT NULL,
    remitente         TEXT NOT NULL,
    asunto            TEXT NOT NULL,
    monto_total       NUMERIC(14,2) NOT NULL,
    fecha_vencimiento DATE NOT NULL,
    link_drive        TEXT,
    pagado            BOOLEAN NOT NULL DEFAULT FALSE,
    id_consolidado    TEXT,   -- legacy (formato del bot); la integridad la da el UNIQUE INDEX
    pertenece         TEXT NOT NULL DEFAULT 'David',
    creado_en         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Deduplicación 1:1 (constraint de integridad, no solo SELECT+INSERT)
CREATE UNIQUE INDEX IF NOT EXISTS uq_consolidado_dedup
    ON consolidado (lower(remitente), fecha_vencimiento, monto_total);

CREATE INDEX IF NOT EXISTS idx_consolidado_remitente ON consolidado (remitente);
CREATE INDEX IF NOT EXISTS idx_consolidado_vto ON consolidado (fecha_vencimiento);

-- ---------------------------------------------------------------------
-- SECCIÓN 5 — Tabla: consumos (hoja 'Consumos')
-- Dedup 1:1 con guardar_o_actualizar_consumos_sheet:
--   id_consumo = fecha|comprobante|detalle|cuota_total|remitente
-- NOTA: comprobante se guarda como TEXT (raw). En Sheets 'USER_ENTERED'
--   convierte '008452' -> 8452; el ID legacy conserva '008452'.
--   El TEXT preserva la forma original del parseo.
-- cuota_actual y cuota_total son INTEGER (se comparan numéricamente en
--   guardar_o_actualizar_consumos_sheet).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS consumos (
    id                BIGSERIAL PRIMARY KEY,
    fecha_consumo     DATE NOT NULL,
    comprobante       TEXT NOT NULL,
    detalle           TEXT NOT NULL,
    cuota_actual      INTEGER NOT NULL DEFAULT 1,
    cuota_total       INTEGER NOT NULL DEFAULT 1,
    pesos             NUMERIC(14,2) NOT NULL DEFAULT 0,
    dolar             NUMERIC(14,2) NOT NULL DEFAULT 0,
    fecha_cierre      DATE,
    fecha_vencimiento DATE,
    remitente         TEXT NOT NULL,
    id_consumo        TEXT,   -- legacy (formato del bot); la integridad la da el UNIQUE INDEX
    pertenece         TEXT NOT NULL DEFAULT 'David',
    creado_en         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Deduplicación 1:1
CREATE UNIQUE INDEX IF NOT EXISTS uq_consumos_dedup
    ON consumos (fecha_consumo, comprobante, detalle, cuota_total, remitente);

CREATE INDEX IF NOT EXISTS idx_consumos_remitente ON consumos (remitente);
CREATE INDEX IF NOT EXISTS idx_consumos_fecha ON consumos (fecha_consumo);
CREATE INDEX IF NOT EXISTS idx_consumos_cierre ON consumos (fecha_cierre);

-- ---------------------------------------------------------------------
-- SECCIÓN 6 — Tabla: ingresos (hoja 'Ingresos')
-- Dedup 1:1 con procesar_mensajes_telegram / procesar_fijos_mensuales:
--   id_ingreso = fecha|Ingreso|categoria|origen
-- El literal 'Ingreso' es constante; categoría = tipo; origen distingue
--   'Manual Telegram' de 'Fijo Config'.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingresos (
    id          BIGSERIAL PRIMARY KEY,
    fecha       DATE NOT NULL,
    tipo        TEXT NOT NULL,
    monto       NUMERIC(14,2) NOT NULL,
    origen      TEXT NOT NULL DEFAULT 'Manual Telegram',
    id_ingreso  TEXT,   -- legacy (formato del bot); la integridad la da el UNIQUE INDEX
    pertenece   TEXT NOT NULL DEFAULT 'David',
    creado_en   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Deduplicación 1:1 (constraint de integridad)
CREATE UNIQUE INDEX IF NOT EXISTS uq_ingresos_dedup
    ON ingresos (fecha, tipo, origen);

CREATE INDEX IF NOT EXISTS idx_ingresos_fecha ON ingresos (fecha);
CREATE INDEX IF NOT EXISTS idx_ingresos_tipo ON ingresos (tipo);

-- ---------------------------------------------------------------------
-- SECCIÓN 7 — Tabla: mensajes_procesados
-- Reemplaza el label Gmail 'Procesado-Resumen' como registro de
-- idempotencia (además del label, para evitar reprocesamientos).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mensajes_procesados (
    id             BIGSERIAL PRIMARY KEY,
    mensaje_id     TEXT NOT NULL,
    remitente      TEXT,
    asunto         TEXT,
    procesado_en   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (mensaje_id)
);

CREATE INDEX IF NOT EXISTS idx_mensajes_procesados_fecha ON mensajes_procesados (procesado_en);

-- ---------------------------------------------------------------------
-- SECCIÓN 8 — Row Level Security (RLS)
-- La app accede con credenciales de servidor (service_role / directa).
-- RLS habilitado SIN políticas para anon/authenticated:
--   * service_role tiene BYPASSRLS -> acceso total (usado por la app)
--   * anon/authenticated no tienen políticas -> acceso DENEGADO
-- Sin exponer datos financieros públicamente.
-- ---------------------------------------------------------------------
ALTER TABLE config             ENABLE ROW LEVEL SECURITY;
ALTER TABLE categorias_fijas   ENABLE ROW LEVEL SECURITY;
ALTER TABLE reglas             ENABLE ROW LEVEL SECURITY;
ALTER TABLE consolidado        ENABLE ROW LEVEL SECURITY;
ALTER TABLE consumos           ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingresos           ENABLE ROW LEVEL SECURITY;
ALTER TABLE mensajes_procesados ENABLE ROW LEVEL SECURITY;

-- Revocar accesos por defecto a roles no-server (defensa en profundidad).
-- NOTA: no se revoca USAGE del schema public (rompería la introspección de
--   PostgREST y extensiones). La protección real: RLS sin políticas + revoke
--   de tabla. Con RLS habilitado y cero políticas, anon/authenticated no
--   ven ninguna fila aunque tengan USAGE del schema.
REVOKE ALL ON TABLE config, categorias_fijas, reglas,
            consolidado, consumos, ingresos, mensajes_procesados
    FROM anon, authenticated;

-- Acceso exclusivo para service_role (credencial de servidor)
GRANT USAGE ON SCHEMA public TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE config, categorias_fijas, reglas,
            consolidado, consumos, ingresos, mensajes_procesados
    TO service_role;
GRANT USAGE, SELECT ON SEQUENCE categorias_fijas_id_seq,
            reglas_id_seq, consolidado_id_seq, consumos_id_seq,
            ingresos_id_seq, mensajes_procesados_id_seq
    TO service_role;

-- ---------------------------------------------------------------------
-- SECCIÓN 9 — Supabase Storage: bucket 'pdfs'
-- Los PDFs se sirven como link temporal (Drive -> Storage).
-- DECISIÓN DE SEGURIDAD: bucket PRIVADO.
--   * Upload/read: solo service_role (BYPASSRLS) o signed URLs.
--   * NO bucket público: los PDFs son datos financieros (facturas).
--   * Si en el futuro se necesita URL permanente, usar signed URLs
--     con expiración (firma con service_role), NUNCA bucket público.
-- La app (bot_Saldo) hoy usa webViewLink público de Drive; en Supabase
-- se reemplazará por signed URL o path en FASE 6. Riesgo documentado.
-- ---------------------------------------------------------------------
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
    'pdfs',
    'pdfs',
    FALSE,
    10485760,                       -- 10 MB (PDFs de facturas)
    ARRAY['application/pdf']
)
ON CONFLICT (id) DO NOTHING;

-- Política de RLS del bucket: solo service_role (BYPASSRLS).
-- Sin políticas públicas -> ningún rol anon puede leer/insertar.
-- (Opcional: políticas explícitas para service_role como documentación.)
DROP POLICY IF EXISTS "pdfs_service_role_insert" ON storage.objects;
CREATE POLICY "pdfs_service_role_insert"
    ON storage.objects FOR INSERT
    TO service_role
    WITH CHECK (bucket_id = 'pdfs');

DROP POLICY IF EXISTS "pdfs_service_role_read" ON storage.objects;
CREATE POLICY "pdfs_service_role_read"
    ON storage.objects FOR SELECT
    TO service_role
    USING (bucket_id = 'pdfs');

DROP POLICY IF EXISTS "pdfs_service_role_delete" ON storage.objects;
CREATE POLICY "pdfs_service_role_delete"
    ON storage.objects FOR DELETE
    TO service_role
    USING (bucket_id = 'pdfs');

-- ---------------------------------------------------------------------
-- FIN — FASE 1 (DISEÑO). NO EJECUTADO.
-- ---------------------------------------------------------------------
