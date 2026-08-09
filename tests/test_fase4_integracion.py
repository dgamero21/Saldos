"""FASE 4 — Integración: escritura real contra Supabase.

Requeridas SUPABASE_DB_URL o SUPABASE_DBPW (si no, se SKIPean, igual que
tests/test_fase3_equivalencia.py).

Escribe filas con marcadores únicos (sentinel) y las ELIMINA al terminar
(fixture tag), sin tocar datos de producción. Verifica:
  - dedup real de consolidado (monto/vto/case-insensitive).
  - consumos: insert / cuota que avanza / cuota que NO retrocede / comprobante raw.
  - ingresos: insert / dup / ID reproducible.
  - fijos: gasto e ingreso, dup, cambio de mes.
  - concurrencia: N escrituras simultáneas -> 1 fila (UNIQUE de la DB).
  - regresión: el estado final de la DB equivale a la lógica anterior.
"""
import os
import sys
import threading
from uuid import uuid4

import pytest

from conftest import REPO_ROOT

pytestmark = pytest.mark.skipif(
    not (os.environ.get("SUPABASE_DB_URL") or os.environ.get("SUPABASE_DBPW")),
    reason="SUPABASE_DB_URL o SUPABASE_DBPW no configurada",
)

sys.path.insert(0, REPO_ROOT)
import supabase_client  # noqa: E402


@pytest.fixture
def tag():
    t = f"FASE4TEST_{uuid4().hex[:8]}"
    yield t
    conn = supabase_client._get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM consolidado WHERE remitente = %s OR remitente LIKE %s", (t, f"%{t}%"))
            cur.execute("DELETE FROM consumos WHERE remitente = %s OR remitente LIKE %s", (t, f"%{t}%"))
            cur.execute("DELETE FROM ingresos WHERE tipo = %s OR tipo LIKE %s", (t, f"%{t}%"))
            cur.execute("DELETE FROM ingresos WHERE origen = %s OR origen LIKE %s", (t, f"%{t}%"))
        conn.commit()
    finally:
        conn.close()


def _fetch(sql, params):
    conn = supabase_client._get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Consolidado
# ---------------------------------------------------------------------------

def test_consolidado_dedup_real(tag):
    vto = "21/08/2026"
    assert supabase_client.guardar_consolidado(
        "05/08/2026", tag, "R1", 100.0, vto
    ) == "insertado"
    # Mismo remitente en MAYÚSCULAS -> lower() choca -> no duplica
    assert supabase_client.guardar_consolidado(
        "05/08/2026", tag.upper(), "R2", 100.0, vto
    ) == "existente"
    assert supabase_client.guardar_consolidado(
        "05/08/2026", tag, "R3", "100,00", vto  # monto '100,00' == 100.0
    ) == "existente"
    filas = _fetch(
        "SELECT id_consolidado, monto_total, lower(remitente) FROM consolidado "
        "WHERE remitente = %s",
        (tag,),
    )
    assert len(filas) == 1
    assert filas[0][0] == f"{tag.lower()}|{vto}|100.0"
    assert float(filas[0][1]) == 100.0


def test_consolidado_monto_o_vto_distinto_inserta(tag):
    assert supabase_client.guardar_consolidado(
        "05/08/2026", tag, "R", 100.0, "21/08/2026"
    ) == "insertado"
    assert supabase_client.guardar_consolidado(
        "05/08/2026", tag, "R", 200.0, "21/08/2026"
    ) == "insertado"  # monto distinto
    assert supabase_client.guardar_consolidado(
        "05/08/2026", tag, "R", 100.0, "22/08/2026"
    ) == "insertado"  # vto distinto
    assert len(_fetch("SELECT 1 FROM consolidado WHERE remitente = %s", (tag,))) == 3


# ---------------------------------------------------------------------------
# Consumos
# ---------------------------------------------------------------------------

def _consumo(**over):
    base = {
        "fecha": "01/08/2026", "comprobante": "008452", "detalle": "CUOTA 2/6",
        "cuota_actual": 2, "cuota_total": 6, "pesos": "1234,56", "dolar": 10.5,
        "fecha_cierre": "15/07/2026", "fecha_vencimiento": "10/08/2026",
    }
    base.update(over)
    return base


