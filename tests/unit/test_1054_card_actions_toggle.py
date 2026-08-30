# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fork #1054 — an option to hide the "Read now" + edit row on book cards.

@Glennza1962: "Can we please have the option of hiding the 'Read Now' and the
edit button in Library view. It makes the main page look messy [...] many users
are reading on their ereaders, so Read Now is redundant (I never use it)."

Behavioural coverage is frontend/e2e/card-actions-toggle.spec.ts, which drives
the real toggle in the browser on desktop and touch. The separate
book-card-actions.spec.ts pins the 2026-08-29 ruling that redundant actions use
one visible disclosure on coarse pointers. These pin the wiring
that a refactor could quietly drop: the single storage key, the removal (not
hiding) of the row, and the fact that EVERY surface rendering a BookCard honours
it — a missed call site is invisible until a user reports the buttons are still
there on shelves.
"""
import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FE = _ROOT / "frontend" / "src"
_E2E = _ROOT / "frontend" / "e2e"

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
    ("pages", "GlobalLibrary.tsx"): "hideActions={cardActionsHidden}",
    ("components", "DiscoverSection.tsx"): "hideActions={hideActions}",
    ("components", "MoreByAuthor.tsx"): "hideActions={hideActions}",
}

# Only these five own the preference; the rails receive it.
_STATE_OWNERS = tuple(k for k in _CARD_SURFACES if k[0] == "pages")

_COARSE_MEDIA = "(any-hover: none), (any-pointer: coarse)"



def _media_block(css: str, opener: str) -> str:
    """Return the body of one @media block, matched by braces.

    Deliberately NOT a `split` on some inner token: an earlier version of this
    test sliced the block at ".moreActionsWrap" and then asserted that the same
    token was present in the slice, which can never hold. The assertion was
    unsatisfiable regardless of the CSS, so it reported a defect in correct
    production code. Brace-match the block so the extraction is independent of
    whatever the caller is about to assert.
    """
    start = css.index(opener) + len(opener)
    depth = 1
    for i in range(start, len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[start:i]
    raise AssertionError("unterminated media block for {!r}".format(opener))


def _css_rules(css: str):
    """Return ordinary CSS rules with their media context and source order.

    This is intentionally a small cascade reader, not a CSS validator.  The
    card-action regression happened because a test inspected one media block
    in isolation and ignored a later, equally-specific rule.  Keeping source
    order and media context makes these assertions exercise the declaration
    that actually wins.
    """
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    rules = []

    def walk(fragment: str, media=()):
        cursor = 0
        while True:
            opening = fragment.find("{", cursor)
            if opening == -1:
                return
            header = fragment[cursor:opening].strip()
            depth = 1
            closing = opening + 1
            while closing < len(fragment) and depth:
                if fragment[closing] == "{":
                    depth += 1
                elif fragment[closing] == "}":
                    depth -= 1
                closing += 1
            if depth:
                raise AssertionError(f"unterminated CSS block after {header!r}")

            body = fragment[opening + 1:closing - 1]
            if header.startswith("@media "):
                walk(body, media + (header.removeprefix("@media ").strip(),))
            elif header.startswith("@"):  # e.g. @container; retain media context
                walk(body, media)
            elif header:
                declarations = {
                    name.strip(): value.strip()
                    for name, value in re.findall(
                        r"(?:^|;)\s*([\w-]+)\s*:\s*([^;{}]+)", body
                    )
                }
                rules.append((tuple(s.strip() for s in header.split(",")), declarations, media))
            cursor = closing

    walk(css)
    return rules


def _effective_class_property(css: str, class_name: str, prop: str, *, coarse: bool):
    """Resolve a property for a plain class selector in the requested mode."""
    requested_media = {(), (_COARSE_MEDIA,)} if coarse else {()}
    winner = None
    for order, (selectors, declarations, media) in enumerate(_css_rules(css)):
        if media not in requested_media or prop not in declarations:
            continue
        if f".{class_name}" not in selectors:
            continue
        # Every accepted selector is one class, so specificity is equal and
        # the later declaration wins exactly as it does in the production bug.
        winner = (declarations[prop], order, media)
    return winner


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


def test_preference_is_server_backed_and_defaults_to_showing_the_row():
    """Off-by-default: an upgrade must not silently remove controls from users
    who never asked for that; the shared hook supplies account persistence."""
    hook = (_FE / "lib" / "useCardActionsHidden.ts").read_text()
    assert "useNamedPreference(" in hook
    assert "'card_actions_hidden'" in hook
    assert "CARD_ACTIONS_HIDDEN_KEY" in hook
    assert "false" in hook


def test_bookcard_removes_the_row_rather_than_hiding_it():
    """`opacity: 0` is how the hover-reveal works, and it leaves a focusable
    link behind. A user who switched these off must not keep tabbing through two
    invisible controls per card, so the row is not rendered at all."""
    src = (_FE / "components" / "BookCard.tsx").read_text()
    assert "hideActions" in src
    assert "const hasAddAction = membership === 'unowned' && !!onAddToLibrary;" in src
    assert "const hasActionRow = hasAddAction || (!hideActions && (Boolean(readTarget) || quickEdit));" in src


def test_coarse_pointer_card_actions_use_a_visible_disclosure():
    """Touch must never hit an opacity-hidden live action.

    The coarse layout removes the legacy controls from layout and exposes one
    named 44px disclosure; fine pointers keep the established hover controls.
    """
    src = (_FE / "components" / "BookCard.tsx").read_text()
    css = (_FE / "components" / "BookCard.module.css").read_text()
    coarse = _media_block(css, "@media (any-hover: none), (any-pointer: coarse) {")

    assert ".moreActionsTrigger" in css
    assert "width: 44px;" in css and "height: 44px;" in css
    assert "aria-expanded={actionsOpen}" in src
    assert "aria-controls={actionsOpen ? actionsPanelId : undefined}" in src
    # `aria-haspopup=true` promises a menu, but this is an ordinary disclosure
    # containing links and a button in a labelled group. Expanded/controls is
    # the correct screen-reader contract without a false popup role promise.
    assert 'aria-haspopup="true"' not in src
    assert "t('More actions for {title}'" in src
    assert "t('Actions for {title}'" in src
    assert 'role="group"' in src

    for selector in (
        ".wrap:hover .removeBtn",
        ".wrap:focus-within .removeBtn",
        ".removeBtn:focus-visible",
        ".wrap:hover .quickEditBtn",
        ".wrap:focus-within .quickEditBtn",
        ".quickEditBtn:focus-visible",
        ".wrap:hover .readNow",
        ".wrap:focus-within .readNow",
        ".readNow:focus-visible",
    ):
        assert selector in css


def test_coarse_pointer_hide_rule_names_the_real_action_classes():
    """The coarse rule must hide the controls, not an unrelated class name.

    Validate both sides of the CSS-module contract: the rule names the three
    concrete controls, and every class it hides is referenced by BookCard.
    """
    src = (_FE / "components" / "BookCard.tsx").read_text()
    css = (_FE / "components" / "BookCard.module.css").read_text()
    hidden = set()
    for selectors, declarations, media in _css_rules(css):
        if media == (_COARSE_MEDIA,) and declarations.get("display") == "none":
            hidden.update(selector.removeprefix(".") for selector in selectors
                          if re.fullmatch(r"\.[A-Za-z_][\w-]*", selector))

    assert hidden == {"readNow", "removeBtn", "quickEditBtn"}, (
        "coarse-pointer hiding must target the three concrete BookCard controls; "
        f"found {sorted(hidden)}"
    )
    unreferenced = sorted(name for name in hidden if f"styles.{name}" not in src)
    assert unreferenced == [], f"CSS hides classes BookCard never renders: {unreferenced}"


@pytest.mark.parametrize("class_name", ("readNow", "removeBtn", "quickEditBtn"))
def test_coarse_pointer_hides_each_action_in_the_effective_cascade(class_name):
    css = (_FE / "components" / "BookCard.module.css").read_text()
    winner = _effective_class_property(css, class_name, "display", coarse=True)
    assert winner is not None and winner[0] == "none", (
        f".{class_name} must resolve to display:none on coarse pointers; winner={winner}"
    )


def test_coarse_pointer_disclosure_wins_the_effective_cascade():
    css = (_FE / "components" / "BookCard.module.css").read_text()
    winner = _effective_class_property(css, "moreActionsWrap", "display", coarse=True)
    assert winner is not None and winner[0] == "block", (
        ".moreActionsWrap must resolve to display:block on coarse pointers; "
        f"winner={winner}"
    )


@pytest.mark.parametrize("class_name", ("readNow", "removeBtn", "quickEditBtn"))
def test_hover_revealed_actions_do_not_disable_pointer_events_at_rest(class_name):
    """Actionability hit-testing happens before Playwright synthesizes hover.

    Coarse pointers remove these controls from layout, so disabling hit-testing
    in the fine-pointer rest state has no remaining job and makes real clicks
    race the hover reveal.
    """
    css = (_FE / "components" / "BookCard.module.css").read_text()
    winner = _effective_class_property(css, class_name, "pointer-events", coarse=False)
    assert winner is None or winner[0] == "auto", (
        f".{class_name} must retain normal pointer hit-testing at rest; winner={winner}"
    )


def test_coarse_pointer_reversal_does_not_touch_primary_actions_or_badges():
    css = (_FE / "components" / "BookCard.module.css").read_text()
    coarse = css.split("@media (any-hover: none), (any-pointer: coarse) {", 1)[1] \
        .split("/* Narrow cards", 1)[0]
    assert ".addToLibrary {\n  opacity: 1;" in css
    assert ".readBadge, .readingBadge, .hiddenBadge, .libraryBadge" in coarse
    assert ".seriesBadge { min-width: 28px; height: 28px;" in coarse


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
        if "useCardActionsHidden(" not in (_FE.joinpath(*parts)).read_text()
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


def test_real_touch_helper_scrolls_before_sampling_raw_coordinates():
    """A raw touchscreen coordinate is viewport-relative and never auto-scrolls.

    Sampling an off-screen disclosure's box made the gesture miss while still
    looking like a real tap in the trace.  Require the helper to bring the
    target into the viewport before it samples the centre point.
    """
    src = (_E2E / "book-card-actions.spec.ts").read_text()
    helper = src[src.index("async function tap("):src.index("async function expectRevealed(")]
    scroll = helper.find("await locator.scrollIntoViewIfNeeded();")
    sample = helper.find("const box = await locator.boundingBox();")
    assert 0 <= scroll < sample, (
        "the real-touch helper must scroll the target before sampling its raw coordinates"
    )


def test_disclosure_stacking_stays_below_global_overlays_until_open():
    """The active card, not merely its child panel, must clear sibling cards."""
    src = (_FE / "components" / "BookCard.tsx").read_text()
    css = (_FE / "components" / "BookCard.module.css").read_text()
    closed = _effective_class_property(css, "moreActionsWrap", "z-index", coarse=True)
    opened = _effective_class_property(css, "wrapActionsOpen", "z-index", coarse=True)

    assert closed is not None and closed[0] == "1", (
        "a closed per-card trigger must remain in the card stacking layer; "
        f"winner={closed}"
    )
    assert opened is not None and opened[0] == "calc(var(--z-bar) - 1)", (
        "an open card disclosure must clear sibling cards without outranking "
        f"global overlays; winner={opened}"
    )
    assert "actionsOpen ? styles.wrapActionsOpen" in src
    assert ".wrap.wrapActionsOpen { content-visibility: visible; }" in css


def test_touch_palette_coverage_measures_the_visible_disclosure_action():
    src = (_E2E / "book-card-actions.spec.ts").read_text()
    assert "Touch quick-edit disclosure" in src
    assert "getByRole('group', { name: /^Actions for / })" in src
    assert "getByRole('link', { name: /^Edit / })" in src


def test_fine_pointer_action_row_geometry_is_not_run_against_removed_touch_row():
    src = (_E2E / "card-action-row.spec.ts").read_text()
    overlap = (_E2E / "card-action-overlap.spec.ts").read_text()
    marker = "test.skip(isTouchProject(), 'fine-pointer action-row geometry');"
    # One parametrized density test plus two standalone geometry tests.
    assert src.count(marker) == 3
    assert marker in overlap


def test_mobile_edit_flows_enter_through_the_disclosure():
    src = (_E2E / "catalog-edit-retains-book.spec.ts").read_text()
    assert "async function openQuickEdit(" in src
    assert "getByRole('button', { name: /^More actions for / })" in src
    assert src.count("await openQuickEdit(page);") == 2


def test_preference_availability_probe_uses_the_active_pointer_contract():
    src = (_E2E / "discover-preference.spec.ts").read_text()
    assert "async function expectCardActionsAvailable(" in src
    assert "getByRole('button', { name: /^More actions for / })" in src
    assert src.count("await expectCardActionsAvailable(") == 2


def test_touch_target_size_coverage_measures_disclosed_actions():
    src = (_E2E / "target-size-sc258.spec.ts").read_text()
    assert "Touch card More actions trigger" in src
    assert "Touch card Read now disclosure action" in src
    assert "Touch card Edit disclosure action" in src
    assert "Touch card Remove disclosure action" in src


def test_horizontal_card_rails_release_clipping_while_a_disclosure_is_open():
    discover = (_FE / "components" / "DiscoverSection.module.css").read_text()
    author = (_FE / "components" / "MoreByAuthor.module.css").read_text()
    state = ':has([aria-expanded="true"])'
    assert f".box{state}" in discover
    assert f".strip{state}" in discover
    assert f".strip{state}" in author
