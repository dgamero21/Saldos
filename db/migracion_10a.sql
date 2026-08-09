-- =====================================================================
-- Saldos — Migración FASE 10A: consolidado.pagado (Web App)
-- ADITIVA: no toca datos existentes; 112 filas actuales quedan pagado=FALSE
-- (backfill por DEFAULT, igual que 'NO'/'PENDIENTE' en la Web App actual).
-- Precondición: NINGÚN endpoint/bot escribe la columna todavía (solo el
-- PATCH de vencimientos, fase 10C, lo hará). La columna entra con DEFAULT
-- para no romper INSERTs existentes de bot/webhook/import_data.
--
-- Aplicar:  psql "$SUPABASE_DB_URL" -f db/migracion_10a.sql
-- =====================================================================

BEGIN;

-- Columna de estado de pago para vencimientos.
-- La auditoría previa confirmó que NO existe ningún campo equivalente
-- (solo fecha_vencimiento), por lo que pagado es necesario.
ALTER TABLE consolidado
    ADD COLUMN IF NOT EXISTS pagado BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN consolidado.pagado IS
    'Estado de pago para la Web App (TRUE = pagado). Backfill inicial FALSE. Escrito solo por PATCH /api/vencimientos/:id (fase 10C).';

COMMIT;
