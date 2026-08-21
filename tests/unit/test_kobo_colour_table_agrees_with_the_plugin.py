# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""The server and the KOReader plugin must agree on Kobo's colour integers.

There are two copies of Kobo's `Bookmark.Color` table in this repository, in two
languages, and they are the same fact:

    cps/services/annotation_colors.py                     (Python, canonical)
    koreader/plugins/cwasync.koplugin/kobo_sqlite_provider.lua   (Lua)

They drifted, and the drift was invisible because each side had its own tests
agreeing with itself. The Lua copy called 1 "red" (a Kobo has no red), had 2 and
3 swapped so every green highlight was written to the device as blue and every
blue one as green, and had no entry for 4 at all — and 4 is what a greyscale
device writes for EVERY organic highlight, so on a Clara BW every highlight ever
made came back as yellow. The plugin's own Lua suite asserted all three of those,
which is why nothing caught it.

The measured table (Clara BW, firmware 4.45.23792, finding F-5769c9) is in the
Python module's docstring. This test makes the Lua file answer to it.

Parsing Lua with a regex is crude, and deliberately so: the alternative is a Lua
interpreter dependency for a check that is really "do these five pairs match".
The parse is guarded — if it stops finding a table, the test fails rather than
passing over an empty comparison.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
PLUGIN_LUA = REPO / "koreader" / "plugins" / "cwasync.koplugin" / "kobo_sqlite_provider.lua"


def _lua_table(body: str, name: str) -> str:
    match = re.search(name + r"\s*=\s*\{(.*?)\}", body, re.S)
    assert match, f"could not find the Lua table {name} in {PLUGIN_LUA.name}"
    return match.group(1)


def _lua_name_to_int() -> dict[str, int]:
    body = PLUGIN_LUA.read_text(encoding="utf-8")
    table = _lua_table(body, "COLOR_NAME_TO_INT")
    pairs = dict(
        (key, int(value))
        for key, value in re.findall(r"(\w+)\s*=\s*(\d+)", table)
    )
    assert pairs, "COLOR_NAME_TO_INT parsed as empty"
    return pairs


def _lua_int_to_name() -> dict[int, str]:
    body = PLUGIN_LUA.read_text(encoding="utf-8")
    table = _lua_table(body, "COLOR_INT_TO_NAME")
    pairs = dict(
        (int(key), value)
        for key, value in re.findall(r"\[(\d+)\]\s*=\s*\"(\w+)\"", table)
    )
    assert pairs, "COLOR_INT_TO_NAME parsed as empty"
    return pairs


def _server_int_to_name() -> dict[int, str]:
    from cps.services import annotation_colors

    return {
        code: annotation_colors.to_display_name(hexval)
        for code, hexval in annotation_colors.KOBO_BOOKMARK_COLOR_HEX.items()
    }


def test_the_parse_finds_a_real_table():
    """Vacuity guard: an empty parse would make every comparison below trivial."""
    assert len(_lua_int_to_name()) >= 5, _lua_int_to_name()
    assert len(_lua_name_to_int()) >= 5, _lua_name_to_int()


def test_the_plugin_reads_every_kobo_colour_the_server_knows():
    server = _server_int_to_name()
    plugin = _lua_int_to_name()
    assert len(server) == 5, server
    assert plugin == server, (
        "the plugin's Bookmark.Color -> name table disagrees with "
        "cps/services/annotation_colors.py. A device highlight would be "
        "reported as the wrong colour.\n"
        f"  plugin: {plugin}\n  server: {server}"
    )


def test_the_plugin_writes_every_colour_back_to_the_same_integer():
    server = _server_int_to_name()
    plugin = _lua_name_to_int()
    for code, name in server.items():
        assert plugin.get(name) == code, (
            f"the plugin writes {name!r} as Bookmark.Color {plugin.get(name)}, "
            f"but the device uses {code}. A highlight made in {name} would "
            f"appear on the device in another colour."
        )


def test_grey_is_present_because_a_greyscale_device_writes_only_grey():
    """The omission that mattered most, pinned on its own.

    A Clara BW writes Color=4 for every organic highlight. While 4 was missing,
    every highlight on such a device read back as yellow.
    """
    assert _lua_int_to_name().get(4) == "grey"
    assert _lua_name_to_int().get("grey") == 4


def test_the_plugin_never_claims_a_kobo_can_store_red():
    """Kobo has no red; only the web reader offers it.

    Reading 1 as "red" is the specific mistake that made pink highlights arrive
    as a colour no device can produce.
    """
    assert "red" not in _lua_int_to_name().values(), (
        "the plugin reports a Bookmark.Color as red; a Kobo cannot store red, "
        "and 1 is pink"
    )
