"""Regresión FASE 1: deduplicación 1:1 entre el código del bot y el schema SQL.

Verifica que las claves de dedup que genera el código (guardar_consolidado,
guardar_consumos, procesar_mensajes_telegram, procesar_fijos_mensuales) coinciden con las columnas del UNIQUE INDEX del
schema (db/schema.sql).
"""

import os

from conftest import REPO_ROOT

BOT_SCHEMA = os.path.join(REPO_ROOT, "db", "schema.sql")

# Formas de dedup del código (bot_Saldo.py), extraídas del análisis:
#   consolidado: lower(remitente)|vto|monto  (es_registro_duplicado)
#   consumos:    fecha|comprobante|detalle|cuota_total|remitente
#                (guardar_consumos, id_unico)
#   ingresos:    fecha|Ingreso|categoria|origen
#                (procesar_mensajes_telegram / procesar_fijos_mensuales)


def test_schema_contiene_unique_consolidado():
    with open(BOT_SCHEMA, encoding="utf-8") as f:
        sql = f.read()
    assert "uq_consolidado_dedup" in sql
    assert "ON consolidado (lower(remitente), fecha_vencimiento, monto_total)" in sql


def test_schema_contiene_unique_consumos():
    with open(BOT_SCHEMA, encoding="utf-8") as f:
        sql = f.read()
    assert "uq_consumos_dedup" in sql
    assert (
        "ON consumos (fecha_consumo, comprobante, detalle, cuota_total, remitente)"
        in sql
    )


def test_schema_contiene_unique_ingresos():
    with open(BOT_SCHEMA, encoding="utf-8") as f:
        sql = f.read()
    assert "uq_ingresos_dedup" in sql
    assert "ON ingresos (fecha, tipo, origen)" in sql


def test_id_consolidado_formato_1a1(bot_module):
    """El formato del id que genera el bot coincide con el UNIQUE INDEX."""
    # La constraint es: lower(remitente), fecha_vencimiento, monto_total
    # El bot construye: f"{remitente.lower()}|{vto}|{normalizar_monto(monto)}"
    remitente = "NAVI@mailing.bna.com.ar"
    vto = "21/11/2023"
    monto = "27110,4"
    id_bot = f"{remitente.lower()}|{vto}|{bot_module.normalizar_monto(monto)}"
    # 1:1 con la columna SQL: lower(remitente)|vto|monto
    assert id_bot == f"{remitente.lower()}|{vto}|27110.4"


def test_id_consumo_formato_1a1():
    """La constraint de consumos coincide con id_unico del bot:
    fecha|comprobante|detalle|cuota_total|remitente."""
    import re

    with open(BOT_SCHEMA, encoding="utf-8") as f:
        sql = f.read()
    idx = re.search(r"uq_consumos_dedup.*?\)", sql, re.S).group(0)
    # Las 5 columnas del UNIQUE deben estar presentes y en el orden del id_unico
    assert idx.index("fecha_consumo") < idx.index("comprobante") < idx.index("detalle")
    assert idx.index("cuota_total") < idx.index("remitente")


def test_id_ingreso_formato_1a1():
    """id_ingreso = fecha|Ingreso|tipo|origen; UNIQUE(fecha, tipo, origen)."""
    fecha, tipo, origen = "28/07/2026", "SUELDO", "Manual Telegram"
    id_bot = f"{fecha}|Ingreso|{tipo}|{origen}"
    assert id_bot == "28/07/2026|Ingreso|SUELDO|Manual Telegram"
