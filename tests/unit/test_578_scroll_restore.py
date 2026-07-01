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


@pytest.mark.unit
def test_dedupappend_upserts_not_append_only():
    """A re-fetched page must UPDATE existing books by id (edits propagate), not be
    add-only — else edit→Back shows the stale card (adversarial-review regression)."""
    src = (_FE / "pages" / "Catalog.tsx").read_text()
    # upsert shape: build a by-id map from the incoming page and merge.
    assert "new Map(next.map" in src
    assert "byId.get(b.id)" in src


@pytest.mark.unit
def test_scroll_restore_raf_is_cancelled():
    """The scroll-restore retry must cancel on unmount, or a quick book-open right
    after Back scrolls the next page / fights the user (adversarial-review finding)."""
    src = (_FE / "pages" / "Catalog.tsx").read_text()
    assert "cancelAnimationFrame" in src
    assert "cancelled = true" in src


@pytest.mark.unit
def test_deleted_book_evicted_from_scroll_cache():
    """Deleting a book must purge it from cached snapshots so scroll-restore can't
    resurrect a ghost card that 404s on click (adversarial-review finding)."""
    cache = (_FE / "lib" / "scrollCache.ts").read_text()
    assert "export function removeBookFromCache" in cache
    queries = (_FE / "lib" / "queries.ts").read_text()
    assert "removeBookFromCache" in queries
