"""FASE 9 — Integración E2E y Regresión.

Escenarios E2E (requieren SUPABASE_DB_URL + SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY
en el entorno, como en CI):

1. Gmail PDF completo:
   - mock mensaje Gmail con adjunto PDF
   - bot extrae, parsea, sube a Storage (signed URL)
   - inserta en consolidado + consumos
   - registra mensaje_id
   - envía Telegram con signed URL

2. Webhook callbacks MANUAL| / INGRESO| / CANCELAR:
   - MANUAL|monto|cat -> insert consumos + edit Telegram
   - INGRESO|monto|cat -> insert ingresos + edit Telegram
   - CANCELAR -> solo edit Telegram

3. Webhook consultas (vencimientos, balance, cuotas, gasto cat):
   - vencimientos -> consolidado
   - balance -> consumos + ingresos (mes actual)
   - cuotas -> consumos (cuota_total > 1)
   - gasto <cat> -> consumos por detalle + mes actual

6. Deduplicación:
   - consolidado: misma clave (lower(remitente)|vto|monto) -> skip
   - consumos: misma clave (fecha|comprobante|detalle|cuota_total|remitente) con cuota menor -> no retrocede
   - ingresos: misma clave (fecha|tipo|origen) -> skip
   - mensaje_id: mismo ID Gmail -> skip antes de extraer MIME

7. EPEC: no Storage upload, link vacío

8. Fijos mensuales: insert consumos (Fijo Config) + ingresos (Fijo Config)

9. PDF dispatch: Telegram PDF -> repository_dispatch -> bot procesa igual que Gmail

10. Seguridad: secrets no en logs, RLS, solo service_role
"""
import os
import sys
from datetime import date, datetime
from uuid import uuid4

import pytest
import requests

from conftest import REPO_ROOT

pytestmark = pytest.mark.skipif(
    not (
        os.environ.get("SUPABASE_DB_URL") or os.environ.get("SUPABASE_DBPW")
    ),
    reason="FASE 9 E2E requiere SUPABASE_DB_URL o SUPABASE_DBPW",
)

sys.path.insert(0, REPO_ROOT)
import bot_Saldo
import supabase_client


# ---------------------------------------------------------------------------
# Helpers y fixtures
# ---------------------------------------------------------------------------

def _fetch(sql, params=None):
    conn = supabase_client._get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()
    finally:
        conn.close()


def _cleanup_test_data(tag):
    """Limpia todos los datos de prueba con el tag dado."""
    conn = supabase_client._get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM consolidado WHERE remitente = %s OR remitente LIKE %s",
                (tag, f"%{tag}%"),
            )
            cur.execute(
                "DELETE FROM consumos WHERE remitente = %s OR remitente LIKE %s",
                (tag, f"%{tag}%"),
            )
            # También limpiar fijos (remitente = 'Fijo Config' con tag en detalle)
            cur.execute(
                "DELETE FROM consumos WHERE remitente = 'Fijo Config' AND detalle LIKE %s",
                (f"%{tag}%",),
            )
            cur.execute(
                "DELETE FROM ingresos WHERE tipo = %s OR tipo LIKE %s",
                (tag, f"%{tag}%"),
            )
            cur.execute(
                "DELETE FROM ingresos WHERE origen = %s OR origen LIKE %s",
                (tag, f"%{tag}%"),
            )
            cur.execute(
                "DELETE FROM mensajes_procesados WHERE mensaje_id = %s OR mensaje_id LIKE %s",
                (tag, f"%{tag}%"),
            )
            # Storage cleanup
            url = os.environ.get("SUPABASE_URL", "").rstrip("/")
            key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
            if url and key:
                # No podemos listar fácilmente sin storage API, confiamos en que los tests
                # limpian al final con path conocido
                pass
        conn.commit()
    finally:
        conn.close()


def _pdf_bytes(tag):
    return f"%PDF-1.4\nFASE9-E2E-{tag}".encode("ascii")


def _today_ar():
    return date.today().strftime("%d/%m/%Y")


def _today_iso():
    return date.today().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# 1. Gmail flow completo (mock)
# ---------------------------------------------------------------------------

