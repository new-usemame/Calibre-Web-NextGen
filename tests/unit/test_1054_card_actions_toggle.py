# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fork #1054 — the account preference for cover action disclosures.

Browser tests verify the actual dialog and persisted hide/show behavior. These
small checks protect the shared account preference and its forwarding to every
BookCard surface without pinning the retired inline action-row implementation.
"""
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FE = _ROOT / "frontend" / "src"

pytestmark = pytest.mark.unit

_CARD_SURFACES = {
    ("pages", "Catalog.tsx"): "hideActions={cardActionsHidden}",
    ("pages", "Shelf.tsx"): "hideActions={cardActionsHidden}",
    ("pages", "MagicShelfView.tsx"): "hideActions={cardActionsHidden}",
    ("pages", "AdvancedSearch.tsx"): "hideActions={cardActionsHidden}",
    ("pages", "BookDetail.tsx"): "hideActions={cardActionsHidden}",
    ("pages", "GlobalLibrary.tsx"): "hideActions={cardActionsHidden}",
    ("components", "DiscoverSection.tsx"): "hideActions={hideActions}",
    ("components", "MoreByAuthor.tsx"): "hideActions={hideActions}",
}
_STATE_OWNERS = tuple(path for path in _CARD_SURFACES if path[0] == "pages")


def test_preference_is_server_backed_and_defaults_to_showing_actions():
    hook = (_FE / "lib" / "useCardActionsHidden.ts").read_text()
    assert "useNamedPreference(" in hook
    assert "'card_actions_hidden'" in hook
    assert "CARD_ACTIONS_HIDDEN_KEY" in hook
    assert "false" in hook


def test_preference_storage_key_has_one_definition():
    hook = (_FE / "lib" / "useCardActionsHidden.ts").read_text()
    assert "'cwng:card-actions-hidden-v1'" in hook
    duplicates = [
        path for path in _FE.rglob("*.ts*")
        if path.name != "useCardActionsHidden.ts"
        and "cwng:card-actions-hidden-v1" in path.read_text()
    ]
    assert duplicates == []


def test_every_book_card_surface_passes_the_live_preference():
    wrong = []
    for parts, expected in _CARD_SURFACES.items():
        if expected not in _FE.joinpath(*parts).read_text():
            wrong.append(f"{parts[-1]} (expected `{expected}`)")
    assert wrong == []


def test_state_owners_read_the_shared_hook():
    missing = [
        parts[-1] for parts in _STATE_OWNERS
        if "useCardActionsHidden(" not in _FE.joinpath(*parts).read_text()
    ]
    assert missing == []


def test_no_book_card_surface_is_missing_from_the_preference_map():
    # CcBrowse has its own real browser preference oracle.
    known = {name for _, name in _CARD_SURFACES} | {"BookCard.tsx", "CcBrowse.tsx"}
    renderers = {
        path.name for path in _FE.rglob("*.tsx")
        if not any(path.name.endswith(suffix)
                   for suffix in (".test.tsx", ".spec.tsx", ".stories.tsx"))
        and "<BookCard" in path.read_text()
    }
    assert renderers <= known, f"unlisted BookCard surface(s): {sorted(renderers - known)}"


def test_toggle_is_exposed_in_catalog_view_settings():
    catalog = (_FE / "pages" / "Catalog.tsx").read_text()
    assert 'data-testid="show-card-actions"' in catalog
    assert "checked={!cardActionsHidden}" in catalog
    assert "t('Show Read now and edit buttons')" in catalog


def test_touch_add_action_and_status_badges_keep_their_minimum_size():
    """The action-panel change must not shrink Global Library's primary Add
    control or the existing read/series badges on touch layouts."""
    css = " ".join((_FE / "components" / "BookCard.module.css").read_text().split())
    assert "@media (hover: none), (pointer: coarse)" in css
    assert ".addToLibrary { opacity: 1; pointer-events: auto;" in css
    assert ".addToLibrary { display: flex; min-height: 44px; }" in css
    assert ".readBadge, .readingBadge, .hiddenBadge, .libraryBadge { min-height: 28px;" in css
    assert ".seriesBadge { min-width: 28px; height: 28px;" in css


def test_action_dialog_strings_are_extractable_and_old_disclosure_is_retired():
    anchors = (_ROOT / "cps" / "spa_strings.py").read_text()
    assert '_("Actions for {title}")' in anchors
    assert '_("More actions for {title}")' not in anchors
    assert '_("Read now")' in anchors
    assert '_("Edit")' in anchors
    assert "_('Close')" in anchors
