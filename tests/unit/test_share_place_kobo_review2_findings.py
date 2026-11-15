# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Acceptance tests for findings 1, 2, 3 and 5 of the second adversarial
review of "Share my place" PR 3a
(state/kindle-kids/legs/share-place-pr3/REVIEW-sol-2.md, reviewed at
83ab28709). Finding 4 (a pre-AUTOINCREMENT nudge table) is out of scope: that
table only existed in an unshipped build.

Spec: state/kindle-kids/specs/2026-10-11-share-my-place.md, sections 3, 11
and 12. Each section pins an outcome, not a fix:

1. A checked KOReader report that wins in the same client second as the
   Kobo report a Kobo already holds (cursor T), arriving through the real
   KOSync HTTP PUT, leaves the shared reading state persisted past T, so the
   Kobo's next sync selects it.
2. An unchecked Kobo's journal keeps place and status as separate
   observations: a newer status-only Finished survives a delayed report
   dated earlier than it, while that report's place, newer than the previous
   place, still replaces it.
3. With legacy future-dated shared status and statistics, a stale checked
   report (older than the capped receipt, so its place loses) changes
   neither status nor statistics.
5. A checked device whose own report is newer than the shared place is
   served its own place, not the older shared one, both by a retained-page
   replay and by GET /state.
