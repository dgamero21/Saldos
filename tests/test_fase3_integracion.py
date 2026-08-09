"""FASE 3/10C — Integración: bot_Saldo.py lee de Supabase (sin Sheets).

Verifica:
  - Ruta Supabase: las lecturas NO tocan Google cuando Supabase responde.
  - Sin Supabase (no configurado) o ante un fallo, la lectura lanza
    SupabaseReadError con el mensaje '[SUPABASE READ ERROR]' visible (nunca
    ocultado); ya NO hay fallback a Google Sheets (FASE 10C).
  - Hora_Ejecucion='12' (sin ':') NO se corrige: debe_ejecutar_ahora() == True
    (comportamiento heredado de Sheets, preservado en FASE 3).
  - Formatos 1:1 con la fuente (reglas, config completa, fijos).
"""
import sys

import pytest

from conftest import REPO_ROOT

sys.path.insert(0, REPO_ROOT)
import supabase_client


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
    canned = [_regla("a@a.com")]
    monkeypatch.setattr(supabase_client, "supabase_disponible", lambda: True)
    monkeypatch.setattr(supabase_client, "obtener_reglas", lambda: canned)
    assert bot_module.obtener_reglas() == canned


def test_obtener_reglas_supabase_no_configurado_raise(
    bot_module, monkeypatch, capsys
):
    monkeypatch.setattr(supabase_client, "supabase_disponible", lambda: False)
    with pytest.raises(supabase_client.SupabaseReadError):
        bot_module.obtener_reglas()
    assert "[SUPABASE READ ERROR]" in capsys.readouterr().out


def test_obtener_reglas_error_propaga(bot_module, monkeypatch, capsys):
    monkeypatch.setattr(supabase_client, "supabase_disponible", lambda: True)

    def boom():
        raise supabase_client.SupabaseReadError("connection timeout")

    monkeypatch.setattr(supabase_client, "obtener_reglas", boom)
    with pytest.raises(supabase_client.SupabaseReadError):
        bot_module.obtener_reglas()
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
    monkeypatch.setattr(supabase_client, "supabase_disponible", lambda: True)
    monkeypatch.setattr(supabase_client, "obtener_config_valor", lambda clave: "12")
    assert bot_module.debe_ejecutar_ahora() is True


def test_debe_ejecutar_ahora_supabase_no_configurado_raise(
    bot_module, monkeypatch, capsys
):
    monkeypatch.setenv("FORZAR_EJECUCION", "false")
    monkeypatch.setattr(supabase_client, "supabase_disponible", lambda: False)
    with pytest.raises(supabase_client.SupabaseReadError):
        bot_module.debe_ejecutar_ahora()
    assert "[SUPABASE READ ERROR]" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# leer_config_completo
# ---------------------------------------------------------------------------

def test_leer_config_completo_usa_supabase(bot_module, monkeypatch):
    monkeypatch.setattr(supabase_client, "supabase_disponible", lambda: True)
    monkeypatch.setattr(
        supabase_client, "obtener_config_completo",
        lambda: (445690403, "state_x", ["ALQUILER"], ["SUELDO"]),
    )
    last_id, state, gastos, ingresos = bot_module.leer_config_completo()
    assert (last_id, state, gastos, ingresos) == (
        445690403, "state_x", ["ALQUILER"], ["SUELDO"],
    )


def test_leer_config_completo_supabase_no_configurado_raise(
    bot_module, monkeypatch, capsys
):
    monkeypatch.setattr(supabase_client, "supabase_disponible", lambda: False)
    with pytest.raises(supabase_client.SupabaseReadError):
        bot_module.leer_config_completo()
    assert "[SUPABASE READ ERROR]" in capsys.readouterr().out


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
    bot_module.procesar_fijos_mensuales()

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


def test_procesar_fijos_mensuales_sin_supabase_raise(
    bot_module, monkeypatch, capsys
):
    """FASE 4/10C: sin Supabase, la lectura de fijos lanza SupabaseReadError;
    no hay fallback a Sheets para leer ni escribir."""
    monkeypatch.setattr(supabase_client, "supabase_disponible", lambda: False)
    # Sin credenciales (aunque el entorno de ejecución las tenga), la lectura
    # debe fallar explícito y no tocar Sheets.
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    monkeypatch.delenv("SUPABASE_DBPW", raising=False)
    with pytest.raises(supabase_client.SupabaseReadError):
        bot_module.procesar_fijos_mensuales()
    assert "[SUPABASE READ ERROR]" in capsys.readouterr().out


def test_procesar_fijos_mensuales_sin_fijos_no_escribe(bot_module, monkeypatch):
    monkeypatch.setattr(supabase_client, "supabase_disponible", lambda: True)
    monkeypatch.setattr(supabase_client, "obtener_fijos", lambda: ([], []))
    bot_module.procesar_fijos_mensuales()
