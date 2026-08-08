"""FASE 5 - Gmail: idempotencia por mensajes_procesados.

Tests unitarios del flujo del bot sin usar Gmail real:
  - la query Gmail deja de depender del label cuando Supabase está disponible;
  - el estado "ya procesado" se consulta primero en Supabase;
  - fallback al label Gmail si la lectura Supabase falla;
  - al cerrar un mail, se registra en Supabase y luego se etiqueta en Gmail;
  - revisar_mails() evita reprocesar mails ya marcados en la DB.
"""
import sys

import pytest

from conftest import REPO_ROOT

sys.path.insert(0, REPO_ROOT)
import supabase_client  # noqa: E402


def test_buscar_mails_nuevos_query_con_y_sin_label(bot_module, monkeypatch):
    queries = []

    class Messages:
        def list(self, userId, q):
            queries.append(q)

            class Req:
                def execute(self):
                    return {"messages": []}

            return Req()

    class Users:
        def messages(self):
            return Messages()

    class Gmail:
        def users(self):
            return Users()

    monkeypatch.setattr(bot_module, "get_gmail_service", lambda: Gmail())

    bot_module.buscar_mails_nuevos("bna", "Resumen", excluir_label_procesado=False)
    bot_module.buscar_mails_nuevos("bna", "Resumen", excluir_label_procesado=True)

    assert queries[0] == 'from:bna subject:"Resumen"'
    assert queries[1] == 'from:bna -label:Procesado-Resumen subject:"Resumen"'


def test_mensaje_ya_procesado_usa_supabase(bot_module, monkeypatch):
    monkeypatch.setattr(supabase_client, "supabase_disponible", lambda: True)
    monkeypatch.setattr(supabase_client, "mensaje_ya_procesado", lambda m: True)
    monkeypatch.setattr(
        bot_module, "mensaje_tiene_label", lambda mensaje_id, label_id: False
    )
    assert bot_module.mensaje_ya_procesado("m1", "LBL") is True


def test_mensaje_ya_procesado_fallback_a_label(bot_module, monkeypatch, capsys):
    monkeypatch.setattr(supabase_client, "supabase_disponible", lambda: True)

    def boom(mensaje_id):
        raise supabase_client.SupabaseReadError("timeout")

    monkeypatch.setattr(supabase_client, "mensaje_ya_procesado", boom)
    monkeypatch.setattr(
        bot_module, "mensaje_tiene_label", lambda mensaje_id, label_id: True
    )
    assert bot_module.mensaje_ya_procesado("m1", "LBL") is True
    assert "[SUPABASE READ ERROR]" in capsys.readouterr().out


def test_registrar_y_marcar_mensaje_procesado_tolera_error_de_label(
    bot_module, monkeypatch, capsys
):
    llamados = []
    monkeypatch.setattr(
        supabase_client,
        "registrar_mensaje_procesado",
        lambda mensaje_id, remitente="", asunto="": (
            llamados.append((mensaje_id, remitente, asunto)) or "insertado"
        ),
    )

    def boom(*args, **kwargs):
        raise RuntimeError("gmail modify down")

    monkeypatch.setattr(bot_module, "marcar_procesado", boom)
    bot_module.registrar_y_marcar_mensaje_procesado("m1", "bna", "Resumen", "LBL")
    assert llamados == [("m1", "bna", "Resumen")]
    assert "[GMAIL LABEL ERROR]" in capsys.readouterr().out


class _WS:
    def get_all_values(self):
        return [["h"]]

    def get_all_records(self):
        return [{"Hora_Ejecucion": "12"}]


class _Sheet:
    def worksheet(self, name):
        return _WS()


class _GC:
    def open_by_key(self, key):
        return _Sheet()


def test_revisar_mails_no_reprocesa_mensaje_ya_en_db(bot_module, monkeypatch):
    flags = []
    monkeypatch.setattr(bot_module, "get_gc", lambda: _GC())
    monkeypatch.setattr(bot_module, "obtener_o_crear_label", lambda nombre: "LBL")
    monkeypatch.setattr(bot_module, "obtener_reglas", lambda: [{
        "Remitente": "bna",
        "Asunto_Contiene": "Resumen",
        "Clave": "",
        "Tiene_Adjunto": "NO",
        "Es_Tarjeta_Credito": "NO",
    }])
    monkeypatch.setattr(bot_module, "procesar_fijos_mensuales", lambda *a: None)
    monkeypatch.setattr(supabase_client, "supabase_disponible", lambda: True)

    def fake_buscar(remitente, asunto_contiene, excluir_label_procesado=False):
        flags.append(excluir_label_procesado)
        return [{"id": "m1"}]

    monkeypatch.setattr(bot_module, "buscar_mails_nuevos", fake_buscar)
    monkeypatch.setattr(bot_module, "mensaje_ya_procesado", lambda mensaje_id, label_id: True)

    def no_deberia_llamarse(*args, **kwargs):
        raise AssertionError("No debe volver a extraer un mail ya procesado")

    monkeypatch.setattr(bot_module, "extraer_datos_mensaje_mime", no_deberia_llamarse)
    bot_module.revisar_mails()
    assert flags == [False]


def test_revisar_mails_dup_registra_mensaje_procesado(bot_module, monkeypatch):
    registrados = []
    monkeypatch.setattr(bot_module, "get_gc", lambda: _GC())
    monkeypatch.setattr(bot_module, "obtener_o_crear_label", lambda nombre: "LBL")
    monkeypatch.setattr(bot_module, "obtener_reglas", lambda: [{
        "Remitente": "bna",
        "Asunto_Contiene": "Resumen",
        "Clave": "",
        "Tiene_Adjunto": "NO",
        "Es_Tarjeta_Credito": "NO",
        "Regex_Cierre": "",
        "Regex_Vencimiento": "",
        "Regex_Monto": "",
    }])
    monkeypatch.setattr(bot_module, "procesar_fijos_mensuales", lambda *a: None)
    monkeypatch.setattr(supabase_client, "supabase_disponible", lambda: True)
    monkeypatch.setattr(
        bot_module,
        "buscar_mails_nuevos",
        lambda *a, **k: [{"id": "m1"}],
    )
    monkeypatch.setattr(bot_module, "mensaje_ya_procesado", lambda *a: False)
    monkeypatch.setattr(
        bot_module,
        "extraer_datos_mensaje_mime",
        lambda mensaje_id: ("Resumen", "Tue, 05 Aug 2026 12:00:00 -0300", "texto", "html", None, None),
    )
    monkeypatch.setattr(
        bot_module,
        "extraer_fechas_y_monto_global",
        lambda *a, **k: ("", "21/08/2026", 100.0),
    )
    monkeypatch.setattr(bot_module, "es_registro_duplicado", lambda *a: True)
    monkeypatch.setattr(
        bot_module,
        "registrar_y_marcar_mensaje_procesado",
        lambda mensaje_id, remitente, asunto, label_id: registrados.append(
            (mensaje_id, remitente, asunto, label_id)
        ),
    )
    monkeypatch.setattr(
        bot_module,
        "guardar_en_sheet",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("No debe escribir")),
    )

    bot_module.revisar_mails()

    assert registrados == [("m1", "bna", "Resumen", "LBL")]