"""

from datetime import timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from tests.unit.kobo_state_world import (  # noqa: F401  (fixtures)
    _kts, _now, _states, kw, read_column_class,
)
from tests.unit.test_share_place_kobo_koreader_bridge import (  # noqa: F401
    BOOK, _koreader, _kobo, _push, world,
)
from tests.unit.test_share_place_kobo_review_findings import (
    _finish_only, _raw_put,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def gates_enforced(monkeypatch):
    monkeypatch.setenv("CWNG_SHARE_PLACE_GATES", "1")


# == 1. KOSync same-second winner past an issued Kobo cursor ==================

@pytest.mark.parametrize("skew", ["fast-within-allowance", "legacy-day-fast"])
def test_a_kosync_winner_is_persisted_past_the_kobos_issued_cursor(world, skew):
    """The checked Kobo's report is at T and a Kobo already holds cursor T.
    ``fast-within-allowance``: T is two minutes ahead (trusted), and KOReader
    reports in the same client second with a later receipt.
    ``legacy-day-fast``: T is a day ahead with its receipt capped by the
    migration, and KOReader reports honestly now."""
    from cps import ub
    _koreader(world, "kindle-a", "Kindle A", checked=True)
    now = _now()
    if skew == "fast-within-allowance":
        cursor, receipt, client = (now + timedelta(minutes=2),
                                   now - timedelta(seconds=2),
                                   now + timedelta(minutes=2))
    else:
        cursor, receipt, client = (now + timedelta(days=1),
                                   now - timedelta(minutes=2), now)
    state = world.session.query(ub.KoboReadingState).filter_by(
        user_id=world.reader.id, book_id=BOOK).one()
    bookmark = ub.KoboBookmark.__table__
    world.session.execute(bookmark.update()
                          .where(bookmark.c.id == state.current_bookmark.id)
                          .values(last_modified=cursor, server_modified_at=receipt))
    state.last_modified = cursor
    state.priority_timestamp = cursor
    world.session.commit()

    _push(world, 30, raw_id="kindle-a", name="Kindle A",
          client_at=client.replace(tzinfo=timezone.utc).timestamp())

    assert _kobo(world).percent == pytest.approx(30.0)
    world.session.close()
    committed = sessionmaker(bind=world.engine)()  # sees only what was committed
    try:
        stored = committed.query(ub.KoboReadingState.last_modified).filter_by(
            user_id=world.reader.id, book_id=BOOK).scalar()
        # The Kobo's next sync selects reading states strictly past its cursor.
        selected = [row.book_id for row in committed.query(
            ub.KoboReadingState.book_id)
            .filter(ub.KoboReadingState.user_id == world.reader.id)
            .filter(ub.KoboReadingState.last_modified > cursor)]
    finally:
        committed.close()
    if stored.tzinfo is not None:
        stored = stored.astimezone(timezone.utc).replace(tzinfo=None)
    assert stored > cursor
    assert selected == [BOOK]


# == 2. Unchecked journal: place and status are separate observations ========

@pytest.fixture
def finished_then_delayed_place(kw):
    """40% Reading at T-60, status-only Finished at T-10, then a delayed
    30% Reading report dated T-30 arrives."""
    book = kw.books[0]
    now = _now()
    kw.put(kw.a, book, 40, now - timedelta(minutes=60))
    _raw_put(kw, kw.a, book, _finish_only(now - timedelta(minutes=10)))
    assert kw.get(kw.a, book)["StatusInfo"]["Status"] == "Finished"
    kw.put(kw.a, book, 30, now - timedelta(minutes=30), status="Reading")
    return book, now


def test_a_newer_status_only_finish_survives_an_older_dated_place_report(
        kw, finished_then_delayed_place):
    book, now = finished_then_delayed_place
    served = kw.get(kw.a, book)
    assert served["StatusInfo"]["Status"] == "Finished"
    assert served["StatusInfo"]["LastModified"] == _kts(now - timedelta(minutes=10))


def test_a_place_newer_than_the_previous_place_still_updates_it(
        kw, finished_then_delayed_place):
    book, _ = finished_then_delayed_place
    served = kw.get(kw.a, book)
    assert served["CurrentBookmark"]["ProgressPercent"] == 30
    assert served["CurrentBookmark"]["Location"]["Value"] == "kobo.30.1"


# == 3. Legacy future status and statistics vs a stale checked report ========

def test_a_stale_checked_report_changes_no_legacy_future_status_or_statistics(kw):
    book = kw.books[0]
    now = _now()
    kw.seed_shared(book, 70, now + timedelta(days=1),
                   status=kw.ub.ReadBook.STATUS_FINISHED)
    kw.session.execute(kw.ub.KoboBookmark.__table__.update().values(
        last_modified=now + timedelta(days=1),
        server_modified_at=now - timedelta(minutes=2)))
    kw.session.commit()
    kw.share(kw.a, True)
    before = kw.shared(book)

    kw.put(kw.a, book, 30, now - timedelta(hours=2), status="Reading")

    after = kw.shared(book)
    assert after["bookmark"][0] == 70  # the stale place lost
    assert after["read"] == before["read"]
    assert after["statistics"] == before["statistics"]


# == 5. Checked own-newer: retained replay and GET ============================

def test_a_checked_replay_serves_no_shared_place_older_than_its_own(kw):
    """A nudge page with shared 70% (T-60) is outstanding; the box goes off,
    the Kobo reports 15% at T-30, the box goes on, and the Kobo retries the
    outstanding page."""
    book = kw.books[0]
    now = _now()
    kw.seed_shared(book, 70, now - timedelta(minutes=60))
    kw.settle(kw.a)
    kw.share(kw.a, True)
    outstanding = kw.tokens[kw.a.id]
    assert book.uuid in _states(kw.sync(kw.a))
    kw.share(kw.a, False)
    kw.put(kw.a, book, 15, now - timedelta(minutes=30))
    kw.share(kw.a, True)
    kw.tokens[kw.a.id] = outstanding

    retry = kw.sync(kw.a)

    assert book.uuid not in _states(retry)


def test_a_checked_get_serves_its_own_newer_place(kw):
    book = kw.books[0]
    now = _now()
    kw.seed_shared(book, 70, now - timedelta(minutes=60))
    kw.put(kw.a, book, 15, now - timedelta(minutes=30))
    kw.share(kw.a, True)
    assert book.uuid not in _states(kw.sync(kw.a))

    served = kw.get(kw.a, book)

    assert served["CurrentBookmark"]["ProgressPercent"] == 15
    assert served["CurrentBookmark"]["Location"]["Value"] == "kobo.15.1"
