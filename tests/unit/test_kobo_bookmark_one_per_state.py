# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""One Kobo reading state owns exactly one bookmark and one statistics row (#2783).

``KoboReadingState.current_bookmark`` is a ``uselist=False`` relationship, but
nothing in the schema stopped a second ``kobo_bookmark`` row pointing at the same
state. A reporter's database had two for one book: a live row at 12.0% written
by the Kobo on 2026-10-31 and a stale row at 2.37% from 2026-10-27. SQLAlchemy
then returns whichever row SQLite yields first ("Multiple rows returned with
uselist=False"), so the Share my place arbitration compared the other device's
place against an arbitrary bookmark and the Kobo was sent ``[]``.

The repair has three parts, each pinned here by behaviour:

* existing duplicates collapse to one row per state, and the survivor is the
  place the server received last, never the lowest or highest id;
* bookmark/statistics rows whose state no longer exists (or is NULL) are
  removed, so a reading state created later cannot adopt a stranger's place;
* the database refuses a second row for the same state from then on, on an
  upgraded database and on a fresh install alike.
"""

from datetime import datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker


def _fresh_engine():
    from cps import ub

    engine = create_engine("sqlite:///:memory:", future=True)
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys = OFF")
    ub.Base.metadata.create_all(engine)
    return engine


def _strip_constraints(engine, table):
    """Rebuild ``table`` with the same columns and no keys or indexes,
    which is the shape every pre-#2783 database has for these two tables."""
    with engine.begin() as conn:
        conn.exec_driver_sql(f"ALTER TABLE {table} RENAME TO {table}_pre")
        conn.exec_driver_sql(
            f"CREATE TABLE {table} AS SELECT * FROM {table}_pre WHERE 0")
        conn.exec_driver_sql(f"DROP TABLE {table}_pre")


@pytest.fixture
def upgraded_db():
    engine = _fresh_engine()
    _strip_constraints(engine, "kobo_bookmark")
    _strip_constraints(engine, "kobo_statistics")
    session = sessionmaker(bind=engine, future=True)()
    yield session, engine
    session.close()
    engine.dispose()


def _state(conn, state_id, book_id=1374, user_id=1):
    conn.execute(text(
        "INSERT INTO kobo_reading_state (id, user_id, book_id, last_modified, "
        "priority_timestamp) VALUES (:i, :u, :b, :t, :t)"),
        {"i": state_id, "u": user_id, "b": book_id, "t": datetime(2026, 10, 31, 8, 44, 49)})


def _bookmark(conn, row_id, state_id, percent, location_type,
              last_modified, server_modified_at, created_at=None):
    conn.execute(text(
        "INSERT INTO kobo_bookmark (id, kobo_reading_state_id, created_at, "
        "last_modified, server_modified_at, progress_percent, location_type, "
        "location_value, shared_from_koreader) "
        "VALUES (:i, :s, :c, :lm, :sm, :p, :lt, :lv, 0)"),
        {"i": row_id, "s": state_id, "c": created_at, "lm": last_modified,
         "sm": server_modified_at, "p": percent, "lt": location_type,
         "lv": "span-%d" % row_id})


def _statistics(conn, row_id, state_id, spent, last_modified):
    conn.execute(text(
        "INSERT INTO kobo_statistics (id, kobo_reading_state_id, last_modified, "
        "spent_reading_minutes, remaining_time_minutes) "
        "VALUES (:i, :s, :lm, :sp, 10)"),
        {"i": row_id, "s": state_id, "lm": last_modified, "sp": spent})


def _run(engine, session):
    from cps import ub
    ub.migrate_kobo_bookmark_one_per_state(engine, session)
    session.commit()


def _bookmarks(engine, state_id):
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT id, progress_percent, location_type, location_value, created_at "
            "FROM kobo_bookmark WHERE kobo_reading_state_id = :s"),
            {"s": state_id}).fetchall()


@pytest.mark.unit
@pytest.mark.parametrize("live_id, stale_id", [(1, 186), (186, 1)])
def test_reporters_duplicate_keeps_the_place_received_last(upgraded_db, live_id, stale_id):
    session, engine = upgraded_db
    with engine.begin() as conn:
        _state(conn, 7)
        _bookmark(conn, live_id, 7, 12.0, "KoboSpan",
                  datetime(2026, 10, 31, 8, 44, 49), datetime(2026, 10, 31, 8, 44, 49, 31277),
                  created_at=datetime(2026, 9, 1))
        _bookmark(conn, stale_id, 7, 2.36917469409008, None,
                  datetime(2026, 10, 27, 22, 47, 28), datetime(2026, 10, 27, 22, 47, 28, 17861),
                  created_at=datetime(2026, 10, 27, 22, 47, 28))

    _run(engine, session)

    rows = _bookmarks(engine, 7)
    assert len(rows) == 1
    survivor = rows[0]
    assert survivor.progress_percent == 12.0
    assert survivor.location_type == "KoboSpan"
    assert survivor.location_value == "span-%d" % live_id

    # The ORM relationship now resolves to the live place, without warnings.
    from cps import ub
    state = session.get(ub.KoboReadingState, 7)
    assert state.current_bookmark.progress_percent == 12.0


