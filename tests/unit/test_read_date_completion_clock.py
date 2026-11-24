# SPDX-License-Identifier: GPL-3.0-or-later
"""Finishing a book is independent of explicit choices and later sync traffic.

The ordering assertion reproduces #2801's stale-start-date bug through the
same conditional status writer Kobo and KOSync use. Dropping the completion
clock write, or touching it on an unchanged status, breaks these tests.
"""
from datetime import datetime

import pytest
from sqlalchemy import select

from cps import db, ub
from cps.services.reading_status import update_automatic_read_status
from cps.sort_orders import book_sort_order
from tests.unit.test_library_recent_sort import library, _toggle_read_checkmark
from tests.unit.test_kobo_state_unique_constraint import memory_db_session, memory_db_session_no_unique_indexes

pytestmark = pytest.mark.unit


def ordered(library):
    return list(library.session.execute(select(db.Books.id).order_by(
        *book_sort_order("readnew", library.reader.id))).scalars())


@pytest.mark.parametrize("touch_unchanged", [False, True])
def test_device_finish_uses_completion_time_and_repeated_reports_do_not_move_it(library, touch_unchanged):
    session = library.session
    row = ub.ReadBook(user_id=library.reader.id, book_id=1,
        read_status=ub.ReadBook.STATUS_IN_PROGRESS,
        read_status_choice_at=datetime(2020, 1, 1), last_modified=datetime(2020, 1, 1))
    other = ub.ReadBook(user_id=library.reader.id, book_id=2,
        read_status=ub.ReadBook.STATUS_FINISHED,
        read_status_choice_at=datetime(2024, 1, 1), last_modified=datetime(2024, 1, 1))
    session.add_all([row, other])
    session.commit()
    assert update_automatic_read_status(row, ub.ReadBook.STATUS_FINISHED,
        observed_clock=datetime(2025, 1, 1), touch_unchanged=touch_unchanged)
    session.commit()
    assert ordered(library)[:2] == [1, 2], "device completion must outrank the older finish"
    assert row.read_status_choice_at == datetime(2020, 1, 1), "automatic progress is not explicit intent"

    # Another book finishes after it. Further reports on the already-finished
    # first book must not replace the date when it was actually completed.
    other.read_status = ub.ReadBook.STATUS_IN_PROGRESS
    session.commit()
    assert update_automatic_read_status(other, ub.ReadBook.STATUS_FINISHED,
        observed_clock=datetime(2026, 1, 1))
    session.commit()
    assert update_automatic_read_status(row, ub.ReadBook.STATUS_FINISHED,
        observed_clock=datetime(2027, 1, 1), touch_unchanged=touch_unchanged)
    session.commit()
    assert ordered(library)[:2] == [2, 1], "repeated completion traffic must not reorder the Read list"


def test_legacy_finish_date_is_frozen_once_during_upgrade():
    from sqlalchemy import create_engine, text

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE book_read_link "
            "(id INTEGER PRIMARY KEY, read_status INTEGER, last_modified DATETIME)"))
        connection.execute(text("INSERT INTO book_read_link VALUES "
            "(1, 1, '2020-01-01 00:00:00'), (2, 2, '2021-01-01 00:00:00')"))
    ub.migrate_read_finished_at(engine, None)
    with engine.begin() as connection:
        assert connection.execute(text("SELECT read_finished_at FROM book_read_link ORDER BY id")).scalars().all() == [
            '2020-01-01 00:00:00', None]
        connection.execute(text("UPDATE book_read_link SET last_modified = '2030-01-01 00:00:00'"))
    ub.migrate_read_finished_at(engine, None)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT read_finished_at FROM book_read_link ORDER BY id")).scalars().all() == [
            '2020-01-01 00:00:00', None]
    engine.dispose()


def test_merging_duplicate_books_preserves_the_latest_finish(library):
    from cps.user_book_data import migrate_user_book_data

    for book, year in [(1, 2020), (2, 2022)]:
        library.session.add(ub.ReadBook(user_id=library.reader.id, book_id=book,
            read_status=ub.ReadBook.STATUS_FINISHED,
            last_modified=datetime(year, 1, 1), read_status_choice_at=datetime(year, 1, 1)))
    library.session.commit()
    migrate_user_book_data(2, 1, library.session)
    library.session.commit()
    kept = library.session.query(ub.ReadBook).filter_by(book_id=1).one()
    assert kept.read_finished_at == datetime(2022, 1, 1), "merging books must not lose the later completion"


def test_opening_a_finished_book_does_not_replace_its_explicit_finish(library, monkeypatch):
    from cps.services.read_status import record_reader_open
    from cps import helper

    monkeypatch.setenv("CWNG_SHARE_PLACE_GATES", "0")
    for book in [1, 2]:
        _toggle_read_checkmark(library, book, True, monkeypatch)
    record_reader_open(library.reader, 1)
    library.session.commit()
    assert ordered(library)[:2] == [2, 1], "opening a finished book is not a new finish"
    assert helper.edit_book_read_status(1, False, sync_hardcover=False) == ""
    library.session.commit()
    assert library.session.query(ub.ReadBook).filter_by(book_id=1).one().read_finished_at is None


def test_duplicate_row_recovery_keeps_finish_date_even_if_another_row_has_later_activity(memory_db_session_no_unique_indexes):
    session, _ = memory_db_session_no_unique_indexes
    session.add_all([
        ub.ReadBook(user_id=1, book_id=42, read_status=ub.ReadBook.STATUS_FINISHED,
            last_modified=datetime(2030, 1, 1), read_finished_at=datetime(2020, 1, 1)),
        ub.ReadBook(user_id=1, book_id=42, read_status=ub.ReadBook.STATUS_FINISHED,
            last_modified=datetime(2022, 1, 1), read_finished_at=datetime(2022, 1, 1)),
    ])
    session.commit()
    assert ub._dedupe_book_read_link(session) == 1
    session.commit()
    assert session.query(ub.ReadBook).one().read_finished_at == datetime(2022, 1, 1)
