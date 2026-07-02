# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Fork #602 (@iroQuai) — logout submenu unreachable on mobile (caliBlur).

caliBlur's ``max-width: 767px`` block lays the drawer out by slot: the rule
``#main-nav > li:nth-child(1)`` reserves a 130px full-width band for the
profile dropdown, and ``a.profileDrop`` is forced to 120px tall with the
username label absolutely placed at ``top: 70px``. The fork's "Switch to New
UI" pill (``li.cwng-switch-li``) is rendered as the *first* item of
``#main-nav``, so the pill stole the 130px slot and the profile li collapsed
to a 60px row. Its 120px anchor then overflowed into the Upload row, whose
invisible ``opacity: 0`` ``#btn-upload`` file input sits on top — every tap on
the username landed on the file input and the logout menu never opened.

Fix: give the pill its natural height when it occupies slot 1, and assign the
slot properties to ``#main-nav > li.dropdown`` explicitly (identical outcome
when the pill is absent, because the profile li is then nth-child(1) anyway).

Pinned by source so a future caliBlur edit can't silently drop the guard while
the pill is still rendered ahead of the profile dropdown.
"""
from __future__ import annotations

import os
import re

HERE = os.path.dirname(__file__)
CALIBLUR = os.path.normpath(
    os.path.join(HERE, "..", "..", "cps", "static", "css", "caliBlur.css")
)
LAYOUT = os.path.normpath(
    os.path.join(HERE, "..", "..", "cps", "templates", "layout.html")
)


def _mobile_block():
    """Return the body of the ``@media only screen and (max-width: 767px)``
    block, extracted by brace counting (the block nests rule braces)."""
    with open(CALIBLUR, encoding="utf-8") as fh:
        css = fh.read()
    m = re.search(r"@media\s+only\s+screen\s+and\s*\(max-width:\s*767px\)\s*\{", css)
    assert m, "caliBlur mobile (max-width: 767px) block not found"
    depth, i = 1, m.end()
    while depth and i < len(css):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
        i += 1
    return re.sub(r"/\*.*?\*/", "", css[m.end():i - 1], flags=re.DOTALL)


def _rule(block, selector_re):
    m = re.search(selector_re + r"\s*\{([^}]*)\}", block)
    return m.group(1) if m else None


def test_switch_pill_does_not_keep_the_130px_profile_slot():
    """The pill in slot 1 must fall back to its natural height, not the
    130px profile band (which mis-stacked every drawer row below it)."""
    body = _rule(_mobile_block(), r"#main-nav\s*>\s*li\.cwng-switch-li:nth-child\(1\)")
    assert body is not None, (
        "caliBlur mobile block must neutralize the slot-1 rule for "
        "li.cwng-switch-li (fork #602)"
    )
    assert re.search(r"height:\s*auto", body), "pill must not keep height:130px"


def test_profile_dropdown_gets_the_slot_by_class_not_position():
    """The profile li must receive the 130px band + z-index regardless of its
    position, so its 120px anchor can't overflow under the invisible upload
    file input (which swallowed the logout tap)."""
    body = _rule(_mobile_block(), r"#main-nav\s*>\s*li\.dropdown")
    assert body is not None, (
        "caliBlur mobile block must style #main-nav > li.dropdown explicitly "
        "(fork #602)"
    )
    assert re.search(r"height:\s*130px", body)
    assert re.search(r"width:\s*100%", body)
    # z-index keeps the opened .profileDropli above #btn-upload — load-bearing
    # for the tap actually reaching the Logout link.
    assert re.search(r"z-index:\s*99", body)


def test_layout_still_renders_pill_before_profile_dropdown():
    """Documents the ordering assumption the CSS guard exists for: the pill is
    rendered ahead of the theme-1 profile dropdown inside #main-nav. If this
    ever flips, the guard is harmless — but if the pill stays first and the
    guard is gone, #602 regresses."""
    with open(LAYOUT, encoding="utf-8") as fh:
        html = fh.read()
    pill = html.find("cwng-switch-li")
    profile = html.find('class="dropdown-toggle profileDrop"')
    assert pill != -1 and profile != -1
    assert pill < profile, (
        "pill no longer precedes the profile dropdown — revisit the #602 "
        "caliBlur slot guard (safe to relax this pin if intentional)"
    )
