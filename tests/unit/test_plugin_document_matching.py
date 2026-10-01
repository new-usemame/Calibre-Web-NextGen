"""Run the shipped KOReader plugin's matching menu and digest behavior."""
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
PLUGIN = Path(__file__).resolve().parents[2] / "koreader/plugins/cwngsync.koplugin"


def test_document_matching_plugin_behavior():
    suite = "document_matching_test.lua"
    lua = shutil.which("lua") or shutil.which("lua5.4")
    if lua is None:
        pytest.skip("Lua 5.4 required for KOReader plugin behavior tests")
    result = subprocess.run([lua, suite], cwd=PLUGIN / "tests", capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
