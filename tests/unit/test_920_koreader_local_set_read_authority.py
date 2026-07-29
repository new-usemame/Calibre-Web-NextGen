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


# Discovered, not listed. A hand-maintained list is how `sync_client_outcome_test`
# came to exist, pass, and be gated by nothing; globbing means a suite added
# later is covered the day it lands rather than the day someone remembers.
LUA_SUITES = sorted(p.name for p in (PLUGIN / "tests").glob("*_test.lua"))


def test_every_lua_suite_is_discovered():
    """Guard the guard: an empty glob would make the runner below vacuous."""
    assert len(LUA_SUITES) >= 4, LUA_SUITES


@pytest.mark.parametrize("suite", LUA_SUITES)
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


def test_the_call_site_consumes_the_decision_rather_than_recomputing_it():
    """The wiring the Lua suites cannot reach.

    ``syncAnnotations`` is a callback inside a 1600-line file with KOReader
    imports, so it cannot be executed here — which is precisely why the bug
    lived there. The behaviour is pinned by ``planLocalContribution`` in the Lua
    suites above; what remains to check is that the call site still *asks* it
    instead of deciding for itself. Both halves are needed: an executable
    contract nobody calls is as useless as a call site nobody tests.
    """
    main = (PLUGIN / "main.lua").read_text()
    logic = (PLUGIN / "sync_logic.lua").read_text()

    assert "function SyncLogic.planLocalContribution" in logic
    assert "SyncLogic.planLocalContribution(" in main, (
        "the call site must take its decision from the resolver"
    )

    # The three fields a wrong answer turns into data loss, each read straight
    # from the plan. Re-deriving any of them locally is the regression.
    assert "local deleted = plan.deletions" in main, (
        "deletions must be the plan's, not recomputed at the call site"
    )
    assert "if ok2 and plan.may_save_watermark then" in main, (
        "the watermark must not be overwritten with a placeholder empty set"
    )
    assert "local localList = plan.list" in main

    # And the negative half: the call site must own none of this. Each of these
    # is a route by which authority could be re-derived locally — which is the
    # bug, in whatever shape it comes back.
    assert "SyncLogic.computeDeletions" not in main, (
        "naming deletions is the resolver's job; computing them at the call "
        "site is how an unreadable device came to delete everything (#920)"
    )
    assert "provider.readAll" not in main, (
        "the device must be read through the resolver, which pcalls it and "
        "rejects a non-table result"
    )
    assert "local_set_known" not in main, (
        "the local authority flag is gone; reintroducing one invites deriving "
        "it from a capability flag again"
    )
