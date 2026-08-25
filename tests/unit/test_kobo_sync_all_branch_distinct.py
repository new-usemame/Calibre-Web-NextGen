# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""The sync-all branch must count and paginate BOOKS, not format rows.

`HandleSyncRequest` builds `changed_entries` in two branches. Both `.join(db.Data)`
to require a Kobo-eligible format -- which yields ONE ROW PER MATCHING FORMAT, not
per book. The shelf-only branch ends `.distinct()`. The sync-all branch did not.

A book holding both an EPUB and a KEPUB therefore contributed two rows. That is
the normal state once `config_kobo_prefer_kepub` is on: measured on a real
library right after the KEPUB backfill ran, 211 of 216 books held both formats
and the join produced 427 rows for 216 books -- a 1.977x multiplier.

Two consequences, both reproduced below:

1. `changed_entries.count()` -- logged as "Kobo Sync: changed entries: N" --
   counts format rows. This is why users report a number wildly past their
   library size (#347: "changed entries: 4458"; #1634: "changed entries: 3481",
   "well past my total library size"). The inflated number is NOT by itself
   evidence of a sync loop, and reading it as such has cost real triage time.

2. LIMIT is applied to the multiplied rows and the ORM uniquifies afterwards, so
   a page of SYNC_ITEM_LIMIT delivers only about half that many books and the
   device needs roughly twice the round trips to finish.

What is explicitly NOT a consequence: duplicate entitlements. Legacy `Query`
uniquifies entity rows, so each book is emitted once per page. This is a
throughput and diagnostics defect, not data corruption -- stated here so the
next reader does not over-claim it.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import datetime

import pytest
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, and_, create_engine
from sqlalchemy.orm import declarative_base, joinedload, relationship, sessionmaker

KOBO_PY = pathlib.Path(__file__).resolve().parents[2] / "cps" / "kobo.py"
KOBO_FORMATS = ["EPUB", "KEPUB"]


# ---------------------------------------------------------------------------
# Source pin: the asymmetry between the two branches is the bug, so pin that
# BOTH end distinct rather than just asserting a count somewhere.
# ---------------------------------------------------------------------------

def _handle_sync_source() -> str:
    tree = ast.parse(KOBO_PY.read_text(encoding="utf-8"))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "HandleSyncRequest"), None)
    assert fn is not None, "HandleSyncRequest not found in cps/kobo.py"
    return ast.unparse(fn)


def _changed_entries_chains_joining_data():
    """Every `changed_entries = (...)` assignment whose chain joins db.Data.

    Counting `.distinct()` occurrences in the function body does NOT work as a
    pin: the pre-fix function already contained two of them (the shelf branch,
    plus an unrelated KoboReadingState query), so a `count >= 2` assertion is
    green on the bug. This walks the AST instead and checks each multiplying
    chain individually.
    """
    tree = ast.parse(KOBO_PY.read_text(encoding="utf-8"))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "HandleSyncRequest"), None)
    assert fn is not None, "HandleSyncRequest not found in cps/kobo.py"
    chains = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "changed_entries" for t in node.targets):
            continue
        text = ast.unparse(node.value)
        if ".join(db.Data)" in text:
            chains.append(text)
    return chains


@pytest.mark.unit
def test_every_changed_entries_chain_that_joins_data_ends_distinct():
    """`.join(db.Data)` multiplies rows per format, so every chain using it
    must dedupe -- not just the shelf one."""
    chains = _changed_entries_chains_joining_data()
    assert len(chains) == 2, (
        f"expected exactly two changed_entries chains joining db.Data, found "
        f"{len(chains)}; this test's structural assumption changed")
    undeduped = [c for c in chains if not c.rstrip().endswith(".distinct()")]
    assert not undeduped, (
        "a changed_entries chain joins db.Data without a terminating "
        ".distinct(); it will count and paginate format rows instead of books "
        "(see module docstring). Offending chain tail: "
        + undeduped[0].rstrip()[-160:])


