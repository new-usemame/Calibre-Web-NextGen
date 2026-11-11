"""Additional browse contract checks; the committed paging gate is unchanged."""
from urllib.parse import urlencode

import pytest
from sqlalchemy import event, update

from .browse_paging_world import catalog  # noqa: F401


pytestmark = pytest.mark.unit


@pytest.mark.parametrize('path', ['/authors', '/series'])
def test_substring_means_contiguous_characters(catalog, path):
    payload = catalog.get(path + '?limit=1&q=LE').get_json()
    # Delany has "el", not "le" (the original gate expects the latter).
    assert payload['total'] == 2
    assert payload['unfiltered_total'] == 10
    assert [row['name'] for row in payload['items']] == ['Le Guin']
    assert payload['next_offset'] == 1


@pytest.mark.parametrize('path', ['/authors', '/series'])
def test_legacy_filters_and_out_of_range_page(catalog, path):
    payload = catalog.get(path + '?q=MOO&min_count=2').get_json()
    assert payload == {'items': [{'id': 9, 'name': 'Moon', 'count': 2}]}
    assert catalog.get(path + '?limit=2&offset=100').get_json() == {
        'items': [], 'total': 10, 'unfiltered_total': 10, 'next_offset': None,
    }


@pytest.mark.parametrize('path', ['/authors', '/series'])
@pytest.mark.parametrize('query', [
    'offset=-1', 'offset=one', 'min_count=0', 'min_count=1.5',
    'limit=', 'offset=', 'min_count=', 'limit=1_0', 'q=a&q=b', 'q=%00',
])
def test_invalid_parameters_have_json_errors_even_without_limit(catalog, path, query):
    response = catalog.get(path + '?' + query)
    assert response.status_code == 400
    assert response.json['error']['code'] == 'bad_request'
    assert response.json['error']['message']


@pytest.mark.parametrize('path', ['/authors', '/series'])
@pytest.mark.parametrize('name, needle', [
    ('Åland', 'åL'), ('東京', '東京'), ('100%_real', '%_'), ('A[B].C', '[b].'),
])
def test_name_filter_is_literal_and_unicode_case_insensitive(catalog, path, name, needle):
    from cps import db
    from cps.api import browse
    entity = db.Authors if path == '/authors' else db.Series
    browse.calibre_db.session.execute(update(entity).where(entity.id == 1).values(name=name))
    browse.calibre_db.session.commit()
    payload = catalog.get(path + '?' + urlencode({'limit': 200, 'q': needle})).get_json()
    assert payload['total'] == 1
    assert payload['items'][0]['name'] == name


def test_author_filter_matches_the_displayed_comma(catalog):
    from cps import db
    from cps.api import browse
    browse.calibre_db.session.execute(update(db.Authors).where(db.Authors.id == 1)
                                     .values(name='Adams|Douglas'))
    browse.calibre_db.session.commit()
    payload = catalog.get('/authors?limit=200&q=adams%2Cdouglas').get_json()
    assert payload['total'] == 1
    assert payload['items'][0]['name'] == 'Adams,Douglas'


@pytest.mark.parametrize('path', ['/authors', '/series'])
def test_visible_book_counts_and_totals_are_computed_in_sql(catalog, monkeypatch, path):
    from cps import db
    from cps.api import browse
    session = browse.calibre_db.session
    # Asimov has books 2, 3, 4. Hiding book 4 moves the count below 3.
    monkeypatch.setattr(browse.calibre_db, 'common_filters', lambda: db.Books.id != 4)
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(session.bind, 'before_cursor_execute', capture)
    try:
        payload = catalog.get(path + '?limit=1&min_count=3').get_json()
    finally:
        event.remove(session.bind, 'before_cursor_execute', capture)
    assert payload['total'] == 1
    assert payload['unfiltered_total'] == 10
    assert payload['items'] == [{'id': 7, 'name': 'Le Guin', 'count': 4}]
    assert len(statements) == 3
    assert all('count(*)' in sql.lower() and 'FROM (SELECT' in sql for sql in statements[:2])
    assert 'HAVING count(' in statements[0]
    assert 'LIMIT' in statements[2] and 'OFFSET' in statements[2]
