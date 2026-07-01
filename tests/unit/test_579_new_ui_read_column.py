# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Acceptance tests for fork #579 — the new UI's read/unread checkmark was missing
for admins who link read status to a Calibre column (config_read_column).

Root cause: for the custom-column path, generate_linked_query selects
`read_column.value` (Row attr `value`), but `_row_to_item` only read
`read_status`, so the read flag was always False and no badge ever rendered. The
read/unread filter and Discover's unread filter also no-op'd for custom columns.

These pin the fix on both paths (custom column + built-in ub.ReadBook) so a
regression can't silently drop the badge again.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit

BOOKS_PY = Path(__file__).resolve().parents[2] / "cps" / "api" / "books.py"


def _inner():
    return SimpleNamespace(id=1, title="A", series_index="1.0", has_cover=1,
                           authors=[SimpleNamespace(name="Auth")], series=[],
                           data=[SimpleNamespace(format="EPUB")])


# ── read badge: custom column path (the reported regression) ─────────────────
def test_read_badge_custom_column_truthy_is_read():
    from cps.api import books as books_mod
    with patch.object(books_mod.config, "config_read_column", 5, create=True):
        # generate_linked_query aliases the custom column as `.value`
        row = SimpleNamespace(Books=_inner(), is_archived=False, value=1)
        item = books_mod._row_to_item(row)
    assert item["read"] is True


def test_read_badge_custom_column_falsy_is_unread():
    from cps.api import books as books_mod
    with patch.object(books_mod.config, "config_read_column", 5, create=True):
        row = SimpleNamespace(Books=_inner(), is_archived=False, value=None)
        item = books_mod._row_to_item(row)
    assert item["read"] is False


def test_read_badge_custom_column_ignores_stale_read_status_attr():
    """With a custom column configured, a leftover read_status attr must NOT be
    used — only the custom column's value decides."""
    from cps.api import books as books_mod
    from cps import ub
    with patch.object(books_mod.config, "config_read_column", 5, create=True):
        row = SimpleNamespace(Books=_inner(), is_archived=False, value=0,
                              read_status=ub.ReadBook.STATUS_FINISHED)
        item = books_mod._row_to_item(row)
    assert item["read"] is False


# ── read badge: built-in path still works (no regression) ────────────────────
def test_read_badge_builtin_column_still_works():
    from cps.api import books as books_mod
    from cps import ub
    with patch.object(books_mod.config, "config_read_column", 0, create=True):
        row = SimpleNamespace(Books=_inner(), is_archived=False,
                              read_status=ub.ReadBook.STATUS_FINISHED)
        assert books_mod._row_to_item(row)["read"] is True
        row2 = SimpleNamespace(Books=_inner(), is_archived=False, read_status=None)
        assert books_mod._row_to_item(row2)["read"] is False


# ── read/unread filter honors the custom column (source-pin) ──────────────────
def _func_src(name: str) -> str:
    tree = ast.parse(BOOKS_PY.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(BOOKS_PY.read_text(), node)
    raise AssertionError(f"{name} not found in books.py")


def test_read_filter_custom_column_no_longer_noop():
    src = _func_src("_build_read_filter")
    assert "not yet supported" not in src, "custom read column filter must be implemented"
    assert "cc_classes[config.config_read_column].value" in src, (
        "custom-column read filter must query the linked column's value"
    )
    # both directions covered
    assert re.search(r"filter_val\s*==\s*[\"']read[\"']", src)
    assert re.search(r"filter_val\s*==\s*[\"']unread[\"']", src)


def test_discover_unread_filter_handles_custom_column():
    src = BOOKS_PY.read_text()
    # The discover branch must filter unread via the custom column, not fall back to
    # showing every book when a read column is configured.
    assert src.count("cc_classes[config.config_read_column].value") >= 2, (
        "both the read/unread filter and the discover unread filter must use the "
        "custom column value"
    )
