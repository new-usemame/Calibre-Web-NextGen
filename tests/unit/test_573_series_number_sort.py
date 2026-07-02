# SPDX-License-Identifier: GPL-3.0-or-later
"""Source pins for #573 — the new UI's series view had no way to sort books by
their metadata series position, and the position wasn't shown on the card unless
duplicated in the title.

The fix wires three things, guarded here against silent removal:
  1. Backend: a stateless series_index sort ("seriesasc"/"seriesdesc") in the
     /api/v1/books SORT_MAP, mirroring web.py's get_sort_function.
  2. Frontend: the series view offers "Series order" sort options and defaults to
     ascending series order.
  3. Frontend: the book card renders the series position (showSeriesIndex).

Behavioural coverage is the live Playwright series-view test; these guard the
wiring. (See tests/unit/test_578_scroll_restore.py for the source-pin pattern.)
"""
import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FE = _ROOT / "frontend" / "src"
_CPS = _ROOT / "cps"


@pytest.mark.unit
def test_backend_sort_map_has_series_index():
    """The read-only books API must map seriesasc/seriesdesc to a series_index
    order (not fall back to newest-first), or the SPA's Series-order sort is a
    no-op server-side."""
    src = (_CPS / "api" / "books.py").read_text()
    assert '"seriesasc": [db.Books.series_index.asc()]' in src
    assert '"seriesdesc": [db.Books.series_index.desc()]' in src


@pytest.mark.unit
def test_catalog_offers_series_order_options():
    """The series view exposes the two series-order sort options."""
    src = (_FE / "pages" / "Catalog.tsx").read_text()
    assert "SERIES_SORT_OPTIONS" in src
    assert "'seriesasc'" in src
    assert "'seriesdesc'" in src
    # The options are only added for a series view, and the dropdown renders the
    # context-aware list (not the fixed base list).
    assert "isSeries ? [...SERIES_SORT_OPTIONS" in src
    assert "sortOptions.map(" in src


@pytest.mark.unit
def test_catalog_defaults_to_series_order_in_series_view():
    """A series opens in ascending series order, not newest-first (the reporter's
    core complaint), matching web.py's series-page default."""
    src = (_FE / "pages" / "Catalog.tsx").read_text()
    assert "const isSeries = entityKind === 'series'" in src
    assert "defaultSort = isSeries ? 'seriesasc' : 'new'" in src
    # The sort state seeds from defaultSort (falling back through the snapshot).
    assert "snap?.sort ?? defaultSort" in src


@pytest.mark.unit
def test_catalog_shows_series_index_on_card_in_series_view():
    """The catalog tells the card to show the series position when, and only
    when, viewing a series."""
    src = (_FE / "pages" / "Catalog.tsx").read_text()
    assert re.search(r"showSeriesIndex=\{isSeries\}", src)


@pytest.mark.unit
def test_bookcard_renders_series_position():
    """The card renders the series position from book.series_index when asked."""
    src = (_FE / "components" / "BookCard.tsx").read_text()
    assert "showSeriesIndex" in src
    assert "formatSeriesIndex" in src
    assert "book.series_index" in src
    assert "seriesBadge" in src


@pytest.mark.unit
def test_bookcard_series_badge_styled():
    """The series badge has a style so it isn't an unstyled overlay."""
    css = (_FE / "components" / "BookCard.module.css").read_text()
    assert ".seriesBadge" in css


@pytest.mark.unit
def test_series_sort_msgids_anchored():
    """SPA-only msgids must be anchored in spa_strings.py or the auto-translation
    job strips them (babel doesn't scan .tsx)."""
    src = (_CPS / "spa_strings.py").read_text()
    assert '_("Series order")' in src
    assert '_("Series order (reverse)")' in src
