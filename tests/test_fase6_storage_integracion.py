"""FASE 6 - Integración opcional con Supabase Storage real."""
import os
import sys
from uuid import uuid4

import pytest
import requests

from conftest import REPO_ROOT

pytestmark = pytest.mark.skipif(
    not (
        os.environ.get("SUPABASE_URL")
        and os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    ),
    reason="SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY no configuradas",
)

sys.path.insert(0, REPO_ROOT)
import supabase_client  # noqa: E402


@pytest.fixture
def storage_path():
    pdf = f"%PDF-1.4\nFASE6-{uuid4().hex}".encode("ascii")
    resultado = supabase_client.subir_pdf_storage("Factura.pdf", pdf, "FASE6TEST")
    path = resultado["path"]
    yield resultado
    url, key = supabase_client._storage_config()
    requests.delete(
        f"{url}/storage/v1/object/pdfs/{supabase_client._storage_quote_path(path)}",
        headers=supabase_client._storage_headers(),
        timeout=20,
    )


def test_storage_upload_y_signed_url_reales(storage_path):
    assert storage_path["link_ref"].startswith("storage://pdfs/")
    assert storage_path["signed_url"].startswith("http")
    resp = requests.get(storage_path["signed_url"], timeout=20)
    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF")
