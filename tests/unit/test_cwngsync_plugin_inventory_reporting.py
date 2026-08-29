# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""The inventory report must describe the device, not just the current view."""

from pathlib import Path

import pytest


pytestmark = pytest.mark.unit

MAIN = (
    Path(__file__).resolve().parents[2]
    / "koreader" / "plugins" / "cwngsync.koplugin" / "main.lua"
)


def _function(source, name, next_name):
    start = source.index(f"function CWNGSync:{name}")
    stop = source.index(f"function CWNGSync:{next_name}", start)
    return source[start:stop]


def test_inventory_recursively_enumerates_the_configured_library_root():
    source = MAIN.read_text(encoding="utf-8")
    reporter = _function(source, "reportInventory", "refreshLibraryViews")
    scanner = _function(source, "getInventoryBooks", "buildInventory")

    assert "self:getInventoryBooks()" in reporter
    assert "getLibraryBooksForSync" not in reporter
    assert "util.findFiles(root_path" in scanner
    assert "DocumentRegistry.hasProvider" in scanner
    assert "ipairs(candidates)" not in scanner, (
        "Lua ipairs stops at the first nil root candidate and can skip every fallback"
    )


def test_inventory_path_prefix_requires_a_directory_boundary():
    source = MAIN.read_text(encoding="utf-8")
    helper_start = source.index("local function inventoryRelativePath")
    helper_stop = source.index("function CWNGSync:getInventoryBooks", helper_start)
    helper = source[helper_start:helper_stop]

    assert "path:sub(#root_path + 1, #root_path + 1) == \"/\"" in helper
