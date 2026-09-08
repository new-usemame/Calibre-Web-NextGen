# SPDX-License-Identifier: GPL-3.0-or-later
"""#573 — the new UI's series view had no way to sort by series position, and
the position wasn't shown on the card unless duplicated in the title.

The user-visible half of that fix now has real coverage in
``frontend/e2e/series-sort-order.spec.ts``: it seeds a series whose ascending
order differs from the library's newest-first order and then drives the actual
SPA — the sort control's options, the order the view opens in, and the position
badge on each card.

Five source-text assertions used to stand in for that here (``'seriesasc' in
src``, ``sortOptions.map(`` and friends). They were deleted when the e2e spec
landed. A string in a .tsx file cannot tell a removed feature from a renamed
variable: on PR #2115 a refactor that preserved every one of these behaviours
built a superset list and rendered ``activeSortOptions.map(``, and the pin
reported that series sorting had disappeared. ``~/.claude/TESTING-STRATEGY.md``
forbids source-text pins for exactly that reason.

What remains here is what a browser cannot see: the server-side sort map,
asserted against the real objects, and the msgid anchors, which are a fact about
the translation toolchain rather than about anything rendered.
"""
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_CPS = _ROOT / "cps"


@pytest.mark.unit
def test_backend_sort_map_has_series_index():
    """The read-only books API must map seriesasc/seriesdesc to a series_index
    order (not fall back to newest-first), or the SPA's Series-order sort is a
    no-op server-side.

    Asserted against the map itself rather than the source text it used to be
    spelled in: #1331 moved the orders into cps/sort_orders.py and gave each one
    a Books.id tiebreaker, which a substring pin reads as the sort disappearing.
    """
    from cps.api.books import SORT_MAP

    assert str(SORT_MAP["seriesasc"][0]) == "books.series_index ASC"
    assert str(SORT_MAP["seriesdesc"][0]) == "books.series_index DESC"


@pytest.mark.unit
def test_series_sort_msgids_anchored():
    """SPA-only msgids must be anchored in spa_strings.py or the auto-translation
    job strips them (babel doesn't scan .tsx).

    Not a source pin on the feature: the subject IS spa_strings.py's anchor list,
    which is the input to the translation job. No amount of browser driving can
    observe a string that the extractor dropped before the catalogs were built.
    """
    src = (_CPS / "spa_strings.py").read_text()
    assert '_("Series order")' in src
    assert '_("Series order (reverse)")' in src
