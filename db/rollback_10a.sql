-- =====================================================================
-- Saldos — Rollback FASE 10A: consolidado.pagado
-- ADVERTENCIA: pierde el estado de pago marcado en la Web App.
-- Aplicar SOLO si 10A falla o se revierte antes de que haya PATCHes.
--
-- Aplicar:  psql "$SUPABASE_DB_URL" -f db/rollback_10a.sql
-- =====================================================================

BEGIN;

ALTER TABLE consolidado DROP COLUMN IF EXISTS pagado;

COMMIT;