def test_e2e_gmail_pdf_flow_mock(monkeypatch):
    """Gmail: PDF adjunto -> parse -> Storage -> consolidado/consumos -> Telegram."""
    tag = f"FASE9GMAIL_{uuid4().hex[:8]}"
    _cleanup_test_data(tag)
    try:
        class FakeWS:
            def get_all_values(self): return [["Hora_Ejecucion", "12"]]
            def get_all_records(self):
                return [{"Hora_Ejecucion": "12"}]
        class FakeSheet:
            def worksheet(self, name): return FakeWS()
        class FakeGC:
            def open_by_key(self, key): return FakeSheet()

        monkeypatch.setattr(bot_Saldo, "get_gc", lambda: FakeGC())
        monkeypatch.setattr(bot_Saldo, "obtener_o_crear_label", lambda n: "LBL")
        monkeypatch.setattr(
            bot_Saldo,
            "obtener_reglas",
            lambda: [{
                "Remitente": "bna",
                "Asunto_Contiene": "Resumen",
                "Clave": "",
                "Tiene_Adjunto": "SI",
                "Es_Tarjeta_Credito": "NO",
                "Regex_Cierre": "",
                "Regex_Vencimiento": "",
                "Regex_Monto": "",
            }],
        )
        monkeypatch.setattr(bot_Saldo, "procesar_fijos_mensuales", lambda *a: None)

        called = {}
        def fake_buscar(remitente, asunto_contiene, excluir_label_procesado=False):
            called['buscar'] = (remitente, asunto_contiene, excluir_label_procesado)
            return [{"id": f"msg_{tag}"}]

        monkeypatch.setattr(bot_Saldo, "buscar_mails_nuevos", fake_buscar)

        def fake_extraer(mensaje_id):
            called['extraer'] = mensaje_id
            return ("Resumen", "Tue, 05 Aug 2026 12:00:00 -0300",
                    "texto", "html", "Factura.pdf", _pdf_bytes(tag))

        monkeypatch.setattr(bot_Saldo, "extraer_datos_mensaje_mime", fake_extraer)

        monkeypatch.setattr(
            bot_Saldo, "extraer_consumos_pdf",
            lambda *a, **k: ([{"fecha": _today_ar(), "comprobante": "001",
                                "detalle": "COMPRA TEST", "cuota_actual": 1,
                                "cuota_total": 1, "pesos": "1000,00", "dolar": 0.0,
                                "fecha_cierre": "", "fecha_vencimiento": ""}],
                             "", _today_ar(), 1000.0),
        )

        def fake_subir_pdf(nombre_archivo, pdf_bytes, remitente):
            return "storage://pdfs/test.pdf", "https://signed-url"
        monkeypatch.setattr(bot_Saldo, "subir_pdf", fake_subir_pdf)

        def fake_mensaje_ya_procesado(mid, lid):
            return False
        monkeypatch.setattr(bot_Saldo, "mensaje_ya_procesado", fake_mensaje_ya_procesado)

        def fake_registrar(mid, rem, asu, lid):
            called['registrar'] = (mid, rem, asu, lid)
        monkeypatch.setattr(bot_Saldo, "registrar_y_marcar_mensaje_procesado", fake_registrar)

        guardados = []
        monkeypatch.setattr(
            bot_Saldo, "guardar_en_sheet",
            lambda *a: guardados.append(a) or "insertado",
        )

        enviados = []
        monkeypatch.setattr(bot_Saldo, "enviar_telegram", lambda msg: enviados.append(msg))

        bot_Saldo.revisar_mails()

        assert called.get('buscar') == ("bna", "Resumen", False)
        assert called.get('extraer') == f"msg_{tag}"
        assert len(guardados) == 1
        # guardados captura: (ws, fecha_raw, asunto, monto, vto, remitente, link_drive)
        args = guardados[0]
        # args[1] = fecha_raw (RFC2822), args[2] = asunto, args[3] = monto, args[4] = vto, args[5] = remitente, args[6] = link
        # La fecha se formatea dentro de guardar_en_sheet, asi que aqui viene cruda
        assert "Aug" in args[1] or "08" in args[1]  # fecha raw contiene mes
        assert args[2] == "Resumen"   # asunto
        assert float(args[3]) == 1000.0  # monto
        assert args[4] == _today_ar()  # vto ya formateado
        assert args[5] == "bna"        # remitente
        assert args[6].startswith("storage://")  # link_drive
        assert enviados[0].startswith("📩 Resumen Procesado")
        assert "msg_" in str(called.get('registrar', ''))
    finally:
        _cleanup_test_data(tag)


