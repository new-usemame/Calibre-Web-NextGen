# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Acceptance tests for the ten findings of the adversarial review of
"Share my place" PR 3a (state/kindle-kids/legs/share-place-pr3/REVIEW-sol.md,
reviewed at 4a9e4fb3e).

Spec: state/kindle-kids/specs/2026-10-11-share-my-place.md, sections 11 and
12. Each section below pins the outcome a finding showed missing, not a fix:

1. Two startups migrating one app.db at once both succeed.
2. A sync page retried after the box was turned off carries no shared
   ReadingState, while its entitlements and frontier still progress. With
   the rollout switch off, replay is unchanged.
3. A Kobo turned on while its own place is newer than the shared one is sent
   no older shared state by any path (entitlement-embedded, standalone,
   repair), even with no unchecked sync between its report and the toggle.
4. Two checked Kobos reporting in the same client second: the winner, chosen
   by later receipt, reaches the other Kobo on its next sync.
5. After legacy future clocks, an honest checked winner reaches the other
   checked Kobo and its Finished status is kept.
6. A receipt migration interrupted after ADD COLUMN completes its backfill on
   the next startup, and a later run keeps receipts already written.
7. Acknowledging an old page never deletes a nudge armed after it.
8. A checked status-only report is journalled, so after turn-off the device
   is served its own Finished status.
9. A report with no clock is not overwritten by an older-dated report that
   arrives after it.
10. "Recently read" ranks a Kobo update by when it arrived, with the rollout
    switch off too, so neither an old nor a far-future device clock decides
    its place.

The Kobo routes run through the real handlers over the in-memory world of
``kobo_state_world``; the migrations run against file-backed SQLite.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask, g
from sqlalchemy import create_engine, event

from tests.unit.kobo_state_world import (  # noqa: F401  (fixtures)
    _kts, _now, _states, kw, read_column_class,
)
from tests.unit.test_library_recent_sort import (  # noqa: F401  (fixture)
    _koreader_sync, _kobo_progress_put, _web_reader_save, library,
)

pytestmark = pytest.mark.unit

GATES_ENV = "CWNG_SHARE_PLACE_GATES"


@pytest.fixture(autouse=True)
def gates_enforced(monkeypatch):
    """Enforcement on for every account; switch-off tests set it to "0"."""
    monkeypatch.setenv(GATES_ENV, "1")


def _raw_put(kw, device, book, reading_state):
    """PUT /state with a hand-built ReadingState (fields the world's ``put``
    always fills may be missing or null here)."""
    app = Flask(__name__)
    payload = {"ReadingStates": [reading_state]}
    with app.test_request_context("/state", method="PUT", json=payload):
        g.annotation_origin_device_id = device.id
        response = kw.kobo.HandleStateRequest.__wrapped__(book.uuid)
    assert response.status_code == 200, response.get_data(as_text=True)
    kw.session.expire_all()
    return response


def _percent(states, book):
    return [s["CurrentBookmark"]["ProgressPercent"] for s in states[book.uuid]]


def _entitled(response):
    """Book ids of the entitlements a sync page carries."""
    ids = set()
    for item in response.get_json() or []:
        entitlement = item.get("NewEntitlement") or item.get("ChangedEntitlement")
        if entitlement:
            ids.add(str(entitlement["BookEntitlement"]["Id"]))
    return ids


def _ids(books):
    return {str(book.id) for book in books}


def _db_time(value):
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


# == 1. Concurrent startup ====================================================

