"""FASE 3 — Integración: bot_Saldo.py lee de Supabase con fallback a Sheets.

Verifica:
  - Ruta Supabase: las lecturas NO tocan Google cuando Supabase responde.
  - Fallback explícito a Sheets cuando Supabase no está configurado o falla,
    con el mensaje '[SUPABASE READ ERROR]' visible (nunca oculto).
  - Hora_Ejecucion='12' (sin ':') NO se corrige: debe_ejecutar_ahora() == True
    (comportamiento heredado de Sheets, preservado en FASE 3).
  - Formatos 1:1 entre ambas fuentes (reglas, config completa, fijos).
"""
import sys

import pytest

from conftest import REPO_ROOT

sys.path.insert(0, REPO_ROOT)
import supabase_client


class FakeWorksheet:
    def __init__(self, records=None, values=None):
        self._records = records or []
        self._values = values if values is not None else []
        self.append_rows_calls = []

    def get_all_records(self):
        return self._records

    def get_all_values(self):
        return self._values

    def append_rows(self, rows, value_input_option="USER_ENTERED"):
        self.append_rows_calls.append((rows, value_input_option))


class FakeSheet:
    def __init__(self, worksheets):
        self._ws = worksheets

    def worksheet(self, name):
        return self._ws[name]


class FakeGC:
    def __init__(self, worksheets):
        self._sheet = FakeSheet(worksheets)

    def open_by_key(self, key):
        return self._sheet


def _regla(remitente, activo="SI"):
    return {
        "Activo": activo,
        "Remitente": remitente,
        "Asunto_Contiene": "R",
        "Clave": "1",
        "Tiene_Adjunto": "NO",
        "Es_Tarjeta_Credito": "NO",
        "Regex_Consumo": "",
        "Regex_Cierre": "",
        "Regex_Vencimiento": "",
        "Regex_Monto": "",
    }


# ---------------------------------------------------------------------------
# obtener_reglas
# ---------------------------------------------------------------------------

def test_obtener_reglas_usa_supabase(bot_module, monkeypatch):
    llamadas_gc = []
    canned = [_regla("a@a.com")]
    monkeypatch.setattr(supabase_client, "supabase_disponible", lambda: True)
    monkeypatch.setattr(supabase_client, "obtener_reglas", lambda: canned)
    monkeypatch.setattr(
        bot_module, "get_gc", lambda: llamadas_gc.append(1) or None
    )
    assert bot_module.obtener_reglas() == canned
    assert llamadas_gc == [], "No debe tocar Google Sheets cuando Supabase responde"


def test_obtener_reglas_fallback_sheets(bot_module, monkeypatch, capsys):
    monkeypatch.setattr(supabase_client, "supabase_disponible", lambda: False)
    ws = FakeWorksheet(records=[
        _regla("a@a.com", "SI"),
        _regla("b@b.com", "NO"),
        _regla("c@c.com", "SI"),
    ])
    monkeypatch.setattr(bot_module, "get_gc", lambda: FakeGC({"Datos": ws}))
    reglas = bot_module.obtener_reglas()
    assert [r["Remitente"] for r in reglas] == ["a@a.com", "c@c.com"]
    assert all(r["Activo"] == "SI" for r in reglas)
    assert "[SUPABASE READ ERROR]" in capsys.readouterr().out


def test_obtener_reglas_fallback_por_error(bot_module, monkeypatch, capsys):
    monkeypatch.setattr(supabase_client, "supabase_disponible", lambda: True)

    def boom():
        raise supabase_client.SupabaseReadError("connection timeout")

    monkeypatch.setattr(supabase_client, "obtener_reglas", boom)
    ws = FakeWorksheet(records=[_regla("a@a.com", "SI")])
    monkeypatch.setattr(bot_module, "get_gc", lambda: FakeGC({"Datos": ws}))
    reglas = bot_module.obtener_reglas()
    assert len(reglas) == 1
    out = capsys.readouterr().out
    assert "[SUPABASE READ ERROR]" in out
    assert "connection timeout" in out


