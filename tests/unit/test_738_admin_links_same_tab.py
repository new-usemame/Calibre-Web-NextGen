# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for fork issue #738: the new UI's Admin page opened
its "More server configuration" cards (Basic configuration, UI settings,
Logs, …) in a NEW TAB even though they are in-app pages.

Beyond being surprising navigation, the new tab landed users on a classic
page that showed the "Switch to New UI" controls again, which read as the
new-UI choice being lost (#739's most visible trigger).

The fix drops ``target="_blank"`` from the server-settings navigation. Genuinely external links
elsewhere (GitHub/Discord in the top bar, download/export links) keep
their new-tab behaviour — this pin is scoped to the Admin context navigation.

Supersedes ``test_584_admin_legacy_link_new_tab.py`` (removed): #584's
new-tab behaviour was a deliberate workaround for "entering a sub-menu
reverts the whole UI", chosen before the new-UI preference was sticky.
With #739 the preference persists (classic home bounces back to the SPA
and the header pill offers "Back to New UI"), so the same-tab navigation
users keep asking for (#738, #739) no longer costs @Glennza1962's
original complaint — the root cause is fixed instead of routed around.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTEXT_SIDEBAR_TSX = REPO_ROOT / "frontend" / "src" / "components" / "ContextSidebar.tsx"
CONTEXT_DEFINITIONS_TS = REPO_ROOT / "frontend" / "src" / "lib" / "contextSidebars.ts"


def test_admin_server_settings_cards_navigate_same_tab():
    src = CONTEXT_SIDEBAR_TSX.read_text(encoding="utf-8")
    assert 'target="_blank"' not in src, (
        "Admin context navigation must not open in-app pages in a new tab "
        "(issue #738) — if a genuinely external link was added to this page, "
        "scope this pin to classic context items instead."
    )


def test_admin_cards_do_not_use_external_link_glyph():
    src = CONTEXT_SIDEBAR_TSX.read_text(encoding="utf-8")
    definitions = CONTEXT_DEFINITIONS_TS.read_text(encoding="utf-8")
    assert "ExternalLink" not in src, (
        "The Admin destinations are in-app navigation; the external-link icon "
        "implies a new tab/site and should stay off the context rail (#738)."
    )
    assert "ExternalLink" not in definitions