def test_two_startups_migrating_one_app_db_at_once_both_succeed(tmp_path):
    """Both workers inspect the database before either creates the nudge
    table (a barrier holds each at its CREATE until both are there)."""
    from cps import ub
    engine = create_engine("sqlite:///" + str(tmp_path / "race.db"))
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE kobo_bookmark (id INTEGER PRIMARY KEY, "
            "last_modified DATETIME, server_modified_at DATETIME)")
    barrier = threading.Barrier(2)
    met = threading.local()

    def both_checked_first(conn, cursor, statement, *_args):
        if ("CREATE TABLE" in statement and "kobo_share_place_nudge" in statement
                and not getattr(met, "done", False)):
            met.done = True
            try:
                barrier.wait(timeout=5)
            except threading.BrokenBarrierError:
                pass

    event.listen(engine, "before_cursor_execute", both_checked_first)

    def startup():
        try:
            ub.migrate_kobo_bookmark_receipt(engine, None)
            return "OK"
        except Exception as error:  # noqa: BLE001 - the outcome under test
            return "%s: %s" % (type(error).__name__, error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [f.result(timeout=30)
                   for f in [pool.submit(startup) for _ in range(2)]]
    with engine.connect() as conn:
        tables = {row[0] for row in conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    engine.dispose()

    assert results == ["OK", "OK"]
    assert "kobo_share_place_nudge" in tables


# == 2. A retried page after turn-off =========================================

def test_a_page_retried_after_turn_off_carries_no_shared_state(kw):
    """The page carrying the shared 70% is lost in transport; the owner turns
    the box off; the Kobo retries with the same token."""
    book = kw.books[0]
    kw.seed_shared(book, 70, _now() - timedelta(minutes=30))
    kw.settle(kw.a)
    kw.share(kw.a, True)
    prior_token = kw.tokens[kw.a.id]
    assert book.uuid in _states(kw.sync(kw.a))

    kw.share(kw.a, False)
    kw.tokens[kw.a.id] = prior_token
    retry = kw.sync(kw.a)

    assert _states(retry) == {}
    assert _states(kw.sync(kw.a)) == {}


def test_a_first_page_retried_after_turn_off_keeps_its_entitlements_only(kw):
    """A new checked Kobo's first page (entitlements, one embedding the
    shared place) is lost; the box is turned off; the retry still delivers
    every entitlement, without the shared state, and the frontier moves on
    so the next sync is empty."""
    book = kw.books[0]
    kw.seed_shared(book, 70, _now() - timedelta(minutes=30))
    kw.share(kw.a, True)
    first = kw.sync(kw.a)
    assert book.uuid in _states(first)
    assert _entitled(first) == _ids(kw.books)

    kw.share(kw.a, False)
    kw.tokens.pop(kw.a.id)
    retry = kw.sync(kw.a)

    assert _entitled(retry) == _ids(kw.books)
    assert _states(retry) == {}
    assert kw.frontier(kw.a) >= kw.shared(book)["state"][0]
    assert kw.sync(kw.a).get_json() in ([], None)


def test_an_unexpected_token_retry_after_turn_off_carries_no_shared_state(kw):
    """The other replay branch: while the page is pending the Kobo presents
    a different valid, older token."""
    book = kw.books[0]
    kw.seed_shared(book, 70, _now() - timedelta(minutes=30))
    kw.sync(kw.a)
    older_token = kw.tokens[kw.a.id]
    kw.settle(kw.a)
    kw.share(kw.a, True)
    assert book.uuid in _states(kw.sync(kw.a))

    kw.share(kw.a, False)
    kw.tokens[kw.a.id] = older_token
    retry = kw.sync(kw.a)

    assert _states(retry) == {}


def test_switch_off_a_retried_page_is_replayed_unchanged(kw, monkeypatch):
    monkeypatch.setenv(GATES_ENV, "0")
    book = kw.books[0]
    kw.settle(kw.a)
    kw.seed_shared(book, 70, _now() - timedelta(minutes=30))
    prior_token = kw.tokens[kw.a.id]
    first = kw.sync(kw.a)
    assert _percent(_states(first), book) == [70]

    kw.share(kw.a, True)
    kw.share(kw.a, False)
    kw.tokens[kw.a.id] = prior_token
    retry = kw.sync(kw.a)

    assert retry.get_json() == first.get_json()


# == 3. Turn-on with the device's own place newer =============================

def test_turn_on_own_newer_first_sync_embeds_no_older_shared_state(kw):
    """Entitlement-embedded: the Kobo has never synced."""
    book = kw.books[0]
    now = _now()
    kw.seed_shared(book, 60, now - timedelta(minutes=90))
    kw.put(kw.a, book, 15, now - timedelta(minutes=30))

    kw.share(kw.a, True)
    first = kw.sync(kw.a)

    assert str(book.id) in _entitled(first)
    assert book.uuid not in _states(first)
    assert book.uuid not in _states(kw.sync(kw.a))


def test_turn_on_own_newer_undrained_frontier_sends_no_older_shared_state(kw):
    """Standalone: entitlements were synced before the shared place existed,
    so the shared place is still above the frontier at the toggle."""
    book = kw.books[0]
    now = _now()
    kw.settle(kw.a)
    kw.seed_shared(book, 60, now - timedelta(minutes=90))
    kw.put(kw.a, book, 15, now - timedelta(minutes=30))

    kw.share(kw.a, True)

    assert book.uuid not in _states(kw.sync(kw.a))
    assert book.uuid not in _states(kw.sync(kw.a))
    assert kw.frontier(kw.a) >= kw.shared(book)["state"][0]


def test_turn_on_own_newer_repair_sends_no_older_shared_state(kw):
    """Repair: a rehydrate latch armed for the book before the toggle."""
    from cps.services import device_reading_position as positions
    book = kw.books[0]
    now = _now()
    kw.seed_shared(book, 60, now - timedelta(minutes=90))
    kw.put(kw.a, book, 15, now - timedelta(minutes=30))
    kw.settle(kw.a)
    positions.mark_rehydrate_needed(kw.a.id, [book.id])
    kw.session.commit()

    kw.share(kw.a, True)

    assert book.uuid not in _states(kw.sync(kw.a))
    assert book.uuid not in _states(kw.sync(kw.a))


# == 4. Same client second, receipt tiebreak ==================================

def test_a_same_second_receipt_winner_reaches_the_other_kobo(kw):
    """Both Kobos provably hold the current KEPUB (completed downloads), so
    the winner's span reaches the other one exactly (spec 14c)."""
    book = kw.books[0]
    kw.hold_current_kepub(kw.a, book)
    kw.hold_current_kepub(kw.b, book)
    kw.seed_shared(book, 10, _now() - timedelta(hours=2))
    kw.share(kw.a, True)
    kw.share(kw.b, True)
    kw.settle(kw.a)
    kw.settle(kw.b)
    clock = _now() - timedelta(minutes=10)
    kw.put(kw.a, book, 70, clock)
    kw.settle(kw.a)

    kw.put(kw.b, book, 30, clock)
    assert kw.shared(book)["bookmark"][0] == 30  # later receipt won

    delivered = _states(kw.sync(kw.a))
    assert _percent(delivered, book) == [30]
    assert delivered[book.uuid][0]["CurrentBookmark"]["Location"]["Value"] \
        == "kobo.30.1"


# == 5. Legacy future clocks ==================================================

@pytest.fixture
def legacy_future(kw):
    """A legacy fast Kobo left every shared clock a day ahead; the migration
    capped only the bookmark receipt at the upgrade instant. Both Kobos are
    checked and have synced that future state."""
    book = kw.books[0]
    now = _now()
    kw.seed_shared(book, 70, now + timedelta(days=1),
                   status=kw.ub.ReadBook.STATUS_IN_PROGRESS)
    kw.session.execute(kw.ub.KoboBookmark.__table__.update().values(
        last_modified=now + timedelta(days=1),
        server_modified_at=now - timedelta(minutes=2)))
    kw.session.commit()
    kw.share(kw.a, True)
    kw.share(kw.b, True)
    kw.settle(kw.a)
    kw.settle(kw.b)
    kw.put(kw.b, book, 30, now, status="Finished")
    assert kw.shared(book)["bookmark"][0] == 30  # the honest report won
    return book


def test_after_legacy_future_clocks_an_honest_winner_reaches_the_other_kobo(
        kw, legacy_future):
    delivered = _states(kw.sync(kw.a))
    assert _percent(delivered, legacy_future) == [30]


def test_after_legacy_future_clocks_an_honest_finished_status_is_kept(
        kw, legacy_future):
    assert kw.shared(legacy_future)["read"][0] == kw.ub.ReadBook.STATUS_FINISHED
    delivered = _states(kw.sync(kw.a))
    assert delivered[legacy_future.uuid][0]["StatusInfo"]["Status"] == "Finished"


# == 6. Interrupted receipt migration =========================================

def test_an_interrupted_receipt_migration_completes_its_backfill_next_startup(
        tmp_path):
    """Startup dies on the first statement after ADD COLUMN; the next startup
    must still give the legacy row its receipt (its own clock)."""
    from cps import ub
    engine = create_engine("sqlite:///" + str(tmp_path / "legacy.db"))
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE kobo_bookmark (id INTEGER PRIMARY KEY, "
            "last_modified DATETIME)")
        conn.exec_driver_sql(
            "INSERT INTO kobo_bookmark VALUES (1, '2026-01-01 00:00:00.000000')")
    fault = {"armed": False, "fired": False}

    def after(conn, cursor, statement, *_args):
        if "ALTER TABLE" in statement and "server_modified_at" in statement:
            fault["armed"] = True

    def before(conn, cursor, statement, *_args):
        if fault["armed"] and not fault["fired"]:
            fault["fired"] = True
            raise RuntimeError("startup interrupted after ADD COLUMN")

    event.listen(engine, "after_cursor_execute", after)
    event.listen(engine, "before_cursor_execute", before)
    with pytest.raises(Exception):
        ub.migrate_kobo_bookmark_receipt(engine, None)
    event.remove(engine, "after_cursor_execute", after)
    event.remove(engine, "before_cursor_execute", before)
    assert fault["fired"]

    ub.migrate_kobo_bookmark_receipt(engine, None)
    with engine.connect() as conn:
        receipt = conn.exec_driver_sql(
            "SELECT server_modified_at FROM kobo_bookmark").scalar_one()
    engine.dispose()

    assert receipt is not None
    assert _db_time(receipt) == datetime(2026, 1, 1)


def test_a_repeated_receipt_migration_keeps_receipts_already_written(tmp_path):
    from cps import ub
    engine = create_engine("sqlite:///" + str(tmp_path / "repeat.db"))
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE kobo_bookmark (id INTEGER PRIMARY KEY, "
            "last_modified DATETIME)")
        conn.exec_driver_sql(
            "INSERT INTO kobo_bookmark VALUES (1, '2026-01-01 00:00:00.000000')")
    ub.migrate_kobo_bookmark_receipt(engine, None)
    with engine.begin() as conn:  # a genuine receipt written after upgrade
        conn.exec_driver_sql(
            "UPDATE kobo_bookmark SET server_modified_at = "
            "'2026-03-03 03:03:03.000000'")

    ub.migrate_kobo_bookmark_receipt(engine, None)
    with engine.connect() as conn:
        receipt = conn.exec_driver_sql(
            "SELECT server_modified_at FROM kobo_bookmark").scalar_one()
    engine.dispose()

    assert _db_time(receipt) == datetime(2026, 3, 3, 3, 3, 3)


# == 7. Nudge generations =====================================================

def test_acknowledging_an_old_page_keeps_a_nudge_armed_after_it(kw):
    """A is turned on (page with book 1 @70, not yet acknowledged), off, then
    B moves book 1 to 30 below A's frontier, and A is turned on again. A then
    acknowledges the old page: the new nudge must still deliver 30."""
    one, two, _ = kw.books
    now = _now()
    kw.seed_shared(one, 70, now - timedelta(minutes=30))
    kw.seed_shared(two, 40, now - timedelta(minutes=10))
    kw.share(kw.b, True)
    kw.settle(kw.b)
    kw.settle(kw.a)
    kw.share(kw.a, True)
    assert one.uuid in _states(kw.sync(kw.a))
    kw.share(kw.a, False)
    kw.put(kw.b, one, 30, now - timedelta(minutes=20))
    kw.share(kw.a, True)

    delivered = _states(kw.sync(kw.a))  # presents the old page's token

    assert _percent(delivered, one) == [30]


# == 8. Status-only checked reports ===========================================

def _finish_only(clock):
    return {"LastModified": _kts(clock), "CurrentBookmark": None,
            "Statistics": None, "StatusInfo": {"Status": "Finished"}}


def test_a_checked_status_only_finish_is_served_back_after_turn_off(kw):
    book = kw.books[0]
    now = _now()
    kw.seed_shared(book, 70, now - timedelta(hours=1))
    kw.share(kw.a, True)
    _raw_put(kw, kw.a, book, _finish_only(now))
    assert kw.shared(book)["read"][0] == kw.ub.ReadBook.STATUS_FINISHED

    kw.share(kw.a, False)

    assert kw.get(kw.a, book)["StatusInfo"]["Status"] == "Finished"


def test_a_checked_status_only_finish_replaces_an_older_own_status(kw):
    book = kw.books[0]
    now = _now()
    kw.seed_shared(book, 70, now - timedelta(hours=1))
    kw.put(kw.a, book, 40, now - timedelta(minutes=30))  # own, unchecked
    kw.share(kw.a, True)
    _raw_put(kw, kw.a, book, _finish_only(now))

    kw.share(kw.a, False)

    assert kw.get(kw.a, book)["StatusInfo"]["Status"] == "Finished"


# == 9. A clockless report ====================================================

def test_a_clockless_report_is_not_overwritten_by_an_older_dated_one(kw):
    book = kw.books[0]
    kw.put(kw.a, book, 70, _now() - timedelta(hours=1))
    _raw_put(kw, kw.a, book, {"CurrentBookmark": {"ProgressPercent": 30},
                              "Statistics": None, "StatusInfo": None})
    assert kw.journal(kw.a, book).progress_percent == 30

    kw.put(kw.a, book, 20, _now() - timedelta(minutes=30))  # stale, late

    assert kw.journal(kw.a, book).progress_percent == 30
    assert kw.get(kw.a, book)["CurrentBookmark"]["ProgressPercent"] == 30


# == 10. "Recently read" with the switch off ==================================

def test_switch_off_a_kobo_update_ranks_by_when_it_arrived(
        library, monkeypatch):
    """Both books already have reading rows (no first report involved).
    Book 1's Kobo clock is months old; book 3 was just read on the web. A new
    Kobo update for book 1 still carries an old clock, but it is the latest
    reading received, so book 1 goes first: a Kobo ranks by receipt, as web
    saves and KOReader syncs already do."""
    monkeypatch.setenv(GATES_ENV, "0")
    older = datetime(2026, 7, 1, tzinfo=timezone.utc)
    _kobo_progress_put(library, 1, 50, older)
    _web_reader_save(library, 3, percentage=30)
    assert library.ids(user=library.reader) == [3, 1, 2, 4]

    _kobo_progress_put(library, 1, 60, older + timedelta(hours=1))

    assert library.ids(user=library.reader) == [1, 3, 2, 4]


def test_a_far_future_kobo_clock_does_not_pin_a_book_to_the_top(
        library, monkeypatch):
    """A Kobo whose clock reads 2030 reports book 1. Reading received after
    it, on the web (book 3) and through KOReader (book 2), still goes ahead
    of it: a device clock cannot hold a book above everything read later."""
    _kobo_progress_put(library, 1, 40,
                       datetime(2030, 1, 1, tzinfo=timezone.utc))
    _web_reader_save(library, 3, percentage=30)
    _koreader_sync(library, 2, 20.0, monkeypatch)

    assert library.ids(user=library.reader) == [2, 3, 1, 4]