# ---------------------------------------------------------------------------
# debe_ejecutar_ahora
# ---------------------------------------------------------------------------

def test_debe_ejecutar_ahora_hora_12_no_se_corrige(bot_module, monkeypatch):
    # Hora_Ejecucion='12' llega raw desde Supabase; split(':') falla ->
    # debe_ejecutar_ahora() == True (igual que con la hoja; NO se corrige).
    monkeypatch.setenv("FORZAR_EJECUCION", "false")
    monkeypatch.setattr(supabase_client, "supabase_disponible", lambda: True)
    monkeypatch.setattr(supabase_client, "obtener_config_valor", lambda clave: "12")
    assert bot_module.debe_ejecutar_ahora() is True


def test_debe_ejecutar_ahora_usa_supabase_sin_sheets(bot_module, monkeypatch):
    monkeypatch.setenv("FORZAR_EJECUCION", "false")
    tocado = []
    monkeypatch.setattr(supabase_client, "supabase_disponible", lambda: True)
    monkeypatch.setattr(supabase_client, "obtener_config_valor", lambda clave: "12")
    monkeypatch.setattr(bot_module, "get_gc", lambda: tocado.append(1) or None)
    assert bot_module.debe_ejecutar_ahora() is True
    assert tocado == []


def test_debe_ejecutar_ahora_fallback_sheets(bot_module, monkeypatch):
    monkeypatch.setenv("FORZAR_EJECUCION", "false")
    monkeypatch.setattr(supabase_client, "supabase_disponible", lambda: False)
    ws = FakeWorksheet(records=[{"Hora_Ejecucion": "12"}])
    monkeypatch.setattr(bot_module, "get_gc", lambda: FakeGC({"Config": ws}))
    assert bot_module.debe_ejecutar_ahora() is True


# ---------------------------------------------------------------------------
# leer_config_completo
# ---------------------------------------------------------------------------

def test_leer_config_completo_usa_supabase(bot_module, monkeypatch):
    monkeypatch.setattr(supabase_client, "supabase_disponible", lambda: True)
    monkeypatch.setattr(
        supabase_client, "obtener_config_completo",
        lambda: (445690403, "state_x", ["ALQUILER"], ["SUELDO"]),
    )
    # ws_config=None: en la ruta Supabase NO se usa la hoja (param solo fallback).
    last_id, state, gastos, ingresos = bot_module.leer_config_completo(None)
    assert (last_id, state, gastos, ingresos) == (
        445690403, "state_x", ["ALQUILER"], ["SUELDO"],
    )


def test_leer_config_completo_fallback_sheets(bot_module, monkeypatch):
    monkeypatch.setattr(supabase_client, "supabase_disponible", lambda: False)
    ws = FakeWorksheet(values=[
        ["Hora_Ejecucion", "12", ""],
        ["", "445690403", "", "", "ALQUILER", "", "", "", "", ""],
        ["", "", "", "", "PASAJE", "", "", "", "SUELDO", ""],
        ["", "", "", "", "", "", "", "", "DEV PREST", ""],
    ])
    last_id, state, gastos, ingresos = bot_module.leer_config_completo(ws)
    assert last_id == 445690403
    assert state == ""
    assert gastos == ["ALQUILER", "PASAJE"]
    assert ingresos == ["SUELDO", "DEV PREST"]


# ---------------------------------------------------------------------------
# procesar_fijos_mensuales
# ---------------------------------------------------------------------------

