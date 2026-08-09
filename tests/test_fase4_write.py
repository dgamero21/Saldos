"""FASE 4 — Escritura: capa Supabase y bot (sin base de datos real).

Verifica con mocks:
  - UPSERT idempotente de consolidado/consumos/ingresos (SQL + estados).
  - Cuota que nunca retrocede en consumos.
  - ID legacy reproducible (mismo formato que el bot).
  - Errores explícitos '[SUPABASE WRITE ERROR]' / SupabaseNotConfiguredError.
  - Sheets NO recibe escrituras accidentales (FASE 10C: las firmas ya no
    reciben ws y el bot no importa gspread).
  - es_registro_duplicado usa Supabase (lectura) y lanza SupabaseReadError
    si Supabase no está disponible o falla (sin fallback a la hoja).
"""
import sys

import pytest

from conftest import REPO_ROOT

sys.path.insert(0, REPO_ROOT)
import bot_Saldo  # noqa: E402
import supabase_client  # noqa: E402


class FakeCursor:
    def __init__(self, rowcounts):
        self._rowcounts = list(rowcounts)
        self.statements = []
        self.params = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.statements.append(sql)
        self.params.append(params)
        self.rowcount = self._rowcounts.pop(0) if self._rowcounts else 0

    def fetchall(self):
        return []

    def fetchone(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    def __init__(self, rowcounts):
        self.cursor_obj = FakeCursor(rowcounts)
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


@pytest.fixture
def fake_conn(monkeypatch):
    def _make(rowcounts):
        conn = FakeConn(rowcounts)
        monkeypatch.setattr(supabase_client, "_get_conn", lambda *a, **k: conn)
        return conn

    return _make


# ---------------------------------------------------------------------------
# guardar_consolidado
# ---------------------------------------------------------------------------

def test_consolidado_inserta(fake_conn):
    conn = fake_conn([1])
    estado = supabase_client.guardar_consolidado(
        "Wed, 05 Aug 2026 10:00:00 -0000",
        "NAVI@mailing.bna.com.ar", "Resumen", "27110,4", "21/08/2026",
    )
    assert estado == "insertado"
    assert conn.commits == 1
    sql = conn.cursor_obj.statements[0]
    assert "INSERT INTO consolidado" in sql
    assert "ON CONFLICT (lower(remitente), fecha_vencimiento, monto_total)" in sql
    assert "DO NOTHING" in sql
    p = conn.cursor_obj.params[0]
    assert p[0] == "2026-08-05"          # fecha_mail RFC2822 -> DATE
    assert p[4] == "2026-08-21"          # vto 'dd/mm/yyyy' -> DATE
    assert p[3] == 27110.4               # '27110,4' normalizado
    assert p[6] == "navi@mailing.bna.com.ar|21/08/2026|27110.4"  # ID legacy


def test_consolidado_duplicado_no_inserta(fake_conn):
    conn = fake_conn([0])
    estado = supabase_client.guardar_consolidado(
        "05/08/2026", "BNA", "Resumen", 100.0, "21/08/2026",
    )
    assert estado == "existente"
    assert conn.commits == 1


def test_consolidado_duplicado_ignora_mayusculas(fake_conn):
    # mismo remitente en mayúsculas -> ON CONFLICT (lower(remitente)) -> no-op
    conn = fake_conn([0])
    estado = supabase_client.guardar_consolidado(
        "05/08/2026", "bna", "Resumen", 100.0, "21/08/2026",
    )
    assert estado == "existente"
    assert conn.cursor_obj.params[0][6] == "bna|21/08/2026|100.0"


def test_consolidado_fecha_invalida_lanza_write_error():
    with pytest.raises(supabase_client.SupabaseWriteError) as exc:
        supabase_client.guardar_consolidado(
            "05/08/2026", "BNA", "Resumen", 100.0, "fecha-invalida",
        )
    assert "[SUPABASE WRITE ERROR]" in str(exc.value)


def test_consolidado_sin_credenciales_lanza_not_configured(monkeypatch):
    monkeypatch.setenv("SUPABASE_DB_URL", "")
    monkeypatch.setenv("SUPABASE_DBPW", "")
    with pytest.raises(supabase_client.SupabaseNotConfiguredError):
        supabase_client.guardar_consolidado(
            "05/08/2026", "BNA", "Resumen", 100.0, "21/08/2026",
        )


def test_consolidado_reintenta_unique_violation(monkeypatch):
    """Carrera concurrente: el primer intento choca con UniqueViolation y el
    segundo (ya resuelto por el otro proceso) queda como no-op."""
    import psycopg2

    fallos = [0]

    class ConnRacing(FakeConn):
        def cursor(self):
            return self.cursor_obj

    class RaceCursor(FakeCursor):
        def execute(self, sql, params=None):
            self.statements.append(sql)
            self.params.append(params)
            if fallos[0] == 0:
                fallos[0] = 1
                self.rowcount = 0
                raise psycopg2.errors.UniqueViolation("duplicate key")
            self.rowcount = 0  # el conflicto ya lo resolvió el otro proceso

    conn = ConnRacing([0])
    conn.cursor_obj = RaceCursor([0])
    monkeypatch.setattr(supabase_client, "_get_conn", lambda *a, **k: conn)
    estado = supabase_client.guardar_consolidado(
        "05/08/2026", "BNA", "Resumen", 100.0, "21/08/2026",
    )
    assert estado == "existente"
    assert conn.rollbacks == 1
    assert conn.commits == 1


# ---------------------------------------------------------------------------
# guardar_o_actualizar_consumos
# ---------------------------------------------------------------------------

def _consumo(**over):
    base = {
        "fecha": "01/08/2026", "comprobante": "008452", "detalle": "CUOTA 2/6",
        "cuota_actual": 2, "cuota_total": 6, "pesos": "1234,56", "dolar": 10.5,
        "fecha_cierre": "15/07/2026", "fecha_vencimiento": "10/08/2026",
    }
    base.update(over)
    return base


def test_consumo_inserta(fake_conn):
    conn = fake_conn([1])
    resultado = supabase_client.guardar_o_actualizar_consumos(
        [_consumo()], "NAVI"
    )
    assert resultado[0]["estado"] == "insertado"
    assert resultado[0]["pesos"] == 1234.56
    sql = conn.cursor_obj.statements[0]
    assert "INSERT INTO consumos" in sql
    assert "ON CONFLICT" in sql
    assert "fecha_consumo, comprobante, detalle, cuota_total, remitente" in sql
    p = conn.cursor_obj.params[0]
    assert p[1] == "008452"  # comprobante RAW (no normalizado)
    assert p[10] == "01/08/2026|008452|CUOTA 2/6|6|NAVI"  # ID legacy
    assert p[7] == "2026-07-15" and p[8] == "2026-08-10"


def test_consumo_existente_avanza_cuota(fake_conn):
    conn = fake_conn([0, 1])  # INSERT no-op, UPDATE ok
    resultado = supabase_client.guardar_o_actualizar_consumos(
        [_consumo()], "NAVI"
    )
    assert resultado[0]["estado"] == "actualizado"
    upd = conn.cursor_obj.statements[1]
    assert "UPDATE consumos SET" in upd
    assert "cuota_actual <= %(ca)s" in upd  # no retrocede
    u = conn.cursor_obj.params[1]
    assert u["ca"] == 2 and u["pe"] == 1234.56


def test_consumo_cuota_menor_no_retrocede(fake_conn):
    conn = fake_conn([0, 0])  # INSERT no-op, UPDATE no matchea (cuota menor)
    resultado = supabase_client.guardar_o_actualizar_consumos(
        [_consumo(cuota_actual=1, pesos="111")], "NAVI"
    )
    assert resultado[0]["estado"] == "sin_cambios"


def test_consumo_lote_varios_insertados(fake_conn):
    conn = fake_conn([1, 1])
    resultado = supabase_client.guardar_o_actualizar_consumos(
        [_consumo(detalle="A"), _consumo(detalle="B")], "NAVI"
    )
    assert [r["estado"] for r in resultado] == ["insertado", "insertado"]
    assert conn.commits == 1  # transacción única para el lote


def test_consumo_vacio_no_escribe(fake_conn):
    conn = fake_conn([])
    assert supabase_client.guardar_o_actualizar_consumos([], "NAVI") == []
    assert conn.cursor_obj.statements == []
    assert conn.commits == 0


def test_consumo_datos_invalidos_lanza_write_error(fake_conn):
    with pytest.raises(supabase_client.SupabaseWriteError) as exc:
        supabase_client.guardar_o_actualizar_consumos(
            [_consumo(fecha="no-es-fecha")], "NAVI"
        )
    assert "[SUPABASE WRITE ERROR]" in str(exc.value)


# ---------------------------------------------------------------------------
# guardar_ingreso
# ---------------------------------------------------------------------------

def test_ingreso_inserta(fake_conn):
    conn = fake_conn([1])
    estado = supabase_client.guardar_ingreso(
        "28/07/2026", "SUELDO", "1700000", "Manual Telegram"
    )
    assert estado == "insertado"
    sql = conn.cursor_obj.statements[0]
    assert "INSERT INTO ingresos" in sql
    assert "ON CONFLICT (fecha, tipo, origen) DO NOTHING" in sql
    p = conn.cursor_obj.params[0]
    assert p[0] == "2026-07-28"
    assert p[1] == "SUELDO"
    assert p[2] == 1700000.0
    assert p[4] == "28/07/2026|Ingreso|SUELDO|Manual Telegram"  # ID reproducible


def test_ingreso_duplicado_no_inserta(fake_conn):
    conn = fake_conn([0])
    assert supabase_client.guardar_ingreso(
        "28/07/2026", "SUELDO", 9999, "Manual Telegram"
    ) == "existente"


def test_ingreso_monto_invalido_lanza_write_error():
    with pytest.raises(supabase_client.SupabaseWriteError) as exc:
        supabase_client.guardar_ingreso("28/07/2026", "SUELDO", "abc")
    assert "[SUPABASE WRITE ERROR]" in str(exc.value)


# ---------------------------------------------------------------------------
# bot_Saldo: las escrituras NO tocan Sheets (FASE 4 / FASE 10C)
# ---------------------------------------------------------------------------

def test_guardar_consolidado_escribe_solo_supabase(bot_module, monkeypatch):
    monkeypatch.setattr(
        supabase_client, "guardar_consolidado",
        lambda *a, **k: "insertado",
    )
    assert bot_module.guardar_consolidado(
        "Wed, 05 Aug 2026 10:00:00 -0000", "Resumen", 100, "21/08/2026",
        "BNA", "http://link",
    ) == "insertado"


def test_guardar_consolidado_error_propaga(bot_module, monkeypatch):
    def boom(*a, **k):
        raise supabase_client.SupabaseWriteError(
            "[SUPABASE WRITE ERROR] connection timeout"
        )

    monkeypatch.setattr(supabase_client, "guardar_consolidado", boom)
    with pytest.raises(supabase_client.SupabaseWriteError):
        bot_module.guardar_consolidado(
            "05/08/2026", "Resumen", 100, "21/08/2026", "BNA"
        )


def test_guardar_consumos_escribe_solo_supabase(bot_module, monkeypatch):
    consumos = [_consumo()]
    monkeypatch.setattr(
        supabase_client, "guardar_o_actualizar_consumos",
        lambda c, r: [{"estado": "insertado"}],
    )
    resultado = bot_module.guardar_consumos(consumos, "NAVI")
    assert resultado == [{"estado": "insertado"}]


def test_telegram_gasto_manual_no_escribe_en_sheets(bot_module, monkeypatch):
    """MANUAL|... registra el gasto en Supabase y NO accede a ninguna hoja."""
    llamado = []

    monkeypatch.setattr(
        bot_module, "leer_config_completo",
        lambda: (0, "", ["ALQUILER"], ["SUELDO"]),
    )
    monkeypatch.setattr(supabase_client, "supabase_disponible", lambda: True)

    def fake_guardar(consumos, remitente):
        llamado.append((consumos, remitente))
        return [{"estado": "insertado"}]

    monkeypatch.setattr(
        supabase_client, "guardar_o_actualizar_consumos", fake_guardar
    )
    editados = []
    monkeypatch.setattr(
        bot_module.requests, "post",
        lambda url, json=None: editados.append((url, json)) or object(),
    )

    payload = '{"callback_query": {"data": "MANUAL|100|ALQUILER", "message": {"chat": {"id": "test-chat-id"}, "message_id": 1}}}'
    monkeypatch.setenv("TELEGRAM_UPDATE_PAYLOAD", payload)
    bot_module.procesar_mensajes_telegram()

    assert len(llamado) == 1
    consumos, remitente = llamado[0]
    assert remitente == "Manual Telegram"
    assert consumos[0]["comprobante"] == "Telegram"
    assert consumos[0]["detalle"] == "ALQUILER"
    assert consumos[0]["pesos"] == 100.0
    assert len(editados) == 1  # confirmación editMessageText


def test_telegram_ingreso_manual_usa_supabase(bot_module, monkeypatch):
    llamado = []

    monkeypatch.setattr(
        bot_module, "leer_config_completo",
        lambda: (0, "", ["ALQUILER"], ["SUELDO"]),
    )
    monkeypatch.setattr(supabase_client, "supabase_disponible", lambda: True)
    monkeypatch.setattr(
        supabase_client, "guardar_ingreso",
        lambda fecha, tipo, monto, origen="Manual Telegram": (
            llamado.append((fecha, tipo, monto, origen)) or "insertado"
        ),
    )
    monkeypatch.setattr(
        bot_module.requests, "post",
        lambda url, json=None: object(),
    )

    payload = '{"callback_query": {"data": "INGRESO|500|SUELDO", "message": {"chat": {"id": "test-chat-id"}, "message_id": 1}}}'
    monkeypatch.setenv("TELEGRAM_UPDATE_PAYLOAD", payload)
    bot_module.procesar_mensajes_telegram()

    assert len(llamado) == 1
    fecha, tipo, monto, origen = llamado[0]
    assert tipo == "SUELDO"
    assert monto == 500.0
    assert origen == "Manual Telegram"


# ---------------------------------------------------------------------------
# es_registro_duplicado (lectura): Supabase; fallo -> SupabaseReadError
# ---------------------------------------------------------------------------

def test_es_registro_duplicado_usa_supabase(bot_module, monkeypatch):
    monkeypatch.setattr(supabase_client, "supabase_disponible", lambda: True)
    monkeypatch.setattr(supabase_client, "existe_consolidado", lambda *a: True)
    assert bot_module.es_registro_duplicado("BNA", 100, "21/08/2026") is True
    monkeypatch.setattr(supabase_client, "existe_consolidado", lambda *a: False)
    assert bot_module.es_registro_duplicado("BNA", 100, "21/08/2026") is False


def test_es_registro_duplicado_supabase_no_disponible_raise(
    bot_module, monkeypatch, capsys
):
    monkeypatch.setattr(supabase_client, "supabase_disponible", lambda: False)
    with pytest.raises(supabase_client.SupabaseReadError):
        bot_module.es_registro_duplicado("BNA", 100, "21/08/2026")
    assert "[SUPABASE READ ERROR]" in capsys.readouterr().out


def test_es_registro_duplicado_error_supabase_propaga(
    bot_module, monkeypatch, capsys
):
    monkeypatch.setattr(supabase_client, "supabase_disponible", lambda: True)

    def boom(*a, **k):
        raise supabase_client.SupabaseReadError("timeout")

    monkeypatch.setattr(supabase_client, "existe_consolidado", boom)
    with pytest.raises(supabase_client.SupabaseReadError):
        bot_module.es_registro_duplicado("BNA", 100, "21/08/2026")
    assert "[SUPABASE READ ERROR]" in capsys.readouterr().out
