# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for #584 — "More server configuration" links revert the whole UI.

The new-UI Admin page keeps a set of "More server configuration" cards that link
out to the deep, rarely-touched legacy (Jinja) config pages — DB path, scheduled
tasks, logs, etc. Rebuilding all of those natively is the SPA admin-migration
project (tracked in notes/DEFERRED-NEWUI-FEATURES.md), not this release.

The reporter's concrete complaint (@Glennza1962): entering one of those sub-menus
"reverts the whole UI back to the old one." That happened because the card anchors
navigated in the *same* tab, so the SPA document was replaced wholesale by the
classic page — even though each card already renders an ExternalLink icon
advertising that it leaves the new UI.

The pragmatic, no-rebuild fix: open those legacy pages in a new tab
(target="_blank" rel="noopener noreferrer"), so the new UI stays put and the
classic config page is a clearly-separate context the user can close to return.
This makes the ExternalLink affordance truthful and removes the jarring
whole-app revert without waiting on the native admin rebuild.

Client-side source pins (the SPA bundle is built in Docker, not committed, so a
runtime assertion isn't reachable here — pin the source that the build compiles).
"""
import pathlib
import re

import pytest

_FE = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"
_ADMIN = _FE / "pages" / "Admin.tsx"


def _admin_src() -> str:
    return _ADMIN.read_text()


def _settings_card_anchor(src: str) -> str:
    """Return the JSX for the settings-card <a> element (the SERVER_SETTINGS map)."""
    # The anchor opens with the resourceUrl'd href (#603) and closes at </a>.
    m = re.search(r"<a\s+key=\{href\}.*?</a>", src, re.S)
    assert m, "settings-card <a> element not found in Admin.tsx"
    return m.group(0)


@pytest.mark.unit
def test_settings_cards_open_in_new_tab():
    """Each 'More server configuration' card must open the legacy page in a new
    tab so entering a sub-menu doesn't replace the whole new UI (#584).
    RED on main, where the anchor had no target attribute."""
    anchor = _settings_card_anchor(_admin_src())
    assert 'target="_blank"' in anchor, \
        "settings-card anchor must set target=\"_blank\" so legacy config opens " \
        "in a new tab instead of reverting the whole new UI (#584)"


@pytest.mark.unit
def test_settings_cards_new_tab_is_secure():
    """target=\"_blank\" without rel=noopener lets the opened legacy page reach
    back into window.opener. Pin the secure rel so the new-tab fix can't
    introduce a reverse-tabnabbing vector."""
    anchor = _settings_card_anchor(_admin_src())
    rel = re.search(r'rel="([^"]*)"', anchor)
    assert rel, "settings-card anchor must carry a rel attribute alongside target=_blank"
    tokens = set(rel.group(1).split())
    assert "noopener" in tokens, "rel must include noopener (block window.opener access)"
    assert "noreferrer" in tokens, "rel must include noreferrer"


@pytest.mark.unit
def test_settings_cards_still_carry_reverse_proxy_prefix():
    """The new-tab fix must not regress #603 — the href must still route through
    the reverse-proxy prefix helper so a /cwa-mounted install opens the legacy
    page inside the mount."""
    anchor = _settings_card_anchor(_admin_src())
    assert "href={resourceUrl(href)}" in anchor, \
        "settings-card anchor must keep href={resourceUrl(href)} (#603 prefix fix)"
