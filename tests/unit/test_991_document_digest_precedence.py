# SPDX-License-Identifier: GPL-3.0-or-later
"""Execute the binary byte-versus-sidecar precedence regression for #991.

The old source pins could pass without exercising hashing. Both suites now
run the shared precedence policy and the shipped content-digest function;
wrong argument order or a stale cache winning over changed bytes fails.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
PLUGIN = Path(__file__).resolve().parents[2] / "koreader/plugins/cwngsync.koplugin"


@pytest.mark.parametrize("suite", ["sync_logic_test.lua", "document_digest_test.lua"])
def test_binary_document_precedence_behavior(suite):
    lua = shutil.which("lua") or shutil.which("lua5.4")
    if lua is None:
        pytest.skip("Lua 5.4 required for KOReader plugin behavior tests")
    completed = subprocess.run([lua, suite], cwd=PLUGIN / "tests",
                               capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stdout + completed.stderr
