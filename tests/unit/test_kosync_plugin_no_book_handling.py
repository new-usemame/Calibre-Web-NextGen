# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression coverage for the kosync plugin backports from CWA #1271 + #1272.

Fork issue #155 asked for two upstream PRs:

* CWA #1271 — `fix(cwasync): handle no-book sync actions`. Tapping push/pull
  menu items when no book is open used to crash the plugin or noop silently.
* CWA #1272 — `feat(cwasync): Add bulk progress pull`. Lets users pull progress
  for every downloaded book in one action — useful when finishing a book on
  one device and switching to another.

The plugin itself is Lua and we don't run a Lua test runner in CI, so these
tests pattern-pin the load-bearing call sites against the main.lua source. A
regression that drops a no-book guard, removes the bulk-pull entry point, or
breaks the sync_logic require would trip the test.

The plugin version gate that used to live here is now derived, in
tests/unit/test_cwasync_plugin_version_bump_gate.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO_ROOT / "koreader" / "plugins" / "cwasync.koplugin"
MAIN_LUA = PLUGIN_DIR / "main.lua"
SYNC_LOGIC_LUA = PLUGIN_DIR / "sync_logic.lua"
SYNC_LOGIC_TEST_LUA = PLUGIN_DIR / "tests" / "sync_logic_test.lua"

# The plugin version gate used to live here as EXPECTED_PLUGIN_VERSION, a
# hand-maintained constant that main.lua had to equal. It is now derived in
# tests/unit/test_cwasync_plugin_version_bump_gate.py. The constant did not
# survive contact with the failure it was written for: it pins the version
# against *itself*, so it only ever caught a unilateral edit to one of the two
# places, and was silent on the case that actually happened — #1427 changed 280
# lines of plugin source while version and constant both sat at 4.1.25, six
# releases behind, with CI green throughout. It also inverted: bumping the
# version correctly turned CI red until someone edited the test to match, which
# teaches the opposite of the intended lesson. The replacement reads the release
# tags and the plugin's own history, so there is nothing left to remember.


def _read(path: Path) -> str:
    assert path.exists(), f"missing file: {path}"
    return path.read_text(encoding="utf-8")


def test_main_lua_requires_sync_logic_module():
    body = _read(MAIN_LUA)
    assert 'local SyncLogic = require("sync_logic")' in body, (
        "main.lua must require the sync_logic module added by CWA #1272"
    )


def test_sync_logic_module_present():
    body = _read(SYNC_LOGIC_LUA)
    # The new module is small; only assert the high-level entry point CWA #1272
    # called out. Keeping the assertion narrow means we are not pinning the
    # implementation, just confirming the module exists with usable surface.
    assert "SyncLogic" in body, "sync_logic.lua must define a SyncLogic table"


def test_sync_logic_test_module_present_for_lua_smoke():
    # CWA #1272 ships a Lua test file (`lua tests/sync_logic_test.lua` per the
    # PR description). We don't run it in CI, but its presence signals the
    # upstream test surface is intact post-backport.
    assert SYNC_LOGIC_TEST_LUA.exists(), (
        "tests/sync_logic_test.lua must be vendored from CWA #1272"
    )


def test_no_book_message_helper_defined():
    body = _read(MAIN_LUA)
    # CWA #1271 added showNoBookMessage(); the plugin now informs the user
    # instead of crashing when push/pull is invoked with no book open.
    assert "local function showNoBookMessage" in body, (
        "main.lua must define showNoBookMessage() (CWA #1271)"
    )
    assert (
        '_("No book is currently open to push progress for.")' in body
    ), "showNoBookMessage must use the upstream message text"


def test_server_address_guard_helper_defined():
    body = _read(MAIN_LUA)
    # CWA #1271 also added ensureServerConfigured(), invoked from menu actions
    # so that tapping push/pull without a configured server shows a sensible
    # message instead of dispatching a half-built request.
    assert "local function ensureServerConfigured" in body, (
        "main.lua must define ensureServerConfigured() (CWA #1271)"
    )
    assert (
        '_("Please set the NextGen Server address first.")' in body
    ), "ensureServerConfigured must use the (NextGen-rebranded) prompt text"


def test_pull_log_lines_include_current_file_context():
    # The conflict-resolved Pull-handler diagnostic logs use the structured
    # `[Pull] end for <current_file> with ...` form from CWA #1271 rather than
    # the older `body.<field> missing` shape. Pin the new form so a future
    # edit that quietly reverts to the less-useful text trips this test.
    #
    # The third tail used to read "with missing progress field", pinning a
    # `body.progress == nil` guard. Fork #1366 replaced that guard with
    # `SyncLogic.resolveRemotePosition(body).kind == "none"`, which rejects a
    # strict superset: nil progress, the empty-string locator that reads as
    # present to every nil-check, and a percentage-kind row with no usable
    # percentage. "Unusable" is what the branch actually rejects, so the log
    # says that. The structured form this test exists to protect is unchanged.
    body = _read(MAIN_LUA)
    for tail in (
        "with invalid body",
        "with no remote progress",
        "with unusable progress field",
    ):
        assert (
            f'"CWASync: [Pull] end for", current_file, "{tail}"' in body
        ), f"main.lua must log '[Pull] end for ... {tail}' (CWA #1271)"


def test_bulk_progress_pull_entry_point_present():
    body = _read(MAIN_LUA)
    # CWA #1272 introduces a bulk-pull pathway. The user-visible entry point is
    # via the main menu — at minimum the menu/dispatcher registration must
    # mention "Pull progress for all".
    assert "Pull progress for all" in body, (
        "main.lua must expose the bulk-pull menu entry from CWA #1272"
    )
