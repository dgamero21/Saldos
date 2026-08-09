"""FASE 8 // Validación de secrets, PostgreSQL, REST/Storage y seguridad.

Estos tests se ejecutan en CI (GitHub Actions) donde los secrets
SUPABASE_DB_URL, SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY están disponibles
como environment variables.

- NUNCA imprimen los valores de los secrets.
- NUNCA los escriben en archivos ni logs.
- Solo comprobar presencia y funcionalidad.

Si los secrets no están presentes, los tests se SKIPean (no FAIL).
"""
import os
import sys
import time
from uuid import uuid4

import pytest

from conftest import REPO_ROOT

sys.path.insert(0, REPO_ROOT)
import supabase_client  # noqa: E402

# Tests que requieren solo PostgreSQL (SUPABASE_DB_URL o SUPABASE_DBPW)
skipif_no_db = pytest.mark.skipif(
    not (os.environ.get("SUPABASE_DB_URL") or os.environ.get("SUPABASE_DBPW")),
    reason="FASE 8 PostgreSQL: requiere SUPABASE_DB_URL o SUPABASE_DBPW",
)

# Tests que requieren REST/Storage (SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY)
skipif_no_rest = pytest.mark.skipif(
    not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY")),
    reason="FASE 8 REST/Storage: requiere SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY",
)


# ---------------------------------------------------------------------------
# 1. Presencia de secrets (sin mostrar valores)
# ---------------------------------------------------------------------------

@skipif_no_db
def test_secret_supabase_db_url_presente():
    """Acepta SUPABASE_DB_URL completa o SUPABASE_DBPW (password del pooler)."""
    val = os.environ.get("SUPABASE_DB_URL", "") or os.environ.get("SUPABASE_DBPW", "")
    assert val, "SUPABASE_DB_URL o SUPABASE_DBPW no configurada"
    assert not any(c in val for c in ("\n", " ")), "valor de DB contiene espacios"


@skipif_no_rest
def test_secret_supabase_url_presente():
    val = os.environ.get("SUPABASE_URL", "")
    assert val, "SUPABASE_URL no configurada"
    assert val.startswith("https://"), "SUPABASE_URL debe empezar con https://"


@skipif_no_rest
def test_secret_supabase_service_role_key_presente():
    val = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    assert val, "SUPABASE_SERVICE_ROLE_KEY no configurada"
    assert len(val) > 20, "SUPABASE_SERVICE_ROLE_KEY demasiado corta"