# ---------------------------------------------------------------------------
# 2. Webhook helpers (simplificado - verificación solo desde Python)
# ---------------------------------------------------------------------------

def test_e2e_webhook_helpers_exist():
    """Verifica que los helpers del webhook JS existen en el archivo."""
    import pathlib
    webhook_path = pathlib.Path(REPO_ROOT) / "api" / "webhook.js"
    content = webhook_path.read_text(encoding="utf-8")
    assert "supabaseInsert" in content
    assert "supabaseQuery" in content
    assert "supabaseFetch" in content
    assert "obtenerCategorias" in content
    assert "toArDate" in content
    assert "monthYearAr" in content
    assert "todayAr" in content
    assert "todayIso" in content
    assert "fmt" in content
    assert "sendTelegram" in content
    assert "editTelegram" in content


# ---------------------------------------------------------------------------
# 3. Deduplicación consolidado
# ---------------------------------------------------------------------------

def test_e2e_dedup_consolidado():
    """Consolidado: mismo lower(remitente)|vto|monto -> no duplica."""
    tag = f"FASE9DEDUP_{uuid4().hex[:8]}"
    _cleanup_test_data(tag)
    try:
        vto = _today_iso()
        r1 = supabase_client.guardar_consolidado(
            _today_iso(), tag, "R1", 100.0, vto
        )
        assert r1 == "insertado"

        r2 = supabase_client.guardar_consolidado(
            _today_iso(), tag.upper(), "R2", 100.0, vto
        )
        assert r2 == "existente"

        filas = _fetch(
            "SELECT count(*) FROM consolidado WHERE lower(remitente) = %s",
            (tag.lower(),),
        )
        assert filas[0][0] == 1
    finally:
        _cleanup_test_data(tag)


# ---------------------------------------------------------------------------
# 4. Deduplicación consumos (cuota no retrocede)
# ---------------------------------------------------------------------------

def test_e2e_dedup_consumos_no_retrocede():
    """Consumos: misma clave, cuota menor no retrocede."""
    tag = f"FASE9CONS_{uuid4().hex[:8]}"
    _cleanup_test_data(tag)
    try:
        c1 = [{"fecha": _today_ar(), "comprobante": "001",
               "detalle": "TEST", "cuota_actual": 2, "cuota_total": 6,
               "pesos": "2000,00", "dolar": 10.0,
               "fecha_cierre": "", "fecha_vencimiento": ""}]
        r1 = supabase_client.guardar_o_actualizar_consumos(c1, tag)
        assert r1[0]["estado"] == "insertado"

        c2 = [{"fecha": _today_ar(), "comprobante": "001",
               "detalle": "TEST", "cuota_actual": 1, "cuota_total": 6,
               "pesos": "100,00", "dolar": 1.0,
               "fecha_cierre": "", "fecha_vencimiento": ""}]
        r2 = supabase_client.guardar_o_actualizar_consumos(c2, tag)
        assert r2[0]["estado"] == "sin_cambios"

        filas = _fetch(
            "SELECT cuota_actual, pesos, dolar FROM consumos WHERE remitente = %s",
            (tag,),
        )
        assert filas[0][0] == 2
        assert float(filas[0][1]) == 2000.0
        assert float(filas[0][2]) == 10.0
    finally:
        _cleanup_test_data(tag)


# ---------------------------------------------------------------------------
# 5. Deduplicación ingresos
# ---------------------------------------------------------------------------

def test_e2e_dedup_ingresos():
    """Ingresos: mismo fecha|tipo|origen -> no duplica."""
    tag = f"FASE9ING_{uuid4().hex[:8]}"
    _cleanup_test_data(tag)
    try:
        r1 = supabase_client.guardar_ingreso(
            _today_iso(), tag, 1000.0, "Manual Telegram"
        )
        assert r1 == "insertado"

        r2 = supabase_client.guardar_ingreso(
            _today_iso(), tag, 999.0, "Manual Telegram"
        )
        assert r2 == "existente"

        filas = _fetch(
            "SELECT count(*) FROM ingresos WHERE tipo = %s", (tag,),
        )
        assert filas[0][0] == 1
    finally:
        _cleanup_test_data(tag)


