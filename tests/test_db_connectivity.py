"""Pruebas de conectividad y schema de Supabase (FASE 1).

PREPARADAS pero NO ejecutadas por defecto: requieren SUPABASE_DB_URL
(connection string a la DB del proyecto) o SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY.
Sin ellas, los tests se SKIPean (pytest -m "db" no existe; usamos skipif).
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    not (os.environ.get("SUPABASE_DB_URL") or (
        os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    )),
    reason="SUPABASE_DB_URL (o SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY) no configurada",
)

EXPECTED_TABLES = [
    "config",
    "categorias_fijas",
    "reglas",
    "consolidado",
    "consumos",
    "ingresos",
    "mensajes_procesados",
]

EXPECTED_UNIQUE_INDEXES = [
    "uq_consolidado_dedup",
    "uq_consumos_dedup",
    "uq_ingresos_dedup",
    "uq_reglas_dedup",
]


def _connect():
    if os.environ.get("SUPABASE_DB_URL"):
        try:
            import psycopg2
        except ImportError as exc:
            pytest.skip(f"psycopg2 no instalado: {exc}")
        return psycopg2.connect(os.environ["SUPABASE_DB_URL"])
    # Fallback: REST con service_role (solo verificaciones de nivel superior)
    return None


def test_conectividad_db():
    conn = _connect()
    if conn is None:
        pytest.skip("Solo SUPABASE_URL disponible: requiere psycopg2 o DB URL para conectividad SQL")
    with conn.cursor() as cur:
        cur.execute("SELECT version()")
        version = cur.fetchone()[0]
    conn.close()
    assert "PostgreSQL" in version


def test_tablas_existentes():
    conn = _connect()
    if conn is None:
        pytest.skip("No disponible para verificación SQL")
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public'
              AND tablename = ANY(%s)
            """,
            (EXPECTED_TABLES,),
        )
        presentes = {row[0] for row in cur.fetchall()}
    conn.close()
    assert presentes == set(EXPECTED_TABLES)


def test_indices_dedup_existentes():
    conn = _connect()
    if conn is None:
        pytest.skip("No disponible para verificación SQL")
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT indexname FROM pg_indexes
            WHERE schemaname = 'public' AND indexname = ANY(%s)
            """,
            (EXPECTED_UNIQUE_INDEXES,),
        )
        presentes = {row[0] for row in cur.fetchall()}
    conn.close()
    assert presentes == set(EXPECTED_UNIQUE_INDEXES)


def test_rls_habilitado():
    conn = _connect()
    if conn is None:
        pytest.skip("No disponible para verificación SQL")
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.relname
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relname = ANY(%s)
              AND c.relrowsecurity = TRUE
            """,
            (EXPECTED_TABLES,),
        )
        habilitadas = {row[0] for row in cur.fetchall()}
    conn.close()
    assert habilitadas == set(EXPECTED_TABLES)


def test_bucket_pdfs_existe():
    conn = _connect()
    if conn is None:
        pytest.skip("No disponible para verificación SQL")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, public FROM storage.buckets WHERE id = 'pdfs'"
        )
        fila = cur.fetchone()
    conn.close()
    assert fila is not None, "Bucket 'pdfs' no existe"
    assert fila[2] is False, "Bucket 'pdfs' debe ser privado (public = FALSE)"


# ---------------------------------------------------------------------------
# FASE 10A — Web App: consolidado.pagado
# ---------------------------------------------------------------------------

def test_consolidado_columna_pagado():
    """FASE 10A: la columna pagado existe, BOOLEAN NOT NULL con DEFAULT FALSE."""
    conn = _connect()
    if conn is None:
        pytest.skip("No disponible para verificación SQL")
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'consolidado'
              AND column_name = 'pagado'
            """
        )
        fila = cur.fetchone()
    conn.close()
    assert fila is not None, "consolidado.pagado no existe (aplicar db/migracion_10a.sql)"
    data_type, is_nullable, column_default = fila
    assert data_type == "boolean"
    assert is_nullable == "NO"
    assert "false" in (column_default or "").lower()


def test_consolidado_pagado_backfill_false():
    """FASE 10A: los registros históricos quedaron con pagado = FALSE."""
    conn = _connect()
    if conn is None:
        pytest.skip("No disponible para verificación SQL")
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) FILTER (WHERE pagado), count(*)
            FROM consolidado
            """
        )
        pagados, total = cur.fetchone()
    conn.close()
    assert pagados == 0, f"{pagados} registros con pagado=TRUE sin haber PATCH (fase 10C)"
    assert total > 0, "consolidado vacío: migración de históricos no aplicada"
