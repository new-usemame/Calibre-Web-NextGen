# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fork #1054 — an option to hide the "Read now" + edit row on book cards.

@Glennza1962: "Can we please have the option of hiding the 'Read Now' and the
edit button in Library view. It makes the main page look messy [...] many users
are reading on their ereaders, so Read Now is redundant (I never use it)."

Behavioural coverage is frontend/e2e/card-actions-toggle.spec.ts, which drives
the real toggle in the browser on desktop and touch. These pin the wiring that a
refactor could quietly drop: the single storage key, the removal (not hiding) of
the row, and the fact that EVERY surface rendering a BookCard honours it — a
missed call site is invisible until a user reports the buttons are still there
on shelves.
"""
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FE = _ROOT / "frontend" / "src"

pytestmark = pytest.mark.unit

# Every surface that renders a BookCard, mapped to the exact expression it must
# hand down. Asserting only that the string "hideActions" appears would pass on
# `hideActions={false}` or a stale constant — the wiring, not its name, is what
# a regression would break, and the e2e only drives the catalog grid.
#
# Pages own the state and pass the live hook value; the two rail components take
# it as a prop and forward it unchanged.
_CARD_SURFACES = {
    ("pages", "Catalog.tsx"): "hideActions={cardActionsHidden}",
    ("pages", "Shelf.tsx"): "hideActions={cardActionsHidden}",
    ("pages", "MagicShelfView.tsx"): "hideActions={cardActionsHidden}",
    ("pages", "AdvancedSearch.tsx"): "hideActions={cardActionsHidden}",
    ("pages", "BookDetail.tsx"): "hideActions={cardActionsHidden}",
    ("components", "DiscoverSection.tsx"): "hideActions={hideActions}",
    ("components", "MoreByAuthor.tsx"): "hideActions={hideActions}",
}

# Only these five own the preference; the rails receive it.
_STATE_OWNERS = tuple(k for k in _CARD_SURFACES if k[0] == "pages")


def test_preference_key_has_exactly_one_definition():
    """The key is the contract between the toggle and seven consumers. Spelled
    twice, a rename fixes the writer and strands every reader on the old value."""
    hook = (_FE / "lib" / "useCardActionsHidden.ts").read_text()
    assert "'cwng:card-actions-hidden-v1'" in hook

    literal_elsewhere = [
        path
        for path in _FE.rglob("*.ts*")
        if path.name != "useCardActionsHidden.ts"
        and "cwng:card-actions-hidden-v1" in path.read_text()
    ]
    assert literal_elsewhere == [], (
        "the storage key must only be spelled in useCardActionsHidden.ts; "
        f"also found in {[p.name for p in literal_elsewhere]}"
    )


def test_preference_defaults_to_showing_the_row():
    """Off-by-default: an upgrade must not silently remove controls from users
    who never asked for that."""
    hook = (_FE / "lib" / "useCardActionsHidden.ts").read_text()
    assert "usePersistentBool(CARD_ACTIONS_HIDDEN_KEY, false)" in hook


def test_bookcard_removes_the_row_rather_than_hiding_it():
    """`opacity: 0` is how the hover-reveal works, and it leaves a focusable
    link behind. A user who switched these off must not keep tabbing through two
    invisible controls per card, so the row is not rendered at all."""
    src = (_FE / "components" / "BookCard.tsx").read_text()
    assert "hideActions" in src
    assert "const hasActionRow = !hideActions && (Boolean(readTarget) || quickEdit);" in src


def test_every_book_card_surface_passes_the_live_preference():
    """A BookCard rendered by a surface that never passes `hideActions` keeps its
    buttons, so the preference looks broken on exactly that page — and the e2e
    only covers the catalog grid, so nothing else would catch it."""
    wrong = []
    for parts, expected in _CARD_SURFACES.items():
        src = (_FE.joinpath(*parts)).read_text()
        if expected not in src:
            wrong.append(f"{parts[-1]} (expected `{expected}`)")
    assert wrong == [], f"these render a BookCard without the live preference: {wrong}"


def test_state_owners_read_the_shared_hook():
    """Pinned separately from the pass-down: a page could name the right variable
    while seeding it from something other than the shared hook, which is how the
    seven surfaces would drift apart again."""
    missing = [
        parts[-1]
        for parts in _STATE_OWNERS
        if "useCardActionsHidden()" not in (_FE.joinpath(*parts)).read_text()
    ]
    assert missing == [], f"these pass hideActions but never read the hook: {missing}"


def test_no_surface_renders_bookcard_without_being_in_the_list():
    """Guards the list above from going stale as new card surfaces are added."""
    known = {name for _, name in _CARD_SURFACES} | {"BookCard.tsx"}
    # Test/story files render BookCard as a fixture, not as a user-facing
    # surface, so they must not read as a missed call site.
    renderers = {
        path.name
        for path in _FE.rglob("*.tsx")
        if not any(path.name.endswith(suffix)
                   for suffix in (".test.tsx", ".spec.tsx", ".stories.tsx"))
        and "<BookCard" in path.read_text()
    }
    assert renderers <= known, (
        f"new BookCard surface(s) {sorted(renderers - known)} — add to _CARD_SURFACES "
        "and pass hideActions, or the #1054 preference silently misses them"
    )


def test_toggle_is_exposed_in_catalog_view_settings():
    """The preference needs a reachable control, in the popover that already
    owns the other per-view settings."""
    src = (_FE / "pages" / "Catalog.tsx").read_text()
    assert 'data-testid="show-card-actions"' in src
    # Checked == shown, so the checkbox reads as "Show ...", not "Hide ...".
    assert "checked={!cardActionsHidden}" in src
    assert "t('Show Read now and edit buttons')" in src


def test_spa_only_msgid_is_anchored_for_extraction():
    """pybabel does not scan .tsx, so an SPA-only string must be referenced from
    Python or msgmerge marks its translations obsolete and the UI falls back to
    English (the #577 failure)."""
    anchors = (_ROOT / "cps" / "spa_strings.py").read_text()
    assert '_("Show Read now and edit buttons")' in anchors
