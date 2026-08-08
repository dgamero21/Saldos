-- =====================================================================
-- Saldos — ROLLBACK de FASE 2 (solo datos de negocio migrados)
-- Elimina únicamente los datos cargados por migracion/import_data.py.
-- NO toca el schema (FASE 1) ni config/categorias_fijas/reglas (seed).
-- NO usar db/rollback.sql (rollback de infraestructura completa).
-- =====================================================================

DELETE FROM consolidado;
DELETE FROM consumos;
DELETE FROM ingresos;

-- Verificación: deben quedar 0 filas en las 3 tablas de datos.
SELECT 'consolidado' AS tabla, count(*) FROM consolidado
UNION ALL SELECT 'consumos', count(*) FROM consumos
UNION ALL SELECT 'ingresos', count(*) FROM ingresos;