def test_consumos_insert_update_no_retrocede(tag):
    r1 = supabase_client.guardar_o_actualizar_consumos([_consumo()], tag)
    assert r1[0]["estado"] == "insertado"

    # Cuota menor (1/6) NO retrocede ni pisa montos
    r2 = supabase_client.guardar_o_actualizar_consumos(
        [_consumo(cuota_actual=1, pesos="111", dolar=1.0)], tag
    )
    assert r2[0]["estado"] == "sin_cambios"

    # Cuota igual (2/6) con valores actualizados -> actualiza (>= en la lógica vieja)
    r3 = supabase_client.guardar_o_actualizar_consumos(
        [_consumo(pesos="2500,75", dolar=11.0)], tag
    )
    assert r3[0]["estado"] == "actualizado"

    filas = _fetch(
        "SELECT cuota_actual, cuota_total, pesos, dolar, fecha_cierre, "
        "fecha_vencimiento, comprobante, id_consumo "
        "FROM consumos WHERE remitente = %s",
        (tag,),
    )
    assert len(filas) == 1
    f = filas[0]
    assert f[0] == 2
    assert float(f[2]) == 2500.75      # pesos actualizados (no la versión '111')
    assert float(f[3]) == 11.0
    assert str(f[4]) == "2026-07-15"   # cierre preservado
    assert str(f[5]) == "2026-08-10"   # vto preservado
    assert f[6] == "008452"            # comprobante RAW (no normalizado)
    assert f[7] == "01/08/2026|008452|CUOTA 2/6|6|" + tag


def test_consumos_lote_y_comprobante_distinto(tag):
    resultado = supabase_client.guardar_o_actualizar_consumos(
        [_consumo(), _consumo(comprobante="445566", detalle="OTRO")], tag
    )
    assert [r["estado"] for r in resultado] == ["insertado", "insertado"]
    assert len(_fetch("SELECT 1 FROM consumos WHERE remitente = %s", (tag,))) == 2
    # El mismo lote de nuevo -> ninguno insertado
    resultado2 = supabase_client.guardar_o_actualizar_consumos(
        [_consumo(), _consumo(comprobante="445566", detalle="OTRO")], tag
    )
    assert [r["estado"] for r in resultado2] == ["actualizado", "actualizado"]


# ---------------------------------------------------------------------------
# Ingresos
# ---------------------------------------------------------------------------

def test_ingresos_dedup_y_id_reproducible(tag):
    assert supabase_client.guardar_ingreso(
        "28/07/2026", tag, "1700000", "Manual Telegram"
    ) == "insertado"
    assert supabase_client.guardar_ingreso(
        "28/07/2026", tag, "999999", "Manual Telegram"
    ) == "existente"
    filas = _fetch(
        "SELECT monto, id_ingreso FROM ingresos WHERE tipo = %s", (tag,)
    )
    assert len(filas) == 1
    assert float(filas[0][0]) == 1700000.0
    assert filas[0][1] == f"28/07/2026|Ingreso|{tag}|Manual Telegram"


# ---------------------------------------------------------------------------
# Fijos
# ---------------------------------------------------------------------------

def test_fijos_gasto_dedup_y_cambio_de_mes(tag):
    rem = f"Fijo {tag}"
    mes1 = "01/08/2026"
    mes2 = "01/09/2026"

    def fijo(fecha):
        return [{
            "fecha": fecha, "comprobante": "Fijo Config", "detalle": f"FIJO {tag}",
            "cuota_actual": 1, "cuota_total": 1, "pesos": 10000.0, "dolar": 0.0,
            "fecha_cierre": "", "fecha_vencimiento": "",
        }]

    assert supabase_client.guardar_o_actualizar_consumos(fijo(mes1), rem)[0]["estado"] == "insertado"
    # Mismo mes de nuevo -> el fijo ya existe (no se re-inserta; el mensaje solo
    # avisa insertados)
    assert supabase_client.guardar_o_actualizar_consumos(fijo(mes1), rem)[0]["estado"] == "actualizado"
    # Cambio de mes -> nuevo registro
    assert supabase_client.guardar_o_actualizar_consumos(fijo(mes2), rem)[0]["estado"] == "insertado"
    assert len(_fetch("SELECT 1 FROM consumos WHERE remitente = %s", (rem,))) == 2


def test_fijos_ingreso_dedup_y_cambio_de_mes(tag):
    tipo = f"ING {tag}"
    assert supabase_client.guardar_ingreso(
        "01/08/2026", tipo, 1000.0, "Fijo Config"
    ) == "insertado"
    assert supabase_client.guardar_ingreso(
        "01/08/2026", tipo, 1000.0, "Fijo Config"
    ) == "existente"
    assert supabase_client.guardar_ingreso(
        "01/09/2026", tipo, 1000.0, "Fijo Config"
    ) == "insertado"
    assert len(_fetch("SELECT 1 FROM ingresos WHERE tipo = %s", (tipo,))) == 2


# ---------------------------------------------------------------------------
# Concurrencia (cron + dispatch simultáneos)
# ---------------------------------------------------------------------------

