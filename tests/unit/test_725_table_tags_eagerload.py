# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression guard for fork issue #725: the Tags column on the new-UI table
view (#725 / PR #756).

The table view serializes ``book.tags`` for every book on a page via the shared
``/api/v1/books`` list path (``cps.api.serializers.serialize_book_list_item``),
which reads from ``calibre_db.fill_indexpage_with_archived_books``. Every book
on the page must arrive with its tags loaded, and loading them must cost a
fixed number of statements per page, never one per book.

Since #2699 the list query relies on the model's ``lazy='selectin'`` rather
than ``joinedload``: one indexed ``WHERE book IN (<page ids>)`` statement per
relationship. ``joinedload`` made SQLite read whole tables for every page
(see ``test_2699_single_book_lookup_plan.py``).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.sql.expression import true

# CI selects tests with ``pytest -m "smoke or unit"``; without this marker the
# whole module is collected but deselected, so the regression would never guard.
pytestmark = pytest.mark.unit


def _build_calibre_engine():
    """A real in-memory SQLite engine with the calibre metadata schema.

    Calibre stores its metadata tables under an attached ``calibre`` database
    (see ``__table_args__ = {'schema': 'calibre'}`` in ``cps/db.py``), so the
    engine attaches an in-memory DB as ``calibre`` before ``create_all``. All
    tables are created (the real ``Books`` model references many relationships).
    A single shared connection (``StaticPool``) keeps the data visible across
    sessions created on the engine.
    """

    def _creator():
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.execute("ATTACH DATABASE ':memory:' AS calibre")
        return conn

    engine = create_engine("sqlite+pysqlite://", creator=_creator, poolclass=StaticPool)
    from cps.db import Base

    Base.metadata.create_all(engine)
    return engine


def _seed(engine, *, n_books=5):
    """Insert n_books books, each carrying two tags and one author."""
    from cps.db import Books, Tags, Authors
    from sqlalchemy.orm import sessionmaker

    session = sessionmaker(bind=engine)()
    # Tags.name is UNIQUE — one shared tag instance referenced across books.
    shared = Tags("shared")
    session.add(shared)
    session.commit()
    now = datetime.now(timezone.utc)
    for i in range(n_books):
        author_sort = f"Author {i}"
        book = Books(
            f"Title {i}", f"Title {i}", author_sort, now, now,
            "1.0", now, f"path{i}", 1, [], [],
        )
        # author_sort matches Authors.sort so order_authors doesn't warn.
        book.authors = [Authors(author_sort, author_sort)]
        book.tags = [Tags(f"tag-{i}"), shared]
        session.add(book)
    session.commit()
    return session


@pytest.fixture
def calibre_db(monkeypatch):
    """A real ``CalibreDB`` pointed at an in-memory calibre schema, with the
    heavy ``common_filters`` (user/config/custom-column logic) bypassed so the
    test isolates the query-construction under test."""
    import cps.db as db
    from cps.db import CalibreDB

    engine = _build_calibre_engine()
    _seed(engine)

    cdb = CalibreDB()
    cdb.session = sessionmaker(bind=engine)()
    cdb.config = type("Cfg", (), {"config_books_per_page": 10})()
    # Bypass the user/archive/custom-column filter machinery — not under test.
    cdb.common_filters = lambda *a, **k: true()
    # Skip the random-books branch; the main list-query block is the list path.
    monkeypatch.setattr(db, "current_user", type("U", (), {
        "show_detail_random": lambda self: False,
    })())
    try:
        yield cdb, engine
    finally:
        cdb.session.close()


def _count_page_statements(engine, cdb, pagesize):
    from cps.db import Books

    total = {"n": 0}

    def _on_execute(conn, cursor, statement, params, context, executemany):
        total["n"] += 1

    cdb.session.expunge_all()
    event.listen(engine, "before_cursor_execute", _on_execute)
    try:
        entries, _randm, _pagination = cdb.fill_indexpage_with_archived_books(
            page=1, database=Books, pagesize=pagesize, db_filter=true(),
            order=[Books.id.asc()], allow_show_archived=True,
            join_archive_read=False, config_read_column=0,
        )
    finally:
        event.remove(engine, "before_cursor_execute", _on_execute)
    return entries, total["n"]


def test_list_page_loads_tags_with_a_fixed_number_of_statements(calibre_db):
    """Every book on the page has its tags, readable after the session is
    gone, and a bigger page costs no extra statements (no per-book query)."""
    cdb, engine = calibre_db

    small, small_count = _count_page_statements(engine, cdb, 2)
    entries, count = _count_page_statements(engine, cdb, 5)

    assert len(small) == 2 and len(entries) == 5
    assert count == small_count, (
        f"a 5-book page took {count} statements and a 2-book page "
        f"{small_count}; the difference is a per-book relationship load")

    cdb.session.expunge_all()
    cdb.session.close()
    assert [sorted(t.name for t in b.tags) for b in entries] == [
        sorted([f"tag-{i}", "shared"]) for i in range(5)]
    assert all(len(b.authors) == 1 for b in entries)


def test_select_all_query_returns_only_ordered_ids_without_loading_book_models(calibre_db):
    from cps.db import Books

    cdb, _engine = calibre_db
    loaded = []
    def record_load(book, _context):
        loaded.append(book.id)

    event.listen(Books, "load", record_load)
    try:
        ids, _random, pagination = cdb.fill_indexpage(
            page=1, pagesize=3, database=Books, db_filter=true(),
            order=[Books.id.desc()], join_archive_read=False,
            config_read_column=0, ids_only=True,
        )
    finally:
        event.remove(Books, "load", record_load)

    assert ids == [5, 4, 3]
    assert pagination.total_count == 5
    assert loaded == []
