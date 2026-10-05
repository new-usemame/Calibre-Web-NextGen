# -*- coding: utf-8 -*-
"""Auto metadata fetch must treat ``ratings`` as calibre's shared lookup table (fork #2435).

Calibre stores one ``ratings`` row per value (``UNIQUE(rating)``) and links books to it.
The auto-fetch apply step used to (a) insert a second row for a value the library already
had, which fails the UNIQUE constraint at commit, and (b) in overwrite mode, rewrite the
shared row in place, silently changing the rating of every other book linked to it.

A failed commit then poisoned the session: the error handler read an expired attribute
before rolling back, so it raised ``PendingRollbackError`` itself, the rollback never ran,
and every later provider in the loop failed on the dead session.

These run against a real SQLite calibre schema, because both failures only exist at the
database layer (a fake session accepts the duplicate row and never expires anything).
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

import cps.metadata_helper as m
from cps import db


class _RealCDB:
    """The parts of CalibreDB the auto-fetch path uses, over a real session."""

    session_factory = True

    def __init__(self, session):
        self.session = session

    def get_book(self, book_id):
        return self.session.get(db.Books, book_id)

    def get_author_by_name(self, name):
        return self.session.query(db.Authors).filter(db.Authors.name == name).first()

    def get_publisher_by_name(self, name):
        return self.session.query(db.Publishers).filter(db.Publishers.name == name).first()

    def get_tag_by_name(self, name):
        return self.session.query(db.Tags).filter(db.Tags.name == name).first()

    def get_series_by_name(self, name):
        return self.session.query(db.Series).filter(db.Series.name == name).first()


def _settings(**over):
    base = {
        "auto_metadata_fetch_enabled": True,
        "auto_metadata_smart_application": True,
        "auto_metadata_update_title": False,
        "auto_metadata_update_authors": False,
        "auto_metadata_update_description": False,
        "auto_metadata_update_publisher": False,
        "auto_metadata_update_tags": False,
        "auto_metadata_update_series": False,
        "auto_metadata_update_published_date": False,
        "auto_metadata_update_rating": True,
        "auto_metadata_update_identifiers": False,
        "auto_metadata_update_cover": False,
    }
    base.update(over)
    return base


def _patch_settings(monkeypatch, **over):
    settings = _settings(**over)
    monkeypatch.setattr(
        m, "CWA_DB",
        lambda: SimpleNamespace(get_cwa_settings=lambda: settings),
    )


def _meta(**kw):
    base = dict(title="", authors=[], description="", publisher="", tags=[],
                series=None, series_index=None, publishedDate=None, rating=None,
                identifiers={}, cover=None)
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def library():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS calibre")
    db.Base.metadata.create_all(engine)
    # Same session configuration fetch_and_apply_metadata asks CalibreDB for.
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.execute(db.Books.__table__.insert(), [
        {"id": i, "title": f"Book {i}", "sort": f"Book {i}", "path": f"b{i}", "author_sort": ""}
        for i in (1, 2, 3)
    ])
    session.execute(db.Ratings.__table__.insert(), [{"id": 1, "rating": 8}])
    # Books 1 and 2 already share the 4-star row; book 3 has no rating.
    session.execute(db.books_ratings_link.insert(), [
        {"book": 1, "rating": 1}, {"book": 2, "rating": 1},
    ])
    session.commit()
    return session


def _ratings_by_book(session):
    session.expire_all()
    return {b.id: [r.rating for r in b.ratings] for b in session.query(db.Books).order_by(db.Books.id)}


def test_rating_value_the_library_already_has_links_the_existing_row(monkeypatch, library):
    _patch_settings(monkeypatch)
    book = library.get(db.Books, 3)

    assert m._apply_metadata_to_book(book, _meta(rating=4), _RealCDB(library)) is True

    assert _ratings_by_book(library) == {1: [8], 2: [8], 3: [8]}
    assert library.query(func.count(db.Ratings.id)).scalar() == 1


def test_overwriting_one_books_rating_leaves_books_sharing_the_old_value_alone(monkeypatch, library):
    _patch_settings(monkeypatch, auto_metadata_smart_application=False)
    book = library.get(db.Books, 2)

    assert m._apply_metadata_to_book(book, _meta(rating=3), _RealCDB(library)) is True

    assert _ratings_by_book(library) == {1: [8], 2: [6], 3: []}


def test_out_of_range_provider_rating_is_ignored(monkeypatch, library):
    # MetaRecord.rating is 0-5 stars; 6+ would be stored outside calibre's 0-10 scale.
    _patch_settings(monkeypatch)
    book = library.get(db.Books, 3)

    m._apply_metadata_to_book(book, _meta(rating=7), _RealCDB(library))

    assert _ratings_by_book(library)[3] == []


def test_a_failed_commit_rolls_back_so_the_session_stays_usable(monkeypatch, library):
    # Any flush failure, not only the rating one, must leave the session usable.
    library.connection().exec_driver_sql(
        "CREATE TRIGGER refuse_publisher BEFORE INSERT ON books_publishers_link "
        "BEGIN SELECT RAISE(ABORT, 'refused'); END"
    )
    library.commit()
    _patch_settings(monkeypatch, auto_metadata_update_publisher=True, auto_metadata_update_rating=False)
    book = library.get(db.Books, 3)

    assert m._apply_metadata_to_book(book, _meta(publisher="Acme"), _RealCDB(library)) is False

    assert library.query(func.count(db.Books.id)).scalar() == 3
    assert book.title == "Book 3"


def test_provider_loop_applies_rating_on_first_provider_and_stops(monkeypatch, library):
    # The reporter's flow: smart mode, a book with no rating, the first provider
    # returns a rating the library already has. Before the fix every provider failed.
    _patch_settings(monkeypatch)
    calls = []

    def provider(pid, rating):
        def search(query, generic_cover, locale):
            calls.append(pid)
            return [_meta(title="Book 3", rating=rating)]
        return SimpleNamespace(__id__=pid, __name__=pid, active=True, search=search)

    monkeypatch.setattr(m, "metadata_providers", [provider("first", 4), provider("second", 5)])
    cdb = _RealCDB(library)
    cdb.session.close = lambda: None
    monkeypatch.setattr(m.db, "CalibreDB", type("CDB", (), {
        "session_factory": True,
        "__new__": lambda cls, *a, **k: cdb,
    }))
    _patch_settings(monkeypatch, metadata_provider_hierarchy='["first", "second"]')
    monkeypatch.setattr(m, "_select_metadata_result", lambda results, *a, **k: results[0])

    assert m.fetch_and_apply_metadata(3) is True

    assert calls == ["first"]
    assert _ratings_by_book(library)[3] == [8]
