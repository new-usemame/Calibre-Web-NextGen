# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""A shelf's count ignores rows whose book no longer exists (issue #2235).

A book deleted outside the app (Calibre desktop, a re-import) leaves its
``book_shelf_link`` row behind. The shelf page joins to the library, so it never
shows that book; v4.1.43 counted raw rows, so the new UI's sidebar badge stayed
one above what the shelf showed, and removing a book looked like it had not
changed the count. Only the classic shelf page deletes those rows, which is why
switching UIs "fixed" it. The count must agree with the page, orphan rows or not.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import db, ub


pytestmark = pytest.mark.unit


def _book(book_id, title):
    now = datetime.now(timezone.utc)
    book = db.Books(
        title, title, "Author", now, db.Books.DEFAULT_PUBDATE,
        "1.0", now, "shelf-count-%d" % book_id, 0, [], [],
    )
    book.id = book_id
    return book


@pytest.fixture
def shelf_with_orphan(monkeypatch):
    from cps import shelf as shelf_module

    app_engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(app_engine)
    app_session = sessionmaker(bind=app_engine)()
    metadata_engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"calibre": None}},
    )
    db.Base.metadata.create_all(metadata_engine)
    metadata_session = sessionmaker(bind=metadata_engine)()

    metadata_session.add_all([_book(1, "Still here"), _book(2, "Also here")])
    metadata_session.commit()

    user = ub.User(name="reader", email="reader@example.invalid", password="",
                   default_language="all", sidebar_view=0)
    shelf = ub.Shelf(name="Mine", user_id=1, is_public=0)
    app_session.add_all([user, shelf])
    app_session.flush()
    shelf.user_id = user.id
    for order, book_id in enumerate((1, 2, 99), start=1):  # 99 was deleted outside the app
        shelf.books.append(ub.BookShelf(book_id=book_id, order=order))
    app_session.commit()

    cdb = object.__new__(db.CalibreDB)
    cdb.session = metadata_session
    cdb.config = SimpleNamespace(config_restricted_column=0)
    monkeypatch.setattr(db.ub, "session", app_session)
    monkeypatch.setattr(db, "current_user", user)
    monkeypatch.setattr(shelf_module, "calibre_db", cdb)

    yield shelf_module, app_session, shelf, user

    metadata_session.close()
    app_session.close()
    metadata_engine.dispose()
    app_engine.dispose()


def test_count_skips_a_row_whose_book_was_deleted_outside_the_app(shelf_with_orphan):
    shelf_module, app_session, shelf, user = shelf_with_orphan
    assert app_session.query(ub.BookShelf).filter_by(shelf=shelf.id).count() == 3
    assert shelf_module._shelf_book_count(shelf, user) == 2


def test_removing_a_book_lowers_the_count_even_with_an_orphan_present(shelf_with_orphan):
    shelf_module, _app_session, shelf, user = shelf_with_orphan
    status, _message = shelf_module.remove_book_from_shelf(shelf, 1)
    assert status == shelf_module.SHELF_OK
    assert shelf_module._shelf_book_count(shelf, user) == 1
