"""FASE 3 — Tests unitarios de la capa supabase_client (sin DB real).

Verifica:
  - Guarda de solo lectura (_read_only): bloquea todo DDL/DML.
  - Conexión desde entorno (nunca hardcodeada): SUPABASE_DB_URL / SUPABASE_DBPW,
    error explícito si faltan ambas.
  - Formato de salida 1:1 con el bot (reglas, config, tipos, fijos).
"""
import inspect
import sys

import pytest

from conftest import REPO_ROOT

sys.path.insert(0, REPO_ROOT)
import supabase_client


# ---------------------------------------------------------------------------
# Solo lectura
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO reglas VALUES (1)",
        "UPDATE reglas SET activo = TRUE",
        "DELETE FROM reglas",
        "DROP TABLE reglas",
        "ALTER TABLE reglas ADD COLUMN x TEXT",
        "TRUNCATE TABLE reglas",
        "GRANT SELECT ON reglas TO anon",
        "REVOKE ALL ON reglas FROM anon",
        "CREATE TABLE foo (id int)",
        "MERGE INTO reglas USING x ON reglas.id = x.id",
    ],
)
def test_read_only_bloquea_escrituras(sql):
    with pytest.raises(supabase_client.SupabaseReadError):
        supabase_client._read_only(sql)


def test_read_only_permite_lecturas():
    assert (
        supabase_client._read_only("SELECT * FROM reglas")
        == "SELECT * FROM reglas"
    )
    assert (
        supabase_client._read_only("SELECT valor FROM config WHERE clave = %s")
        == "SELECT valor FROM config WHERE clave = %s"
    )


# ---------------------------------------------------------------------------
# Conexión desde entorno (nunca hardcodeada)
# ---------------------------------------------------------------------------

def test_build_dsn_sin_credenciales_lanza(monkeypatch):
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    monkeypatch.delenv("SUPABASE_DBPW", raising=False)
    with pytest.raises(supabase_client.SupabaseNotConfiguredError):
        supabase_client._build_dsn()


def test_build_dsn_prefiere_db_url(monkeypatch):
    url = "postgresql://user:pw@host:6543/db?sslmode=require"
    monkeypatch.setenv("SUPABASE_DB_URL", url)
    monkeypatch.setenv("SUPABASE_DBPW", "otra-pw")
    assert supabase_client._build_dsn() == url


def test_build_dsn_con_solo_dbpw(monkeypatch):
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    monkeypatch.setenv("SUPABASE_DBPW", "secreta")
    dsn = supabase_client._build_dsn()
    assert dsn.startswith("postgresql://")
    assert "secreta" in dsn
    assert "sslmode=require" in dsn
    # La contraseña NO debe estar hardcodeada en el código fuente.
    source = open(supabase_client.__file__, encoding="utf-8").read()
    assert "secreta" not in source


def test_supabase_disponible(monkeypatch):
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    monkeypatch.delenv("SUPABASE_DBPW", raising=False)
    assert supabase_client.supabase_disponible() is False
    monkeypatch.setenv("SUPABASE_DBPW", "x")
    assert supabase_client.supabase_disponible() is True


# ---------------------------------------------------------------------------
# Reglas — formato del bot
# ---------------------------------------------------------------------------

def test_obtener_reglas_formato_bot(monkeypatch):
    filas = [
        # SELECT: remitente, asunto, clave, tiene_adjunto, es_tarjeta,
        #         regex_consumo, regex_cierre, regex_vencimiento, regex_monto,
        #         pertenece, entidad  (11 columnas)
        ("no-reply@banco.com", "Resumen", "1234", True, True,
         "regex_c", "kw_cierre", "kw_vto", "kw_monto", "David", "BANCO"),
        ("info@x.com", None, None, False, False,
         None, None, None, None, "David", None),
    ]
    monkeypatch.setattr(
        supabase_client, "_fetch_all", lambda sql, params=None: filas
    )
    reglas = supabase_client.obtener_reglas()
    assert reglas[0] == {
        "Remitente": "no-reply@banco.com",
        "Asunto_Contiene": "Resumen",
        "Clave": "1234",
        "Activo": "SI",
        "Tiene_Adjunto": "SI",
        "Es_Tarjeta_Credito": "SI",
        "Regex_Consumo": "regex_c",
        "Regex_Cierre": "kw_cierre",
        "Regex_Vencimiento": "kw_vto",
        "Regex_Monto": "kw_monto",
        "Pertenece": "David",
        "Entidad": "BANCO",
    }
    # Filas inactivas (o sin flags) -> 'NO' / '' (mismo formato que Sheets).
    assert reglas[1]["Tiene_Adjunto"] == "NO"
    assert reglas[1]["Es_Tarjeta_Credito"] == "NO"
    assert reglas[1]["Regex_Consumo"] == ""


