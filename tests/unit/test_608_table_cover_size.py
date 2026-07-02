# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Acceptance tests for fork #608 — new UI Table view cover thumbnails rendered
as 32x46 slivers (aspect 0.70), so real book covers (2:3 = 0.67) were side-cropped
by object-fit: cover and looked "very squished" despite ample horizontal space.

These pin the Table view cover box to a readable size with a true book-cover
aspect ratio so a style refactor can't silently shrink it back.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

TABLE_CSS = (
    Path(__file__).resolve().parents[2]
    / "frontend" / "src" / "pages" / "Table.module.css"
)


def _px(css: str, class_name: str, prop: str) -> float:
    """Extract a px value for `prop` inside the `.class_name { ... }` rule."""
    rule = re.search(r"\.%s\s*\{([^}]*)\}" % re.escape(class_name), css)
    assert rule, f".{class_name} rule missing from Table.module.css"
    value = re.search(r"(?:^|[\s;])%s\s*:\s*([\d.]+)px" % re.escape(prop), rule.group(1))
    assert value, f".{class_name} has no px value for {prop}"
    return float(value.group(1))


@pytest.fixture(scope="module")
def css() -> str:
    return TABLE_CSS.read_text(encoding="utf-8")


def test_cover_thumb_is_wide_enough(css):
    """#608: 32px covers were unreadable slivers; require a meaningfully wider box."""
    assert _px(css, "coverThumb", "width") >= 44


def test_cover_thumb_has_book_cover_aspect(css):
    """Box must match the standard 2:3 book-cover aspect so object-fit: cover
    doesn't visibly crop normal covers (the reported 'squished' effect)."""
    width = _px(css, "coverThumb", "width")
    height = _px(css, "coverThumb", "height")
    assert 0.65 <= width / height <= 0.68, (
        f"coverThumb {width}x{height} (aspect {width / height:.3f}) is not ~2:3"
    )


def test_cover_placeholder_matches_thumb(css):
    """The no-cover placeholder must stay the same size as the real thumbnail
    so rows with and without covers align."""
    assert _px(css, "coverThumbEmpty", "width") == _px(css, "coverThumb", "width")
    assert _px(css, "coverThumbEmpty", "height") == _px(css, "coverThumb", "height")


def test_cover_column_fits_thumb(css):
    """The header column width hint must be at least as wide as the thumbnail."""
    assert _px(css, "coverCol", "width") >= _px(css, "coverThumb", "width")


def test_cover_thumb_keeps_object_fit_cover(css):
    """object-fit: cover (not stretch/fill) is what prevents actual distortion;
    pin it so the fix isn't 'solved' by letting images deform."""
    rule = re.search(r"\.coverThumb\s*\{([^}]*)\}", css)
    assert rule and re.search(r"object-fit\s*:\s*cover", rule.group(1))


def test_table_cells_break_unbreakable_tokens_on_desktop_only(css):
    """Adjacent fix found while verifying #608: a title with a long unbreakable
    token (common for auto-ingested filename titles) forced the whole table to
    horizontally scroll on desktop. Cells must be allowed to break anywhere as a
    last resort — but ONLY behind a min-width media query: applied unconditionally,
    `anywhere` collapses min-content width on phones and shreds cells to ~1ch."""
    rules = re.findall(r"\.table td\s*\{([^}]*)\}", css)
    assert any(re.search(r"overflow-wrap\s*:\s*anywhere", r) for r in rules)
    gated = re.search(
        r"@media\s*\(min-width:\s*\d+px\)\s*\{[^{]*\.table td\s*\{([^}]*)\}", css
    )
    assert gated and re.search(r"overflow-wrap\s*:\s*anywhere", gated.group(1))
    base = rules[0]
    assert not re.search(r"overflow-wrap\s*:\s*anywhere", base), (
        "overflow-wrap: anywhere must not apply to the base (mobile) rule"
    )