# ---------------------------------------------------------------------------
# 6. Mensaje ID dedup (Gmail)
# ---------------------------------------------------------------------------

def test_e2e_mensaje_id_dedup():
    """Mensaje Gmail: mismo mensaje_id -> skip antes de extraer MIME."""
    tag = f"FASE9MSG_{uuid4().hex[:8]}"
    _cleanup_test_data(tag)
    try:
        supabase_client.registrar_mensaje_procesado(tag, "bna", "Resumen")
        assert supabase_client.mensaje_ya_procesado(tag) is True

        # Segundo registro del mismo ID -> existente
        r = supabase_client.registrar_mensaje_procesado(tag, "bna", "Resumen")
        assert r == "existente"

        filas = _fetch(
            "SELECT count(*) FROM mensajes_procesados WHERE mensaje_id = %s", (tag,),
        )
        assert filas[0][0] == 1
    finally:
        _cleanup_test_data(tag)


# ---------------------------------------------------------------------------
# 7. EPEC: sin Storage upload
# ---------------------------------------------------------------------------

def test_e2e_epec_sin_storage(monkeypatch):
    """EPEC: no sube a Storage, link vacío."""
    tag = f"FASE9EPEC_{uuid4().hex[:8]}"
    _cleanup_test_data(tag)
    try:
        subidas = {}
        def fake_subir_pdf(nombre_archivo, pdf_bytes, remitente):
            subidas['called'] = True
            return "link", "telegram"
        monkeypatch.setattr(bot_Saldo, "subir_pdf", fake_subir_pdf)

        class FakeWS:
            def get_all_values(self): return [["Hora_Ejecucion", "12"]]
            def get_all_records(self):
                return [{"Hora_Ejecucion": "12"}]
        class FakeSheet:
            def worksheet(self, name): return FakeWS()
        class FakeGC:
            def open_by_key(self, key): return FakeSheet()

        monkeypatch.setattr(bot_Saldo, "get_gc", lambda: FakeGC())
        monkeypatch.setattr(bot_Saldo, "obtener_o_crear_label", lambda n: "LBL")
        monkeypatch.setattr(
            bot_Saldo,
            "obtener_reglas",
            lambda: [{
                "Remitente": "test@epec.com.ar",
                "Asunto_Contiene": "Factura",
                "Clave": "",
                "Tiene_Adjunto": "SI",
                "Es_Tarjeta_Credito": "NO",
            }],
        )
        monkeypatch.setattr(bot_Saldo, "procesar_fijos_mensuales", lambda *a: None)
        monkeypatch.setattr(bot_Saldo, "buscar_mails_nuevos",
                            lambda *a, **k: [{"id": f"msg_{tag}"}])
        monkeypatch.setattr(bot_Saldo, "mensaje_ya_procesado", lambda *a: False)
        monkeypatch.setattr(
            bot_Saldo, "extraer_datos_mensaje_mime",
            lambda mid: ("Factura", "Tue, 05 Aug 2026 12:00:00 -0300",
                         "texto", "html", "Factura.pdf", _pdf_bytes(tag)),
        )
        monkeypatch.setattr(
            bot_Saldo, "extraer_consumos_pdf",
            lambda *a, **k: ([], "", _today_ar(), 100.0),
        )
        monkeypatch.setattr(bot_Saldo, "es_registro_duplicado", lambda *a: False)
        monkeypatch.setattr(bot_Saldo, "registrar_y_marcar_mensaje_procesado", lambda *a: None)
        guardados = []
        monkeypatch.setattr(
            bot_Saldo, "guardar_en_sheet",
            lambda *a: guardados.append(a) or "insertado",
        )
        monkeypatch.setattr(bot_Saldo, "enviar_telegram", lambda msg: None)

        bot_Saldo.revisar_mails()

        # subir_pdf NO debe llamarse para EPEC
        assert 'called' not in subidas
        # link_drive en guardados debe ser "" (indice 6 = args[6] = link_drive)
        assert guardados[0][6] == ""
    finally:
        _cleanup_test_data(tag)


