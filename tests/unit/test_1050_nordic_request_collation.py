# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-request Nordic order through real SQLite and the public JSON view."""
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask
from flask_babel import Babel
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def catalog(monkeypatch, tmp_path):
    from cps import db, cw_babel
    from cps.api import browse

    engine = create_engine('sqlite://', execution_options={'schema_translate_map': {'calibre': None}})
    event.listen(engine, 'connect', db._register_sqlite_udfs)
    for table in (db.Books.__table__, db.Authors.__table__, db.books_authors_link, db.Data.__table__,
                  db.Tags.__table__, db.Series.__table__, db.Publishers.__table__,
                  db.books_tags_link, db.books_series_link, db.books_publishers_link):
        table.create(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(browse.calibre_db, 'session', session)
    monkeypatch.setattr(browse.calibre_db, 'common_filters', lambda *args, **kwargs: True)
    app = Flask(__name__)
    from babel.messages.pofile import read_po
    from babel.messages.mofile import write_mo
    catalog_dir = tmp_path / 'translations'
    target = catalog_dir / 'fr/LC_MESSAGES/messages.mo'
    target.parent.mkdir(parents=True)
    with (Path(__file__).parents[2] / 'cps/translations/fr/LC_MESSAGES/messages.po').open() as source:
        french = read_po(source, locale='fr')
    with target.open('wb') as output:
        write_mo(output, french)
    app.config['BABEL_TRANSLATION_DIRECTORIES'] = str(catalog_dir)
    Babel(app, locale_selector=cw_babel.get_locale)
    monkeypatch.setattr(cw_babel, 'current_user', SimpleNamespace(name='reader', locale='en'))
    app.add_url_rule('/authors', view_func=inspect.unwrap(browse.list_authors))
    with engine.begin() as conn:
        for i, name in enumerate(['Aalto', 'Zulu', 'Åland', 'Ängel', 'Örebro'], 1):
            conn.execute(db.Authors.__table__.insert().values(id=i, name=name, sort=name, link=''))
            conn.execute(text("INSERT INTO books (id,title,sort,author_sort,timestamp,pubdate,series_index,last_modified,path,has_cover,uuid) VALUES (:i,:n,:n,:n,'2026-01-01','2026-01-01',1,'2026-01-01','.',0,:n)"), {'i': i, 'n': name})
            conn.execute(db.books_authors_link.insert().values(book=i, author=i))
            conn.execute(db.Data.__table__.insert().values(book=i, format='EPUB', uncompressed_size=1, name=name))
            for entity, link, field in [(db.Tags, db.books_tags_link, 'tag'),
                                        (db.Series, db.books_series_link, 'series'),
                                        (db.Publishers, db.books_publishers_link, 'publisher')]:
                values = {'id': i, 'name': name}
                if entity != db.Tags:
                    values['sort'] = name
                conn.execute(entity.__table__.insert().values(**values))
                conn.execute(link.insert().values(**{'book': i, field: i}))
    try:
        yield app, session, cw_babel
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize('locale', ['sv', 'fi'])
def test_nordic_author_json_puts_distinct_letters_after_z(catalog, locale):
    app, _, _ = catalog
    response = app.test_client().get('/authors?lang=' + locale)
    assert response.status_code == 200
    assert [row['name'] for row in response.json['items']] == ['Aalto', 'Zulu', 'Åland', 'Ängel', 'Örebro']



@pytest.mark.parametrize('locale, expected', [
    ('sv', ['Aalto', 'Zulu', 'Åland', 'Ängel', 'Örebro']),
    ('de', ['Aalto', 'Åland', 'Ängel', 'Örebro', 'Zulu']),
])
@pytest.mark.parametrize('surface', ['api', 'opds'])
def test_publisher_json_sorts_displayed_names_when_calibre_sort_is_null(catalog, monkeypatch, locale, expected, surface):
    from cps import db
    from cps.api import browse
    from sqlalchemy import update
    app, session, _ = catalog
    # Calibre imports publisher names without a populated sort column. Use
    # deliberately different ID/name order so NULL-key/ID order cannot pass.
    for identity in range(1, 6):
        session.execute(update(db.Publishers).where(db.Publishers.id == identity)
                        .values(name='Publisher placeholder ' + str(identity), sort=None))
    for identity, name in enumerate(['Örebro', 'Aalto', 'Åland', 'Zulu', 'Ängel'], 1):
        session.execute(update(db.Publishers).where(db.Publishers.id == identity).values(name=name, sort=None))
    session.commit()
    if surface == 'api':
        app.add_url_rule('/publishers', view_func=inspect.unwrap(browse.list_publishers))
        response = app.test_client().get('/publishers?lang=' + locale)
        assert response.status_code == 200
        assert [row['name'] for row in response.json['items']] == expected
    else:
        from cps import opds, jinjia
        from jinja2 import FileSystemLoader
        from xml.etree import ElementTree
        from urllib.parse import urlsplit, parse_qs
        viewer = SimpleNamespace(name='Publisher reader', locale=locale, check_visibility=lambda _: True)
        monkeypatch.setattr(opds.auth, 'current_user', lambda: viewer)
        monkeypatch.setattr(opds, 'get_opds_restricted_common_filter', lambda: True)
        monkeypatch.setattr(opds.config, 'config_books_per_page', 2, raising=False)
        monkeypatch.setattr(opds.config, 'config_calibre_web_title', 'Catalog', raising=False)
        app.jinja_loader = FileSystemLoader(str(Path(__file__).parents[2] / 'cps/templates'))
        app.register_blueprint(jinjia.jinjia)
        app.register_blueprint(opds.opds)
        app.view_functions['opds.feed_publisherindex'] = inspect.unwrap(opds.feed_publisherindex)
        client = app.test_client()
        path, names = '/opds/publisher?lang=' + locale, []
        atom = '{http://www.w3.org/2005/Atom}'
        for page in range(4):
            response = client.get(path)
            assert response.status_code == 200
            xml = ElementTree.fromstring(response.data)
            names.extend(entry.findtext(atom + 'title') for entry in xml.findall(atom + 'entry'))
            next_path = next((link.get('href') for link in xml.findall(atom + 'link')
                              if link.get('rel') == 'next'), None)
            if not next_path:
                break
            assert parse_qs(urlsplit(next_path).query).get('lang') == [locale]
            path = next_path
        else:
            pytest.fail('Publisher paging did not finish')
        assert names == expected

def test_two_locales_reuse_one_connection_without_changing_each_other(catalog):
    from cps import db
    from cps.sort_orders import book_sort_order
    app, session, cw_babel = catalog
    for locale, expected in [('sv', ['Aalto', 'Zulu', 'Åland', 'Ängel', 'Örebro']),
                             ('de', ['Aalto', 'Åland', 'Ängel', 'Örebro', 'Zulu']),
                             ('fi', ['Aalto', 'Zulu', 'Åland', 'Ängel', 'Örebro'])]:
        cw_babel.current_user.locale = locale
        with app.test_request_context('/books'):
            rows = session.query(db.Books.sort).order_by(*book_sort_order('abc')).all()
            assert [row[0] for row in rows] == expected



@pytest.mark.parametrize('locale, expected', [
    ('sv', ['Aalto', 'Zulu', 'Åland', 'Ängel', 'Örebro']),
    ('fi', ['Aalto', 'Zulu', 'Åland', 'Ängel', 'Örebro']),
    ('de', ['Aalto', 'Åland', 'Ängel', 'Örebro', 'Zulu']),
])
@pytest.mark.parametrize('sort', ['abc', 'zyx'])
def test_book_json_alphabetical_sort_orders_actual_rows(catalog, monkeypatch, locale, expected, sort):
    from cps import db
    from cps.api import books
    from cps.pagination import Pagination
    from sqlalchemy.orm import noload
    app, session, _ = catalog
    def fill(page, database, limit, predicate, order, *args, **kwargs):
        rows = session.query(db.Books).options(noload('*')).order_by(*order).offset(1).limit(3).all()
        # Keep the real sort/query and JSON serializer; unrelated metadata is
        # represented at the established catalog-row seam.
        entries = [SimpleNamespace(Books=SimpleNamespace(
            id=row.id, title=row.title, series_index=row.series_index, has_cover=row.has_cover,
            authors=[], series=[], data=[]), is_archived=False, read_status=None) for row in rows]
        return entries, None, Pagination(1, 3, 5)
    monkeypatch.setattr(books.calibre_db, 'fill_indexpage', fill)
    monkeypatch.setattr(books.config, 'config_books_per_page', 3, raising=False)
    monkeypatch.setattr(books.config, 'config_read_column', 0, raising=False)
    app.add_url_rule('/api/v1/books', view_func=inspect.unwrap(books.list_books))
    response = app.test_client().get('/api/v1/books?lang=' + locale + '&sort=' + sort)
    assert response.status_code == 200
    ordered = expected if sort == 'abc' else expected[::-1]
    assert [row['title'] for row in response.json['items']] == ordered[1:4]

def test_classic_author_order_and_letter_buckets_share_request_locale(catalog, monkeypatch):
    from cps import web
    app, _, _ = catalog
    monkeypatch.setattr(web, 'current_user', SimpleNamespace(
        check_visibility=lambda _: True, get_view_property=lambda *_: 'asc'))
    monkeypatch.setattr(web, 'render_title_template', lambda template, **context: {
        'names': [row[0].name for row in context['entries']],
        'letters': [row[0] for row in context['charlist']],
    })
    app.add_url_rule('/classic-authors', view_func=inspect.unwrap(web.author_list))
    response = app.test_client().get('/classic-authors?lang=sv')
    assert response.status_code == 200
    assert response.json == {'names': ['Aalto', 'Zulu', 'Åland', 'Ängel', 'Örebro'],
                             'letters': ['A', 'Z', 'Å', 'Ä', 'Ö']}


@pytest.mark.parametrize('locale, words, expected', [
    ('da', ['Aalto', 'Åland', 'Øresund', 'Ægir', 'Zulu'], ['Zulu', 'Ægir', 'Øresund', 'Åland', 'Aalto']),
    ('no', ['Aalto', 'Åland', 'Øresund', 'Ægir', 'Zulu'], ['Zulu', 'Ægir', 'Øresund', 'Åland', 'Aalto']),
    ('nn', ['Aalto', 'Åland', 'Øresund', 'Ægir', 'Zulu'], ['Zulu', 'Ægir', 'Øresund', 'Åland', 'Aalto']),
])
def test_untranslated_nordic_override_sorts_actual_sql_and_buckets(catalog, locale, words, expected):
    from cps.unicode_collation import locale_sort_key, locale_initial
    from sqlalchemy import literal, union_all, select
    app, session, _ = catalog
    values = union_all(*(select(literal(word).label('name')) for word in words)).subquery()
    with app.test_request_context('/authors?lang=' + locale):
        rows = session.execute(select(values.c.name, locale_initial(values.c.name))
                               .order_by(locale_sort_key(values.c.name), values.c.name)).all()
    assert [row[0] for row in rows] == expected
    assert dict(rows)['Aalto'] == 'Å'
    assert dict(rows)['Ægir'] == 'Æ'
    assert dict(rows)['Øresund'] == 'Ø'


def test_basic_auth_viewer_locale_controls_opds_even_with_another_cookie_user(catalog, monkeypatch):
    from cps import opds
    app, _, cw_babel = catalog
    cw_babel.current_user.locale = 'de'
    basic_viewer = SimpleNamespace(name='Nordic reader', locale='sv', check_visibility=lambda _: True)
    monkeypatch.setattr(opds.auth, 'current_user', lambda: basic_viewer)
    monkeypatch.setattr(opds, 'get_opds_restricted_common_filter', lambda: True)
    monkeypatch.setattr(opds.config, 'config_books_per_page', 30, raising=False)
    monkeypatch.setattr(opds, 'render_xml_template', lambda template, **context: {
        'names': [row.name for row in context['listelements']],
    })
    app.add_url_rule('/opds/author/letter/<book_id>', view_func=inspect.unwrap(opds.feed_letter_author))
    response = app.test_client().get('/opds/author/letter/Å')
    assert response.status_code == 200
    assert response.json['names'] == ['Åland']


def test_higher_priority_regional_ui_language_beats_lower_nordic_preference(catalog):
    app, _, cw_babel = catalog
    cw_babel.current_user.name = 'Guest'
    with app.test_request_context('/authors', headers={'Accept-Language': 'fr-FR, sv;q=0.8'}):
        assert cw_babel.get_locale() == 'fr'
        assert cw_babel.get_collation_locale() == 'fr'


def test_native_key_fault_does_not_turn_a_book_query_into_empty_catalog(catalog, monkeypatch):
    from cps import db, nordic_collation
    from cps.unicode_collation import locale_sort_key
    from sqlalchemy.exc import OperationalError
    app, session, _ = catalog
    native = nordic_collation.context('sv')
    original = native.sort_key
    def fail_one(value):
        if value == 'Örebro':
            raise nordic_collation.NativeKeyError('injected native buffer fault')
        return original(value)
    monkeypatch.setattr(native, 'sort_key', fail_one)
    monkeypatch.setattr(db, 'current_user', SimpleNamespace(show_detail_random=lambda: False))
    catalog_db = SimpleNamespace(
        ensure_session=lambda: None, session=session, common_filters=lambda *args, **kwargs: True,
        config=SimpleNamespace(config_books_per_page=30), order_authors=lambda entries, *args: entries)
    with app.test_request_context('/books?lang=sv'):
        with pytest.raises(OperationalError, match='user-defined function raised exception'):
            db.CalibreDB.fill_indexpage_with_archived_books(
                catalog_db, 1, db.Authors, 30, True, [locale_sort_key(db.Authors.sort)], False, False, None)
        # Keep the pre-existing handling of an unrelated query failure bounded.
        entries, _, _ = db.CalibreDB.fill_indexpage_with_archived_books(
            catalog_db, 1, db.Authors, 30, True, [text('missing_ordinary_column')], False, False, None)
        assert entries == []


@pytest.mark.parametrize('sort', ['title', 'sort', 'author_sort', 'authors_sort', 'tags', 'series', 'publishers', 'authors'])
@pytest.mark.parametrize('direction', ['asc', 'desc'])
def test_classic_table_string_sort_uses_the_same_nordic_order(catalog, monkeypatch, sort, direction):
    from cps import web, db
    app, session, _ = catalog
    # Exercise the public handler's ORM order against the actual catalog table;
    # the rendering seam returns the resulting titles without metadata encoding.
    captured = []
    def fill(page, database, limit, predicate, order, *args, **kwargs):
        from sqlalchemy.orm import joinedload, noload
        from sqlalchemy.sql.elements import ColumnElement
        query = session.query(db.Books).options(noload('*'), joinedload(db.Books.data))
        joins = list(args[3:])  # production joins follow archive/read/config flags
        while joins:
            entity = joins.pop(0)
            if joins and isinstance(joins[0], ColumnElement):
                query = query.outerjoin(entity, joins.pop(0))
            else:
                query = query.outerjoin(entity)
        rows = query.order_by(*order).offset(1).limit(3).all()
        assert all(row.data for row in rows)
        captured.extend(row.title for row in rows)
        return [], None, None
    monkeypatch.setattr(web.calibre_db, 'fill_indexpage_with_archived_books', fill)
    monkeypatch.setattr(web.config, 'config_read_column', '', raising=False)
    app.add_url_rule('/table-books', view_func=inspect.unwrap(web.list_books))
    response = app.test_client().get('/table-books?lang=sv&sort=' + sort + '&order=' + direction + '&offset=1&limit=3')
    assert response.status_code == 200
    expected = ['Aalto', 'Zulu', 'Åland', 'Ängel', 'Örebro']
    assert captured == (expected if direction == 'asc' else expected[::-1])[1:4]


def test_opds_xml_index_links_and_pages_preserve_nordic_sorting(catalog, monkeypatch):
    from cps import opds
    from jinja2 import FileSystemLoader
    from xml.etree import ElementTree
    from urllib.parse import urlsplit, parse_qs
    app, _, cw_babel = catalog
    cw_babel.current_user.locale = 'de'
    viewer = SimpleNamespace(name='Nordic reader', locale='sv', check_visibility=lambda _: True)
    monkeypatch.setattr(opds.auth, 'current_user', lambda: viewer)
    monkeypatch.setattr(opds, 'get_opds_restricted_common_filter', lambda: True)
    monkeypatch.setattr(opds.config, 'config_books_per_page', 2, raising=False)
    monkeypatch.setattr(opds.config, 'config_calibre_web_title', 'Catalog', raising=False)
    app.jinja_loader = FileSystemLoader(str(Path(__file__).parents[2] / 'cps/templates'))
    from cps import jinjia
    app.register_blueprint(jinjia.jinjia)
    app.register_blueprint(opds.opds)
    # Supply the authorized viewer at the auth seam; keep real SQL, XML,
    # blueprint URL defaults, pagination and subsequently followed links.
    for endpoint, view in [('opds.feed_authorindex', opds.feed_authorindex),
                           ('opds.feed_letter_author', opds.feed_letter_author)]:
        app.view_functions[endpoint] = inspect.unwrap(view)
    client = app.test_client()
    atom = '{http://www.w3.org/2005/Atom}'
    path = '/opds/author?lang=sv'
    names, letter_link = [], None
    for _ in range(4):
        response = client.get(path)
        assert response.status_code == 200
        assert response.mimetype == 'application/atom+xml'
        feed = ElementTree.fromstring(response.data)
        for entry in feed.findall(atom + 'entry'):
            name = entry.findtext(atom + 'title')
            names.append(name)
            href = entry.find(atom + 'link').get('href')
            assert parse_qs(urlsplit(href).query)['lang'] == ['sv']
            if name == 'Å':
                letter_link = href
        next_link = feed.find(atom + "link[@rel='next']")
        if next_link is None:
            break
        path = next_link.get('href')
        assert parse_qs(urlsplit(path).query)['lang'] == ['sv']
    assert names == ['All', 'A', 'Z', 'Å', 'Ä', 'Ö']
    assert letter_link is not None
    response = client.get(letter_link)
    assert response.status_code == 200
    feed = ElementTree.fromstring(response.data)
    assert [entry.findtext(atom + 'title') for entry in feed.findall(atom + 'entry')] == ['Åland']
