"""Source pins for #578 — the new UI didn't keep the library scroll position when
going Back from a book. The Catalog now stashes its loaded pages + filters +
scrollY in an in-memory cache (lib/scrollCache) keyed by route and rehydrates
them on remount, restoring the scroll offset. Behavioural coverage is the live
Playwright scroll test; these guard the wiring from silent removal.
"""
import pathlib

import pytest

_FE = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"


@pytest.mark.unit
def test_scroll_cache_module_present():
    src = (_FE / "lib" / "scrollCache.ts").read_text()
    assert "export function saveCatalog" in src
    assert "export function loadCatalog" in src
    assert "scrollY" in src


@pytest.mark.unit
def test_catalog_restores_and_persists():
    src = (_FE / "pages" / "Catalog.tsx").read_text()
    assert "loadCatalog(restoreKey)" in src
    assert "saveCatalog(restoreKey" in src
    # scrollY is captured on unmount and re-applied on mount.
    assert "window.scrollY" in src
    assert "window.scrollTo(0, y)" in src
    # The rehydrated page/filters must survive the mount reset + urlQ effects.
    assert "restoringRef" in src
    # A snapshot is only restored when consistent with the URL query, so a fresh
    # top-bar search (/?q=…) isn't ignored in favour of a stale snapshot.
    assert "urlQAtMount" in src


@pytest.mark.unit
def test_catalog_state_seeded_from_snapshot():
    """State initializers read from the snapshot so the grid renders at full
    height on first paint (making the saved scroll offset reachable)."""
    src = (_FE / "pages" / "Catalog.tsx").read_text()
    assert "snap?.page ?? 1" in src
    assert "snap?.books ?? []" in src
