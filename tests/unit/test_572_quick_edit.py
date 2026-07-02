# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fork #572 — the new UI lacked quick-edit affordances the old one had.

Two focused v1 additions, source-pinned here so they can't be silently removed:

1. A "drop into edit" pencil on book cards (catalog + search results) that jumps
   straight to the edit page without opening the book first.
2. Inline add/remove tags on the book detail page, so you can tweak a book's tags
   without opening the full editor and hand-editing a comma-separated string.

Behavioural coverage is the live Playwright pass; these guard the wiring.
"""
import pathlib

import pytest

_FE = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"

pytestmark = pytest.mark.unit


def test_bookcard_has_quick_edit_affordance():
    src = (_FE / "components" / "BookCard.tsx").read_text()
    # Opt-in prop so shelves/discover rows don't get the control unless asked.
    assert "quickEdit" in src
    # Navigates straight to the edit route (not the detail page).
    assert "/edit" in src
    assert "useLocation" in src or "navigate" in src
    # A pencil icon, consistent with the detail-page Edit button.
    assert "Pencil" in src
    # Must not fire when the card is in multi-select mode.
    assert "!selectable" in src


def test_bookcard_quick_edit_stops_navigation_bubble():
    """The overlay button sits inside the card's <Link>; the click must not also
    navigate to the detail page (preventDefault + stopPropagation), same pattern
    as the existing remove-from-shelf button."""
    src = (_FE / "components" / "BookCard.tsx").read_text()
    # The quick-edit handler guards against the parent link firing.
    assert "e.preventDefault()" in src
    assert "e.stopPropagation()" in src


def test_catalog_wires_quick_edit_gated_on_edit_role():
    src = (_FE / "pages" / "Catalog.tsx").read_text()
    assert "useMe" in src
    assert "quickEdit=" in src
    # Gated on the edit permission, and never shown while selecting.
    assert "role?.edit" in src


def test_book_detail_has_inline_tag_editor():
    src = (_FE / "pages" / "BookDetail.tsx").read_text()
    assert "useUpdateMetadata" in src
    # A dedicated inline tag editor component drives add/remove.
    assert "TagEditor" in src
    # Add path: a text input + an add action.
    assert "Add tag" in src or "addTag" in src
    # Remove path: a per-tag remove control.
    assert "removeTag" in src or "Remove tag" in src


def test_tag_editor_uses_replace_semantics_from_current_tags():
    """Add/remove must rebuild the comma-separated tags string from the book's
    current tags and POST it (the /metadata endpoint has replace semantics), not
    send a lone value that would wipe the rest."""
    src = (_FE / "pages" / "BookDetail.tsx").read_text()
    # Joins names back into the comma-separated string the endpoint expects.
    assert ".join(', ')" in src or '.join(", ")' in src
    # Submits via the tags field of the metadata update.
    assert "tags:" in src
