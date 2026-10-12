# SPDX-License-Identifier: GPL-3.0-or-later
"""Run Share my place's plugin behavior suites in the Python CI job."""

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
PLUGIN = Path(__file__).resolve().parents[2] / "koreader/plugins/cwngsync.koplugin"


@pytest.mark.parametrize("suite", [
    "share_place_progress_traffic_test.lua",
    "share_place_builtin_sync_test.lua",
])
def test_share_place_plugin_behavior(suite):
    lua = shutil.which("lua5.4") or shutil.which("lua")
    if lua is None:
        pytest.skip("Lua 5.4 required for KOReader plugin behavior tests")
    completed = subprocess.run([lua, suite], cwd=PLUGIN / "tests",
                               capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stdout + completed.stderr
