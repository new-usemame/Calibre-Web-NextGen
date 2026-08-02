# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression test for fork issue #1310.

The "new interface is ready" banner is ``position: fixed`` at the bottom of the
viewport, so ``layout.html`` reserves its measured height at the end of the
caliBlur scroll pane (``.col-sm-10``). That reserve was inert, and a trailing
button sat under the banner: its centre hit-tested to ``#cwng-newui-banner``,
so only a thin top sliver responded to clicks.

Two independent defects, both pinned here because either one alone reproduces
the symptom:

1. **The padding never reserved anything.** caliBlur's content column
   (``div.col-md-10``) is a *float* inside a static ``.discover`` wrapper that
   never clears it, so the wrapper collapses to ``height: 0`` and the pane's
   scrollable extent comes from an overflowing float. A scroll container's
   bottom padding does not extend the scrollable region past overflowing
   floats, so the declaration computed as "applied" while doing nothing —
   measured in Chromium, ``padding-bottom`` of ``0px``, ``63px`` and ``200px``
   all produced the identical ``scrollHeight`` of 1872 and left the button's
   bottom at y855 under a banner starting at y837. The ``::after`` with
   ``clear: both`` puts the padding back into normal flow.

2. **Phones had no reserve at all.** The rule lived inside the
   ``@media (min-width: 768px)`` block that exists for the *toast* offset, so
   below 768px nothing was reserved — and that is the worse case, because the
   banner wraps to ~133px there and covered the whole button rather than its
   bottom edge.

The toast/alert offset stays desktop-only on purpose: on mobile,
``mobile-alert-toast-position.css`` moves toasts to the top, and a bottom
offset would fight that rule. That asymmetry is pinned too, so a future edit
cannot "tidy" the two back into one block in either direction.
"""

import os
import re

import pytest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LAYOUT_HTML = os.path.join(REPO_ROOT, "cps", "templates", "layout.html")

PANE_SELECTOR = (
    "body.cwng-has-newui-banner.blur > div.container-fluid > "
    "div.row-fluid > div.col-sm-10"
)


def _layout_source():
    with open(LAYOUT_HTML, encoding="utf-8") as fh:
        return fh.read()


def _strip_css_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def _media_block_bodies(css, min_width):
    """Return the body text of every ``@media (min-width: <n>px)`` block.

    Brace-matched rather than regex-captured so a nested block cannot make a
    rule look like it escaped the media query when it did not.
    """
    bodies = []
    needle = "@media (min-width: %dpx)" % min_width
    idx = css.find(needle)
    while idx != -1:
        open_brace = css.find("{", idx)
        assert open_brace != -1, "malformed @media block in layout.html"
        depth, i = 0, open_brace
        while i < len(css):
            if css[i] == "{":
                depth += 1
            elif css[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        bodies.append(css[open_brace + 1:i])
        idx = css.find(needle, i)
    return bodies


@pytest.mark.unit
def test_scroll_pane_clears_floats_so_the_reserve_is_not_inert():
    """The ``::after`` clear is what makes ``padding-bottom`` reserve anything."""
    css = _strip_css_comments(_layout_source())
    match = re.search(
        re.escape(PANE_SELECTOR) + r"::after\s*\{([^}]*)\}", css
    )
    assert match, (
        "layout.html must define %s::after — without it the caliBlur content "
        "float is never cleared, the pane's bottom padding reserves nothing, "
        "and a trailing button sits under the banner (fork #1310)" % PANE_SELECTOR
    )
    body = match.group(1)
    assert re.search(r"clear\s*:\s*both", body), (
        "the ::after must declare `clear: both` — that is the entire point of "
        "the rule; a non-clearing pseudo-element does not restore the reserve"
    )
    assert re.search(r"display\s*:\s*block", body), (
        "the ::after must be `display: block` so it participates in flow and "
        "pushes the pane's bottom padding below the float"
    )
    assert re.search(r"content\s*:", body), (
        "the ::after needs a `content` declaration or it is never generated"
    )


@pytest.mark.unit
def test_scroll_pane_reserve_applies_at_every_width():
    """Phones must get the reserve too — there the banner wraps and is taller."""
    css = _strip_css_comments(_layout_source())
    desktop_only = "\n".join(_media_block_bodies(css, 768))

    for rule in (PANE_SELECTOR, "body.cwng-has-newui-banner.blur #scnd-nav"):
        assert rule in css, "layout.html lost the reserve rule for %r" % rule
        assert rule not in desktop_only, (
            "%r must NOT be gated behind @media (min-width: 768px) — below "
            "768px the banner wraps to roughly twice its desktop height and "
            "with no reserve it covers a trailing button entirely, which is "
            "the worst case of fork #1310, not an edge case" % rule
        )


@pytest.mark.unit
def test_toast_offset_stays_desktop_only():
    """Blast-radius pin: the *toast* offset must not follow the reserve out.

    On mobile, ``mobile-alert-toast-position.css`` relocates toasts to the top
    of the screen; a bottom offset there would fight it and push toasts
    off-screen. This is the one rule that genuinely belongs in the media query.
    """
    css = _strip_css_comments(_layout_source())
    desktop_only = "\n".join(_media_block_bodies(css, 768))

    for rule in (
        "body.cwng-has-newui-banner .cwa-toast-stack",
        "body.cwng-has-newui-banner .alert",
    ):
        assert rule in css, "layout.html lost the toast offset rule for %r" % rule
        assert rule in desktop_only, (
            "%r must stay inside @media (min-width: 768px) — mobile moves "
            "toasts to the top and a bottom offset fights that rule" % rule
        )


@pytest.mark.unit
def test_reserve_uses_the_measured_banner_height():
    """The reserve must track the measured height, not a hard-coded number.

    The banner wraps at narrow widths and in longer locales, so any constant
    would be wrong for someone. ``--cwng-banner-gap`` is fed from the script's
    ``offsetHeight`` measurement.
    """
    css = _strip_css_comments(_layout_source())
    match = re.search(
        re.escape(PANE_SELECTOR) + r"\s*\{([^}]*)\}", css
    )
    assert match, "layout.html lost the pane reserve rule entirely"
    assert "var(--cwng-banner-gap)" in match.group(1), (
        "the pane reserve must use var(--cwng-banner-gap) so it tracks the "
        "measured banner height across wrapping and locales"
    )
