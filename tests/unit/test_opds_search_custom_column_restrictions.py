# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""OPDS search must apply the authenticated reader's current visibility policy."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from flask import Flask
from flask_babel import Babel
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from cps import db, opds, ub


pytestmark = pytest.mark.unit
BOOL_COLUMN_ID = 13222


@pytest.mark.parametrize(
    ("cookie_allowed", "cookie_denied"),
    [
        ("", ""),
        # The browser account's policy is deliberately stricter and opposite:
        # this distinguishes passing Basic Auth into search_query's own
        # common_filters() from applying only a later OPDS filter.
        ("false", "true,undefined"),
    ],
    ids=["unrestricted-cookie", "stricter-cookie"],
)
def test_opds_search_hides_boolean_denials_for_the_basic_auth_user(
        monkeypatch, cookie_allowed, cookie_denied):
    if BOOL_COLUMN_ID not in db.cc_classes:
        db.CalibreDB.setup_db_cc_classes([
            SimpleNamespace(id=BOOL_COLUMN_ID, datatype="bool"),
        ])
    value_model = db.cc_classes[BOOL_COLUMN_ID]
    engine = create_engine(
        "sqlite://",
        execution_options={"schema_translate_map": {"calibre": None}},
    )
    event.listen(engine, "connect", db._register_sqlite_udfs)
    db.Books.metadata.create_all(engine)
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    books = []
    for book_id, title in enumerate((
            "Value true", "Value false", "Value undefined", "Value missing"), 1):
        created = datetime.now(timezone.utc)
        book = db.Books(
            title, title, "Author", created, created, "1.0", created,
            "opds-restriction-{}".format(book_id), 0, [], [],
        )
        book.id = book_id
        books.append(book)
    session.add_all(books)
    session.flush()
    session.add_all([
        value_model(book=books[0].id, value=True),
        value_model(book=books[1].id, value=False),
        value_model(book=books[2].id, value=None),
    ])
    # The same reader owns the read/archive joins returned with each search
    # result. The cookie user has a conflicting state for this book.
    session.add_all([
        ub.ReadBook(book_id=books[0].id, user_id=20, read_status=ub.ReadBook.STATUS_IN_PROGRESS),
        ub.ReadBook(book_id=books[0].id, user_id=10, read_status=ub.ReadBook.STATUS_UNREAD),
        ub.ArchivedBook(book_id=books[0].id, user_id=20, is_archived=False),
        ub.ArchivedBook(book_id=books[0].id, user_id=10, is_archived=True),
    ])
    # Deliberately make the Flask cookie user unrestricted. OPDS Basic Auth
    # is the policy identity for this request, and denies the explicit False.
    secondary = SimpleNamespace(
        id=20,
        is_anonymous=False,
        opds_only_shelves_sync=0,
        has_own_library=False,
        filter_language=lambda: "all",
        list_allowed_tags=lambda: [""],
        list_denied_tags=lambda: [""],
        allowed_column_value="true,undefined",
        denied_column_value="false",
    )
    web_session_user = SimpleNamespace(
        id=10,
        is_anonymous=False,
        filter_language=lambda: "all",
        list_allowed_tags=lambda: [""],
        list_denied_tags=lambda: [""],
        allowed_column_value=cookie_allowed,
        denied_column_value=cookie_denied,
        has_own_library=False,
    )
    session.commit()

    cdb = object.__new__(db.CalibreDB)
    cdb.session = session
    cdb.ensure_session = lambda: None
    cdb.config = SimpleNamespace(config_restricted_column=BOOL_COLUMN_ID)
    cdb.get_cc_columns = lambda *_args, **_kwargs: []
    config = SimpleNamespace(config_read_column=0)
    app = Flask(__name__)
    Babel(app)

    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(db, "current_user", web_session_user)
    monkeypatch.setattr(opds, "calibre_db", cdb)
    monkeypatch.setattr(opds, "config", config)
    monkeypatch.setattr(opds.auth, "current_user", lambda: secondary)
    monkeypatch.setattr(opds, "render_xml_template", lambda *_args, **kwargs: kwargs)

    try:
        with app.test_request_context("/opds/search/value"):
            result = opds.feed_search("value")

        visible_ids = sorted(entry[0].id for entry in result["entries"])
        assert visible_ids == [books[0].id, books[2].id, books[3].id]
        first_book_entry = next(entry for entry in result["entries"] if entry[0].id == books[0].id)
        assert first_book_entry[1] is False
        assert first_book_entry[2] == ub.ReadBook.STATUS_IN_PROGRESS
    finally:
        session.close()
        engine.dispose()
