# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression guard for the removed new-UI banner (#1310, #1311).

#1310 reported that the fixed banner covered the final Classic-page action and
intercepted its clicks. #1311 tracked the banner behavior itself. The SPA-default
change removes the banner entirely, so the bug class is now closed by removal
rather than by maintaining a height reserve and float clearfix for an element
that no longer exists. Keep the four implementation tokens out of both the
Classic layout and every shipped static JavaScript file.
"""

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
LAYOUT_HTML = REPO_ROOT / "cps" / "templates" / "layout.html"
STATIC_JS = REPO_ROOT / "cps" / "static"


def _shipped_sources():
    yield LAYOUT_HTML
    yield from STATIC_JS.rglob("*.js")


@pytest.mark.unit
@pytest.mark.parametrize("removed_token", [
    "cwng-newui-banner",
    "cwng-has-newui-banner",
    "--cwng-banner-gap",
    "cwng_newui_banner_dismissed",
])
def test_removed_banner_and_its_reserve_stay_removed(removed_token):
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in _shipped_sources()
        if removed_token in path.read_text(encoding="utf-8", errors="replace")
    ]
    assert offenders == [], (
        f"removed new-UI banner token {removed_token!r} returned in {offenders}; "
        "that can reintroduce the fixed-overlay click interception from #1310"
    )
