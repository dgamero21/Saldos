"""FASE 3 — Equivalencia Sheets <-> Supabase (datos reales).

Requeridas SUPABASE_DB_URL o SUPABASE_DBPW en el entorno; si no, se SKIPean
(mismo patrón que tests/test_db_connectivity.py).

Compara las lecturas de supabase_client contra la fuente de verdad Sheets
(CSV export, la misma que usó FASE 2 para migrar) SIN tocar Google APIs:
  - reglas activas (formato del bot, SOLO activas).
  - config: Hora_Ejecucion raw ('12'), Last_Telegram_Update_ID, Telegram_State.
  - tipos de gasto e ingreso (categorias_fijas).
  - fijos (monto > 0).
"""
import csv
import io
import os
import sys
from pathlib import Path

import pytest

from conftest import REPO_ROOT

pytestmark = pytest.mark.skipif(
    not (os.environ.get("SUPABASE_DB_URL") or os.environ.get("SUPABASE_DBPW")),
    reason="SUPABASE_DB_URL o SUPABASE_DBPW no configurada",
)

sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "migracion"))
import import_data  # noqa: E402  (migracion/import_data.py)

import supabase_client  # noqa: E402

CSV_DIR = Path(r"C:\Users\dgame\OneDrive\Escritorio\APPS\RESUMENES\sheet_resumen")


def _csv_rows(fname):
    path = CSV_DIR / fname
    if not path.exists():
        pytest.skip(f"Fuente Sheets (CSV) no disponible: {path}")
    header, rows = import_data.load_csv(path)
    return header, rows


# ---------------------------------------------------------------------------
# Reglas
# ---------------------------------------------------------------------------

def test_reglas_supabase_equivale_a_hoja():
    _, rows = _csv_rows("Resumenes_bot - Datos.csv")
    activas_hoja = [r for r in rows if (r[3] or "").strip().upper() == "SI"]

    reglas = supabase_client.obtener_reglas()
    assert len(reglas) == len(activas_hoja), (
        f"reglas activas: Supabase={len(reglas)} hoja={len(activas_hoja)}"
    )
    remitentes_supabase = {r["Remitente"] for r in reglas}
    remitentes_hoja = {(r[0] or "").strip() for r in activas_hoja}
    assert remitentes_supabase == remitentes_hoja

    for r in reglas:
        assert r["Activo"] == "SI"
        assert r["Remitente"]
        # Flags normalizados al formato del bot ('SI'/'NO').
        assert r["Tiene_Adjunto"] in ("SI", "NO")
        assert r["Es_Tarjeta_Credito"] in ("SI", "NO")


def test_reglas_formato_campos_del_bot():
    reglas = supabase_client.obtener_reglas()
    for r in reglas:
        for clave in ("Remitente", "Asunto_Contiene", "Clave", "Activo",
                      "Tiene_Adjunto", "Es_Tarjeta_Credito", "Regex_Consumo",
                      "Regex_Cierre", "Regex_Vencimiento", "Regex_Monto",
                      "Pertenece", "Entidad"):
            assert clave in r, f"Falta clave {clave!r} en {r!r}"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def test_hora_ejecucion_raw_12_no_corregida():
    # Riesgo P6 de la auditoría PRESERVADO: '12' sin ':'.
    assert supabase_client.obtener_config_valor("Hora_Ejecucion") == "12"


def test_config_equivale_a_hoja():
    _, rows = _csv_rows("Resumenes_bot - Config.csv")
    esperado = {
        "Hora_Ejecucion": (rows[0][0] or "").strip(),
        "Last_Telegram_Update_ID": (rows[0][1] or "").strip(),
        "Telegram_State": (rows[0][2] or "").strip(),
    }
    cfg = supabase_client.obtener_config()
    for clave, valor in esperado.items():
        assert cfg.get(clave) == valor, (
            f"config[{clave!r}]: Supabase={cfg.get(clave)!r} hoja={valor!r}"
        )


def test_config_telegram_consistente():
    cfg = supabase_client.obtener_config()
    last_id, state = supabase_client.obtener_config_telegram()
    assert last_id == int(cfg.get("Last_Telegram_Update_ID", 0) or 0)
    assert state == cfg.get("Telegram_State", "").strip()


# ---------------------------------------------------------------------------
# Tipos / fijos (categorias_fijas)
# ---------------------------------------------------------------------------

def test_tipos_equivale_a_hoja():
    _, rows = _csv_rows("Resumenes_bot - Config.csv")
    gastos_hoja = {(r[4] or "").strip() for r in rows if len(r) > 4 and r[4].strip()}
    ingresos_hoja = {(r[8] or "").strip() for r in rows if len(r) > 8 and r[8].strip()}

    gastos, ingresos = supabase_client.obtener_tipos()
    assert set(gastos) == gastos_hoja, (
        f"gastos: Supabase={set(gastos)} hoja={gastos_hoja}"
    )
    assert set(ingresos) == ingresos_hoja, (
        f"ingresos: Supabase={set(ingresos)} hoja={ingresos_hoja}"
    )


def test_fijos_equivale_a_hoja():
    # La hoja Config tiene Monto_Fijo vacío (todas NULL en categorias_fijas) ->
    # el bot NO genera fijos (mismo comportamiento que procesar_fijos_mensuales
    # con monto > 0).
    _, rows = _csv_rows("Resumenes_bot - Config.csv")
    fijos_hoja_g = {
        (r[4] or "").strip()
        for r in rows
        if len(r) > 5 and r[4].strip() and (r[5] or "").strip()
    }
    fijos_hoja_i = {
        (r[8] or "").strip()
        for r in rows
        if len(r) > 9 and r[8].strip() and (r[9] or "").strip()
    }

    gastos, ingresos = supabase_client.obtener_fijos()
    assert {g["tipo"] for g in gastos} == fijos_hoja_g
    assert {i["tipo"] for i in ingresos} == fijos_hoja_i
    for g in gastos + ingresos:
        assert g["monto"] > 0
