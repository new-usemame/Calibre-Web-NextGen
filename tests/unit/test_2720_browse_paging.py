# SPDX-License-Identifier: GPL-3.0-or-later
"""#2720: the New UI Authors and Series lists page through real SQLite.

On a 272k-book library the endpoints returned every author (about 113,000)
in one response and the page drew all of them. A caller that passes
``limit`` gets one ordered page, a ``total`` for the filtered list and a
``next_offset`` to continue from; the name filter (``q``) and the minimum
book count (``min_count``) are applied by the server so a page is a page of
the filtered list. Without ``limit`` the full list is still returned.
"""
import pytest

from .browse_paging_world import AUTHORS, catalog  # noqa: F401


def _names(payload):
    return [item['name'] for item in payload['items']]


@pytest.mark.unit
@pytest.mark.parametrize('path', ['/authors', '/series'])
def test_pages_walk_the_whole_list_in_order_without_gaps(catalog, path):
    expected = [name for name, _ in AUTHORS]
    seen, offset, pages = [], 0, 0
    while offset is not None:
        response = catalog.get('%s?limit=3&offset=%d&lang=en' % (path, offset))
        assert response.status_code == 200
        payload = response.get_json()
        assert payload['total'] == len(AUTHORS)
        assert len(payload['items']) <= 3
        seen.extend(_names(payload))
        offset = payload['next_offset']
        pages += 1
        assert pages <= 10, 'next_offset never reached the end'
    assert seen == expected
    assert pages == 4


@pytest.mark.unit
@pytest.mark.parametrize('path', ['/authors', '/series'])
def test_filters_apply_before_paging(catalog, path):
    # 'LE' matches Le Guin and Leckie case-insensitively; Delany has 'el', not 'le'.
    payload = catalog.get(path + '?limit=1&offset=0&q=LE&lang=en').get_json()
    assert payload['total'] == 2
    assert payload['unfiltered_total'] == len(AUTHORS)
    assert _names(payload) == ['Le Guin']
    assert payload['next_offset'] == 1
    rest = catalog.get(path + '?limit=1&offset=1&q=LE&lang=en').get_json()
    assert _names(rest) == ['Leckie']
    assert rest['next_offset'] is None

    payload = catalog.get(path + '?limit=50&min_count=2&lang=en').get_json()
    assert _names(payload) == ['Asimov', 'Bradbury', 'Le Guin', 'Moon']
    assert payload['total'] == 4
    assert [item['count'] for item in payload['items']] == [3, 2, 4, 2]


@pytest.mark.unit
@pytest.mark.parametrize('path', ['/authors', '/series'])
def test_without_limit_the_full_list_is_unchanged(catalog, path):
    payload = catalog.get(path + '?lang=en').get_json()
    assert _names(payload) == [name for name, _ in AUTHORS]


@pytest.mark.unit
@pytest.mark.parametrize('query', ['limit=0', 'limit=-1', 'limit=abc', 'limit=5&offset=-1',
                                   'limit=100000', 'limit=5&min_count=0'])
def test_bad_paging_values_are_rejected(catalog, query):
    assert catalog.get('/authors?lang=en&' + query).status_code == 400
