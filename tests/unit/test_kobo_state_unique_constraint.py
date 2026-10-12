# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for the Kobo reading-state race condition (audit
2026-05-11, item B2).

Without (user_id, book_id) uniqueness on ReadBook / KoboReadingState /
KoboSyncedBooks / ArchivedBook, two concurrent PUTs to
``/v1/library/<uuid>/state`` from the same device could race past the
read-then-write check in ``get_or_create_reading_state`` and both
INSERT, producing duplicate rows. The next ``.one_or_none()`` then
raises MultipleResultsFound -> 500 to the device.

These tests cover:

1. Model declarations carry UniqueConstraint on (user_id, book_id).
2. The migration ``migrate_kobo_unique_constraints`` dedupes existing
   duplicates (newest LM wins for KoboReadingState, status-level + LM
   for ReadBook, archived-wins-LM-tiebreak for ArchivedBook), reparents
   KoboReadingState's bookmark/statistics children, and writes a
   marker file so it's idempotent.
3. The UNIQUE INDEX is actually created and rejects duplicate INSERTs
   from then on.
4. ``get_or_create_reading_state`` itself: a repeated first contact, and
   a competing request that creates the rows in the gap before this one's
   INSERT, both leave one row per table and raise nothing.

Concurrency is tested in
``test_kobo_state_concurrent_insert.py`` (separate file, threaded).
"""

from datetime import datetime, timezone, timedelta

import pytest


@pytest.mark.unit
class TestModelConstraints:
    def test_read_book_has_user_book_unique_constraint(self):
        from cps.ub import ReadBook
        names = [c.name for c in ReadBook.__table_args__ if hasattr(c, "name")]
        assert "uq_book_read_link_user_book" in names

    def test_kobo_reading_state_has_user_book_unique_constraint(self):
        from cps.ub import KoboReadingState
        names = [c.name for c in KoboReadingState.__table_args__ if hasattr(c, "name")]
        assert "uq_kobo_reading_state_user_book" in names

    def test_kobo_synced_books_has_user_book_unique_constraint(self):
        from cps.ub import KoboSyncedBooks
        names = [c.name for c in KoboSyncedBooks.__table_args__ if hasattr(c, "name")]
        assert "uq_kobo_synced_books_user_book" in names

    def test_archived_book_has_user_book_unique_constraint(self):
        from cps.ub import ArchivedBook
        names = [c.name for c in ArchivedBook.__table_args__ if hasattr(c, "name")]
        assert "uq_archived_book_user_book" in names


# ---------------------------------------------------------------------------
# Fixtures for in-memory DB-driven tests.
# We mount an in-memory SQLite DB using the ub.Base metadata. Each test gets
# a fresh DB + session. We skip the User FK constraint by using PRAGMA
# foreign_keys=OFF for the test DB — we only care about the (user, book)
# uniqueness on the four target tables.
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_duplicate_pause_resume_recovery_retains_winners_choice_clock(memory_db_session_no_unique_indexes):
    from cps.ub import ReadBook, _dedupe_book_read_link
    session, _ = memory_db_session_no_unique_indexes
    session.add_all([
        ReadBook(user_id=1, book_id=42, read_status=4, times_started_reading=2,
                 last_modified=datetime(2020, 1, 1), read_status_choice_at=datetime(2020, 1, 1)),
        ReadBook(user_id=1, book_id=42, read_status=2, times_started_reading=3,
                 last_modified=datetime(2025, 1, 1), read_status_choice_at=datetime(2025, 1, 1)),
    ])
    session.commit()
    assert _dedupe_book_read_link(session) == 1
    session.commit()
    row = session.query(ReadBook).filter_by(user_id=1, book_id=42).one()
    assert (row.read_status, row.times_started_reading, row.last_modified) == (2, 5, datetime(2025, 1, 1))

@pytest.fixture
def memory_db_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from cps import ub

    engine = create_engine("sqlite:///:memory:", future=True)
    # FKs off so we don't have to insert a User row for every test.
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys = OFF")

    ub.Base.metadata.create_all(engine)

    Session = sessionmaker(bind=engine, future=True)
    session = Session()
    yield session, engine
    session.close()


@pytest.fixture
def memory_db_session_no_unique_indexes(memory_db_session):
    """Same fixture but rebuilds the four target tables without the
    (user_id, book_id) UniqueConstraint, simulating a pre-migration DB.
    Required for migration / dedupe tests because SQLite enforces table-
    level UNIQUE constraints in CREATE TABLE — DROP INDEX alone won't
    remove them.
    """
    session, engine = memory_db_session

    # Drop and recreate each target table with the SAME columns but no
    # unique constraint. Use raw SQL so we don't depend on ORM metadata.
    pre_migration_schemas = {
        "book_read_link": """
            CREATE TABLE book_read_link (
                id INTEGER PRIMARY KEY,
                book_id INTEGER,
                user_id INTEGER,
                read_status INTEGER NOT NULL DEFAULT 0,
                last_modified DATETIME,
                read_status_choice_at DATETIME,
                last_time_started_reading DATETIME,
                times_started_reading INTEGER NOT NULL DEFAULT 0
            )
        """,
        "kobo_reading_state": """
            CREATE TABLE kobo_reading_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                book_id INTEGER,
                last_modified DATETIME,
                priority_timestamp DATETIME
            )
        """,
        "kobo_synced_books": """
            CREATE TABLE kobo_synced_books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                book_id INTEGER,
                book_uuid VARCHAR(64)
            )
        """,
        "archived_book": """
            CREATE TABLE archived_book (
                id INTEGER PRIMARY KEY,
                user_id INTEGER,
                book_id INTEGER,
                is_archived BOOLEAN,
                last_modified DATETIME
            )
        """,
    }
    with engine.connect() as conn:
        for table, ddl in pre_migration_schemas.items():
            conn.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")
            conn.exec_driver_sql(ddl)
        conn.commit()
    session.expire_all()
    return session, engine


@pytest.mark.unit
class TestUniqueIndexEnforced:
    def test_duplicate_kobo_reading_state_rejected(self, memory_db_session):
        from sqlalchemy.exc import IntegrityError
        from cps.ub import KoboReadingState

        session, _ = memory_db_session
        session.add(KoboReadingState(user_id=1, book_id=42))
        session.commit()

        session.add(KoboReadingState(user_id=1, book_id=42))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    def test_duplicate_read_book_rejected(self, memory_db_session):
        from sqlalchemy.exc import IntegrityError
        from cps.ub import ReadBook

        session, _ = memory_db_session
        session.add(ReadBook(user_id=1, book_id=42))
        session.commit()

        session.add(ReadBook(user_id=1, book_id=42))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    def test_duplicate_archived_book_rejected(self, memory_db_session):
        from sqlalchemy.exc import IntegrityError
        from cps.ub import ArchivedBook

        session, _ = memory_db_session
        session.add(ArchivedBook(user_id=1, book_id=42, is_archived=True))
        session.commit()

        session.add(ArchivedBook(user_id=1, book_id=42, is_archived=False))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    def test_different_user_or_book_still_allowed(self, memory_db_session):
        from cps.ub import KoboReadingState
        session, _ = memory_db_session
        session.add(KoboReadingState(user_id=1, book_id=42))
        session.add(KoboReadingState(user_id=1, book_id=43))
        session.add(KoboReadingState(user_id=2, book_id=42))
        session.commit()
        assert session.query(KoboReadingState).count() == 3


@pytest.mark.unit
class TestDedupeKoboReadingState:
    def test_newer_lm_wins(self, memory_db_session_no_unique_indexes):
        from cps.ub import KoboReadingState, _dedupe_kobo_reading_state

        session, _ = memory_db_session_no_unique_indexes
        older = KoboReadingState(
            user_id=1, book_id=42,
            last_modified=datetime(2026, 5, 1, tzinfo=timezone.utc),
        )
        newer = KoboReadingState(
            user_id=1, book_id=42,
            last_modified=datetime(2026, 5, 10, tzinfo=timezone.utc),
        )
        session.add_all([older, newer])
        session.commit()

        removed = _dedupe_kobo_reading_state(session)
        session.commit()
        assert removed == 1
        rows = session.query(KoboReadingState).filter_by(user_id=1, book_id=42).all()
        assert len(rows) == 1
        assert rows[0].last_modified == newer.last_modified

    def test_null_lm_loses_to_dated(self, memory_db_session_no_unique_indexes):
        from cps.ub import KoboReadingState, _dedupe_kobo_reading_state

        session, _ = memory_db_session_no_unique_indexes
        null_lm = KoboReadingState(user_id=1, book_id=42, last_modified=None)
        dated = KoboReadingState(
            user_id=1, book_id=42,
            last_modified=datetime(2026, 5, 10, tzinfo=timezone.utc),
        )
        session.add_all([null_lm, dated])
        session.commit()

        _dedupe_kobo_reading_state(session)
        session.commit()
        rows = session.query(KoboReadingState).filter_by(user_id=1, book_id=42).all()
        assert len(rows) == 1
        assert rows[0].last_modified is not None

    def test_bookmark_children_merged_into_winner(self, memory_db_session_no_unique_indexes):
        from cps.ub import (KoboReadingState, KoboBookmark,
                            _dedupe_kobo_reading_state)

        session, _ = memory_db_session_no_unique_indexes
        older = KoboReadingState(
            user_id=1, book_id=42,
            last_modified=datetime(2026, 5, 1, tzinfo=timezone.utc),
        )
        older_bm = KoboBookmark(
            location_source="abc", location_type="KoboSpan",
            location_value="(1)/4/2/4",
            progress_percent=20.0,
            last_modified=datetime(2026, 5, 1, tzinfo=timezone.utc),
        )
        older.current_bookmark = older_bm
        newer = KoboReadingState(
            user_id=1, book_id=42,
            last_modified=datetime(2026, 5, 10, tzinfo=timezone.utc),
        )
        newer_bm = KoboBookmark(
            location_source="def", location_type="KoboSpan",
            location_value="(1)/4/2/8",
            progress_percent=80.0,
            last_modified=datetime(2026, 5, 10, tzinfo=timezone.utc),
        )
        newer.current_bookmark = newer_bm
        session.add_all([older, newer])
        session.commit()

        _dedupe_kobo_reading_state(session)
        session.commit()

        rows = session.query(KoboReadingState).filter_by(user_id=1, book_id=42).all()
        assert len(rows) == 1
        assert rows[0].current_bookmark.progress_percent == 80.0
        assert rows[0].current_bookmark.location_value == "(1)/4/2/8"

    def test_winner_inherits_loser_bookmark_when_winner_had_none(
            self, memory_db_session_no_unique_indexes):
        from cps.ub import (KoboReadingState, KoboBookmark,
                            _dedupe_kobo_reading_state)

        session, _ = memory_db_session_no_unique_indexes
        winner = KoboReadingState(
            user_id=1, book_id=42,
            last_modified=datetime(2026, 5, 10, tzinfo=timezone.utc),
        )
        loser = KoboReadingState(
            user_id=1, book_id=42,
            last_modified=datetime(2026, 5, 1, tzinfo=timezone.utc),
        )
        loser_bm = KoboBookmark(
            progress_percent=33.0,
            last_modified=datetime(2026, 5, 1, tzinfo=timezone.utc),
        )
        loser.current_bookmark = loser_bm
        session.add_all([winner, loser])
        session.commit()

        _dedupe_kobo_reading_state(session)
        session.commit()

        rows = session.query(KoboReadingState).filter_by(user_id=1, book_id=42).all()
        assert len(rows) == 1
        # Winner had no bookmark; it should now have inherited loser's.
        assert rows[0].current_bookmark is not None
        assert rows[0].current_bookmark.progress_percent == 33.0

    def test_no_duplicates_is_noop(self, memory_db_session_no_unique_indexes):
        from cps.ub import KoboReadingState, _dedupe_kobo_reading_state
        session, _ = memory_db_session_no_unique_indexes
        session.add(KoboReadingState(user_id=1, book_id=42))
        session.commit()
        removed = _dedupe_kobo_reading_state(session)
        assert removed == 0
        assert session.query(KoboReadingState).count() == 1


@pytest.mark.unit
class TestDedupeReadBook:
    def test_higher_status_wins(self, memory_db_session_no_unique_indexes):
        from cps.ub import ReadBook, _dedupe_book_read_link
        session, _ = memory_db_session_no_unique_indexes
        unread = ReadBook(user_id=1, book_id=42,
                          read_status=ReadBook.STATUS_UNREAD,
                          last_modified=datetime(2026, 5, 10, tzinfo=timezone.utc))
        finished = ReadBook(user_id=1, book_id=42,
                            read_status=ReadBook.STATUS_FINISHED,
                            last_modified=datetime(2026, 5, 1, tzinfo=timezone.utc))
        session.add_all([unread, finished])
        session.commit()
        _dedupe_book_read_link(session)
        session.commit()
        rows = session.query(ReadBook).filter_by(user_id=1, book_id=42).all()
        assert len(rows) == 1
        assert rows[0].read_status == ReadBook.STATUS_FINISHED

    def test_times_started_summed(self, memory_db_session_no_unique_indexes):
        from cps.ub import ReadBook, _dedupe_book_read_link
        session, _ = memory_db_session_no_unique_indexes
        a = ReadBook(user_id=1, book_id=42, times_started_reading=3,
                     read_status=ReadBook.STATUS_IN_PROGRESS,
                     last_modified=datetime(2026, 5, 10, tzinfo=timezone.utc))
        b = ReadBook(user_id=1, book_id=42, times_started_reading=2,
                     read_status=ReadBook.STATUS_IN_PROGRESS,
                     last_modified=datetime(2026, 5, 1, tzinfo=timezone.utc))
        session.add_all([a, b])
        session.commit()
        _dedupe_book_read_link(session)
        session.commit()
        rows = session.query(ReadBook).filter_by(user_id=1, book_id=42).all()
        assert len(rows) == 1
        assert rows[0].times_started_reading == 5

    def test_latest_start_time_kept(self, memory_db_session_no_unique_indexes):
        from cps.ub import ReadBook, _dedupe_book_read_link
        session, _ = memory_db_session_no_unique_indexes
        # SQLite strips timezone info on DATETIME round-trip, so compare
        # without it. The dedupe logic only orders by comparison, which
        # works on either aware or naive datetimes consistently.
        older_start = datetime(2026, 4, 1)
        newer_start = datetime(2026, 5, 1)
        a = ReadBook(user_id=1, book_id=42,
                     read_status=ReadBook.STATUS_IN_PROGRESS,
                     last_modified=datetime(2026, 5, 10),
                     last_time_started_reading=older_start)
        b = ReadBook(user_id=1, book_id=42,
                     read_status=ReadBook.STATUS_IN_PROGRESS,
                     last_modified=datetime(2026, 5, 9),
                     last_time_started_reading=newer_start)
        session.add_all([a, b])
        session.commit()
        _dedupe_book_read_link(session)
        session.commit()
        rows = session.query(ReadBook).filter_by(user_id=1, book_id=42).all()
        assert len(rows) == 1
        # Test that the newer start time is picked. Compare without tzinfo
        # since SQLite naive-DATETIME round-trip drops it.
        got = rows[0].last_time_started_reading
        assert got.replace(tzinfo=None) == newer_start.replace(tzinfo=None)


@pytest.mark.unit
class TestDedupeArchivedBook:
    def test_archived_true_beats_false(self, memory_db_session_no_unique_indexes):
        from cps.ub import ArchivedBook, _dedupe_archived_book
        session, _ = memory_db_session_no_unique_indexes
        a = ArchivedBook(user_id=1, book_id=42, is_archived=False,
                         last_modified=datetime(2026, 5, 10, tzinfo=timezone.utc))
        b = ArchivedBook(user_id=1, book_id=42, is_archived=True,
                         last_modified=datetime(2026, 5, 1, tzinfo=timezone.utc))
        session.add_all([a, b])
        session.commit()
        _dedupe_archived_book(session)
        session.commit()
        rows = session.query(ArchivedBook).filter_by(user_id=1, book_id=42).all()
        assert len(rows) == 1
        assert rows[0].is_archived is True


@pytest.mark.unit
class TestDedupeKoboSyncedBooks:
    def test_keeps_one_row(self, memory_db_session_no_unique_indexes):
        from cps.ub import KoboSyncedBooks, _dedupe_kobo_synced_books
        session, _ = memory_db_session_no_unique_indexes
        for _ in range(4):
            session.add(KoboSyncedBooks(user_id=1, book_id=42))
        session.commit()
        removed = _dedupe_kobo_synced_books(session)
        session.commit()
        assert removed == 3
        assert session.query(KoboSyncedBooks).filter_by(user_id=1, book_id=42).count() == 1


@pytest.mark.unit
class TestMigrationIdempotent:
    def test_marker_file_written(self, tmp_path, monkeypatch,
                                 memory_db_session_no_unique_indexes):
        from cps import ub
        session, engine = memory_db_session_no_unique_indexes

        # Point CONFIG_DIR at tmp_path so the marker is sandboxed.
        monkeypatch.setattr(ub.constants, "CONFIG_DIR", str(tmp_path))

        ub.migrate_kobo_unique_constraints(engine, session)
        marker = tmp_path / ".cwa_migrations" / "kobo_unique_constraints_v1"
        assert marker.is_file()

    def test_second_run_is_noop(self, tmp_path, monkeypatch,
                                 memory_db_session_no_unique_indexes):
        from cps import ub
        from cps.ub import KoboReadingState
        session, engine = memory_db_session_no_unique_indexes
        monkeypatch.setattr(ub.constants, "CONFIG_DIR", str(tmp_path))

        ub.migrate_kobo_unique_constraints(engine, session)

        # Now manually insert a duplicate (we have to drop the index first
        # since the migration created it).
        with engine.connect() as conn:
            conn.exec_driver_sql("DROP INDEX IF EXISTS uq_kobo_reading_state_user_book")
            conn.commit()
        session.add(KoboReadingState(user_id=1, book_id=42))
        session.add(KoboReadingState(user_id=1, book_id=42))
        session.commit()

        # Second run should bail at the marker check and NOT dedupe.
        ub.migrate_kobo_unique_constraints(engine, session)
        assert session.query(KoboReadingState).filter_by(user_id=1, book_id=42).count() == 2

    def test_runs_dedupe_when_marker_absent(self, tmp_path, monkeypatch,
                                            memory_db_session_no_unique_indexes):
        from cps import ub
        from cps.ub import KoboReadingState
        session, engine = memory_db_session_no_unique_indexes
        monkeypatch.setattr(ub.constants, "CONFIG_DIR", str(tmp_path))

        session.add(KoboReadingState(
            user_id=1, book_id=42,
            last_modified=datetime(2026, 5, 1, tzinfo=timezone.utc),
        ))
        session.add(KoboReadingState(
            user_id=1, book_id=42,
            last_modified=datetime(2026, 5, 10, tzinfo=timezone.utc),
        ))
        session.commit()
        assert session.query(KoboReadingState).filter_by(user_id=1, book_id=42).count() == 2

        ub.migrate_kobo_unique_constraints(engine, session)

        rows = session.query(KoboReadingState).filter_by(user_id=1, book_id=42).all()
        assert len(rows) == 1
        # SQLite strips tz on DATETIME round-trip; compare naive.
        assert rows[0].last_modified.replace(tzinfo=None) == datetime(2026, 5, 10)


@pytest.fixture
def reading_state_db(tmp_path, monkeypatch):
    """``get_or_create_reading_state`` against a file-backed app.db with the
    UNIQUE indexes, as ``kobo.current_user`` 1. File-backed so a competing
    request can write through a connection of its own."""
    from types import SimpleNamespace

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from cps import kobo, ub

    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}", future=True)
    ub.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    session = Session()
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(kobo, "current_user", SimpleNamespace(id=1))
    yield SimpleNamespace(engine=engine, Session=Session, session=session)
    session.close()
    engine.dispose()


def _graph_counts(engine, book_id):
    with engine.connect() as conn:
        return {
            table: conn.exec_driver_sql(sql, (book_id,)).scalar()
            for table, sql in (
                ("book_read_link", "SELECT COUNT(*) FROM book_read_link "
                                   "WHERE user_id = 1 AND book_id = ?"),
                ("kobo_reading_state", "SELECT COUNT(*) FROM kobo_reading_state "
                                       "WHERE user_id = 1 AND book_id = ?"),
                ("kobo_bookmark",
                 "SELECT COUNT(*) FROM kobo_bookmark b JOIN kobo_reading_state s "
                 "ON b.kobo_reading_state_id = s.id "
                 "WHERE s.user_id = 1 AND s.book_id = ?"),
                ("kobo_statistics",
                 "SELECT COUNT(*) FROM kobo_statistics t JOIN kobo_reading_state s "
                 "ON t.kobo_reading_state_id = s.id "
                 "WHERE s.user_id = 1 AND s.book_id = ?"),
            )
        }


ONE_OF_EACH = {"book_read_link": 1, "kobo_reading_state": 1,
               "kobo_bookmark": 1, "kobo_statistics": 1}


@pytest.mark.unit
def test_a_repeated_first_contact_reuses_the_one_reading_state(reading_state_db):
    """A Kobo asks about a book twice (a GET then its PUT, or a retried PUT):
    the second call returns the row the first one made."""
    from cps import kobo

    first = kobo.get_or_create_reading_state(7)
    second = kobo.get_or_create_reading_state(7)

    assert first.id == second.id
    assert _graph_counts(reading_state_db.engine, 7) == ONE_OF_EACH


@pytest.mark.unit
@pytest.mark.parametrize("raced_table", [
    "book_read_link", "kobo_reading_state", "kobo_bookmark"])
def test_a_competing_request_that_creates_the_rows_first_is_not_an_error(
        reading_state_db, raced_table):
    """Two state requests for one book from a Kobo waking from sleep (#132).
    The competitor runs the whole creation through its own connection and
    commits in the gap just before this request's INSERT into
    ``raced_table`` -- after any check this request made, before its write.
    Both requests succeed with the same reading state, and the book has one
    row in each table: a duplicate made every later ``.one()`` a 500 the
    device retried forever, and a plain INSERT hands the caller an
    IntegrityError."""
    from sqlalchemy import event
    from cps import kobo, ub

    competitor = {}

    def compete(conn, cursor, statement, *_args):
        if competitor or not statement.lstrip().upper().startswith(
                "INSERT INTO " + raced_table.upper()):
            return
        competitor["running"] = True
        mine = ub.session
        other = reading_state_db.Session()
        ub.session = other
        try:
            competitor["state_id"] = kobo.get_or_create_reading_state(7).id
        finally:
            ub.session = mine
            other.close()

    event.listen(reading_state_db.engine, "before_cursor_execute", compete)
    try:
        state = kobo.get_or_create_reading_state(7)
    finally:
        event.remove(reading_state_db.engine, "before_cursor_execute", compete)

    assert "state_id" in competitor, (
        "the competing request never ran: nothing was raced")
    assert state.id == competitor["state_id"]
    assert _graph_counts(reading_state_db.engine, 7) == ONE_OF_EACH
