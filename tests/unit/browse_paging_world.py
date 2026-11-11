# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared real-SQLite catalog for the #2720 Authors/Series paging tests.

One Flask app for every test that browses this catalog, kept out of the
``test_*.py`` files per the Flask-app ratchet (tests/fitness/test_no_new_flask_apps.py).
"""
import inspect
from types import SimpleNamespace

import pytest
from flask import Flask
from flask_babel import Babel
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

# (name, number of books). Sort keys equal the names, so the expected order
# is plain alphabetical in English.
AUTHORS = [
    ('Adams', 1), ('Asimov', 3), ('Banks', 1), ('Bradbury', 2), ('Clarke', 1),
    ('Delany', 1), ('Le Guin', 4), ('Leckie', 1), ('Moon', 2), ('Zelazny', 1),
]


@pytest.fixture
def catalog(monkeypatch):
    from cps import db, cw_babel
    from cps.api import browse

    engine = create_engine('sqlite://', execution_options={'schema_translate_map': {'calibre': None}})
    event.listen(engine, 'connect', db._register_sqlite_udfs)
    for table in (db.Books.__table__, db.Authors.__table__, db.books_authors_link,
                  db.Series.__table__, db.books_series_link):
        table.create(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(browse.calibre_db, 'session', session)
    monkeypatch.setattr(browse.calibre_db, 'common_filters', lambda *args, **kwargs: True)
    app = Flask(__name__)
    Babel(app, locale_selector=cw_babel.get_locale)
    monkeypatch.setattr(cw_babel, 'current_user', SimpleNamespace(name='reader', locale='en'))
    app.add_url_rule('/authors', view_func=inspect.unwrap(browse.list_authors))
    app.add_url_rule('/series', view_func=inspect.unwrap(browse.list_series))
    book_id = 0
    with engine.begin() as conn:
        for entity_id, (name, books) in enumerate(AUTHORS, 1):
            conn.execute(db.Authors.__table__.insert().values(id=entity_id, name=name, sort=name, link=''))
            conn.execute(db.Series.__table__.insert().values(id=entity_id, name=name, sort=name))
            for _ in range(books):
                book_id += 1
                conn.execute(text(
                    "INSERT INTO books (id,title,sort,author_sort,timestamp,pubdate,series_index,"
                    "last_modified,path,has_cover,uuid) VALUES (:i,:n,:n,:n,'2026-01-01','2026-01-01',"
                    "1,'2026-01-01','.',0,:u)"), {'i': book_id, 'n': name, 'u': 'u%d' % book_id})
                conn.execute(db.books_authors_link.insert().values(book=book_id, author=entity_id))
                conn.execute(db.books_series_link.insert().values(book=book_id, series=entity_id))
    try:
        yield app.test_client()
    finally:
        session.close()
        engine.dispose()
