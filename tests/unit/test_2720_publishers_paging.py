# SPDX-License-Identifier: GPL-3.0-or-later
"""#2720: the New UI Publishers list pages on the server like Authors and Series.

On the reporter's 272k-book library Authors and Series became usable once
they paged (v4.1.60), but Publishers still returned every publisher in one
response and the page could barely scroll. ``/api/v1/publishers`` now takes
the same ``limit``/``offset``/``q``/``min_count`` contract. Calibre leaves
``publishers.sort`` empty, so pages are ordered by name. Without ``limit``
the full list is still returned for the other callers.
"""
import pytest

from .browse_paging_world import AUTHORS, catalog  # noqa: F401

pytestmark = pytest.mark.unit

NAMES = [name for name, _ in AUTHORS]


def _names(payload):
    return [item['name'] for item in payload['items']]


def test_publisher_pages_walk_the_whole_list(catalog):
    seen, offset, pages = [], 0, 0
    while offset is not None:
        response = catalog.get('/publishers?limit=3&offset=%d&lang=en' % offset)
        assert response.status_code == 200
        payload = response.get_json()
        assert payload['total'] == len(NAMES)
        assert len(payload['items']) <= 3
        seen.extend(_names(payload))
        offset = payload['next_offset']
        pages += 1
        assert pages <= 10, 'next_offset never reached the end'
    assert seen == NAMES
    assert pages == 4


def test_publisher_name_filter_and_count_apply_before_paging(catalog):
    payload = catalog.get('/publishers?limit=1&offset=0&q=LE&lang=en').get_json()
    assert payload['total'] == 2
    assert payload['unfiltered_total'] == len(NAMES)
    assert _names(payload) == ['Le Guin']
    assert payload['next_offset'] == 1

    payload = catalog.get('/publishers?limit=50&min_count=2&lang=en').get_json()
    assert _names(payload) == ['Asimov', 'Bradbury', 'Le Guin', 'Moon']
    assert [item['count'] for item in payload['items']] == [3, 2, 4, 2]


def test_publishers_without_limit_return_the_full_list(catalog):
    payload = catalog.get('/publishers?lang=en').get_json()
    assert _names(payload) == NAMES
    assert set(payload) == {'items'}


@pytest.mark.parametrize('query', ['limit=0', 'limit=abc', 'limit=100000', 'offset=-1', 'q=a&q=b'])
def test_bad_publisher_paging_values_are_rejected(catalog, query):
    response = catalog.get('/publishers?lang=en&' + query)
    assert response.status_code == 400
    assert response.json['error']['code'] == 'bad_request'
