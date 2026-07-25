# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression pins for fork issue #1089 — visible scrollbars in Firefox >= 153.

Background
----------
The SPA styled scrollbars exclusively through the legacy ``::-webkit-scrollbar``
pseudo-elements. Those were a WebKit/Blink extension that Firefox ignored, so
Firefox users got the platform's native scrollbars: on macOS and Android those
are *overlay* scrollbars, which fade out when idle and take no layout space.

Firefox 153 started honouring ``::-webkit-scrollbar``. Any browser that honours
it switches the affected scroller from an overlay scrollbar to a **classic**
one that is always visible while the content overflows and permanently steals
its width from the content box. Overnight, every Firefox user got persistent
scrollbars on the window, the sidebar and the Discover strip (#1089).

Measured in Firefox 153 with overlay scrollbars enabled
(``ui.useOverlayScrollbars=1``, i.e. the reporter's configuration), against the
live SPA:

===========================================  ==========  =========
scroller                                     as shipped  with fix
===========================================  ==========  =========
sidebar ``nav``                                 15 px       0 px
Discover / MoreByAuthor ``.strip``              11 px       0 px
===========================================  ==========  =========

The sidebar case is the sharpest illustration: it asks for
``scrollbar-width: none`` — "do not show a scrollbar here at all" — and
Firefox 153 gave it a 15px one anyway, because the global
``::-webkit-scrollbar`` rule takes precedence over the standard property.

The fix
-------
``scrollbar-width`` / ``scrollbar-color`` are the *standard* properties for
this (Firefox 64+, Chrome/Edge 121+, Safari 18.2+). They express the same
"slim, theme-coloured scrollbar" intent **without** forcing a scroller off its
native overlay behaviour. So the standard properties become the single source
of truth, and the legacy ``::-webkit-scrollbar`` block is scoped to browsers
that lack them via ``@supports not (scrollbar-width: thin)``.

That is deliberately *not* a Firefox-specific carve-out. Keying off the feature
rather than the engine means the rule stays correct for whichever engine adopts
``::-webkit-scrollbar`` next, and old Chrome/Safari keep the styling they have
today.

Why a source pin rather than an e2e test
----------------------------------------
This regression only reproduces in a **headed** browser that is in overlay-
scrollbar mode. Headless Firefox reports a 0px gutter for the broken CSS as
well, so an e2e assertion in the SPA harness (headless, Chromium) would pass
against the bug and give a false green. The invariant below — "no ungated
``::-webkit-scrollbar`` rule ships in the SPA" — is what actually holds the
fix in place, and it runs everywhere.
"""

import re
from pathlib import Path

import pytest


SPA_SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"

# The feature query that makes a legacy webkit block safe: it only applies in
# browsers WITHOUT the standard scrollbar properties, which by definition are
# not the browsers that would flip to a classic scrollbar because of it.
GUARD_RE = re.compile(r"@supports\s+not\s*\(\s*scrollbar-width\s*:")

COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _css_files():
    assert SPA_SRC.is_dir(), f"SPA source tree missing at {SPA_SRC}"
    return sorted(SPA_SRC.rglob("*.css"))


def _strip_comments(text):
    """Blank out comments, preserving newlines so line numbers stay accurate."""
    return COMMENT_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def _unguarded_webkit_scrollbar_rules(text):
    """Yield ``(line_no, snippet)`` for each ``::-webkit-scrollbar`` selector
    that is NOT lexically inside an ``@supports not (scrollbar-width: ...)``
    block.

    Walks the stylesheet tracking brace depth, remembering the depth at which
    each guard block was opened, so nesting (CSS layers, media queries) is
    handled rather than assumed away.
    """
    text = _strip_comments(text)
    offenders = []
    depth = 0
    guard_depths = []
    pending_guard = False
    line = 1

    i = 0
    n = len(text)
    while i < n:
        ch = text[i]

        if ch == "\n":
            line += 1
        elif text.startswith("@supports", i):
            head_end = text.find("{", i)
            if head_end != -1 and GUARD_RE.search(text[i:head_end]):
                pending_guard = True
        elif text.startswith("::-webkit-scrollbar", i):
            if not guard_depths:
                snippet = text[i : text.find("{", i) + 1 if text.find("{", i) != -1 else i + 40]
                offenders.append((line, " ".join(snippet.split())))
        elif ch == "{":
            depth += 1
            if pending_guard:
                guard_depths.append(depth)
                pending_guard = False
        elif ch == "}":
            if guard_depths and guard_depths[-1] == depth:
                guard_depths.pop()
            depth -= 1

        i += 1

    return offenders


def test_no_ungated_webkit_scrollbar_rules_in_spa():
    """No ``::-webkit-scrollbar`` rule may ship unguarded.

    This is the pin that keeps #1089 fixed: an ungated rule is exactly what
    demotes Firefox (and any future engine that adopts the pseudo-element) from
    overlay scrollbars to always-visible classic ones.
    """
    offenders = []
    for path in _css_files():
        for line, snippet in _unguarded_webkit_scrollbar_rules(path.read_text()):
            offenders.append(f"  {path.relative_to(SPA_SRC)}:{line}: {snippet}")

    assert not offenders, (
        "::-webkit-scrollbar styled outside an `@supports not (scrollbar-width: ...)` "
        "guard. Any browser honouring it swaps that scroller's overlay scrollbar for "
        "an always-visible classic one that steals layout width (fork issue #1089).\n"
        "Express the intent with `scrollbar-width` / `scrollbar-color` instead, and "
        "keep the webkit block only as a guarded fallback for older Chrome/Safari:\n"
        + "\n".join(offenders)
    )


def test_scan_helper_flags_an_unguarded_rule():
    """The scanner must actually catch the shape of the original bug.

    Without this, ``test_no_ungated_webkit_scrollbar_rules_in_spa`` could pass
    because the parser silently matches nothing.
    """
    broken = """
    @layer base {
      ::-webkit-scrollbar { width: 11px; height: 11px; }
    }
    """
    found = _unguarded_webkit_scrollbar_rules(broken)
    assert len(found) == 1, f"scanner missed the unguarded rule: {found}"


def test_scan_helper_accepts_a_guarded_rule():
    """A properly guarded fallback must not be reported."""
    ok = """
    @layer base {
      * { scrollbar-width: thin; }
      @supports not (scrollbar-width: thin) {
        ::-webkit-scrollbar { width: 11px; height: 11px; }
        ::-webkit-scrollbar-thumb { background: #444; }
      }
      ::selection { background: red; }
    }
    """
    assert _unguarded_webkit_scrollbar_rules(ok) == []


def test_scan_helper_ignores_commented_out_rules():
    """A rule inside a comment is documentation, not shipped CSS."""
    commented = """
    @layer base {
      /* Previously: ::-webkit-scrollbar { width: 11px; } — see #1089. */
      * { scrollbar-width: thin; }
    }
    """
    assert _unguarded_webkit_scrollbar_rules(commented) == []


@pytest.mark.parametrize(
    "relpath",
    [
        "styles/global.css",
        "components/DiscoverSection.module.css",
        "components/MoreByAuthor.module.css",
    ],
)
def test_themed_scrollbars_still_expressed_via_standard_properties(relpath):
    """The fix must preserve the intent, not just delete the styling.

    Every surface that used to theme its scrollbar must still do so through the
    standard properties, otherwise #1089 gets "fixed" by shipping default
    light-grey scrollbars on a dark theme.
    """
    css = (SPA_SRC / relpath).read_text()
    assert "scrollbar-width:" in css, f"{relpath} lost its scrollbar-width declaration"
    assert "scrollbar-color:" in css, f"{relpath} lost its scrollbar-color declaration"
