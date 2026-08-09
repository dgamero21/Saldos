import os
import subprocess
import sys

import pytest

# bot_Saldo.py lee env vars a nivel de módulo (líneas 26-28).
# Sin ellas el módulo no es importable para tests. Se inyectan dummies en la
# sesión ANTES de cualquier import de bot_Saldo. No se toca producción (FASE 0).
# TELEGRAM_TOKEN/TELEGRAM_CHAT_ID se fuerzan (no setdefault): los tests
# unitarios hardcodean "test-chat-id"/dummy en sus payloads y mockean la red;
# en CI los secrets reales de Telegram rompían ese aislamiento (FASE 10B).
os.environ["TELEGRAM_TOKEN"] = "test-token"
os.environ["TELEGRAM_CHAT_ID"] = "test-chat-id"
os.environ.setdefault("SHEET_ID", "test-sheet-id")
os.environ.setdefault("GOOGLE_REFRESH_TOKEN", "test-refresh")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-secret")
os.environ.setdefault("GMAIL_USER", "test@gmail.com")
os.environ.setdefault("GMAIL_APP_PASSWORD", "test-app-pass")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GIT_HEAD_MAIN = "d34fb56fad368fa7eb31661d23b7fddc49fcdadc"
BOT_FILE = os.path.join(REPO_ROOT, "bot_Saldo.py")
WEBHOOK_FILE = os.path.join(REPO_ROOT, "api", "webhook.js")


@pytest.fixture(scope="session")
def repo_head():
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture(scope="session")
def repo_root():
    return REPO_ROOT


@pytest.fixture(scope="session")
def bot_file():
    return BOT_FILE


@pytest.fixture(scope="session")
def webhook_file():
    return WEBHOOK_FILE


def _run_python(*args):
    result = subprocess.run(
        [sys.executable, *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return result


@pytest.fixture(scope="session")
def bot_module():
    """Importa bot_Saldo como módulo (requiere env vars dummy ya inyectadas)."""
    sys.path.insert(0, REPO_ROOT)
    import bot_Saldo

    return bot_Saldo
