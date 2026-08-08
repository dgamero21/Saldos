"""Smoke test de FASE 0: verifica que el entorno de trabajo está alineado con el baseline (main)."""

import os
import subprocess

import pytest

from conftest import BOT_FILE, GIT_HEAD_MAIN, REPO_ROOT, WEBHOOK_FILE


def test_repo_head_es_descendiente_del_baseline(repo_root):
    """El HEAD debe ser el baseline conocido o un descendiente directo suyo.

    Se relaja la igualdad estricta porque cada commit legítimo mueve el HEAD;
    el guard real es que la historia nunca diverge del baseline pinneado.
    """
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", GIT_HEAD_MAIN, "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"HEAD no es descendiente del baseline {GIT_HEAD_MAIN}"
    )


def test_working_tree_limpio_para_produccion(repo_root):
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    tracked_changes = [
        line
        for line in result.stdout.splitlines()
        if line and not line.startswith("??")
    ]
    assert tracked_changes == [], f"Hay cambios en archivos trackeados: {tracked_changes}"


def test_bot_y_webhook_presentes():
    assert os.path.isfile(BOT_FILE), f"No existe {BOT_FILE}"
    assert os.path.isfile(WEBHOOK_FILE), f"No existe {WEBHOOK_FILE}"


def test_blobs_de_git_coinciden_con_working_tree(repo_root):
    expected = {
        # FASE 4 baseline (escritura a Supabase): hash del blob actual.
        "bot_Saldo.py": "d1d56203791c502c239bd8b397cd00a00d6369ff",
        "api/webhook.js": "003091bb9ef385ace7c4d84b3c145ce6c7d1b3bd",
    }
    for path, blob_hash in expected.items():
        result = subprocess.run(
            ["git", "hash-object", path],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == blob_hash, f"{path} no coincide con main@532e762"


def test_bot_compila_sintaxis():
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "py_compile", BOT_FILE],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"bot_Saldo.py no compila:\n{result.stderr}"


def test_bot_tiene_guard_main():
    with open(BOT_FILE, encoding="utf-8", errors="replace") as f:
        content = f.read()
    assert '__name__ == "__main__"' in content
    assert "def revisar_mails():" in content
    assert "def procesar_mensajes_telegram():" in content


def test_import_bot_con_env_dummies(bot_module):
    assert bot_module.TELEGRAM_TOKEN == "test-token"
    assert callable(bot_module.revisar_mails)
    assert callable(bot_module.procesar_mensajes_telegram)
    assert callable(bot_module.guardar_en_sheet)
    assert callable(bot_module.guardar_o_actualizar_consumos_sheet)