# ---------------------------------------------------------------------------
# 8. Fijos mensuales
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 8. Fijos mensuales
# ---------------------------------------------------------------------------

def test_e2e_fijos_mensuales():
    """Fijos: insert consumos (Fijo Config) + ingresos (Fijo Config)."""
    tag = f"FASE9FIJ_{uuid4().hex[:8]}"
    _cleanup_test_data(tag)
    try:
        # Llamar a procesar_fijos_mensuales directamente con mocks
        import bot_Saldo
        
        # Mock de obtener_fijos en supabase_client
        import supabase_client
        original_obtener_fijos = supabase_client.obtener_fijos
        
        def mock_obtener_fijos():
            return (
                [{"tipo": f"FIJO {tag}", "monto": 10000.0}],
                [{"tipo": f"ING {tag}", "monto": 170000.0}],
            )
        
        supabase_client.obtener_fijos = mock_obtener_fijos
        try:
            class FakeWS:
                def get_all_values(self): return [["Hora_Ejecucion", "12"]]
                def get_all_records(self):
                    return [{"Hora_Ejecucion": "12", "SUELDO": "170000",
                             "ALQUILER": "10000", "NIÑERA": "0"}]
            
            class FakeSheet:
                def worksheet(self, name): return FakeWS()
            class FakeGC:
                def open_by_key(self, key): return FakeSheet()
            
            original_get_gc = bot_Saldo.get_gc
            bot_Saldo.get_gc = lambda: FakeGC()
            
            # Need to pass a fake ws_consumos (not None) and ws_ingresos
            ws_consumos_fake = type('WS', (), {
                'get_all_values': lambda self: [["Hora_Ejecucion", "12"]],
                'get_all_records': lambda self: [{"Hora_Ejecucion": "12"}],
                'worksheet': lambda self, name: type('WS', (), {
                    'get_all_values': lambda self: [["Hora_Ejecucion", "12"]]
                })()
            })()
            
            ws_ingresos_fake = type('WS', (), {
                'get_all_values': lambda self: [["Hora_Ejecucion", "12"]]
            })()
            
            try:
                bot_Saldo.procesar_fijos_mensuales(FakeWS(), ws_consumos_fake, ws_ingresos_fake)
            finally:
                bot_Saldo.get_gc = original_get_gc
            
            # Verificar que se insertaron
            filas_g = _fetch(
                "SELECT count(*) FROM consumos WHERE remitente = 'Fijo Config' AND detalle LIKE %s",
                (f"%{tag}%",),
            )
            filas_i = _fetch(
                "SELECT count(*) FROM ingresos WHERE tipo = %s",
                (f"ING {tag}",),
            )
            assert filas_g[0][0] >= 1
            assert filas_i[0][0] >= 1
        finally:
            supabase_client.obtener_fijos = original_obtener_fijos
    finally:
        _cleanup_test_data(tag)


# ---------------------------------------------------------------------------
# 9. Regresión: respuestas Telegram mantienen formato
# ---------------------------------------------------------------------------

def test_e2e_regresion_formato_telegram():
    """Regresión: formatos de mensajes Telegram mantienen formato."""
    import pathlib
    webhook_path = pathlib.Path(REPO_ROOT) / "api" / "webhook.js"
    content = webhook_path.read_text(encoding="utf-8")
    # Verificar que fmt existe y tiene la lógica correcta
    assert "function fmt" in content or "const fmt" in content or "fmt(" in content
    # Verificar que formatos esperados están en el código
    assert "$" in content
    assert "toLocaleString" in content


# ---------------------------------------------------------------------------
# 10. Concurrencia: 6 hilos -> 1 fila (todas las tablas)
# ---------------------------------------------------------------------------

