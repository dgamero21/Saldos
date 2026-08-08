"""FASE 5 - Integración: mensajes_procesados contra Supabase real."""
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
    t = f"FASE5TEST_{uuid4().hex[:8]}"
    yield t
    conn = supabase_client._get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM mensajes_procesados WHERE mensaje_id = %s OR mensaje_id LIKE %s",
                (t, f"%{t}%"),
            )
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


def test_mensajes_procesados_dedup_real(tag):
    assert supabase_client.registrar_mensaje_procesado(
        tag, "bna", "Resumen"
    ) == "insertado"
    assert supabase_client.registrar_mensaje_procesado(
        tag, "otro", "Otro asunto"
    ) == "existente"
    filas = _fetch(
        "SELECT mensaje_id, remitente, asunto FROM mensajes_procesados WHERE mensaje_id = %s",
        (tag,),
    )
    assert len(filas) == 1
    assert filas[0] == (tag, "bna", "Resumen")


def test_mensaje_ya_procesado_real(tag):
    assert supabase_client.mensaje_ya_procesado(tag) is False
    supabase_client.registrar_mensaje_procesado(tag, "bna", "Resumen")
    assert supabase_client.mensaje_ya_procesado(tag) is True


def test_concurrencia_mensaje_procesado_una_sola_fila(tag):
    barrera = threading.Barrier(6)

    def tarea():
        barrera.wait()
        supabase_client.registrar_mensaje_procesado(tag, "bna", "Resumen")

    hilos = [threading.Thread(target=tarea) for _ in range(6)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    filas = _fetch(
        "SELECT 1 FROM mensajes_procesados WHERE mensaje_id = %s",
        (tag,),
    )
    assert len(filas) == 1
