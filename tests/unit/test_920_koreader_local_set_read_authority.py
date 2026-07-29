# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""An unreadable device must not be read as an empty one (#920, device side).

#920's server half is closed: the server no longer infers a deletion from an
omission, it deletes exactly the ids the device names (see
``test_920_koreader_push_only_authority``). That moved the decision onto the
device, and the device got it wrong in the same way, one layer down.

``syncAnnotations`` picks a provider *before* the pull and reads it *after* —
a gap of up to one HTTP round trip (15s) during which the user can close the
book and KOReader can tear ``ui.annotation`` down. Both providers answered that
with ``{}``: the KOReader one when its collection had gone, the Kobo one when
KoboReader.sqlite would not open or the query failed. The caller then decided
whether to trust that list from ``provider.push_all_local`` — a *constant*,
true on every call — so a read that never happened became "the user deleted
every highlight", and the plugin named each one to a server that obeys explicit
deletes and never un-hides a tombstone.

So the fix is a contract, not a guard at one call site: ``readAll`` returns nil
when it could not read and ``{}`` only for a device that genuinely holds no
highlights, and ``SyncLogic.resolveLocalSet`` is the single place that turns
that into ``known``. These tests pin both halves — the Lua behaviour by running
the plugin's own suite, and the wiring by reading the shipped source, so a
refactor cannot quietly restore the capability-flag shortcut.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PLUGIN = Path(__file__).parents[2] / "koreader/plugins/cwasync.koplugin"


def _lua() -> str | None:
    for candidate in ("lua", "lua5.4", "lua5.3", "lua5.1", "luajit"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


@pytest.mark.parametrize("suite", [
    "sync_logic_test.lua",
    "device_annotations_test.lua",
    "document_digest_test.lua",
])
def test_plugin_lua_suites_pass(suite):
    """Run the plugin's Lua tests for real.

    They are pure logic with no KOReader imports, so they run anywhere a Lua
    interpreter does. CI installs one; if it ever stops doing so this fails
    rather than skipping, because a gate nobody runs is what let 19 SPA specs
    rot unnoticed (#1130).
    """
    lua = _lua()
    if lua is None:
        if os.environ.get("CI"):
            pytest.fail(
                "no Lua interpreter on a CI runner: the plugin's behavioural "
                "tests would silently stop running. Install lua5.4 in the "
                "workflow's system dependencies step."
            )
        pytest.skip("no Lua interpreter available locally (CI installs one)")

    result = subprocess.run(
        [lua, suite],
        cwd=PLUGIN / "tests",
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"{suite} failed:\n{result.stdout}\n{result.stderr}"
    )
    assert "passed" in result.stdout


def test_providers_report_an_unreadable_device_as_nil_not_empty():
    """{} means "no highlights"; nil means "do not ask me". Never the reverse."""
    native = (PLUGIN / "koreader_annotations_provider.lua").read_text()
    kobo = (PLUGIN / "kobo_sqlite_provider.lua").read_text()

    assert "if not Provider.available() then return nil end" in native, (
        "the native provider must report a torn-down reader as unreadable"
    )

    # Both of the Kobo provider's failure exits are inside readAll: the DB that
    # would not open, and the query that raised. Neither may answer with a set.
    read_all = kobo.split("function KoboSqliteProvider.readAll", 1)[1]
    read_all = read_all.split("\nfunction ", 1)[0]
    assert "if not ok then return nil end" in read_all, (
        "a KoboReader.sqlite that will not open has read nothing"
    )
    assert "if not ok2 then return nil end" in read_all, (
        "a query that raised has read nothing"
    )
    assert "return {}" not in read_all, (
        "no failure path in readAll may report an empty device"
    )


def test_authority_comes_from_the_read_not_from_a_capability_flag():
    """The regression shape: `push_all_local` deciding whether a set is known."""
    main = (PLUGIN / "main.lua").read_text()
    logic = (PLUGIN / "sync_logic.lua").read_text()

    assert "function SyncLogic.resolveLocalSet" in logic
    # Treating a provider that raises as unreadable is part of the contract, not
    # incidental hardening: an error is the least trustworthy answer of all.
    resolve = logic.split("function SyncLogic.resolveLocalSet", 1)[1]
    resolve = resolve.split("\nfunction ", 1)[0]
    assert "pcall(provider.readAll" in resolve

    assert "SyncLogic.resolveLocalSet(provider, volume_id)" in main, (
        "the call site must take both the list and its trustworthiness from "
        "the resolver"
    )
    assert "local_set_known = (provider.push_all_local" not in main, (
        "a constant capability flag cannot tell a failed read from an empty "
        "device (#920)"
    )
    # The two consumers that a wrong `known` would turn into data loss.
    assert "local deleted = local_set_known" in main, (
        "deletions must still be gated on the set being known"
    )
    assert "if ok2 and local_set_known then" in main, (
        "the watermark must not be overwritten with a placeholder empty set"
    )
