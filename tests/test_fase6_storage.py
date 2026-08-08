"""FASE 6 - Storage privado: unit tests del helper Supabase y del bot."""
import sys

import pytest
import requests

from conftest import REPO_ROOT

sys.path.insert(0, REPO_ROOT)
import bot_Saldo  # noqa: E402
import supabase_client  # noqa: E402


class _Resp:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text

    def json(self):
        return self._json_data


def test_subir_pdf_storage_ok(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "srk")
    llamadas = []

    def fake_post(url, headers=None, data=None, json=None, timeout=None):
        llamadas.append((url, headers, data, json, timeout))
        if "/object/sign/" in url:
            return _Resp(200, {"signedURL": "/object/sign/pdfs/x.pdf?token=t"})
        return _Resp(200, {"Key": "ok"})

    monkeypatch.setattr(requests, "post", fake_post)
    r = supabase_client.subir_pdf_storage("Factura.pdf", b"%PDF-1.4\nabc", "BNA")

    assert r["link_ref"].startswith("storage://pdfs/resumenes/bna/")
    assert r["link_ref"].endswith(".pdf")
    assert r["signed_url"] == (
        "https://example.supabase.co/storage/v1/object/sign/pdfs/x.pdf?token=t"
    )
    assert any("/storage/v1/object/pdfs/" in c[0] for c in llamadas)
    assert any("/storage/v1/object/sign/pdfs/" in c[0] for c in llamadas)


def test_subir_pdf_storage_invalida_pdf(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "srk")
    with pytest.raises(supabase_client.SupabaseStorageError):
        supabase_client.subir_pdf_storage("Factura.pdf", b"no-pdf", "BNA")


def test_subir_pdf_storage_excede_10mb(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "srk")
    grande = b"%PDF" + (b"a" * (10 * 1024 * 1024 + 1))
    with pytest.raises(supabase_client.SupabaseStorageError):
        supabase_client.subir_pdf_storage("Factura.pdf", grande, "BNA")


def test_subir_pdf_usa_storage(bot_module, monkeypatch):
    monkeypatch.setattr(
        supabase_client,
        "subir_pdf_storage",
        lambda nombre_archivo, pdf_bytes, remitente: {
            "link_ref": "storage://pdfs/resumenes/bna/abc.pdf",
            "signed_url": "https://signed",
        },
    )
    monkeypatch.setattr(
        bot_module,
        "subir_a_drive",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("No debe usar Drive")),
    )
    assert bot_module.subir_pdf("Factura.pdf", b"%PDF-1.4", "BNA") == (
        "storage://pdfs/resumenes/bna/abc.pdf",
        "https://signed",
    )


def test_subir_pdf_fallback_drive(bot_module, monkeypatch, capsys):
    monkeypatch.setattr(
        supabase_client,
        "subir_pdf_storage",
        lambda *a, **k: (_ for _ in ()).throw(
            supabase_client.SupabaseStorageError("[SUPABASE STORAGE ERROR] boom")
        ),
    )
    monkeypatch.setattr(bot_module, "subir_a_drive", lambda *a, **k: "https://drive")
    assert bot_module.subir_pdf("Factura.pdf", b"%PDF-1.4", "BNA") == (
        "https://drive",
        "https://drive",
    )
    assert "Usando Google Drive como respaldo temporal" in capsys.readouterr().out


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


def test_revisar_mails_storage_ref_en_db_y_signed_url_en_telegram(bot_module, monkeypatch):
    guardados = []
    enviados = []
    registrados = []

    monkeypatch.setattr(bot_module, "get_gc", lambda: _GC())
    monkeypatch.setattr(bot_module, "obtener_o_crear_label", lambda nombre: "LBL")
    monkeypatch.setattr(bot_module, "obtener_reglas", lambda: [{
        "Remitente": "bna",
        "Asunto_Contiene": "Resumen",
        "Clave": "",
        "Tiene_Adjunto": "SI",
        "Es_Tarjeta_Credito": "NO",
    }])
    monkeypatch.setattr(bot_module, "procesar_fijos_mensuales", lambda *a: None)
    monkeypatch.setattr(supabase_client, "supabase_disponible", lambda: True)
    monkeypatch.setattr(bot_module, "buscar_mails_nuevos", lambda *a, **k: [{"id": "m1"}])
    monkeypatch.setattr(bot_module, "mensaje_ya_procesado", lambda *a: False)
    monkeypatch.setattr(
        bot_module,
        "extraer_datos_mensaje_mime",
        lambda mensaje_id: ("Resumen", "Tue, 05 Aug 2026 12:00:00 -0300", "texto", "html", "Factura.pdf", b"%PDF-1.4\nabc"),
    )
    monkeypatch.setattr(
        bot_module,
        "extraer_consumos_pdf",
        lambda *a, **k: ([], "", "21/08/2026", 100.0),
    )
    monkeypatch.setattr(bot_module, "es_registro_duplicado", lambda *a: False)
    monkeypatch.setattr(
        bot_module,
        "subir_pdf",
        lambda *a, **k: ("storage://pdfs/resumenes/bna/abc.pdf", "https://signed"),
    )
    monkeypatch.setattr(
        bot_module,
        "guardar_en_sheet",
        lambda *a: guardados.append(a) or "insertado",
    )
    monkeypatch.setattr(bot_module, "enviar_telegram", lambda msg: enviados.append(msg))
    monkeypatch.setattr(
        bot_module,
        "registrar_y_marcar_mensaje_procesado",
        lambda *a: registrados.append(a),
    )

    bot_module.revisar_mails()

    assert guardados[0][6] == "storage://pdfs/resumenes/bna/abc.pdf"
    assert "PDF: https://signed" in enviados[0]
    assert registrados == [("m1", "bna", "Resumen", "LBL")]


