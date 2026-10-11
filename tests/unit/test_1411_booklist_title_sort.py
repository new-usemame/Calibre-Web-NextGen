# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for CWA upstream issue #1411 (reporter @Mr-Me-torn) — the
"Books List" table view 500s / floods the log with
``sqlite3.OperationalError: no such column: title`` when sorting by the Title,
Title Sort, or Series ID columns.

Root cause: ``cps/web.py`` ``list_books()`` built the ORDER BY for the
``sort`` / ``title`` / ``series_index`` columns as a *raw* SQL fragment,
``text("title asc")``. The default render path passes that into
``fill_indexpage_with_archived_books``, which eager-loads the one-to-many
``Books.data`` relationship under a LIMIT. SQLAlchemy therefore wraps the book
query in a subquery alias (``anon_1``) where the column is exposed as
``books_title``; the bare outer ``ORDER BY title`` cannot resolve against that
alias and SQLite raises ``no such column: title``.

Sorting by Author never crashed because those branches use ORM column objects
(``db.Books.author_sort.asc()`` etc.) — SQLAlchemy rewrites those to
``anon_1.books_author_sort`` at the outer level. The fix maps the text() branch
to the same ORM column objects.

Test 1 reproduces the exact failure mechanism with a real in-memory SQLite
engine (the operator-preferred over mocks for SQL-semantics bugs) using the
same joined-eager-load-under-LIMIT shape the production query uses. The public-handler sorting and eager-load/LIMIT behavior are covered by
test_1050_nordic_request_collation.py.
"""


import pytest
from sqlalchemy import Column, ForeignKey, Integer, String, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import joinedload, relationship, sessionmaker

try:  # SQLAlchemy 1.4+ / 2.0
    from sqlalchemy.orm import declarative_base
except ImportError:  # pragma: no cover - older SQLAlchemy
    from sqlalchemy.ext.declarative import declarative_base

# CI selects tests with ``pytest -m "smoke or unit"``; without this marker the
# whole module is collected but deselected, so the regression below would never
# guard against reintroduction.
pytestmark = pytest.mark.unit

Base = declarative_base()


class Books(Base):
    """Minimal mirror of cps.db.Books — only the columns the Books List view
    sorts on, plus the one-to-many ``data`` relationship that forces the
    subquery wrap."""
    __tablename__ = "books"
    id = Column(Integer, primary_key=True)
    title = Column(String)
    sort = Column(String)
    author_sort = Column(String)
    series_index = Column(String)
    data = relationship("Data", backref="book")


class Data(Base):
    __tablename__ = "data"
    id = Column(Integer, primary_key=True)
    book_id = Column("book", Integer, ForeignKey("books.id"))
    format = Column(String)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    # Two books with a format row each so the joined load produces multiple
    # joined rows per parent — this is what makes SQLAlchemy wrap the parent
    # query in the anon_1 subquery once a LIMIT is applied.
    b1 = Books(id=1, title="Zebra", sort="Zebra", author_sort="A", series_index="1.0")
    b2 = Books(id=2, title="Apple", sort="Apple", author_sort="B", series_index="2.0")
    s.add_all([b1, b2])
    s.add_all([
        Data(id=1, book_id=1, format="EPUB"),
        Data(id=2, book_id=1, format="PDF"),
        Data(id=3, book_id=2, format="EPUB"),
    ])
    s.commit()
    yield s
    s.close()


def _ordered_query(session, order_clause):
    """Reproduce the production shape (cps/db.py fill_indexpage_with_archived_books):
    ``query.order_by(*order).offset(off).limit(pagesize)`` with a joined eager
    load of the one-to-many ``data`` relationship. The joined collection under a
    LIMIT forces SQLAlchemy to wrap the parent query in the ``anon_1`` subquery
    alias — the exact condition that breaks a bare text() ORDER BY."""
    return (session.query(Books)
            .options(joinedload(Books.data))
            .order_by(order_clause)
            .limit(10))


@pytest.mark.parametrize("sort_param", ["title", "sort", "series_index"])
def test_raw_text_order_reproduces_no_such_column_crash(session, sort_param):
    """RED: the pre-fix raw text() ORDER BY raises the reporter's exact error
    against the subquery alias, for every column the Books List frontend sends
    into this branch."""
    query = _ordered_query(session, text(sort_param + " asc"))
    with pytest.raises(OperationalError) as exc:
        query.all()
    assert "no such column" in str(exc.value).lower()


@pytest.mark.parametrize("sort_param,expected_first_title", [
    ("title", "Apple"),
    ("sort", "Apple"),
    ("series_index", "Zebra"),  # series_index "1.0" < "2.0"; b1 is "Zebra"
])
def test_orm_column_order_resolves_against_alias(session, sort_param, expected_first_title):
    """GREEN: mapping to the ORM column object (the fix) lets SQLAlchemy rewrite
    the reference against the anon_1 alias, so the query resolves and sorts."""
    column = getattr(Books, sort_param)
    rows = _ordered_query(session, column.asc()).all()
    # Ascending sort resolves cleanly (no OperationalError) and orders rows.
    assert rows[0].title == expected_first_title


def test_authors_sort_is_a_dead_token_real_column_is_author_sort(session):
    """The text() branch listed "authors_sort" (with the trailing s), which is
    NOT a real column — author_sort is. The fix maps the stale alias so it can
    never crash even if something sends it."""
    assert not hasattr(Books, "authors_sort")
    assert hasattr(Books, "author_sort")
