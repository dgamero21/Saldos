"""FASE 7 - Webhook: ejecuta la batería de `node:test` del handler JS."""
import subprocess
import sys

from conftest import REPO_ROOT


def test_webhook_node_suite():
    result = subprocess.run(
        ["node", "--test", "api/webhook.test.mjs"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, (
        "Node webhook tests failed\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
