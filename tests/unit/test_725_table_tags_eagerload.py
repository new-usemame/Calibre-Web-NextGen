# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression guard for fork issue #725 — the Tags column on the new-UI table
view (#725 / PR #756).

The table view serializes ``book.tags`` for every book on a page via the shared
``/api/v1/books`` list path (``cps.api.serializers.serialize_book_list_item``),
which reads from ``calibre_db.fill_indexpage_with_archived_books``. That list
path must **eager-load** ``Books.tags`` in the same query it loads the books in,
so the page does not pay a follow-up ``SELECT … FROM tags`` per poll.

Why this matters here specifically: ``Books.tags`` is configured
``lazy='selectin'`` (``cps/db.py``), so the list query's eager-load options block
must include ``joinedload(Books.tags)``. The detail path
(``get_filtered_book``) already eager-loads tags; PR #756 originally claimed the
list path did too — it did not, so the list page issued an extra ``selectin``
query every page.

These two tests pin the fix from two angles:

* ``test_fill_indexpage_options_block_eager_loads_books_tags`` — a source pin on
  the real ``fill_indexpage_with_archived_books``: it MUST apply
  ``joinedload(Books.tags)`` in BOTH eager-load options blocks (the main list
  query block AND the random-books block). Removing either line turns this red.
  (``inspect.getsource`` source-pin for a refactor-fragile invariant, per the
  project's no-slop bar — the eager-load directive is exactly the property under
  test, not a field-name triviality.)
* ``test_list_query_eager_loads_tags_no_follow_up_selectin`` — behavioural:
  drives the REAL ``fill_indexpage_with_archived_books`` against an in-memory
  calibre metadata DB (real ``Books``/``Tags``/``Authors`` models) and counts SQL
  statements. With the eager load the page resolves in the eager-join baseline
  (6 statements: total_count + the main eager join + the model's other selectin
  relationship loads). Removing ``joinedload(Books.tags)`` makes the ``selectin``
  strategy fire a follow-up ``SELECT`` for tags → the count rises to 7 → this
  assertion fails. The baseline is stable across page sizes (the statements are
  per-relationship, not per-book).
"""

from __future__ import annotations

import inspect
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


def test_fill_indexpage_options_block_eager_loads_books_tags():
    """Source pin (the deterministic removal guard): the list-path function
    ``CalibreDB.fill_indexpage_with_archived_books`` must apply
    ``joinedload(Books.tags)`` in BOTH of its eager-load options blocks — the
    main list query and the random-books query. Fails the instant either line is
    dropped."""
    from cps.db import CalibreDB

    src = inspect.getsource(CalibreDB.fill_indexpage_with_archived_books)
    # The function must reference the Books.tags joined eager load.
    assert "joinedload(Books.tags)" in src, (
        "fill_indexpage_with_archived_books must eager-load Books.tags so the "
        "/api/v1/books list path (table view Tags column) does not pay a "
        "follow-up SELECT for tags on every page."
    )
    # …and it must do so in BOTH options blocks (main query + random query).
    assert src.count("joinedload(Books.tags)") >= 2, (
        "fill_indexpage_with_archived_books must eager-load Books.tags in both "
        "the main list-query options block and the random-books options block."
    )


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


def test_list_query_eager_loads_tags_no_follow_up_selectin(calibre_db):
    """Behavioural guard: a list page must load books AND their tags in the
    eager-join baseline — no follow-up ``SELECT`` for tags.

    With ``joinedload(Books.tags)`` the page resolves in 6 statements (verified
    stable across page sizes 3/5/10). Drop the eager load and the ``selectin``
    strategy issues a 7th statement for tags, failing this budget. This drives
    the real ``fill_indexpage_with_archived_books`` (not a mock), so it catches
    the regression in the production code path."""
    from cps.db import Books
    cdb, engine = calibre_db

    total = {"n": 0}

    def _on_execute(conn, cursor, statement, params, context, executemany):
        total["n"] += 1

    event.listen(engine, "before_cursor_execute", _on_execute)
    try:
        entries, _randm, _pagination = cdb.fill_indexpage_with_archived_books(
            page=1, database=Books, pagesize=10, db_filter=true(),
            order=[Books.id.asc()], allow_show_archived=True,
            join_archive_read=False, config_read_column=0,
        )
    finally:
        event.remove(engine, "before_cursor_execute", _on_execute)

    # Sanity: the page actually returned books with their tags populated.
    assert len(entries) >= 1
    assert all(len(b.tags) >= 1 for b in entries), \
        "tags must be populated on every book returned by the list path"

    # Budget: the eager join folds tags into the main query. Without the eager
    # load, the selectin strategy adds a 7th statement for tags -> this fails.
    assert total["n"] <= 6, (
        f"list path emitted {total['n']} SQL statements for one page; expected "
        "the eager-join baseline (6). A 7th statement means Books.tags was NOT "
        "eager-loaded (the lazy selectin fired) — re-add joinedload(Books.tags) "
        "to fill_indexpage_with_archived_books (cps/db.py)."
    )