def test_concurrencia_consolidado_una_sola_fila(tag):
    barrera = threading.Barrier(6)

    def tarea():
        barrera.wait()
        supabase_client.guardar_consolidado(
            "05/08/2026", tag, "R", 100.0, "21/08/2026"
        )

    hilos = [threading.Thread(target=tarea) for _ in range(6)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert len(_fetch("SELECT 1 FROM consolidado WHERE remitente = %s", (tag,))) == 1


def test_concurrencia_ingreso_una_sola_fila(tag):
    barrera = threading.Barrier(6)

    def tarea():
        barrera.wait()
        supabase_client.guardar_ingreso("01/08/2026", tag, 500.0, "Manual Telegram")

    hilos = [threading.Thread(target=tarea) for _ in range(6)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert len(_fetch("SELECT 1 FROM ingresos WHERE tipo = %s", (tag,))) == 1


def test_concurrencia_consumo_una_sola_fila(tag):
    barrera = threading.Barrier(6)

    def tarea():
        barrera.wait()
        supabase_client.guardar_o_actualizar_consumos([_consumo()], tag)

    hilos = [threading.Thread(target=tarea) for _ in range(6)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert len(_fetch("SELECT 1 FROM consumos WHERE remitente = %s", (tag,))) == 1


# ---------------------------------------------------------------------------
# Regresión: estado final equivalente a la lógica anterior
# ---------------------------------------------------------------------------

def _normalizar_monto(v):
    t = str(v).replace("$", "").strip()
    if "." in t and "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif "," in t:
        t = t.replace(",", ".")
    return round(float(t), 2)


def _referencia_consumos(consumos, remitente):
    """Referencia 1:1 de la lógica ANTERIOR (guardar_consumos
    original): devuelve la lista de filas final (id | cuota | pesos | dolar)."""
    filas = []
    for c in consumos:
        id_unico = (
            f"{str(c['fecha']).strip()}|{str(c['comprobante']).strip()}|"
            f"{str(c['detalle']).strip()}|{str(c['cuota_total']).strip()}|"
            f"{str(remitente).strip()}"
        )
        idx = None
        for i, fila in enumerate(filas):
            if fila[0] == id_unico:
                idx = i
                break
        if idx is not None:
            fila = filas[idx]
            try:
                cuota_nueva = int(c["cuota_actual"])
                cuota_existente = int(fila[2]) if str(fila[2]).isdigit() else 0
            except Exception:
                cuota_nueva = 1
                cuota_existente = 0
            if cuota_nueva >= cuota_existente:
                filas[idx] = (id_unico, c["cuota_actual"], c["pesos"], c["dolar"])
        else:
            filas.append(
                (id_unico, c["cuota_actual"], c["pesos"], c["dolar"])
            )
    return filas


def test_equivalencia_consumos_con_logica_anterior(tag):
    secuencia = [
        _consumo(cuota_actual=1, cuota_total=6, pesos="1000,00", dolar=0.0),
        _consumo(cuota_actual=2, cuota_total=6, pesos="2000,00", dolar=1.0),
        _consumo(cuota_actual=1, cuota_total=6, pesos="999,00", dolar=9.0),   # no retrocede
        _consumo(cuota_actual=2, cuota_total=6, pesos="2100,00", dolar=2.0),  # igual -> actualiza
        _consumo(comprobante="445566", detalle="OTRO", pesos="50,00", dolar=0.0),
    ]
    referencia = _referencia_consumos(secuencia, tag)

    supabase_client.guardar_o_actualizar_consumos(secuencia, tag)

    filas = _fetch(
        "SELECT id_consumo, cuota_actual, pesos, dolar "
        "FROM consumos WHERE remitente = %s",
        (tag,),
    )
    resultado = sorted(
        (r[0], r[1], float(r[2]), float(r[3])) for r in filas
    )
    esperado = sorted(
        (id_, int(cuota), float(_normalizar_monto(p)), float(d))
        for id_, cuota, p, d in referencia
    )
    assert resultado == esperado


def test_equivalencia_consolidado_con_logica_anterior(tag):
    """La dedup de la DB produce el mismo conjunto de IDs que la lógica anterior
    (es_registro_duplicado + guardar_consolidado)."""
    vto = "21/08/2026"
    casos = [
        ("05/08/2026", tag, "R1", 100.0, vto),
        ("05/08/2026", tag.upper(), "R2", 100.0, vto),   # dup (case-insensitive)
        ("05/08/2026", tag, "R3", "100,00", vto),         # dup (mismo monto)
        ("05/08/2026", tag, "R4", 200.0, vto),            # monto distinto
        ("05/08/2026", tag, "R5", 100.0, "22/08/2026"),   # vto distinto
    ]
    for fecha, rem, asunto, monto, v in casos:
        supabase_client.guardar_consolidado(fecha, rem, asunto, monto, v)

    ids_db = {
        r[0] for r in _fetch(
            "SELECT id_consolidado FROM consolidado WHERE remitente = %s", (tag,)
        )
    }
    # Lógica anterior: inserta solo si el id (rem|vto|monto) no existe.
    ids_antiguos = set()
    for _, rem, _, monto, v in casos:
        m = _normalizar_monto(monto)
        id_ = f"{str(rem).strip().lower()}|{str(v).strip()}|{m}"
        if id_ not in ids_antiguos:
            ids_antiguos.add(id_)
    assert ids_db == ids_antiguos
