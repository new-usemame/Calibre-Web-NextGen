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

TESTS = (Path(__file__).resolve().parents[2] / "koreader" / "plugins"
         / "cwngsync.koplugin" / "tests")
TEST = TESTS / "share_place_device_headers_test.lua"
DORMANT = TESTS / "share_place_guard_dormant_test.lua"


@pytest.fixture(autouse=True)
def gates_enforced(monkeypatch):
    """Rollout switch on (spec section 12); the plugin side does not read it."""
    monkeypatch.setenv("CWNG_SHARE_PLACE_GATES", "1")


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


def test_builtin_sync_guard_stays_dormant_unless_the_server_enforces_gates():
    result = subprocess.run([_lua(), DORMANT.name], cwd=DORMANT.parent,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "share-place guard dormant tests passed" in result.stdout
