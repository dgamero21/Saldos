-- =====================================================================
-- Saldos — Rollback de FASE 1 (infraestructura)
-- FASE 1 (DISEÑO + SQL GENERADO — NO EJECUTADO)
-- Orden inverso al schema: storage -> políticas -> RLS -> tablas -> ext.
-- ADVERTENCIA: DESTRUYE datos de las tablas de FASE 1.
-- =====================================================================

BEGIN;

-- 1) Storage: eliminar políticas del bucket
DROP POLICY IF EXISTS "pdfs_service_role_insert" ON storage.objects;
DROP POLICY IF EXISTS "pdfs_service_role_read"   ON storage.objects;
DROP POLICY IF EXISTS "pdfs_service_role_delete" ON storage.objects;

-- 2) Eliminar bucket (borra objetos asociados)
DELETE FROM storage.objects WHERE bucket_id = 'pdfs';
DELETE FROM storage.buckets WHERE id = 'pdfs';

-- 3) Revertir grants (restaurar accesos por defecto de Supabase)
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO anon, authenticated;
GRANT USAGE ON SCHEMA public TO anon, authenticated;

-- 4) Drop tablas (RLS se elimina con la tabla)
DROP TABLE IF EXISTS mensajes_procesados;
DROP TABLE IF EXISTS ingresos;
DROP TABLE IF EXISTS consumos;
DROP TABLE IF EXISTS consolidado;
DROP TABLE IF EXISTS reglas;
DROP TABLE IF EXISTS categorias_fijas;
DROP TABLE IF EXISTS config;

-- 5) Extensiones (opcional: eliminar si ya no se usan)
-- DROP EXTENSION IF EXISTS pg_trgm;
-- DROP EXTENSION IF EXISTS pgcrypto;

COMMIT;