@pytest.mark.unit
def test_receipt_clock_decides_over_device_clock(upgraded_db):
    """A device with a fast clock must not keep a place the server received earlier."""
    session, engine = upgraded_db
    with engine.begin() as conn:
        _state(conn, 3)
        # Device clock says 2030, but the server received it first.
        _bookmark(conn, 10, 3, 40.0, "KoboSpan",
                  datetime(2030, 1, 1), datetime(2026, 10, 1))
        _bookmark(conn, 11, 3, 55.0, "KoboSpan",
                  datetime(2026, 10, 2), datetime(2026, 10, 2))

    _run(engine, session)

    rows = _bookmarks(engine, 3)
    assert [r.progress_percent for r in rows] == [55.0]


@pytest.mark.unit
def test_statistics_duplicates_collapse_to_one_row(upgraded_db):
    session, engine = upgraded_db
    with engine.begin() as conn:
        _state(conn, 7)
        _bookmark(conn, 1, 7, 12.0, "KoboSpan",
                  datetime(2026, 10, 31), datetime(2026, 10, 31))
        _statistics(conn, 1, 7, 30, datetime(2026, 10, 27))
        _statistics(conn, 2, 7, 95, datetime(2026, 10, 31))

    _run(engine, session)

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT spent_reading_minutes FROM kobo_statistics "
            "WHERE kobo_reading_state_id = 7")).fetchall()
    assert [r.spent_reading_minutes for r in rows] == [95]


@pytest.mark.unit
def test_rows_without_a_living_state_are_removed(upgraded_db):
    """A row whose state is gone must not be adopted by a state created later."""
    session, engine = upgraded_db
    with engine.begin() as conn:
        _state(conn, 7)
        _bookmark(conn, 1, 7, 12.0, "KoboSpan",
                  datetime(2026, 10, 31), datetime(2026, 10, 31))
        _bookmark(conn, 2, 99, 80.0, "KoboSpan",          # state 99 was deleted
                  datetime(2026, 1, 1), datetime(2026, 1, 1))
        _bookmark(conn, 3, None, 50.0, "KoboSpan",        # detached
                  datetime(2026, 1, 1), datetime(2026, 1, 1))
        _statistics(conn, 5, 99, 10, datetime(2026, 1, 1))

    _run(engine, session)

    with engine.connect() as conn:
        assert conn.execute(text("SELECT id FROM kobo_bookmark")).scalars().all() == [1]
        assert conn.execute(text("SELECT id FROM kobo_statistics")).scalars().all() == []


@pytest.mark.unit
def test_upgraded_database_refuses_a_second_row_for_a_state(upgraded_db):
    session, engine = upgraded_db
    with engine.begin() as conn:
        _state(conn, 7)
        _bookmark(conn, 1, 7, 12.0, "KoboSpan",
                  datetime(2026, 10, 31), datetime(2026, 10, 31))
        _statistics(conn, 1, 7, 30, datetime(2026, 10, 31))

    _run(engine, session)
    _run(engine, session)  # idempotent: a second start changes nothing

    assert len(_bookmarks(engine, 7)) == 1
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            _bookmark(conn, 2, 7, 2.0, None, datetime(2026, 11, 1), datetime(2026, 11, 1))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            _statistics(conn, 2, 7, 1, datetime(2026, 11, 1))


@pytest.mark.unit
def test_fresh_install_refuses_a_second_row_for_a_state():
    engine = _fresh_engine()
    try:
        with engine.begin() as conn:
            _state(conn, 7)
            _bookmark(conn, 1, 7, 12.0, "KoboSpan",
                      datetime(2026, 10, 31), datetime(2026, 10, 31))
            _statistics(conn, 1, 7, 30, datetime(2026, 10, 31))
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                _bookmark(conn, 2, 7, 2.0, None, datetime(2026, 11, 1), datetime(2026, 11, 1))
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                _statistics(conn, 2, 7, 1, datetime(2026, 11, 1))
    finally:
        engine.dispose()