@skipif_no_rest
def test_secrets_no_aparecen_en_codigo():
    """Los secrets no deben estar hardcodeados en ningún archivo del repo."""
    import subprocess
    result = subprocess.run(
        ["git", "grep", "-l", "eyJ", "--", "*.py", "*.js", "*.mjs", "*.yml", "*.json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    # Los tests que buscan JWT por patrón contienen el literal "eyJ" (como
    # argumento de git grep), por lo que deben excluirse de los resultados.
    archivos = result.stdout.strip().splitlines() if result.stdout.strip() else []
    archivos_reales = [f for f in archivos if not f.startswith("tests/")]
    assert archivos_reales == [], f"JWT hardcodeado en: {archivos_reales}"


# ---------------------------------------------------------------------------
# 2. PostgreSQL: tablas, conteos, UNIQUE, RLS (solo lectura)
# ---------------------------------------------------------------------------

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


def _fetch(sql, params=None):
    conn = supabase_client._get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()
    finally:
        conn.close()


@skipif_no_db
def test_postgres_conexion_ok():
    filas = _fetch("SELECT version()")
    assert "PostgreSQL" in filas[0][0]


@skipif_no_db
def test_postgres_tablas_presentes():
    filas = _fetch(
        """
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'public' AND tablename = ANY(%s)
        """,
        (EXPECTED_TABLES,),
    )
    presentes = {r[0] for r in filas}
    assert presentes == set(EXPECTED_TABLES), f"Faltan tablas: {set(EXPECTED_TABLES) - presentes}"


@skipif_no_db
def test_postgres_conteos_migrados():
    """Verifica que las tablas tienen datos migrados (no vacías)."""
    for tabla in ["config", "categorias_fijas", "reglas", "consolidado", "consumos"]:
        filas = _fetch(f"SELECT count(*) FROM {tabla}")
        assert filas[0][0] > 0, f"Tabla {tabla} esta vacia"


@skipif_no_db
def test_postgres_indices_unique_presentes():
    filas = _fetch(
        """
        SELECT indexname FROM pg_indexes
        WHERE schemaname = 'public' AND indexname = ANY(%s)
        """,
        (EXPECTED_UNIQUE_INDEXES,),
    )
    presentes = {r[0] for r in filas}
    assert presentes == set(EXPECTED_UNIQUE_INDEXES)


@skipif_no_db
def test_postgres_rls_habilitado():
    filas = _fetch(
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
    habilitadas = {r[0] for r in filas}
    assert habilitadas == set(EXPECTED_TABLES)


# ---------------------------------------------------------------------------
# 3. Supabase REST: conexion
# ---------------------------------------------------------------------------

@skipif_no_rest
def test_rest_conexion_ok():
    import requests
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/config?select=clave&limit=1"
    headers = {
        "apikey": os.environ["SUPABASE_SERVICE_ROLE_KEY"],
        "Authorization": f"Bearer {os.environ['SUPABASE_SERVICE_ROLE_KEY']}",
    }
    resp = requests.get(url, headers=headers, timeout=15)
    assert resp.status_code == 200, f"REST config HTTP {resp.status_code}"
    assert len(resp.json()) > 0


# ---------------------------------------------------------------------------
# 4. Storage: bucket, upload, signed URL, acceso, cleanup, privado
# ---------------------------------------------------------------------------

@skipif_no_db
def test_storage_bucket_pdfs_existe_y_es_privado():
    filas = _fetch("SELECT id, public FROM storage.buckets WHERE id = 'pdfs'")
    assert filas, "Bucket 'pdfs' no existe"
    assert filas[0][1] is False, "Bucket 'pdfs' debe ser privado (public = FALSE)"


@skipif_no_rest
def test_storage_upload_signed_url_acceso_y_cleanup():
    """Sube un PDF de prueba, verifica signed URL, lo descarga, lo elimina."""
    tag = f"FASE8TEST_{uuid4().hex[:8]}"
    pdf_bytes = f"%PDF-1.4\nFASE8-{tag}".encode("ascii")
    resultado = supabase_client.subir_pdf_storage("FASE8_test.pdf", pdf_bytes, tag)
    assert resultado["link_ref"].startswith("storage://pdfs/")

    # 1. Signed URL funciona
    import requests
    signed_url = resultado["signed_url"]
    assert signed_url.startswith("http")

    # 2. Descargar PDF via signed URL
    resp = requests.get(signed_url, timeout=20)
    assert resp.status_code == 200, f"Signed URL HTTP {resp.status_code}"
    assert resp.content.startswith(b"%PDF"), "El contenido no es un PDF"

    # 3. Eliminar el PDF de prueba
    url_base = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    path = resultado["path"]
    quote_path = "/".join(
        __import__("urllib.parse", fromlist=["quote"]).quote(p, safe="")
        for p in path.split("/")
    )
    del_resp = requests.delete(
        f"{url_base}/storage/v1/object/pdfs/{quote_path}",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        timeout=20,
    )
    assert del_resp.status_code in (200, 204, 404), f"Delete HTTP {del_resp.status_code}"

    # 4. Verificar que no queda residuo. El DELETE es inmediato en
    #    storage.objects, pero el CDN puede seguir sirviendo la signed URL
    #    por unos segundos (eventual consistency), así que se reintenta.
    get_resp = requests.get(signed_url, timeout=20)
    intentos = 0
    while get_resp.status_code in (200, 304) and intentos < 10:
        time.sleep(2)
        intentos += 1
        get_resp = requests.get(signed_url, timeout=20)
    assert get_resp.status_code in (400, 404), (
        f"El PDF de prueba sigue accesible tras eliminar (HTTP {get_resp.status_code})"
    )


@skipif_no_db
def test_storage_no_deja_residuos():
    """Verifica que no hay archivos FASE8TEST residuales de corridas anteriores."""
    filas = _fetch(
        "SELECT count(*) FROM storage.objects "
        "WHERE bucket_id = %s AND name LIKE %s",
        ("pdfs", "resumenes/%/FASE8TEST_%"),
    )
    assert filas[0][0] == 0, f"Hay {filas[0][0]} archivos FASE8TEST residuales en Storage"