def test_obtener_reglas_sql_filtra_activas():
    # El filtro de activas lo hace la SQL (server-side), igual que el bot
    # (Activo == 'SI'). Nada de traer inactivas a Python.
    src = inspect.getsource(supabase_client.obtener_reglas)
    assert "WHERE activo = TRUE" in src


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def test_obtener_config(monkeypatch):
    monkeypatch.setattr(
        supabase_client, "_fetch_all",
        lambda sql, params=None: [
            ("Hora_Ejecucion", "12"),
            ("Last_Telegram_Update_ID", "445690403"),
        ],
    )
    cfg = supabase_client.obtener_config()
    assert cfg == {"Hora_Ejecucion": "12", "Last_Telegram_Update_ID": "445690403"}


def test_obtener_config_valor_existente(monkeypatch):
    monkeypatch.setattr(
        supabase_client, "_fetch_one", lambda sql, params=None: ("12",)
    )
    assert supabase_client.obtener_config_valor("Hora_Ejecucion") == "12"


def test_obtener_config_valor_inexistente(monkeypatch):
    monkeypatch.setattr(
        supabase_client, "_fetch_one", lambda sql, params=None: None
    )
    assert supabase_client.obtener_config_valor("NoExiste") == ""


def test_obtener_config_telegram(monkeypatch):
    monkeypatch.setattr(
        supabase_client, "_fetch_all",
        lambda sql, params=None: [
            ("Last_Telegram_Update_ID", "445690403"),
            ("Telegram_State", ""),
        ],
    )
    last_id, state = supabase_client.obtener_config_telegram()
    assert last_id == 445690403
    assert state == ""


# ---------------------------------------------------------------------------
# Tipos / fijos (categorias_fijas)
# ---------------------------------------------------------------------------

def test_obtener_tipos(monkeypatch):
    filas = [
        (False, "ALQUILER", None, "David"),
        (False, "PASAJE", None, "David"),
        (True, "SUELDO", None, "David"),
        (True, "DEV PREST", None, "David"),
    ]
    monkeypatch.setattr(
        supabase_client, "_fetch_all", lambda sql, params=None: filas
    )
    gastos, ingresos = supabase_client.obtener_tipos()
    assert gastos == ["ALQUILER", "PASAJE"]
    assert ingresos == ["SUELDO", "DEV PREST"]


def test_obtener_fijos_solo_monto_positivo(monkeypatch):
    filas = [
        (False, "ALQUILER", "10000", "David"),
        (False, "PASAJE", "0", "David"),
        (False, "NIÑERA", None, "David"),
        (True, "SUELDO", "1700000", "David"),
        (True, "DEV PREST", "-5", "David"),
    ]
    monkeypatch.setattr(
        supabase_client, "_fetch_all", lambda sql, params=None: filas
    )
    gastos, ingresos = supabase_client.obtener_fijos()
    assert gastos == [{"tipo": "ALQUILER", "monto": 10000.0}]
    assert ingresos == [{"tipo": "SUELDO", "monto": 1700000.0}]


def test_obtener_config_completo(monkeypatch):
    config = [("Last_Telegram_Update_ID", "5"), ("Telegram_State", "x")]
    categorias = [
        (False, "ALQUILER", None, "David"),
        (True, "SUELDO", None, "David"),
    ]
    monkeypatch.setattr(
        supabase_client, "_fetch_all",
        lambda sql, params=None: config if "FROM config" in sql else categorias,
    )
    last_id, state, gastos, ingresos = supabase_client.obtener_config_completo()
    assert last_id == 5
    assert state == "x"
    assert gastos == ["ALQUILER"]
    assert ingresos == ["SUELDO"]
