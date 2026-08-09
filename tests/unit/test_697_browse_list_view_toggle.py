# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_shared_browse_list_has_persisted_accessible_view_toggle():
    src = (ROOT / "frontend/src/pages/BrowseList.tsx").read_text()
    assert "usePersistentBool('cwng:browse-list-compact'" in src
    assert "aria-pressed={!compact}" in src
    assert "aria-pressed={compact}" in src
    # The invariant is that `compact` chooses the list class and the other
    # branch is a grid class — not one exact expression. #1396 moved the grid
    # side into a `gridClass` local (the track widens where the #973 row
    # actions render), which is a correct change this pin used to fail on.
    assert re.search(r"compact \? styles\.list : \w+", src)
    assert "styles.grid" in src
    assert 'role="list"' in src


@pytest.mark.unit
def test_compact_rows_keep_mobile_touch_target():
    css = (ROOT / "frontend/src/pages/BrowseList.module.css").read_text()
    assert ".list .item" in css
    assert "min-height: 40px" in css
