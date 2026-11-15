# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Quick search must read each name once, not once per book that links to it (#2720).

``lower()`` on the Calibre connection is a Python function (``lcase``: lower
case plus unidecode, so "cafe" finds "Café"). The search predicate used to
call it inside a correlated EXISTS for every book and every tag, author,
series and publisher linked to that book, twice per row because ``ilike``
wraps ``lower()`` in another ``lower()``. On a generated 270,000-book library
one search made 8.6 to 11.6 million calls and took 6 to 14 seconds before any
custom column was added; the reporter saw more than 30 seconds.

The matching semantics stay as they were, so this file checks both: which
books a term finds, and that the number of ``lower()`` calls grows with the
number of books plus the number of names, not with books times links.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from cps import db, ub


pytestmark = pytest.mark.unit

N_SHARED = 120  # books that share one tag, author, series and publisher


@pytest.fixture
def library(monkeypatch):
    calls = [0]
    real_lcase = db.lcase

    def counting_lcase(value):
        calls[0] += 1
        return real_lcase(value)

    # _register_sqlite_udfs reads the module global at connect time.
    monkeypatch.setattr(db, "lcase", counting_lcase)
    engine = create_engine(
        "sqlite://",
        execution_options={"schema_translate_map": {"calibre": None}},
    )
    event.listen(engine, "connect", db._register_sqlite_udfs)
    db.Books.metadata.create_all(engine)
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    def book(book_id, title):
        created = datetime.now(timezone.utc)
        b = db.Books(title, title, "x", created, created, "1.0", created,
                     "search-cost-{}".format(book_id), 0, [], [])
        b.id = book_id
        return b

    shared_tag = db.Tags("Gardening")
    shared_author = db.Authors("Ursula Le Guin", "Le Guin, Ursula")
    shared_series = db.Series("Earthsea", "Earthsea")
    shared_publisher = db.Publishers("Parnassus", "Parnassus")
    accented_tag = db.Tags("Café culture")
    other_author = db.Authors("Ted Chiang", "Chiang, Ted")

    books = []
    for i in range(1, N_SHARED + 1):
        b = book(i, "Volume {}".format(i))
        b.tags = [shared_tag]
        b.authors = [shared_author]
        b.series = [shared_series]
        b.publishers = [shared_publisher]
        books.append(b)
    # Distinct books that each match through exactly one field.
    by_title = book(1001, "A Wizard of the Night")
    by_accent = book(1002, "Plain")
    by_accent.tags = [accented_tag]
    two_authors = book(1003, "Anthology")
    two_authors.authors = [shared_author, other_author]
    nothing = book(1004, "Unrelated")
    nothing.authors = [other_author]
    books += [by_title, by_accent, two_authors, nothing]
    session.add_all(books)
    session.commit()

    user = SimpleNamespace(
        id=1, is_anonymous=False, filter_language=lambda: "all",
        list_allowed_tags=lambda: [""], list_denied_tags=lambda: [""],
        allowed_column_value="", denied_column_value="", has_own_library=False,
    )
    monkeypatch.setattr(db, "current_user", user)
    monkeypatch.setattr(ub, "session", session)
    cdb = object.__new__(db.CalibreDB)
    cdb.session = session
    cdb.ensure_session = lambda: None
    cdb.config = SimpleNamespace(config_restricted_column=0)
    cdb.get_cc_columns = lambda *_a, **_k: []
    config = SimpleNamespace(config_read_column=0)

    def search(term):
        calls[0] = 0
        query = cdb.search_query(term, config, user=user, eager_data=False)
        ids = sorted(row[0].id for row in query.all())
        return ids, calls[0]

    try:
        yield search
    finally:
        session.close()
        engine.dispose()


SHARED_IDS = list(range(1, N_SHARED + 1))