def test_revisar_mails_epec_sin_storage(bot_module, monkeypatch):
    guardados = []
    enviados = []

    monkeypatch.setattr(bot_module, "get_gc", lambda: _GC())
    monkeypatch.setattr(bot_module, "obtener_o_crear_label", lambda nombre: "LBL")
    monkeypatch.setattr(bot_module, "obtener_reglas", lambda: [{
        "Remitente": "avisos@oficinaepec.com.ar",
        "Asunto_Contiene": "Factura",
        "Clave": "",
        "Tiene_Adjunto": "SI",
        "Es_Tarjeta_Credito": "NO",
    }])
    monkeypatch.setattr(bot_module, "procesar_fijos_mensuales", lambda *a: None)
    monkeypatch.setattr(supabase_client, "supabase_disponible", lambda: True)
    monkeypatch.setattr(bot_module, "buscar_mails_nuevos", lambda *a, **k: [{"id": "m1"}])
    monkeypatch.setattr(bot_module, "mensaje_ya_procesado", lambda *a: False)
    monkeypatch.setattr(
        bot_module,
        "extraer_datos_mensaje_mime",
        lambda mensaje_id: ("Factura EPEC", "Tue, 05 Aug 2026 12:00:00 -0300", "texto", "html", "Factura.pdf", b"%PDF-1.4\nabc"),
    )
    monkeypatch.setattr(
        bot_module,
        "extraer_consumos_pdf",
        lambda *a, **k: ([], "", "21/08/2026", 100.0),
    )
    monkeypatch.setattr(bot_module, "es_registro_duplicado", lambda *a: False)
    monkeypatch.setattr(
        bot_module,
        "subir_pdf",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("EPEC no debe subir archivo")),
    )
    monkeypatch.setattr(
        bot_module,
        "guardar_en_sheet",
        lambda *a: guardados.append(a) or "insertado",
    )
    monkeypatch.setattr(bot_module, "enviar_telegram", lambda msg: enviados.append(msg))
    monkeypatch.setattr(bot_module, "registrar_y_marcar_mensaje_procesado", lambda *a: None)

    bot_module.revisar_mails()

    assert guardados[0][6] == ""
    assert "PDF:" not in enviados[0]


def test_telegram_pdf_manual_guarda_storage_ref_y_envia_signed_url(bot_module, monkeypatch):
    guardados = []
    enviados = []
    pdf_bytes = b"%PDF-1.4\nabc"

    class FakePDF:
        pages = []

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class RespFile:
        def json(self):
            return {"result": {"file_path": "docs/test.pdf"}}

    class RespDownload:
        content = pdf_bytes

    class WSConfig:
        def get_all_values(self):
            return [["h"]]

    class Sheet:
        def worksheet(self, name):
            return WSConfig()

    class GC:
        def open_by_key(self, key):
            return Sheet()

    monkeypatch.setattr(bot_module, "get_gc", lambda: GC())
    monkeypatch.setattr(
        supabase_client, "obtener_config_completo", lambda: (0, "", ["ALQ"], ["SUELDO"])
    )
    monkeypatch.setattr(
        bot_module.requests,
        "get",
        lambda url: RespFile() if "getFile" in url else RespDownload(),
    )
    monkeypatch.setattr(bot_module.pdfplumber, "open", lambda *a, **k: FakePDF())
    monkeypatch.setattr(bot_module, "obtener_reglas", lambda: [{
        "Remitente": "bna",
        "Clave": "",
        "Es_Tarjeta_Credito": "NO",
    }])
    monkeypatch.setattr(bot_module, "identificar_regla_por_pdf", lambda *a: {
        "Remitente": "bna",
        "Clave": "",
        "Es_Tarjeta_Credito": "NO",
    })
    monkeypatch.setattr(
        bot_module,
        "extraer_consumos_pdf",
        lambda *a, **k: ([], "", "21/08/2026", 100.0),
    )
    monkeypatch.setattr(bot_module, "es_registro_duplicado", lambda *a: False)
    monkeypatch.setattr(
        bot_module,
        "subir_pdf",
        lambda *a, **k: ("storage://pdfs/resumenes/bna/abc.pdf", "https://signed"),
    )
    monkeypatch.setattr(
        bot_module,
        "guardar_en_sheet",
        lambda *a: guardados.append(a) or "insertado",
    )
    monkeypatch.setattr(bot_module, "enviar_telegram", lambda msg: enviados.append(msg))

    payload = '{"message": {"chat": {"id": "test-chat-id"}, "document": {"file_id": "f1", "file_name": "Factura.pdf", "mime_type": "application/pdf"}}}'
    monkeypatch.setenv("TELEGRAM_UPDATE_PAYLOAD", payload)
    bot_module.procesar_mensajes_telegram()

    assert guardados[0][6] == "storage://pdfs/resumenes/bna/abc.pdf"
    assert "📂 Archivo: https://signed" in enviados[0]
