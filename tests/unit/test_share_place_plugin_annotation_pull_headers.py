# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Share my place PR 5: the cwngsync plugin identifies itself on the annotation pull.

Spec: state/kindle-kids/specs/2026-10-11-share-my-place.md, section 17. The
server serves a device only its own highlights when the annotation GET carries
``X-CWNG-Device-ID``; without it the pull stays today's full set. This runs the
plugin's own Lua test, which drives ``CWNGSyncClient:pull_annotations`` against
a recording Spore client and checks the headers it sends.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

TEST = (Path(__file__).resolve().parents[2] / "koreader" / "plugins"
        / "cwngsync.koplugin" / "tests" / "share_place_annotation_pull_headers_test.lua")


def _lua():
    for candidate in ("lua", "lua5.4", "lua5.3", "lua5.1", "luajit"):
        found = shutil.which(candidate)
        if found:
            return found
    pytest.skip("no Lua interpreter installed")


def test_annotation_pull_sends_the_device_identity_headers():
    result = subprocess.run([_lua(), TEST.name], cwd=TEST.parent,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "share-place annotation pull header tests passed" in result.stdout