@pytest.mark.parametrize(("term", "expected"), [
    ("garden", SHARED_IDS),                 # tag, case-insensitive substring
    ("le guin", SHARED_IDS + [1003]),       # author: every word must match an author
    ("guin ursula", SHARED_IDS + [1003]),   # word order does not matter
    ("guin chiang", [1003]),                # words may match different authors of one book
    ("EARTH", SHARED_IDS),                  # series
    ("parnass", SHARED_IDS),                # publisher
    ("wizard of", [1001]),                  # title keeps the phrase whole
    ("cafe", [1002]),                       # accents fold: "cafe" finds "Café"
    ("café", [1002]),
    ("zzz", []),
])
def test_quick_search_finds_the_same_books(library, term, expected):
    ids, _calls = library(term)
    assert ids == expected


@pytest.mark.parametrize("term", ["garden", "le guin", "nomatch"])
def test_quick_search_calls_lower_once_per_book_and_name(library, term):
    _ids, calls = library(term)
    books = N_SHARED + 4
    names = 6  # tags, authors, series and publishers in the library
    words = len(term.split())
    # Title once per book, each name table once per author word or term,
    # plus the term itself. The old predicate made several calls per link
    # row per book (750 to 1,830 here for 124 books).
    assert calls <= books + names * (words + 1) + 10, calls


CC_TEXT, CC_COMMENTS = 13301, 13302


def test_quick_search_matches_each_kind_of_custom_column(monkeypatch):
    """Text (link table) and long-text (one row per book) columns each keep a
    matching book and leave the others out."""
    if CC_TEXT not in db.cc_classes:
        db.CalibreDB.setup_db_cc_classes([
            SimpleNamespace(id=CC_TEXT, datatype="text"),
            SimpleNamespace(id=CC_COMMENTS, datatype="comments"),
        ])
    engine = create_engine(
        "sqlite://",
        execution_options={"schema_translate_map": {"calibre": None}},
    )
    event.listen(engine, "connect", db._register_sqlite_udfs)
    db.Books.metadata.create_all(engine)
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    created = datetime.now(timezone.utc)
    books = []
    for book_id in range(1, 6):
        b = db.Books("Book {}".format(book_id), "x", "x", created, created, "1.0", created,
                     "search-cc-{}".format(book_id), 0, [], [])
        b.id = book_id
        books.append(b)
    session.add_all(books)
    session.flush()
    text_value = db.cc_classes[CC_TEXT](value="Shelf Fantôme")
    other_text = db.cc_classes[CC_TEXT](value="Elsewhere")
    session.add_all([text_value, other_text])
    session.flush()
    session.execute(db.Books.custom_column_13301.property.secondary.insert(), [
        {"book": 1, "value": text_value.id}, {"book": 2, "value": other_text.id},
    ])
    session.add(db.cc_classes[CC_COMMENTS](book=3, value="A note about fantomes"))
    session.commit()

    user = SimpleNamespace(
        id=1, is_anonymous=False, filter_language=lambda: "all",
        list_allowed_tags=lambda: [""], list_denied_tags=lambda: [""],
        allowed_column_value="", denied_column_value="", has_own_library=False,
    )
    monkeypatch.setattr(db, "current_user", user)
    monkeypatch.setattr(ub, "session", session)
    cdb = object.__new__(db.CalibreDB)
    cdb.session = session
    cdb.ensure_session = lambda: None
    cdb.config = SimpleNamespace(config_restricted_column=0)
    columns = [SimpleNamespace(id=i, datatype=d) for i, d in
               ((CC_TEXT, "text"), (CC_COMMENTS, "comments"))]
    cdb.get_cc_columns = lambda *_a, **_k: columns
    config = SimpleNamespace(config_read_column=0)

    def search(term):
        query = cdb.search_query(term, config, user=user, eager_data=False)
        return sorted(row[0].id for row in query.all())

    try:
        assert search("fantome") == [1, 3]   # text and long-text columns, accents folded
        assert search("elsewhere") == [2]
        assert search("nowhere") == []
    finally:
        session.close()
        engine.dispose()
