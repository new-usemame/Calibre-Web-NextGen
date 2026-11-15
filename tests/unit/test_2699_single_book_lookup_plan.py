# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""#2699: one book's lookup must not scan whole tables.

``CalibreDB.get_filtered_book`` serves every cover, the reader, downloads and
the detail page. It joined all nine relationships into one statement, and
SQLite materializes each ``LEFT JOIN (link JOIN table)`` group by scanning the
whole target table (every author, tag, series, publisher, language) before it
can narrow to the one book. On the reporter's 272k-book library that was about
4 seconds per cover, from the ``EXPLAIN QUERY PLAN`` they posted.

The invariant: every statement the lookup issues reaches its rows through the
primary key or an index. The relationships must still arrive loaded, so a
caller that reads them after the session is gone keeps working.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.sql.expression import true

pytestmark = pytest.mark.unit


def _engine():
    def _creator():
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.execute("ATTACH DATABASE ':memory:' AS calibre")
        return conn

    engine = create_engine("sqlite+pysqlite://", creator=_creator,
                           poolclass=StaticPool)
    from cps.db import Base
    Base.metadata.create_all(engine)
    # The models do not declare calibre's own indexes; a real metadata.db has
    # them (the reporter's plan names data_idx, comments_idx and the
    # identifiers autoindex), so the plan is only meaningful with them.
    with engine.begin() as conn:
        for stmt in (
            "CREATE INDEX calibre.data_idx ON data (book)",
            "CREATE INDEX comments_idx ON comments (book)",
            "CREATE UNIQUE INDEX identifiers_book_type"
            " ON identifiers (book, type)",
        ):
            conn.exec_driver_sql(stmt)
    return engine


def _seed(engine):
    from cps.db import (Authors, Books, Comments, Data, Identifiers,
                        Languages, Publishers, Series, Tags)

    session = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc)
    for i in range(1, 6):
        book = Books(f"Title {i}", f"Title {i}", f"Author {i}", now, now,
                     "1.0", now, f"path{i}", 1, [], [])
        book.authors = [Authors(f"Author {i}", f"Author {i}")]
        book.tags = [Tags(f"tag-{i}")]
        book.series = [Series(f"Series {i}", f"Series {i}")]
        book.publishers = [Publishers(f"Pub {i}", f"Pub {i}")]
        book.languages = [Languages(f"l{i}")]
        session.add(book)
        session.flush()
        session.add(Comments(f"comment {i}", book.id))
        session.add(Data(book.id, "EPUB", 100, f"file{i}"))
        session.add(Identifiers(f"isbn{i}", "isbn", book.id))
    session.commit()
    session.close()


@pytest.fixture
def cdb():
    from cps.db import CalibreDB

    engine = _engine()
    _seed(engine)
    db = CalibreDB()
    db.session = sessionmaker(bind=engine)()
    db.ensure_session = lambda: None
    # The visibility predicate is per user and not what this test measures.
    db.common_filters = lambda *a, **k: true()
    try:
        yield db, engine
    finally:
        db.session.close()


def _plans(engine, statements):
    """EXPLAIN QUERY PLAN for each captured statement, as lists of detail rows."""
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        out = []
        for sql, params in statements:
            cur.execute("EXPLAIN QUERY PLAN " + sql, params)
            out.append((sql, [row[-1] for row in cur.fetchall()]))
        return out
    finally:
        raw.close()


def _whole_table_steps(detail_rows):
    """Plan steps that read a whole table rather than seeking into it."""
    bad = []
    for row in detail_rows:
        if row.startswith("MATERIALIZE"):
            bad.append(row)
        elif row.startswith("SCAN ") and " USING " not in row:
            bad.append(row)
    return bad


def test_single_book_lookup_reads_through_indexes_only(cdb):
    db, engine = cdb
    captured = []

    def _capture(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            captured.append((statement, params))

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        book = db.get_filtered_book(3, allow_show_archived=True,
                                    allow_show_hidden=True)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert book is not None and book.id == 3
    assert captured, "the lookup issued no SELECT at all"

    offenders = []
    for sql, rows in _plans(engine, captured):
        bad = _whole_table_steps(rows)
        if bad:
            offenders.append((bad, sql.split(" FROM ")[0][:80]))
    assert not offenders, (
        "a single-book lookup scanned or materialized whole tables: %r"
        % offenders)


def test_single_book_lookup_still_returns_its_relationships_loaded(cdb):
    db, _engine = cdb
    book = db.get_filtered_book(3, allow_show_archived=True,
                                allow_show_hidden=True)
    # Detach it the way a finished request does; reads must not need the
    # session (the DetachedInstanceError case behind the eager loading).
    db.session.expunge_all()
    db.session.close()

    assert [a.name for a in book.authors] == ["Author 3"]
    assert [t.name for t in book.tags] == ["tag-3"]
    assert [s.name for s in book.series] == ["Series 3"]
    assert [p.name for p in book.publishers] == ["Pub 3"]
    assert [lang.lang_code for lang in book.languages] == ["l3"]
    assert [c.text for c in book.comments] == ["comment 3"]
    assert [d.format for d in book.data] == ["EPUB"]
    assert [i.val for i in book.identifiers] == ["isbn3"]
    assert book.ratings == []


def test_list_page_reads_through_indexes_only(cdb, monkeypatch):
    """The library list had the same joined load: about 3s per page of 60 on
    a generated 270k-book library, against about 90ms without it."""
    import cps.db as dbmod
    from cps.db import Books

    db, engine = cdb
    db.config = type("Cfg", (), {"config_books_per_page": 3})()
    monkeypatch.setattr(dbmod, "current_user", type("U", (), {
        "show_detail_random": lambda self: False,
    })())
    captured = []

    def _capture(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            captured.append((statement, params))

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        entries, _randm, _pagination = db.fill_indexpage_with_archived_books(
            page=1, database=Books, pagesize=3, db_filter=true(),
            order=[Books.id.desc()], allow_show_archived=True,
            join_archive_read=False, config_read_column=0,
        )
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert [b.id for b in entries] == [5, 4, 3]
    assert all(b.tags and b.authors for b in entries)

    offenders = []
    for sql, rows in _plans(engine, captured):
        bad = [r for r in _whole_table_steps(rows)
               # The page and count queries walk books (or their own
               # derived id list) in order; that is what a list page reads.
               if not r.startswith(("SCAN books", "SCAN anon_"))]
        if bad:
            offenders.append((bad, sql.split(" FROM ")[0][:80]))
    assert not offenders, (
        "a list page scanned or materialized whole relationship tables: %r"
        % offenders)
