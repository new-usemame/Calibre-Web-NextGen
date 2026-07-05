# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for the in-app "What's New" feature log (SPA /whats-new + the
Help-menu entry and unread dot).

These are source-pin tests: the SPA has no JS unit runner, so we pin the
load-bearing invariants at the source level. Each asserts a specific way the
feature has broken (or would break) rather than trivia:

1. The user-visible chrome msgids are anchored in ``cps/spa_strings.py``. babel
   only scans ``.py``/``.jinja`` — a SPA-only string that lives solely in a
   ``.tsx`` file never reaches the POT, so its translations go obsolete and the
   English fallback ships (the #577 failure mode; see the
   ``spa-only-msgid-extraction`` note). Guard the anchors.
2. The route and the Help-menu link are wired — a page nobody can reach is dead.
3. The unread dot is keyed to the newest *logged* version, not a runtime
   version query (no such SPA constant exists), and opening the page marks it
   seen. This is what makes the dot light once on discovery and then stay quiet.
4. Every ``link.to`` deep-link in the data file resolves to a real SPA route, so
   a future release entry can't ship a "Try it" button that 404s.
"""
import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FE = _ROOT / "frontend" / "src"


def _read(rel: str) -> str:
    return (_FE / rel).read_text()


@pytest.mark.unit
def test_whats_new_chrome_msgids_anchored_in_spa_strings():
    """The page/menu chrome strings must be anchored so babel extracts them.
    Without this, the /whats-new chrome is un-translatable (English-only)."""
    anchor = (_ROOT / "cps" / "spa_strings.py").read_text()
    for msgid in (
        '"What\'s new"',
        '"Help — new updates available"',
        '"The latest features and fixes in Calibre-Web NextGen — newest first."',
        '"No release notes yet — check back after the next update."',
        '"The interface is translated into your language; these update notes are written in English."',
    ):
        assert msgid in anchor, f"SPA-only msgid {msgid} not anchored in spa_strings.py"


@pytest.mark.unit
def test_category_chip_labels_anchored():
    """Category names double as chip labels rendered through t(); every category
    in the union must be anchored or its chip ships untranslated."""
    anchor = (_ROOT / "cps" / "spa_strings.py").read_text()
    data = _read("data/whatsNew.ts")
    # Extract the WhatsNewCategory union members from the type declaration.
    m = re.search(r"WhatsNewCategory\s*=\s*([^;]+);", data)
    assert m, "WhatsNewCategory union not found in data/whatsNew.ts"
    categories = re.findall(r"'([^']+)'", m.group(1))
    assert categories, "no categories parsed from the union"
    for cat in categories:
        assert f'"{cat}"' in anchor, f"category chip label {cat!r} not anchored in spa_strings.py"


@pytest.mark.unit
def test_route_and_help_menu_wired():
    """A page you can't reach is dead code. Pin the route + the Help-menu link."""
    app = _read("App.tsx")
    assert 'path="/whats-new"' in app, "/whats-new route not registered in App.tsx"
    assert "WhatsNew" in app and "from './pages/WhatsNew'" in app

    topbar = _read("components/TopBar.tsx")
    assert 'to="/whats-new"' in topbar, "Help menu does not link to /whats-new"
    assert "useWhatsNewUnread" in topbar, "Help menu does not consume the unread hook"


@pytest.mark.unit
def test_unread_dot_keyed_to_logged_version_and_marks_seen():
    """The dot keys off LATEST_WHATS_NEW_VERSION (the newest version baked into
    the data file), NOT a runtime/installed-version query — the data file and the
    version it announces ship in the same image. And opening the page must mark
    it seen so the dot clears."""
    lib = _read("lib/whatsNew.ts")
    assert "LATEST_WHATS_NEW_VERSION" in lib
    # The unread test compares seen-state against the newest logged version.
    assert re.search(r"!==\s*LATEST_WHATS_NEW_VERSION", lib), \
        "unread comparison must be against LATEST_WHATS_NEW_VERSION"
    # No dependency on a runtime installed-version constant (there is none).
    assert "INSTALLED_VERSION" not in lib

    page = _read("pages/WhatsNew.tsx")
    assert "markWhatsNewSeen()" in page, "opening /whats-new must mark it seen (clears the dot)"


@pytest.mark.unit
def test_latest_version_derives_from_newest_entry():
    """LATEST_WHATS_NEW_VERSION must come from WHATS_NEW[0] so the dot tracks the
    newest release automatically when the populate skill prepends an entry."""
    data = _read("data/whatsNew.ts")
    assert re.search(r"LATEST_WHATS_NEW_VERSION[^=]*=\s*WHATS_NEW\[0\]", data), \
        "LATEST_WHATS_NEW_VERSION must derive from WHATS_NEW[0].version"


@pytest.mark.unit
def test_every_deep_link_resolves_to_a_real_route():
    """A 'Try it' deep-link that points at a non-existent SPA route 404s. Pin
    that every link.to in the data file matches a registered route in App.tsx."""
    data = _read("data/whatsNew.ts")
    app = _read("App.tsx")
    routes = set(re.findall(r'path="(/[^"]*)"', app))
    assert routes, "no routes parsed from App.tsx"
    links = re.findall(r"to:\s*'([^']+)'", data)
    assert links, "no deep-links parsed from whatsNew.ts (expected several)"
    for to in links:
        # Compare against the static route (strip any :params — none expected here).
        assert to in routes, f"deep-link {to!r} has no matching route in App.tsx"
