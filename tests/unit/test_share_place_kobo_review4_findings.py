# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Acceptance tests for the two findings of the fourth adversarial review of
"Share my place" PR 3a (state/kindle-kids/legs/share-place-pr3/REVIEW-sol-4.md,
reviewed at f7b498257).

Spec: state/kindle-kids/specs/2026-10-11-share-my-place.md, sections 3, 11
and 12. A report is ordered by its effective time (its client clock, or its
receipt when that clock is too fast) and then by its actual receipt.

1. Receipt ties and existing rows.
   * An honest own report in the same client second as the shared one, but
     received later, wins at turn-on: no nudge, GET serves it, sync sends
     no older shared state.
   * Two reports with the same trusted (slightly fast) client clock: the
     later receipt wins, for place and for status, in the journal and for a
     checked Kobo reading the shared place.
   * A journal row written with the switch off, carrying a day-fast clock:
     once the switch is on, an honest newer report wins.
2. A checked PUT that loses the shared bookmark write to a concurrent legacy
   writer changes nothing else: the legacy day-fast status and statistics
   clocks stay as they were, so a later stale Reading does not erase
   Finished. The concurrent writer commits between the checked request's
   reads and its first update of the reading graph (scheduled at that
   update, over a WAL
   file database). The control runs the same schedule with no legacy clock
   normalization at all.
3. KOSync's journal (PR 2) keeps the status observation as one time, so a
   status tie has no receipt to break it (spec section 13). An unchecked,
   identified KOReader pushes two reports in the same client second,
   reading then finished: the later receipt wins, and the library listing
   serves finished.
"""

import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from tests.unit.kobo_state_world import (  # noqa: F401  (fixtures)
    _kts, _now, _states, kw, read_column_class,
)
from tests.unit.test_share_place_kobo_review_findings import _raw_put
from tests.unit.test_share_place_kosync_gates import (  # noqa: F401  (fixture)
    _library_status, _put, _register, world,
)

pytestmark = pytest.mark.unit

GATES_ENV = "CWNG_SHARE_PLACE_GATES"


@pytest.fixture(autouse=True)
def gates_enforced(monkeypatch):
    monkeypatch.setenv(GATES_ENV, "1")


def _report(clock, *, percent=None, status=None, statistics=None, location=None):
    bookmark = None if percent is None else {"ProgressPercent": percent}
    if bookmark is not None and location is not None:
        bookmark["Location"] = location
    return {"LastModified": _kts(clock),
            "CurrentBookmark": bookmark,
            "Statistics": statistics,
            "StatusInfo": None if status is None else {"Status": status}}


def _instant():
    """The server's current time to the microsecond (naive UTC)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# == 1. Receipt ties and existing rows ========================================

def test_an_honest_same_second_own_report_received_later_wins_at_turn_on(kw):
    """Shared 70% and A's own 30% carry the same client second T; the shared
    one was received first."""
    book = kw.books[0]
    second = _now()
    kw.seed_shared(book, 70, second)
    kw.session.execute(kw.ub.KoboBookmark.__table__.update().values(
        last_modified=second, server_modified_at=_instant()))
    kw.session.commit()
    time.sleep(0.02)
    _raw_put(kw, kw.a, book, _report(second, percent=30))

    kw.share(kw.a, True)

    assert kw.session.query(kw.ub.KoboSharePlaceNudge).filter_by(
        device_id=kw.a.id, book_id=book.id).count() == 0
    assert kw.get(kw.a, book)["CurrentBookmark"]["ProgressPercent"] == 30
    assert book.uuid not in _states(kw.sync(kw.a))


@pytest.mark.parametrize("field", ["place", "status"])
def test_the_later_receipt_wins_a_trusted_fast_clock_tie(kw, field):
    """Both reports carry the same client clock two minutes fast (inside the
    allowance, so trusted); the second one received wins."""
    book = kw.books[0]
    clock = _now() + timedelta(minutes=2)
    first, second = ((dict(percent=70), dict(percent=20)) if field == "place"
                     else (dict(status="Reading"), dict(status="Finished")))
    _raw_put(kw, kw.a, book, _report(clock, **first))
    time.sleep(0.02)
    _raw_put(kw, kw.a, book, _report(clock, **second))

    served = kw.get(kw.a, book)
    if field == "place":
        assert served["CurrentBookmark"]["ProgressPercent"] == 20
    else:
        assert served["StatusInfo"]["Status"] == "Finished"


def test_a_checked_kobo_is_served_a_shared_trusted_fast_clock_tie_winner(kw):
    """Both Kobos provably hold the current KEPUB (completed downloads), so
    the winner's place reaches the other one exactly (spec 14c)."""
    book = kw.books[0]
    kw.hold_current_kepub(kw.a, book)
    kw.hold_current_kepub(kw.b, book)
    kw.share(kw.a, True)
    kw.share(kw.b, True)
    clock = _now() + timedelta(minutes=2)

    def span(percent):
        return {"Value": f"kobo.{percent}.1", "Type": "KoboSpan", "Source": "OEBPS/chap.xhtml"}

    _raw_put(kw, kw.a, book, _report(clock, percent=50, location=span(50)))
    time.sleep(0.02)
    _raw_put(kw, kw.b, book, _report(clock, percent=75, location=span(75)))
    assert kw.shared(book)["bookmark"][0] == 75

    served = kw.get(kw.a, book)["CurrentBookmark"]
    assert served["ProgressPercent"] == 75
    assert served["Location"]["Value"] == "kobo.75.1"