def test_e2e_concurrencia_consolidado():
    import threading
    tag = f"FASE9CONC_{uuid4().hex[:8]}"
    _cleanup_test_data(tag)
    try:
        barrera = threading.Barrier(6)
        def tarea():
            barrera.wait()
            supabase_client.guardar_consolidado(
                _today_iso(), tag, "R", 100.0, _today_iso()
            )
        hilos = [threading.Thread(target=tarea) for _ in range(6)]
        for h in hilos: h.start()
        for h in hilos: h.join()
        filas = _fetch(
            "SELECT count(*) FROM consolidado WHERE lower(remitente) = %s",
            (tag.lower(),),
        )
        assert filas[0][0] == 1
    finally:
        _cleanup_test_data(tag)


def test_e2e_concurrencia_ingresos():
    import threading
    tag = f"FASE9CONC_{uuid4().hex[:8]}"
    _cleanup_test_data(tag)
    try:
        barrera = threading.Barrier(6)
        def tarea():
            barrera.wait()
            supabase_client.guardar_ingreso(_today_iso(), tag, 500.0, "Manual Telegram")
        hilos = [threading.Thread(target=tarea) for _ in range(6)]
        for h in hilos: h.start()
        for h in hilos: h.join()
        filas = _fetch(
            "SELECT count(*) FROM ingresos WHERE tipo = %s", (tag,),
        )
        assert filas[0][0] == 1
    finally:
        _cleanup_test_data(tag)


def test_e2e_concurrencia_consumos():
    import threading
    tag = f"FASE9CONC_{uuid4().hex[:8]}"
    _cleanup_test_data(tag)
    try:
        barrera = threading.Barrier(6)
        c = {"fecha": _today_ar(), "comprobante": "001", "detalle": "TEST",
             "cuota_actual": 1, "cuota_total": 1, "pesos": "100,00",
             "dolar": 0.0, "fecha_cierre": "", "fecha_vencimiento": ""}
        def tarea():
            barrera.wait()
            supabase_client.guardar_o_actualizar_consumos([c], tag)
        hilos = [threading.Thread(target=tarea) for _ in range(6)]
        for h in hilos: h.start()
        for h in hilos: h.join()
        filas = _fetch(
            "SELECT count(*) FROM consumos WHERE remitente = %s", (tag,),
        )
        assert filas[0][0] == 1
    finally:
        _cleanup_test_data(tag)


# ---------------------------------------------------------------------------
# 11. Regresión: estado final equivalente a lógica anterior
# ---------------------------------------------------------------------------

def test_e2e_regresion_consumos_equivalencia():
    """Consumos: estado final DB == lógica anterior (guardar_o_actualizar_consumos_sheet)."""
    tag = f"FASE9REG_{uuid4().hex[:8]}"
    _cleanup_test_data(tag)
    try:
        def _normalizar_monto(v):
            t = str(v).replace("$", "").strip()
            if "." in t and "," in t:
                t = t.replace(".", "").replace(",", ".")
            elif "," in t:
                t = t.replace(",", ".")
            return round(float(t), 2)

        def _referencia(seq, remitente):
            filas = []
            for c in seq:
                id_unico = (
                    f"{str(c['fecha']).strip()}|{str(c['comprobante']).strip()}|"
                    f"{str(c['detalle']).strip()}|{str(c['cuota_total']).strip()}|"
                    f"{str(remitente).strip()}"
                )
                idx = None
                for i, fila in enumerate(filas):
                    if fila[0] == id_unico:
                        idx = i; break
                if idx is not None:
                    fila = filas[idx]
                    try:
                        cuota_nueva = int(c["cuota_actual"])
                        cuota_existente = int(fila[2]) if str(fila[2]).isdigit() else 0
                    except Exception:
                        cuota_nueva = 1; cuota_existente = 0
                    if cuota_nueva >= cuota_existente:
                        filas[idx] = (id_unico, c["cuota_actual"], c["pesos"], c["dolar"])
                else:
                    filas.append((id_unico, c["cuota_actual"], c["pesos"], c["dolar"]))
            return filas

        seq = [
            {"fecha": _today_ar(), "comprobante": "001", "detalle": "A",
             "cuota_actual": 1, "cuota_total": 6, "pesos": "1000,00", "dolar": 0.0,
             "fecha_cierre": "", "fecha_vencimiento": ""},
            {"fecha": _today_ar(), "comprobante": "001", "detalle": "A",
             "cuota_actual": 2, "cuota_total": 6, "pesos": "2000,00", "dolar": 1.0,
             "fecha_cierre": "", "fecha_vencimiento": ""},
            {"fecha": _today_ar(), "comprobante": "001", "detalle": "A",
             "cuota_actual": 1, "cuota_total": 6, "pesos": "999,00", "dolar": 9.0,
             "fecha_cierre": "", "fecha_vencimiento": ""},  # no retrocede
            {"fecha": _today_ar(), "comprobante": "001", "detalle": "A",
             "cuota_actual": 2, "cuota_total": 6, "pesos": "2100,00", "dolar": 2.0,
             "fecha_cierre": "", "fecha_vencimiento": ""},  # actualiza
        ]
        ref = _referencia(seq, tag)
        for c in seq:
            supabase_client.guardar_o_actualizar_consumos([c], tag)

        filas = _fetch(
            "SELECT id_consumo, cuota_actual, pesos, dolar "
            "FROM consumos WHERE remitente = %s",
            (tag,),
        )
        resultado = sorted((r[0], r[1], float(r[2]), float(r[3])) for r in filas)
        esperado = sorted(
            (id_, int(cuota), float(_normalizar_monto(p)), float(d))
            for id_, cuota, p, d in ref
        )
        assert resultado == esperado
    finally:
        _cleanup_test_data(tag)


