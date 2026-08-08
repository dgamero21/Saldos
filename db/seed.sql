-- =====================================================================
-- Saldos — Seed / datos iniciales
-- FASE 1 (DISEÑO + SQL GENERADO — NO EJECUTADO)
-- Fuente: export actual de las hojas (CSV) + auditoría.
-- Idempotente: ON CONFLICT DO NOTHING.
-- =====================================================================

-- ---------------------------------------------------------------------
-- config — pares clave/valor operativos (hoja 'Config')
-- NOTA: Hora_Ejecucion en la hoja es '12' (sin ':').
--   El riesgo P6 de la auditoría (debe_ejecutar_ahora siempre True) se
--   PRESERVA: no se "arregla" aquí. La corrección debe ser una decisión
--   explícita con sus tests, no parte de FASE 1.
-- ---------------------------------------------------------------------
INSERT INTO config (clave, valor, descripcion) VALUES
    ('Hora_Ejecucion',          '12',       'HH:MM o HH configurada en la hoja (legacy sin dos puntos)'),
    ('Last_Telegram_Update_ID', '445690403','Último update_id procesado por el webhook'),
    ('Telegram_State',          '',         'Estado transitorio del diálogo de Telegram')
ON CONFLICT (clave) DO NOTHING;

-- ---------------------------------------------------------------------
-- categorias_fijas — columnas Tipo/Tipo_Ingreso de la hoja 'Config'
-- es_ingreso = FALSE -> gasto fijo (Tipo)
-- es_ingreso = TRUE  -> ingreso fijo (Tipo_Ingreso)
-- Monto_Fijo vacío en la hoja -> NULL (el bot solo genera fijos cuando
--   hay monto > 0; conserva exactamente ese comportamiento).
-- ---------------------------------------------------------------------
INSERT INTO categorias_fijas (es_ingreso, tipo, monto_fijo, pertenece) VALUES
    (FALSE, 'ALQUILER',  NULL, 'David'),
    (FALSE, 'PASAJE',    NULL, 'David'),
    (FALSE, 'AHORRO',    NULL, 'David'),
    (FALSE, 'NIÑERA',    NULL, 'David'),
    (FALSE, 'TELEFONO',  NULL, 'David'),
    (FALSE, 'PROCREAR',  NULL, 'David'),
    (TRUE,  'SUELDO',    NULL, 'David'),
    (TRUE,  'DEV PREST', NULL, 'David')
ON CONFLICT (es_ingreso, tipo) DO NOTHING;

-- ---------------------------------------------------------------------
-- reglas — hoja 'Datos' (activas SI y Manual Telegram)
-- Naranja tiene Pertenece='Nayla' (tal cual la hoja).
-- Manual Telegram: fila sin datos (solo marcador).
-- ---------------------------------------------------------------------
INSERT INTO reglas (
    remitente, asunto_contiene, clave, activo, tiene_adjunto,
    es_tarjeta_credito, regex_consumo, regex_cierre, regex_vencimiento,
    regex_monto, pertenece, entidad
) VALUES
    ('NAVI@mailing.bna.com.ar', 'TU RESUMEN DE VISA', '95292940', TRUE, TRUE, TRUE,
     NULL, 'CIERRE ACTUAL', 'VENCIMIENTO', 'SALDO $', 'David', 'BNA'),
    ('avisos@oficinaepec.com.ar', 'EPEC - Factura Digital', NULL, TRUE, FALSE, FALSE,
     NULL, NULL, 'VENCIMIENTO', 'Saldo', 'David', 'EPEC'),
    ('AVISOS@email.hipotecario.com.ar', 'Tu resumen está próximo a vencer', NULL, TRUE, FALSE, FALSE,
     NULL, NULL, 'Vencimiento', 'Vencimiento', 'David', 'HIPOTECARIO'),
    ('info@mail-hipotecario.com.ar', 'Aviso', NULL, TRUE, FALSE, FALSE,
     NULL, NULL, 'vence', 'monto', 'David', 'PROCREAR'),
    ('avisos@avisos.ecogas.com.ar', 'Ecogas informa que tu factura de gas vence hoy', NULL, TRUE, FALSE, FALSE,
     NULL, NULL, 'Vence', 'pagar', 'David', 'ECOGAS'),
    ('Naranja', 'Resumen', NULL, TRUE, TRUE, TRUE,
     '^(\d{2}/\d{2}/\d{2})\s+([A-Za-z0-9\s]{2,15}?)\s+(\d+)\s+(.+?)\s+(Zeta|\d{2}/\d{2}|\d{2})\s+(\d[\d.]*,\d{2})\s*',
     NULL, 'vence el', '3 CERO INTERES', 'Nayla', 'NARANJA'),
    ('Manual Telegram', NULL, NULL, FALSE, FALSE, FALSE,
     NULL, NULL, NULL, NULL, 'David', NULL)
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------------
-- FIN — FASE 1 (DISEÑO). NO EJECUTADO.
-- ---------------------------------------------------------------------
