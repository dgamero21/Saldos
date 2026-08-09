-- =====================================================================
-- Saldos — Validación FASE 10A: consolidado.pagado
-- Post-aplicación. Debe devolver valores esperados.
-- 100% READ-ONLY: no inserta ni elimina datos en producción.
--
-- Aplicar:  psql "$SUPABASE_DB_URL" -f db/validate_10a.sql
-- =====================================================================

-- 1) Columna existe (esperado: 1 fila, data_type = boolean,
--    column_default = 'false', is_nullable = 'NO')
SELECT column_name, data_type, column_default, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'consolidado'
  AND column_name = 'pagado';

-- 2) Backfill correcto (esperado: pagados = 0, total = 112)
SELECT count(*) FILTER (WHERE pagado) AS pagados,
       count(*)                        AS total
FROM consolidado;

-- 3) Compatibilidad de INSERTs existentes: el DEFAULT de la columna
--    garantiza que bot/webhook/import_data (que NO listan 'pagado')
--    sigan insertando sin error. Verificación estática del default:
--    column_default = 'false' (ya cubierto en 1). Sin escrituras reales.
SELECT 'consolidado.pagado lista para 10C (PATCH vencimientos)' AS estado;