def test_procesar_fijos_mensuales_usa_supabase(bot_module, monkeypatch):
    monkeypatch.setattr(supabase_client, "supabase_disponible", lambda: True)
    monkeypatch.setattr(
        supabase_client, "obtener_fijos",
        lambda: ([{"tipo": "ALQUILER", "monto": 10000.0}],
                 [{"tipo": "SUELDO", "monto": 1700000.0}]),
    )
    llamadas_consumos = []
    llamadas_ingresos = []

    def fake_guardar_consumos(consumos, remitente):
        llamadas_consumos.append((consumos, remitente))
        return [
            {"estado": "insertado", "detalle": c["detalle"], "pesos": c["pesos"]}
            for c in consumos
        ]

    def fake_guardar_ingreso(fecha, tipo, monto, origen="Manual Telegram"):
        llamadas_ingresos.append((fecha, tipo, monto, origen))
        return "insertado"

    monkeypatch.setattr(
        supabase_client, "guardar_o_actualizar_consumos", fake_guardar_consumos
    )
    monkeypatch.setattr(supabase_client, "guardar_ingreso", fake_guardar_ingreso)

    msgs = []
    monkeypatch.setattr(bot_module, "enviar_telegram", lambda m: msgs.append(m))
    # ws_config=None: en la ruta Supabase NO se lee la hoja Config.
    bot_module.procesar_fijos_mensuales(None, FakeWorksheet(), FakeWorksheet())

    assert len(llamadas_consumos) == 1
    consumos, remitente = llamadas_consumos[0]
    assert remitente == "Fijo Config"
    assert len(consumos) == 1
    assert consumos[0]["comprobante"] == "Fijo Config"
    assert consumos[0]["detalle"] == "ALQUILER"
    assert consumos[0]["cuota_actual"] == 1
    assert consumos[0]["cuota_total"] == 1
    assert consumos[0]["pesos"] == 10000.0
    assert consumos[0]["dolar"] == 0.0

    assert len(llamadas_ingresos) == 1
    fecha_fijo, tipo, monto, origen = llamadas_ingresos[0]
    assert fecha_fijo.startswith("01/")
    assert tipo == "SUELDO"
    assert monto == 1700000.0
    assert origen == "Fijo Config"

    assert len(msgs) == 2


def test_procesar_fijos_mensuales_fallback_sheets_escritura_falla_explicito(
    bot_module, monkeypatch
):
    """FASE 4: sin Supabase, los fijos se LEEN de la hoja (fallback de lectura)
    pero la ESCRITURA falla explícito (SupabaseNotConfiguredError) y NO escribe
    en Sheets: no hay doble escritura ni fallback silencioso."""
    monkeypatch.setattr(supabase_client, "supabase_disponible", lambda: False)
    # Sin credenciales (aunque el entorno de ejecución las tenga), la escritura
    # NO puede caer en la DB real: debe fallar explícito y no tocar Sheets.
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    monkeypatch.delenv("SUPABASE_DBPW", raising=False)
    ws_config = FakeWorksheet(values=[
        ["Hora_Ejecucion", "12", ""],
        ["", "", "", "", "ALQUILER", "10000", "", "", "", ""],
        ["", "", "", "", "PASAJE", "", "", "", "SUELDO", "1700000"],
    ])
    ws_consumos = FakeWorksheet(values=[
        ["Fecha Consumo", "Comprobante", "Detalle", "Cuota Actual", "Cuota Total",
         "Pesos", "Dolar", "Fecha Cierre", "Fecha Vencimiento", "Remitente", "ID_Consumo"],
    ])
    ws_ingresos = FakeWorksheet(values=[["Fecha", "Tipo", "Monto", "Origen", "ID_Ingreso"]])
    msgs = []
    monkeypatch.setattr(bot_module, "enviar_telegram", lambda m: msgs.append(m))
    with pytest.raises(supabase_client.SupabaseNotConfiguredError):
        bot_module.procesar_fijos_mensuales(ws_config, ws_consumos, ws_ingresos)
    assert ws_consumos.append_rows_calls == []
    assert ws_ingresos.append_rows_calls == []


def test_procesar_fijos_mensuales_sin_fijos_no_escribe(bot_module, monkeypatch):
    monkeypatch.setattr(supabase_client, "supabase_disponible", lambda: True)
    monkeypatch.setattr(supabase_client, "obtener_fijos", lambda: ([], []))
    ws_consumos = FakeWorksheet(values=[[]])
    bot_module.procesar_fijos_mensuales(None, ws_consumos, None)
    assert ws_consumos.append_rows_calls == []