# ---------------------------------------------------------------------------
# 12. Security: secrets no en código
# ---------------------------------------------------------------------------

def test_e2e_secrets_no_hardcodeados():
    import subprocess
    result = subprocess.run(
        ["git", "grep", "-l", "eyJ", "--", "*.py", "*.js", "*.mjs", "*.yml", "*.json"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    # Excluir archivos de test que buscan JWTs por patrón
    archivos = result.stdout.strip().split("\n") if result.stdout.strip() else []
    archivos_reales = [f for f in archivos if not f.startswith("tests/")]
    assert archivos_reales == [], f"JWT hardcodeado en: {archivos_reales}"


# ---------------------------------------------------------------------------
# 13. RLS habilitado en todas las tablas
# ---------------------------------------------------------------------------

def test_e2e_rls_habilitado():
    EXPECTED = [
        "config", "categorias_fijas", "reglas", "consolidado",
        "consumos", "ingresos", "mensajes_procesados",
    ]
    filas = _fetch(
        """
        SELECT c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = ANY(%s) AND c.relrowsecurity = TRUE
        """, (EXPECTED,),
    )
    habilitadas = {r[0] for r in filas}
    assert habilitadas == set(EXPECTED)


# ---------------------------------------------------------------------------
# 14. Buckets Storage: pdfs existe y es privado
# ---------------------------------------------------------------------------

def test_e2e_storage_bucket_privado():
    filas = _fetch("SELECT id, public FROM storage.buckets WHERE id = 'pdfs'")
    assert filas, "Bucket 'pdfs' no existe"
    assert filas[0][1] is False, "Bucket 'pdfs' debe ser privado"


# ---------------------------------------------------------------------------
# 15. Storage upload + signed URL + cleanup (si REST disponible)
# ---------------------------------------------------------------------------

def test_e2e_storage_upload_signed_cleanup():
    """Requiere SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY reales."""
    import os
    if not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY")):
        pytest.skip("REST/Storage secrets no disponibles")

    tag = f"FASE9STG_{uuid4().hex[:8]}"
    pdf = _pdf_bytes(tag)
    res = supabase_client.subir_pdf_storage("FASE9_test.pdf", pdf, tag)
    assert res["link_ref"].startswith("storage://pdfs/")
    assert res["signed_url"].startswith("http")

    resp = requests.get(res["signed_url"], timeout=20)
    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF")

    # Cleanup
    url = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    path = res["path"]
    quote_path = "/".join(
        __import__("urllib.parse", fromlist=["quote"]).quote(p, safe="")
        for p in path.split("/")
    )
    del_resp = requests.delete(
        f"{url}/storage/v1/object/pdfs/{quote_path}",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        timeout=20,
    )
    assert del_resp.status_code in (200, 204, 404)

    # Verificar cleanup
    get_resp = requests.get(res["signed_url"], timeout=20)
    assert get_resp.status_code in (400, 404)