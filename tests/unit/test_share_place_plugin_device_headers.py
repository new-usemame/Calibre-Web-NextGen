# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Share my place PR 2: the cwngsync plugin identifies itself on progress calls.

Spec: state/kindle-kids/specs/2026-10-11-share-my-place.md, section 10. The
server gates a kosync request only when it carries ``X-CWNG-Device-ID``; this
runs the plugin's own Lua test, which drives ``CWNGSyncClient`` against a
recording Spore client and checks the headers its pull and push send.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

TEST = (Path(__file__).resolve().parents[2] / "koreader" / "plugins"
        / "cwngsync.koplugin" / "tests" / "share_place_device_headers_test.lua")


def _lua():
    for candidate in ("lua", "lua5.4", "lua5.3", "lua5.1", "luajit"):
        found = shutil.which(candidate)
        if found:
            return found
    pytest.skip("no Lua interpreter installed")


def test_progress_pull_and_push_send_the_device_identity_headers():
    result = subprocess.run([_lua(), TEST.name], cwd=TEST.parent,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "share-place device header tests passed" in result.stdout
