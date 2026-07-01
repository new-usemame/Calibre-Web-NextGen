# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression pin for the #60 feedback popup being undismissable (reported in #576).

Root cause: the popup overlay is hidden via the HTML ``hidden`` attribute, but the
partial's own stylesheet declares ``.cwng-fb-overlay { display:flex; ... }``. Author
CSS overrides the UA/normalize ``[hidden] { display:none }`` rule, so the overlay
rendered on EVERY classic page (the partial ships in layout.html unconditionally for
logged-in users; the JS only decides whether to attach listeners). With no
``?cwng_feedback`` marker the script returns early, so no dismiss listener was ever
attached — and even with the marker, ``close()`` sets ``overlay.hidden = true``,
which the ``display:flex`` rule visually ignored. Result: a permanent full-screen
modal no button could close.

Fix: an explicit ``.cwng-fb-overlay[hidden] { display:none !important }`` guard makes
the ``hidden`` attribute authoritative, and the card gets a viewport-bounded
max-height + own scroll so it fits small (mobile) screens.
"""
import pathlib
import re

import pytest

TEMPLATE = (pathlib.Path(__file__).resolve().parents[2]
            / "cps" / "templates" / "cwng_feedback_popup.html").read_text()


def _rule_blocks(css_or_html):
    """Yield (selector, declarations) for every CSS rule in the text."""
    for m in re.finditer(r"([^{}]+)\{([^}]*)\}", css_or_html):
        yield m.group(1).strip(), m.group(2)


def _block_for(selector_contains):
    for sel, block in _rule_blocks(TEMPLATE):
        if selector_contains in sel:
            return sel, block
    return None, None


@pytest.mark.unit
def test_hidden_attribute_actually_hides_the_overlay():
    """THE regression: .cwng-fb-overlay sets display:flex, which silently defeats
    the [hidden] attribute unless an explicit [hidden] override exists."""
    sel, block = _block_for(".cwng-fb-overlay[hidden]")
    assert block is not None, (
        "missing '.cwng-fb-overlay[hidden]' rule — the hidden attribute is "
        "overridden by the overlay's display:flex and the popup can never be "
        "dismissed (#576)")
    assert re.search(r"display\s*:\s*none\s*!important", block), (
        "'.cwng-fb-overlay[hidden]' must set display:none !important so the "
        "hidden attribute stays authoritative over the display:flex base rule")


@pytest.mark.unit
def test_overlay_base_rule_still_uses_display_flex():
    """Companion pin: the base rule keeps display:flex (centering), which is WHY
    the [hidden] guard above must exist. If someone drops flex for a different
    show/hide mechanism, both tests flag the area for a rethink together."""
    sel, block = None, None
    for s, b in _rule_blocks(TEMPLATE):
        # the first rule's captured selector includes the <style> tag text, so
        # match on the selector's tail rather than exact equality
        if s.endswith(".cwng-fb-overlay"):
            sel, block = s, b
            break
    assert block is not None, "base .cwng-fb-overlay rule not found"
    assert "display:flex" in block.replace(" ", ""), (
        "base overlay rule no longer uses display:flex — revisit the [hidden] "
        "guard pinned by test_hidden_attribute_actually_hides_the_overlay")


@pytest.mark.unit
def test_card_fits_small_viewports():
    """#576 mobile symptom: the card (707px tall on a 720px viewport at desktop
    width) overflowed small screens with no way to scroll it. The card must be
    viewport-bounded and scroll its own overflow."""
    sel, block = _block_for(".cwng-fb-card")
    assert block is not None, "base .cwng-fb-card rule not found"
    compact = block.replace(" ", "")
    assert re.search(r"max-height:calc\(100(d?)vh-", compact), (
        ".cwng-fb-card needs a viewport-relative max-height so it cannot "
        "overflow small screens")
    assert "overflow-y:auto" in compact, (
        ".cwng-fb-card must scroll its own overflow (overflow-y:auto)")
