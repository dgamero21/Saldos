-- =====================================================================
-- Saldos — Validación post-aplicación de FASE 1
-- FASE 1 (DISEÑO + SQL GENERADO — NO EJECUTADO)
-- Consultas de verificación. Deben devolver valores esperados.
-- =====================================================================

-- 1) Tablas existentes (esperado: 7)
SELECT tablename
FROM pg_tables
WHERE schemaname = 'public'
  AND tablename IN ('config','categorias_fijas','reglas','consolidado',
                    'consumos','ingresos','mensajes_procesados')
ORDER BY tablename;

-- 2) RLS habilitado (esperado: 7 filas, relrowsecurity = t)
SELECT c.relname, c.relrowsecurity
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relname IN ('config','categorias_fijas','reglas','consolidado',
                    'consumos','ingresos','mensajes_procesados')
ORDER BY c.relname;

-- 3) Índices UNIQUE de deduplicación (esperado: 4)
SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname IN ('uq_consolidado_dedup','uq_consumos_dedup','uq_ingresos_dedup',
                    'uq_reglas_dedup')
ORDER BY indexname;

-- 4) Bucket storage 'pdfs' (esperado: 1 fila, public = f)
SELECT id, name, public
FROM storage.buckets
WHERE id = 'pdfs';

-- 5) Políticas de storage para 'pdfs' (esperado: 3)
SELECT policyname, cmd, roles
FROM pg_policies
WHERE schemaname = 'storage'
  AND tablename = 'objects'
  AND policyname LIKE 'pdfs_%'
ORDER BY policyname;

-- 6) Verificación de deduplicación 1:1 en consolidado (esperado: 0 filas)
SELECT lower(remitente) AS remitente_lower, fecha_vencimiento, monto_total, count(*) AS duplicados
FROM consolidado
GROUP BY 1, 2, 3
HAVING count(*) > 1;

-- 7) Verificación de deduplicación 1:1 en consumos (esperado: 0 filas)
SELECT fecha_consumo, comprobante, detalle, cuota_total, remitente, count(*) AS duplicados
FROM consumos
GROUP BY 1, 2, 3, 4, 5
HAVING count(*) > 1;

-- 8) Verificación de deduplicación 1:1 en ingresos (esperado: 0 filas)
SELECT fecha, tipo, origen, count(*) AS duplicados
FROM ingresos
GROUP BY 1, 2, 3
HAVING count(*) > 1;

-- 8b) Verificación de deduplicación 1:1 en reglas (esperado: 0 filas)
-- FASE 2: mismo patrón que uq_reglas_dedup.
SELECT remitente, COALESCE(asunto_contiene, ''), COALESCE(clave, ''), count(*) AS duplicados
FROM reglas
GROUP BY 1, 2, 3
HAVING count(*) > 1;

-- 9) Semilla aplicada (esperado: 3 config, 8 categorias, 7 reglas)
SELECT 'config' AS tabla, count(*) FROM config
UNION ALL
SELECT 'categorias_fijas', count(*) FROM categorias_fijas
UNION ALL
SELECT 'reglas', count(*) FROM reglas
ORDER BY tabla;

-- 10) Privacidad: anon/authenticated sin privilegios sobre tablas
SELECT grantee, privilege_type, table_name
FROM information_schema.role_table_grants
WHERE table_schema = 'public'
  AND grantee IN ('anon','authenticated')
  AND table_name IN ('consolidado','consumos','ingresos');
-- Esperado: 0 filas (sin privilegios otorgados a roles no-server)
