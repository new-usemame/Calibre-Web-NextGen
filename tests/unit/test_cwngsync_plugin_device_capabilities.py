# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioral contracts for on-device storage, deletion and collections."""

from pathlib import Path
import shutil
import subprocess

import pytest


pytestmark = pytest.mark.unit
PLUGIN = Path(__file__).resolve().parents[2] / "koreader/plugins/cwngsync.koplugin"
SCRIPT = PLUGIN / "tests/device_capabilities_test.lua"


def _lua():
    for candidate in ("lua", "lua5.4", "lua5.3", "lua5.1", "luajit"):
        executable = shutil.which(candidate)
        if executable:
            return executable
    pytest.fail("a Lua interpreter is required for the device capability contract")


def test_device_storage_delete_and_collection_behaviors():
    result = subprocess.run(
        [_lua(), SCRIPT.name], cwd=SCRIPT.parent,
        capture_output=True, text=True, timeout=30, check=False,
    )

    assert result.returncode == 0, (
        f"Lua device capability contract failed:\n{result.stdout}\n{result.stderr}"
    )
