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

Measured in a **headed** Firefox 153 with overlay scrollbars enabled
(``ui.useOverlayScrollbars=1``, i.e. the reporter's configuration), against the
live SPA:

===========================================  ==========  =========
scroller                                     as shipped  with fix
===========================================  ==========  =========
sidebar ``nav``                                 15 px       0 px
Discover / MoreByAuthor ``.strip``              11 px       0 px
===========================================  ==========  =========

"Gutter" there is ``offsetWidth - clientWidth``: the width the scrollbar
permanently takes away from the content. An overlay scrollbar takes none.

The fix
-------
``scrollbar-width`` / ``scrollbar-color`` are the *standard* properties for
this. They express the same "slim, theme-coloured scrollbar" intent **without**
forcing a scroller off its native overlay behaviour. So the standard properties
become the single source of truth, and the legacy ``::-webkit-scrollbar`` block
is scoped to engines that lack them.

That is deliberately *not* a Firefox-specific carve-out. Keying off the feature
rather than the engine means the rule stays correct for whichever engine adopts
``::-webkit-scrollbar`` next, and old Chrome/Safari keep the styling they have
today.

The feature query must test **both** standard properties. They did not ship
together: Safari added ``scrollbar-width`` in 18.2 but ``scrollbar-color`` only
in 26.2. A guard on width alone would drop the legacy styling across that whole
Safari range while its replacement was only half-supported — the scrollbars
would be thin but fall back to the platform's default light colour on this dark
UI. Only an engine supporting the pair may take the standards-only path. This
is why :data:`SAFE_GUARD_RE` insists on the exact two-property form rather than
accepting anything that mentions ``scrollbar-width``.

Why a source pin rather than an e2e test
----------------------------------------
This regression only reproduces in a **headed** browser that is in overlay-
scrollbar mode. Headless Firefox reports a 0px gutter for the broken CSS as
well, so an e2e assertion in the SPA harness (headless, Chromium) would pass
against the bug and give a false green. The invariant below — "no ungated
``::-webkit-scrollbar`` rule ships in the SPA" — is what actually holds the
fix in place, and it runs everywhere.

Two traps for anyone re-verifying this by hand, both of which cost time here:

* A machine set to "always show scroll bars" (macOS System Settings, and the
  default on Windows) has no overlay scrollbars to lose, so the bug is
  invisible on it no matter which browser you use.
* **Headless** Firefox reports ``scrollbar-width: none`` as the *computed*
  value for an element that declares nothing at all. Read computed scrollbar
  properties in a headed browser or they will describe the harness rather than
  the page.
"""

import re
from pathlib import Path

import pytest


SPA_SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"

# The only ``@supports`` condition that makes a legacy webkit block safe.
#
# Deliberately exact rather than a substring match. Conditions that *mention*
# scrollbar-width can still be true in a modern browser and switch the legacy
# rules back on — ``not (scrollbar-width: thin) or (display: grid)`` is true
# anywhere ``display: grid`` works, and ``not (scrollbar-width: 12px)`` is true
# everywhere because ``12px`` is not a valid value. Both would sail through a
# looser check while shipping the bug.
SAFE_GUARD_RE = re.compile(
    r"^not\s*\(\s*\(\s*scrollbar-width\s*:\s*thin\s*\)"
    r"\s+and\s+"
    r"\(\s*scrollbar-color\s*:[^()]+\)\s*\)$",
    re.IGNORECASE,
)

WEBKIT_SCROLLBAR = "::-webkit-scrollbar"


def _css_files():
    assert SPA_SRC.is_dir(), f"SPA source tree missing at {SPA_SRC}"
    return sorted(SPA_SRC.rglob("*.css"))


def _skip_string(text, i):
    """Return the index just past the quoted string starting at ``i``."""
    quote = text[i]
    j = i + 1
    while j < len(text):
        if text[j] == "\\":
            j += 2
            continue
        if text[j] == quote:
            return j + 1
        if text[j] == "\n":  # unterminated string — don't run away
            return j
        j += 1
    return j


def _skip_comment(text, i):
    """Return the index just past the ``/* */`` comment starting at ``i``."""
    end = text.find("*/", i + 2)
    return len(text) if end == -1 else end + 2


def _unguarded_webkit_scrollbar_rules(text):
    """Yield ``(line_no, snippet)`` for each ``::-webkit-scrollbar`` selector
    that is not inside a correctly guarded ``@supports`` block.

    Lexes rather than pattern-matches, because the cheap version of this check
    is wrong in both directions. A regex comment-stripper treats ``"/*"`` and
    ``"*/"`` inside two ordinary strings as one giant comment and blanks out
    whatever real CSS sits between them (false pass); a plain text search
    reports ``content: "::-webkit-scrollbar"`` and
    ``@supports selector(::-webkit-scrollbar)`` as if they were style rules
    (false fail). So this walks the stylesheet tracking strings, comments,
    at-rule preludes and brace depth, and only counts an occurrence that is in
    selector position.
    """
    offenders = []
    depth = 0
    guard_depths = []
    pending_guard = None
    line = 1
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]

        if ch == "\n":
            line += 1
            i += 1
        elif text.startswith("/*", i):
            end = _skip_comment(text, i)
            line += text.count("\n", i, end)
            i = end
        elif ch in "\"'":
            end = _skip_string(text, i)
            line += text.count("\n", i, end)
            i = end
        elif ch == "@":
            # Consume the at-rule prelude, which is *not* selector text: an
            # `@supports selector(::-webkit-scrollbar)` query names the
            # pseudo-element without styling anything.
            j = i
            while j < n and text[j] not in "{;":
                if text.startswith("/*", j):
                    j = _skip_comment(text, j)
                elif text[j] in "\"'":
                    j = _skip_string(text, j)
                else:
                    j += 1
            prelude = " ".join(text[i:j].split())
            if prelude.lower().startswith("@supports"):
                condition = prelude[len("@supports") :].strip()
                pending_guard = bool(SAFE_GUARD_RE.match(condition))
            else:
                pending_guard = False
            if j < n and text[j] == ";":  # at-rule with no block, e.g. @import
                pending_guard = None
            line += text.count("\n", i, j)
            i = j
        elif text.startswith(WEBKIT_SCROLLBAR, i):
            if not guard_depths:
                brace = text.find("{", i)
                snippet = text[i : brace + 1] if brace != -1 else text[i : i + 48]
                offenders.append((line, " ".join(snippet.split())))
            i += len(WEBKIT_SCROLLBAR)
        elif ch == "{":
            depth += 1
            if pending_guard:
                guard_depths.append(depth)
            pending_guard = None
            i += 1
        elif ch == "}":
            if guard_depths and guard_depths[-1] == depth:
                guard_depths.pop()
            depth -= 1
            i += 1
        else:
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
        "::-webkit-scrollbar styled outside a `@supports not ((scrollbar-width: thin) "
        "and (scrollbar-color: ...))` guard. Any browser honouring it swaps that "
        "scroller's overlay scrollbar for an always-visible classic one that steals "
        "layout width (fork issue #1089).\nExpress the intent with `scrollbar-width` / "
        "`scrollbar-color` instead, and keep the webkit block only as a guarded "
        "fallback for older Chrome/Safari:\n" + "\n".join(offenders)
    )


# --- scanner self-checks -------------------------------------------------
# Without these the assertion above could pass simply because the scanner
# matches nothing, or because it accepts a guard that doesn't actually hold.


def test_scan_helper_flags_an_unguarded_rule():
    """The scanner must catch the shape of the original bug."""
    broken = """
    @layer base {
      ::-webkit-scrollbar { width: 11px; height: 11px; }
    }
    """
    assert len(_unguarded_webkit_scrollbar_rules(broken)) == 1


def test_scan_helper_accepts_a_correctly_guarded_rule():
    ok = """
    @layer base {
      * { scrollbar-width: thin; }
      @supports not ((scrollbar-width: thin) and (scrollbar-color: red transparent)) {
        ::-webkit-scrollbar { width: 11px; height: 11px; }
        ::-webkit-scrollbar-thumb { background: #444; }
      }
      ::selection { background: red; }
    }
    """
    assert _unguarded_webkit_scrollbar_rules(ok) == []


def test_scan_helper_rejects_a_width_only_guard():
    """The Safari gap: ``scrollbar-width`` shipped in 18.2, ``scrollbar-color``
    in 26.2. A width-only guard disables the legacy styling for that whole
    range while the replacement is only half-supported, so it is not safe."""
    width_only = """
    @supports not (scrollbar-width: thin) {
      ::-webkit-scrollbar { width: 11px; }
    }
    """
    assert _unguarded_webkit_scrollbar_rules(width_only)


def test_scan_helper_rejects_an_or_guard_that_is_true_in_modern_browsers():
    """``not (scrollbar-width: thin) or (display: grid)`` is true wherever grid
    works — the legacy rules would apply in exactly the browsers they must not."""
    or_guard = """
    @supports not (scrollbar-width: thin) or (display: grid) {
      ::-webkit-scrollbar { width: 11px; }
    }
    """
    assert _unguarded_webkit_scrollbar_rules(or_guard)


def test_scan_helper_rejects_a_guard_on_an_invalid_value():
    """``12px`` is not a valid ``scrollbar-width``, so the negation is true
    everywhere, including Firefox 153."""
    bogus = """
    @supports not (scrollbar-width: 12px) {
      ::-webkit-scrollbar { width: 11px; }
    }
    """
    assert _unguarded_webkit_scrollbar_rules(bogus)


def test_scan_helper_ignores_commented_out_rules():
    """A rule inside a comment is documentation, not shipped CSS."""
    commented = """
    @layer base {
      /* Previously: ::-webkit-scrollbar { width: 11px; } — see #1089. */
      * { scrollbar-width: thin; }
    }
    """
    assert _unguarded_webkit_scrollbar_rules(commented) == []


def test_scan_helper_is_not_fooled_by_comment_markers_inside_strings():
    """Two strings containing ``/*`` and ``*/`` are not a comment.

    A regex comment-stripper blanks out everything between them and silently
    loses the real rule in the middle — a false pass on shipping CSS.
    """
    tricky = """
    .a::before { content: "/* not a comment"; }
    ::-webkit-scrollbar { width: 11px; }
    .b::before { content: "*/"; }
    """
    assert _unguarded_webkit_scrollbar_rules(tricky), (
        "scanner treated quoted comment markers as a real comment and missed "
        "the unguarded rule between them"
    )


def test_scan_helper_ignores_the_pseudo_element_name_inside_a_string():
    """Naming the pseudo-element in content/url text styles nothing."""
    quoted = """
    .a::after { content: "::-webkit-scrollbar"; }
    """
    assert _unguarded_webkit_scrollbar_rules(quoted) == []


def test_scan_helper_ignores_the_pseudo_element_in_a_supports_prelude():
    """``@supports selector(::-webkit-scrollbar)`` is a feature query, not a
    style rule — flagging it would make the pin noisy enough to get worked
    around."""
    prelude_only = """
    @supports selector(::-webkit-scrollbar) {
      .a { color: red; }
    }
    """
    assert _unguarded_webkit_scrollbar_rules(prelude_only) == []


# --- intent pins ---------------------------------------------------------


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