# ---------------------------------------------------------------------------
# Behavioural: reproduce the real query shape and prove the effect.
# ---------------------------------------------------------------------------

Base = declarative_base()


class _Books(Base):
    __tablename__ = "books"
    id = Column(Integer, primary_key=True)
    last_modified = Column(DateTime, default=datetime(2026, 1, 1))
    data = relationship("_Data", backref="book_ref")


class _Data(Base):
    __tablename__ = "data"
    id = Column(Integer, primary_key=True)
    book = Column(Integer, ForeignKey("books.id"))
    format = Column(String)


class _ArchivedBook(Base):
    __tablename__ = "archived_book"
    id = Column(Integer, primary_key=True)
    book_id = Column(Integer)
    user_id = Column(Integer)
    is_archived = Column(Boolean)
    last_modified = Column(DateTime)


class _KoboReadingState(Base):
    __tablename__ = "kobo_reading_state"
    id = Column(Integer, primary_key=True)
    book_id = Column(Integer)
    user_id = Column(Integer)


@pytest.fixture
def session_with_dual_format_library():
    """20 books; 18 hold BOTH formats, mirroring a library after the KEPUB
    backfill with config_kobo_prefer_kepub on."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    did = 0
    for bid in range(1, 21):
        s.add(_Books(id=bid))
        did += 1
        s.add(_Data(id=did, book=bid, format="EPUB"))
        if bid <= 18:
            did += 1
            s.add(_Data(id=did, book=bid, format="KEPUB"))
    s.commit()
    return s


def _query(session, *, distinct: bool):
    q = (session.query(_Books, _ArchivedBook.last_modified, _ArchivedBook.is_archived, _KoboReadingState)
         .join(_Data, _Data.book == _Books.id)
         .outerjoin(_ArchivedBook, and_(_Books.id == _ArchivedBook.book_id, _ArchivedBook.user_id == 1))
         .outerjoin(_KoboReadingState, and_(_Books.id == _KoboReadingState.book_id,
                                            _KoboReadingState.user_id == 1))
         .filter(_Data.format.in_(KOBO_FORMATS))
         .order_by(_Books.last_modified)
         .order_by(_Books.id)
         .options(joinedload(_Books.data)))
    return q.distinct() if distinct else q


@pytest.mark.unit
def test_without_distinct_the_logged_count_is_format_rows_not_books(session_with_dual_format_library):
    """The number in "Kobo Sync: changed entries: N"."""
    s = session_with_dual_format_library
    assert _query(s, distinct=False).count() == 38   # 18*2 + 2
    assert _query(s, distinct=True).count() == 20    # the honest answer


@pytest.mark.unit
def test_without_distinct_a_page_delivers_about_half_a_page_of_books(session_with_dual_format_library):
    """LIMIT hits the multiplied rows; the ORM uniquifies afterwards."""
    s = session_with_dual_format_library
    page = 10
    assert len(_query(s, distinct=False).limit(page).all()) < page
    assert len(_query(s, distinct=True).limit(page).all()) == page


@pytest.mark.unit
def test_entitlements_are_never_duplicated_either_way(session_with_dual_format_library):
    """Guard against over-claiming: the device does NOT receive a book twice.
    If this ever fails, the bug is far more serious than throughput."""
    s = session_with_dual_format_library
    for distinct in (False, True):
        ids = [r._Books.id if hasattr(r, "_Books") else r[0].id
               for r in _query(s, distinct=distinct).all()]
        assert len(ids) == len(set(ids)), f"duplicate entitlements with distinct={distinct}"


@pytest.mark.unit
def test_single_format_library_is_unaffected():
    """Before prefer_kepub, every book had one format and the bug was dormant.
    Pins that the fix is a no-op for those installs."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    for bid in range(1, 11):
        s.add(_Books(id=bid))
        s.add(_Data(id=bid, book=bid, format="EPUB"))
    s.commit()
    assert _query(s, distinct=False).count() == _query(s, distinct=True).count() == 10
    assert len(_query(s, distinct=False).limit(5).all()) == 5