def test_a_switch_off_journal_rows_fast_clock_loses_to_an_honest_newer_report(
        kw, monkeypatch):
    book = kw.books[0]
    monkeypatch.setenv(GATES_ENV, "0")
    kw.put(kw.a, book, 70, _now() + timedelta(days=1))
    monkeypatch.setenv(GATES_ENV, "1")

    # The fast row is ordered by its receipt; the honest clock is read after
    # that receipt, so it is newer however long the first PUT took. (Read
    # before it, a slow first PUT could land its receipt past the honest
    # clock, and the test then failed under load.)
    kw.put(kw.a, book, 20, _now() + timedelta(seconds=1))

    assert kw.get(kw.a, book)["CurrentBookmark"]["ProgressPercent"] == 20


# == 2. A checked PUT losing the bookmark write to a concurrent writer ========

@pytest.mark.parametrize("normalization", ["as-implemented", "none"],
                         ids=["losing-put", "control-without-normalization"])
def test_a_checked_put_losing_to_a_concurrent_writer_keeps_finished(
        kw, monkeypatch, normalization):
    """Shared 70% / Finished / statistics, every clock a day fast, bookmark
    receipt capped two minutes ago. A's own journal holds a newer report
    (so its checked PUT writes no journal row). A's checked place-only 30%
    dated a minute ago beats the capped baseline on read, but an unresolved
    writer commits 95% (legacy path) before A's first write. Afterwards a
    stale checked Reading dated a minute ago arrives."""
    from cps import ub
    from cps.services import device_reading_position as legacy
    from cps.services import kobo_place_gate as gate

    book = kw.books[0]
    now = _now()
    future = now + timedelta(days=1)
    kw.seed_shared(book, 70, future, status=ub.ReadBook.STATUS_FINISHED)
    kw.session.execute(ub.KoboBookmark.__table__.update().values(
        last_modified=future, server_modified_at=now - timedelta(minutes=2)))
    kw.session.commit()
    kw.put(kw.a, book, 50, now + timedelta(minutes=2))
    kw.share(kw.a, True)
    if normalization == "none":
        monkeypatch.setattr(gate, "_settle_legacy_future_clocks",
                            lambda *_a, **_k: None, raising=False)

    with tempfile.TemporaryDirectory(prefix="share-place-review4-") as tmp:
        path = Path(tmp) / "app.db"
        copy = sqlite3.connect(str(path))
        kw.session.connection().connection.driver_connection.backup(copy)
        copy.close()
        kw.session.rollback()
        engine = create_engine("sqlite:///" + str(path),
                               connect_args={"timeout": 5})
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        maker = sessionmaker(bind=engine)
        session = maker()
        monkeypatch.setattr(ub, "session", session)
        monkeypatch.setattr(ub, "session_commit",
                            lambda *_a, **_k: session.commit() or True)
        mark_id = session.execute(select(ub.KoboBookmark.id)).scalar_one()

        request_thread = threading.current_thread()
        reached_first_write, other_committed = threading.Event(), threading.Event()
        graph = ("book_read_link", "kobo_statistics", "kobo_bookmark",
                 "kobo_reading_state")

        def at_first_write(_conn, _cursor, statement, *_args):
            if (threading.current_thread() is request_thread
                    and not reached_first_write.is_set()
                    and statement.lstrip().upper().startswith("UPDATE")
                    and any(table in statement for table in graph)):
                reached_first_write.set()
                assert other_committed.wait(timeout=5)

        def unresolved_writer():
            assert reached_first_write.wait(timeout=5)
            with maker() as other:
                result = legacy.advance_kobo_bookmark(
                    other.get(ub.KoboBookmark, mark_id), 95,
                    incoming_clock=now, clock_accepts=False, session=other)
                assert result.accepted
                other.commit()
            other_committed.set()

        event.listen(engine, "before_cursor_execute", at_first_write)
        worker = threading.Thread(target=unresolved_writer)
        worker.start()
        try:
            _raw_put(kw, kw.a, book, _report(now - timedelta(minutes=1),
                                             percent=30))
            worker.join(timeout=10)
            assert reached_first_write.is_set() and other_committed.is_set()
            event.remove(engine, "before_cursor_execute", at_first_write)
            session.expire_all()
            assert session.get(ub.KoboBookmark, mark_id).progress_percent == 95
            read = session.query(ub.ReadBook).one()
            statistics = session.query(ub.KoboStatistics).one()
            legacy_clocks = (read.last_modified, statistics.last_modified)

            _raw_put(kw, kw.a, book, _report(now - timedelta(minutes=1),
                                             status="Reading"))
            session.expire_all()
            status = session.query(ub.ReadBook.read_status).scalar()
        finally:
            worker.join(timeout=10)
            session.close()
            engine.dispose()

    def naive(value):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    assert tuple(naive(clock) for clock in legacy_clocks) == (future, future)
    assert status == ub.ReadBook.STATUS_FINISHED


# == 3. KOSync status ties ====================================================

@pytest.mark.parametrize("ahead", [timedelta(0), timedelta(minutes=2)],
                         ids=["honest-same-second", "trusted-fast-clock"])
def test_a_kosync_status_tie_in_one_client_second_goes_to_the_later_receipt(
        world, ahead):
    """With an honest clock the one stored time (the client second) is never
    later than a receipt, so that case passes today; a trusted fast clock
    stores a time later than the second report's receipt."""
    _register(world, "kindle-a", "Kindle A")
    second = int(time.time() + ahead.total_seconds())
    _put(world, 50, device_id="kindle-a", name="Kindle A", client_at=second)
    time.sleep(0.02)
    _put(world, 100, device_id="kindle-a", name="Kindle A", client_at=second)

    assert _library_status(world, device_id="kindle-a", name="Kindle A") == "finished"
