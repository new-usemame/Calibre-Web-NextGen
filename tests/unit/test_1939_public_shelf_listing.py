# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Public-shelf listing exception for personal-library users (issue #1939)."""

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import db, ub


pytestmark = pytest.mark.unit
REPO_ROOT = Path(__file__).resolve().parents[2]


def _book(book_id, title):
    now = datetime.now(timezone.utc)
    book = db.Books(
        title, title, "Author", now, db.Books.DEFAULT_PUBDATE,
        "1.0", now, "public-shelf-%d" % book_id, 0, [], [],
    )
    book.id = book_id
    return book


@pytest.fixture
def library(monkeypatch):
    app_engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(app_engine)
    app_session = sessionmaker(bind=app_engine)()

    metadata_engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"calibre": None}},
    )
    db.Base.metadata.create_all(metadata_engine)
    metadata_session = sessionmaker(bind=metadata_engine)()
    metadata_session.add_all([
        _book(1, "Member book"),
        _book(2, "Public shelf book"),
        _book(3, "Private shelf book"),
    ])
    metadata_session.commit()

    user = ub.User(
        name="shelf-viewer",
        email="shelf-viewer@example.invalid",
        password="",
        has_own_library=True,
        user_library_seeded=True,
        default_language="all",
    )
    public_shelf = ub.Shelf(name="Shared", user_id=99, is_public=1)
    private_shelf = ub.Shelf(name="Private", user_id=1, is_public=0)
    app_session.add_all([user, public_shelf, private_shelf])
    app_session.flush()
    public_shelf.books.append(ub.BookShelf(book_id=2, order=1))
    private_shelf.user_id = user.id
    private_shelf.books.append(ub.BookShelf(book_id=3, order=1))
    app_session.add(ub.UserLibraryBook(user_id=user.id, book_id=1))
    app_session.commit()

    cdb = object.__new__(db.CalibreDB)
    cdb.session = metadata_session
    cdb.config = SimpleNamespace(config_restricted_column=0)
    monkeypatch.setattr(db.ub, "session", app_session)
    monkeypatch.setattr(db, "current_user", user)

    yield app_session, metadata_session, cdb, user, public_shelf, private_shelf

    metadata_session.close()
    app_session.close()
    metadata_engine.dispose()
    app_engine.dispose()


def _visible_ids(metadata_session, cdb, **filter_options):
    return [book.id for book in (
        metadata_session.query(db.Books)
        .filter(cdb.common_filters(**filter_options))
        .order_by(db.Books.id).all()
    )]


def test_public_shelf_lists_out_of_set_book(library, monkeypatch):
    from cps import shelf as shelf_module

    app_session, metadata_session, cdb, user, public_shelf, _private = library
    visible = (metadata_session.query(db.Books.id)
               .filter(db.Books.id == 2)
               .filter(cdb.common_filters(allow_public_shelf_books=True)).all())
    assert [row.id for row in visible] == [2]

    monkeypatch.setattr(shelf_module, "calibre_db", cdb)
    assert shelf_module._shelf_book_count(public_shelf, user) == 1
    assert app_session.query(ub.UserLibraryBook).filter_by(
        user_id=user.id, book_id=2,
    ).count() == 0


def test_private_shelf_keeps_out_of_set_book_hidden(library, monkeypatch):
    from cps import shelf as shelf_module

    _app, metadata_session, cdb, user, _public, private_shelf = library
    visible = (metadata_session.query(db.Books.id)
               .filter(db.Books.id == 3)
               .filter(cdb.common_filters(
                   allow_public_shelf_books=bool(private_shelf.is_public)
               )).all())
    assert visible == []

    monkeypatch.setattr(shelf_module, "calibre_db", cdb)
    assert shelf_module._shelf_book_count(private_shelf, user) == 0


def test_ordinary_browse_stays_membership_scoped(library):
    _app, metadata_session, cdb, _user, _public, _private = library
    assert _visible_ids(metadata_session, cdb) == [1]


def test_viewing_public_shelf_does_not_create_membership(library):
    app_session, metadata_session, cdb, user, _public, _private = library
    before = app_session.query(ub.UserLibraryBook).filter_by(user_id=user.id).count()
    assert 2 in _visible_ids(
        metadata_session, cdb, allow_public_shelf_books=True
    )
    after = app_session.query(ub.UserLibraryBook).filter_by(user_id=user.id).count()
    assert (before, after) == (1, 1)


def test_only_public_shelf_listing_paths_opt_in():
    shelf_source = (REPO_ROOT / "cps" / "shelf.py").read_text()
    api_source = (REPO_ROOT / "cps" / "api" / "shelves.py").read_text()
    assert "allow_public_shelf_books=bool(shelf.is_public)" in shelf_source
    assert "allow_public_shelf_books=bool(shelf.is_public)" in api_source
    assert "allow_public_shelf_books" not in (REPO_ROOT / "cps" / "kobo.py").read_text()
    assert "allow_public_shelf_books" not in (REPO_ROOT / "cps" / "opds.py").read_text()
