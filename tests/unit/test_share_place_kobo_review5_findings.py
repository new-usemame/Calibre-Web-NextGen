# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Acceptance tests for the findings of the fifth adversarial review of
"Share my place" PR 3a (state/kindle-kids/legs/share-place-pr3/REVIEW-sol-5.md,
reviewed at d9315f26f), against the behaviour decided in spec section 13a
(state/kindle-kids/specs/2026-10-11-share-my-place.md).

1. A status row PR 2 (main) wrote, with no status receipt, keeps main's
   ordering after the app's startup migration: a delayed older Reading
   loses to the stored Finished.
2. A legacy status with no receipt (and no clock) is frozen once by the
   migration, so a later place-only write does not re-time it: a later
   honest Finished still wins (Kobo; and a KOSync progress PUT to 100%).
   A day-fast legacy status clock is not used here: section 13a keeps
   main's key for it, under which an honest Finished loses either way.
3. Two concurrent clockless place reports with the same receipt, landing on
   a row whose place is empty: the first committed one stays.
4. A status write over a legacy row with no status receipt, racing a place
   write, ends as section 13a orders it: the place's receipt is never the
   status's key, so the stored day-fast Finished stays. With the row's own
   receipt (control) the newer status is still written.

The concurrent cases run two connections over a WAL file database; one
writer is held at its UPDATE until the other has committed.
"""

import sqlite3
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, insert, select
from sqlalchemy.orm import sessionmaker

from tests.unit.kobo_state_world import (  # noqa: F401  (fixtures)
    _now, kw, read_column_class,
)
from tests.unit.test_share_place_kobo_review4_findings import _report
from tests.unit.test_share_place_kobo_review_findings import _raw_put
from tests.unit.test_share_place_kosync_gates import (  # noqa: F401  (fixture)
    BOOK, _library_status, _put, _register, _status, world,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def gates_enforced(monkeypatch):
    monkeypatch.setenv("CWNG_SHARE_PLACE_GATES", "1")


def _naive(value):
    if isinstance(value, datetime) and value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _seed_journal(session, device_id, book_id, **values):
    from cps import ub
    session.execute(insert(ub.DeviceReadingPosition.__table__).values(
        device_id=device_id, book_id=book_id,
        **{key: _naive(value) for key, value in values.items()}))
    session.commit()


def _startup_migration(session):
    from cps import ub
    ub.migrate_Database(session)
    session.commit()
    session.expire_all()


def _served_status(kw, book):
    return kw.get(kw.a, book)["StatusInfo"]["Status"]


# == 1. Main's status rows keep main's ordering ===============================

def test_a_main_status_row_keeps_finished_against_a_delayed_reading(kw):
    """Main wrote a 40% place at T and an explicit Finished at T+30 (its
    status key is (T+30, T+30); it stored no status receipt). After the
    upgrade a Reading dated T+5 arrives, received at T+31."""
    from cps import ub
    book = kw.books[0]
    place_at = _now() - timedelta(minutes=31)
    _seed_journal(kw.session, kw.a.id, book.id, progress_percent=40,
                  client_modified_at=place_at, server_modified_at=place_at,
                  reading_status=ub.ReadBook.STATUS_FINISHED,
                  status_modified_at=place_at + timedelta(minutes=30))
    _startup_migration(kw.session)

    _raw_put(kw, kw.a, book, _report(place_at + timedelta(minutes=5),
                                     status="Reading"))

    assert _served_status(kw, book) == "Finished"


# == 2. A legacy status's key does not move with the place ====================

@pytest.mark.parametrize("receipt", ["legacy-null", "own-receipt-control"])
def test_a_place_write_does_not_retime_a_legacy_kobo_status(kw, receipt):
    from cps import ub
    book = kw.books[0]
    then = _now() - timedelta(hours=1)
    _seed_journal(kw.session, kw.a.id, book.id, progress_percent=30,
                  client_modified_at=then, server_modified_at=then,
                  reading_status=ub.ReadBook.STATUS_IN_PROGRESS,
                  status_received_at=None if receipt == "legacy-null" else then)
    _startup_migration(kw.session)

    _raw_put(kw, kw.a, book, _report(_now() - timedelta(minutes=10), percent=40))
    _raw_put(kw, kw.a, book, _report(_now() - timedelta(minutes=5),
                                     status="Finished"))

    assert _served_status(kw, book) == "Finished"


@pytest.mark.parametrize("receipt", ["legacy-null", "own-receipt-control"])
def test_a_kosync_progress_put_to_the_end_finishes_a_legacy_status(world, receipt):
    from cps import ub
    device = _register(world, "kindle-a", "Kindle A")
    then = datetime.now(timezone.utc) - timedelta(hours=1)
    _seed_journal(world.session, device.id, BOOK, progress_percent=40,
                  client_modified_at=then, server_modified_at=then,
                  reading_status=ub.ReadBook.STATUS_IN_PROGRESS,
                  status_received_at=None if receipt == "legacy-null" else then)
    _startup_migration(world.session)

    _put(world, 100, device_id="kindle-a", name="Kindle A",
         client_at=time.time() - 300)

    assert _library_status(world, device_id="kindle-a", name="Kindle A") == "finished"


# == WAL file databases =======================================================

@contextmanager
def _wal_copy(monkeypatch, session):
    """The world's database copied to a WAL file; the app's session points at
    it. Yields (engine, maker)."""
    from cps import ub
    with tempfile.TemporaryDirectory(prefix="share-place-review5-") as tmp:
        path = Path(tmp) / "app.db"
        copy = sqlite3.connect(str(path))
        session.connection().connection.driver_connection.backup(copy)
        copy.close()
        session.rollback()
        engine = create_engine("sqlite:///" + str(path), connect_args={"timeout": 5})
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        maker = sessionmaker(bind=engine)
        app_session = maker()
        monkeypatch.setattr(ub, "session", app_session)
        monkeypatch.setattr(ub, "session_commit",
                            lambda *_a, **_k: app_session.commit() or True)
        try:
            yield engine, maker
        finally:
            app_session.close()
            engine.dispose()


@contextmanager
def _empty_journal_db(tmp_path):
    from cps import ub
    engine = create_engine("sqlite:///" + str(tmp_path / "journal.db"),
                           connect_args={"timeout": 5})
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    ub.Base.metadata.create_all(engine, tables=[
        ub.DeviceReadingPosition.__table__, ub.KoboSharePlaceNudge.__table__])
    try:
        yield engine, sessionmaker(bind=engine)
    finally:
        engine.dispose()


# == 3. Empty place, then two first places with the same receipt ==============

@pytest.mark.parametrize("schedule", ["concurrent", "sequential-control"])
@pytest.mark.parametrize("protocol", ["kobo", "kosync"])
def test_the_first_of_two_tied_first_places_stays(tmp_path, monkeypatch,
                                                  protocol, schedule):
    """A status-only first report left the row with an empty place and a
    receipt T. Two clockless place reports, 70% then 20%, are received at T
    as well; both read the empty place."""
    from cps import ub
    from cps.services import kobo_place_gate as gate
    from cps.services import kosync_place
    from cps.services.place_arbiter import Report

    received = datetime.now(timezone.utc)
    monkeypatch.setattr(gate, "_now", lambda: received)

    with _empty_journal_db(tmp_path) as (engine, maker):
        with maker() as s:
            _seed_journal(s, 1, 1, server_modified_at=received,
                          reading_status=ub.ReadBook.STATUS_FINISHED,
                          status_modified_at=received, status_received_at=received)

        def writer(percent):
            with maker() as s:
                if protocol == "kobo":
                    accepted = gate.record_own_report(
                        device_id=1, book_id=1, client_at=None,
                        place={"progress_percent": percent}, session=s).accepted
                else:
                    accepted = kosync_place.latest_write(
                        ub.DeviceReadingPosition, dict(device_id=1, book_id=1),
                        dict(progress_percent=percent, client_modified_at=None,
                             server_modified_at=received),
                        client_column="client_modified_at",
                        server_column="server_modified_at",
                        present_column="progress_percent",
                        candidate=Report(None, received), session=s)
                s.commit()
                return accepted

        if schedule == "sequential-control":
            outcome = (writer(70), writer(20))
        else:
            both_read = threading.Barrier(2)
            first_committed = threading.Event()
            second = []
            local = threading.local()

            def read_together(_conn, _cursor, statement, *_args):
                if (statement.lstrip().upper().startswith("SELECT")
                        and "device_reading_position" in statement
                        and not getattr(local, "met", False)):
                    local.met = True
                    both_read.wait(5)

            def second_writes_after_first(_conn, _cursor, statement, *_args):
                if (threading.current_thread() in second
                        and statement.lstrip().upper().startswith("UPDATE")
                        and "device_reading_position" in statement):
                    assert first_committed.wait(5)

            def first():
                try:
                    return writer(70)
                finally:
                    first_committed.set()

            def later():
                second.append(threading.current_thread())
                return writer(20)

            event.listen(engine, "after_cursor_execute", read_together)
            event.listen(engine, "before_cursor_execute", second_writes_after_first)
            results = {}
            threads = [threading.Thread(target=lambda: results.update(a=first())),
                       threading.Thread(target=lambda: results.update(b=later()))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)
            event.remove(engine, "after_cursor_execute", read_together)
            event.remove(engine, "before_cursor_execute", second_writes_after_first)
            outcome = (results.get("a"), results.get("b"))

        with maker() as s:
            place = s.execute(select(ub.DeviceReadingPosition.progress_percent)).scalar()

    assert outcome == (True, False)
    assert place == 70


# == 4. A status write racing a place write ===================================

def _race_status_with_place(monkeypatch, session, device_id, book_id, *,
                            place_at, status_write):
    """Run ``status_write`` (a real handler) on this thread, holding its first
    journal UPDATE until a place-only report for the same row has committed
    on another connection. Returns the final journal row."""
    from cps import ub
    from cps.services import kobo_place_gate as gate
    with _wal_copy(monkeypatch, session) as (engine, maker):
        request_thread = threading.current_thread()
        go, place_done = threading.Event(), threading.Event()

        def hold(_conn, _cursor, statement, *_args):
            if (threading.current_thread() is request_thread and not go.is_set()
                    and statement.lstrip().upper().startswith("UPDATE")
                    and "device_reading_position" in statement):
                go.set()
                assert place_done.wait(5)

        def place_writer():
            go.wait(15)
            with maker() as s:
                assert gate.record_own_report(
                    device_id=device_id, book_id=book_id, client_at=place_at,
                    place={"progress_percent": 50}, session=s).accepted
                s.commit()
            place_done.set()

        event.listen(engine, "before_cursor_execute", hold)
        worker = threading.Thread(target=place_writer)
        worker.start()
        try:
            status_write()
        finally:
            go.set()
            worker.join(timeout=15)
            event.remove(engine, "before_cursor_execute", hold)
        assert place_done.is_set()
        with maker() as s:
            row = s.execute(select(ub.DeviceReadingPosition.__table__)).one()
    return row


def _legacy_or_own(receipt, base):
    """A Finished status: legacy (day-fast clock, no receipt) or its own
    honest clock and receipt."""
    if receipt == "legacy-null":
        return dict(status_modified_at=base + timedelta(days=1), status_received_at=None)
    return dict(status_modified_at=base, status_received_at=base)


@pytest.mark.parametrize("receipt", ["legacy-null", "own-receipt-control"])
def test_a_kobo_status_racing_a_place_write_is_ordered_by_its_own_key(
        kw, monkeypatch, receipt):
    from cps import ub
    book = kw.books[0]
    now = _now()
    base = now - timedelta(minutes=20)
    _seed_journal(kw.session, kw.a.id, book.id, progress_percent=40,
                  client_modified_at=base, server_modified_at=base,
                  reading_status=ub.ReadBook.STATUS_FINISHED,
                  **_legacy_or_own(receipt, base))

    row = _race_status_with_place(
        monkeypatch, kw.session, kw.a.id, book.id, place_at=now - timedelta(minutes=5),
        status_write=lambda: _raw_put(kw, kw.a, book, _report(
            now - timedelta(minutes=10), status="Reading")))

    assert row.progress_percent == 50
    assert row.reading_status == (ub.ReadBook.STATUS_FINISHED
                                  if receipt == "legacy-null"
                                  else ub.ReadBook.STATUS_IN_PROGRESS)


@pytest.mark.parametrize("receipt", ["legacy-null", "own-receipt-control"])
def test_a_kosync_status_racing_a_place_write_is_ordered_by_its_own_key(
        world, monkeypatch, receipt):
    from cps import ub
    device = _register(world, "kindle-a", "Kindle A")
    now = datetime.now(timezone.utc)
    base = now - timedelta(minutes=20)
    _seed_journal(world.session, device.id, BOOK, progress_percent=40,
                  client_modified_at=base, server_modified_at=base,
                  reading_status=ub.ReadBook.STATUS_FINISHED,
                  **_legacy_or_own(receipt, base))

    def status_write():
        response = _status(world, "reading", device_id="kindle-a", name="Kindle A")
        assert response.status_code == 200, response.get_data(as_text=True)

    row = _race_status_with_place(
        monkeypatch, world.session, device.id, BOOK,
        place_at=now - timedelta(minutes=5), status_write=status_write)

    assert row.progress_percent == 50
    assert row.reading_status == (ub.ReadBook.STATUS_FINISHED
                                  if receipt == "legacy-null"
                                  else ub.ReadBook.STATUS_IN_PROGRESS)
