"""Regression pin for #576 — the mobile navigation drawer was transparent and
un-scrollable. Root cause: the base .nav rule (background / padding / overflow-y)
did NOT apply to the open-drawer class .navOpen, so the open mobile drawer had no
background (see-through) and no overflow-y (couldn't scroll — the page behind
scrolled instead). Desktop uses .nav (drawer never opens), so it was unaffected.

Fix: the base declarations cover both .nav and .navOpen, and the mobile drawer
contains its own scroll (overscroll-behavior: contain).
"""
import pathlib
import re

import pytest

CSS = (pathlib.Path(__file__).resolve().parents[2]
       / "frontend" / "src" / "components" / "Sidebar.module.css").read_text()


def _rule_block(css, selector_contains):
    """Return the declaration block of the first rule whose selector list
    contains `selector_contains`."""
    for m in re.finditer(r"([^{}]+)\{([^}]*)\}", css):
        if selector_contains in m.group(1):
            return m.group(1), m.group(2)
    return None, None


@pytest.mark.unit
def test_open_drawer_has_background_and_scroll():
    """The base block that sets background + overflow-y must list BOTH .nav and
    .navOpen — the exact regression: an open drawer (.navOpen) with neither."""
    # Find the base block that declares the background.
    sel, block = None, None
    for m in re.finditer(r"([^{}]+)\{([^}]*)\}", CSS):
        if "background: var(--surface-1)" in m.group(2) and "overflow-y" in m.group(2):
            sel, block = m.group(1), m.group(2)
            break
    assert block is not None, "no base nav block with background+overflow-y found"
    assert ".nav" in sel and ".navOpen" in sel, (
        "background/overflow base block must cover both .nav and .navOpen")
    assert "overflow-y: auto" in block


@pytest.mark.unit
def test_mobile_drawer_contains_its_scroll():
    """The mobile drawer must not chain its scroll to the page behind it."""
    assert "overscroll-behavior: contain" in CSS
