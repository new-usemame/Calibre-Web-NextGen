# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Acceptance tests for "Share my place" PR 3a: the Kobo write and serve gates,
sync suppression and the turn-on nudge.

Spec: state/kindle-kids/specs/2026-10-11-share-my-place.md, section 11 (3a
only; the exact-span work of 3b is not tested here), with section 2, section 3
criteria 2-7, section 8 and section 9. Read as a spec:

* A Kobo is *resolved* when ``requires_kobo_auth`` set
  ``g.annotation_origin_device_id``. An unresolved Kobo keeps today's
  behaviour exactly on PUT /state, GET /state and library/sync.
* A resolved Kobo whose Share my place box is OFF ("unchecked"):
  - PUT /state writes only its own ``DeviceReadingPosition`` row (place and
    clock through the arbiter; StatusInfo into ``reading_status`` /
    ``status_modified_at``). No ReadBook, KoboReadingState, KoboBookmark or
    KoboStatistics row is created or changed, no kosync row, no custom read
    column. Hardcover still hears about the reading, as the account's.
  - GET /state is rendered from that row, echoing its own LastModified and
    status. With no row it is a 200 with the normal list shape rendered from a
    transient (never persisted) reading state: no ``CurrentBookmark`` and every
    clock at the Unix epoch, so Nickel's local state always wins.
  - library/sync carries no ReadingState for it at any emission site
    (entitlement-embedded, standalone, rehydrate latch, confirmation echo),
    while its reading-state frontier in the sync token still advances.
* Turning the box ON: before anything is emitted, the device's own journal row
  goes through the arbiter against the shared place. Shared wins: the next
  sync emits it, once. Own wins: nothing is emitted. Other Kobos see nothing
  extra. Turning it OFF rewinds nothing.
* Checked Kobos share one place by the arbiter: latest wins (a backward move
  propagates), an older report arriving later loses, and a client clock far in
  the future is not trusted over the server's receipt time.
* Rollout switch (section 12): all of the above is enforced only when
  ``share_place.gates_enforced(user_id)`` is true, which the env
  ``CWNG_SHARE_PLACE_GATES`` turns on (``1`` for every account,
  ``user:3,107`` for those account ids). Off, an unchecked resolved Kobo gets
  today's behaviour. An autouse fixture here turns it on for every account.
* A checked Kobo whose clock runs a day fast does not hold the shared place
  against an honest report received later (its clock is not its receipt).
* An unidentified KOReader push still reaches the Kobo bookmark as a
  forward-only percentage (the checked, identified KOReader bridge waits on
  PR 2 and lives in ``test_share_place_kobo_koreader_bridge.py``).

The Kobo routes run through the real ``HandleStateRequest`` / ``HandleSyncRequest``
bodies (``__wrapped__``, past ``requires_kobo_auth``) over an in-memory app.db
with metadata.db attached; the toggle goes through the real devices API route.
Sync tokens are handed back exactly as a Kobo does, so pending-page
acknowledgement happens inside the handler. Hardcover is stubbed at
``kobo.push_reading_state_to_hardcover``.
"""

import time
from datetime import datetime, timedelta, timezone

import pytest

from tests.unit.kobo_state_world import (  # noqa: F401  (fixtures)
    EMPTY, EPOCH, USER_ID, _kts, _naive, _now, _states, _timestamps, kw,
    read_column_class,
)

pytestmark = pytest.mark.unit

GATES_ENV = "CWNG_SHARE_PLACE_GATES"


@pytest.fixture(autouse=True)
def gates_enforced(monkeypatch):
    """Spec section 12: the gates enforce only when switched on. These tests
    pin the enforced behaviour, so every test here starts with the switch on
    for every account; switch-off tests unset it themselves."""
    monkeypatch.setenv(GATES_ENV, "1")


# == 0. The rollout switch (spec section 12) =================================

def test_switch_off_an_unchecked_kobo_gets_todays_put_get_and_sync(kw, monkeypatch):
    monkeypatch.delenv(GATES_ENV, raising=False)
    one, two = kw.books[0], kw.books[1]
    kw.seed_shared(one, 20.0, _now() - timedelta(hours=2))
    kw.seed_shared(two, 45.0, _now() - timedelta(hours=2))
    kw.settle(kw.a)

    kw.put(kw.a, one, 60.0, _now() - timedelta(minutes=10), status="Finished")

    shared = kw.shared(one)
    assert shared["bookmark"][0] == 60.0
    assert shared["read"][0] == kw.ub.ReadBook.STATUS_FINISHED
    assert kw.counts()["kosync_progress"] == 1
    assert kw.get(kw.a, one)["CurrentBookmark"]["ProgressPercent"] == 60
    # No journal row for book 2: today's GET serves the shared place.
    assert kw.get(kw.a, two)["CurrentBookmark"]["ProgressPercent"] == 45
    delivered = _states(kw.sync(kw.a))
    assert [s["CurrentBookmark"]["ProgressPercent"] for s in delivered[one.uuid]] \
        == [60]


@pytest.mark.parametrize("setting, gated", [
    ("user:3,107", False),
    (f"user:{USER_ID}", True),
    (f"user:3,{USER_ID}", True),
], ids=["other-accounts", "this-account", "list-with-this-account"])
def test_switch_allow_list_gates_only_the_listed_accounts(
        kw, monkeypatch, setting, gated):
    monkeypatch.setenv(GATES_ENV, setting)
    book = kw.books[0]
    kw.seed_shared(book, 20.0, _now() - timedelta(hours=2))
    before = kw.shared(book)

    kw.put(kw.a, book, 60.0, _now() - timedelta(minutes=10))

    if gated:
        assert kw.shared(book) == before
        assert kw.journal(kw.a, book).progress_percent == 60.0
    else:
        assert kw.shared(book)["bookmark"][0] == 60.0


# == 1. Unresolved Kobo: today's behaviour exactly ============================

def test_unresolved_kobo_put_writes_the_shared_rows_as_today(kw):
    """No device id: the shared graph, the KOReader carrier and Hardcover are
    all written, even though every Kobo on the account is unchecked."""
    book = kw.books[0]
    kw.seed_shared(book, 20.0, _now() - timedelta(hours=2))
    clock = _now() - timedelta(minutes=30)

    kw.put(None, book, 40.0, clock, status="Finished", spent=9)

    shared = kw.shared(book)
    assert shared["bookmark"][0] == 40.0
    assert shared["bookmark"][2] == "kobo.40.1"
    assert shared["read"][0] == kw.ub.ReadBook.STATUS_FINISHED
    assert shared["statistics"][0] == 9
    assert kw.counts()["kosync_progress"] == 1
    assert len(kw.read_column(book)) == 1
    assert kw.hardcover == [(book.id, 40.0)]


def test_unresolved_kobo_get_and_sync_serve_the_shared_place(kw):
    book = kw.books[0]
    at = _now() - timedelta(minutes=30)
    kw.seed_shared(book, 55.0, at)

    served = kw.get(None, book)
    assert served["CurrentBookmark"]["ProgressPercent"] == 55
    assert served["LastModified"] == _kts(at)

    kw.settle(None)
    kw.put(None, book, 65.0, _now() - timedelta(minutes=10))
    delivered = _states(kw.sync(None))
    assert [s["CurrentBookmark"]["ProgressPercent"] for s in delivered[book.uuid]] \
        == [65]


# == 2. Unchecked Kobo PUT /state: its own journal only ======================

def test_unchecked_put_with_no_shared_rows_creates_none(kw):
    book = kw.books[0]
    clock = _now() - timedelta(minutes=30)

    kw.put(kw.a, book, 40.0, clock, status="Reading")

    assert kw.shared(book) == EMPTY
    assert kw.counts() == {"ReadBook": 0, "KoboReadingState": 0, "KoboBookmark": 0,
                           "KoboStatistics": 0, "kosync_progress": 0}
    row = kw.journal(kw.a, book)
    assert row is not None
    assert row.progress_percent == 40.0
    assert row.content_source_progress_percent == 40.0
    assert (row.location_value, row.location_type, row.location_source) == (
        "kobo.40.1", "KoboSpan", "OEBPS/chap.xhtml")
    assert _naive(row.client_modified_at) == clock
    assert row.reading_status == kw.ub.ReadBook.STATUS_IN_PROGRESS
    assert _naive(row.status_modified_at) == clock


def test_unchecked_put_leaves_existing_shared_rows_untouched(kw):
    """A newer report, a finish and new statistics from the unchecked Kobo
    change nothing the account shares: not the bookmark, the reading-state
    clocks, ReadBook, the statistics row, the KOReader carrier or the
    configured read column."""
    book = kw.books[0]
    kw.seed_shared(book, 50.0, _now() - timedelta(hours=2))
    before = kw.shared(book)
    rows_before = kw.counts()
    clock = _now() - timedelta(minutes=5)

    kw.put(kw.a, book, 99.0, clock, status="Finished", spent=60)

    assert kw.shared(book) == before
    assert kw.counts() == rows_before
    assert kw.read_column(book) == []
    row = kw.journal(kw.a, book)
    assert row.progress_percent == 99.0
    assert row.reading_status == kw.ub.ReadBook.STATUS_FINISHED
    assert _naive(row.status_modified_at) == clock


def test_unchecked_put_statistics_never_reach_the_shared_statistics_row(kw):
    """Spec section 11 review note 3: only this is pinned. Whether the
    unchecked Kobo's statistics are journalled or dropped is the
    implementer's documented choice."""
    book = kw.books[0]
    kw.seed_shared(book, 50.0, _now() - timedelta(hours=2))
    before = kw.shared(book)["statistics"]

    kw.put(kw.a, book, 50.0, _now() - timedelta(minutes=5), spent=93)

    assert kw.shared(book)["statistics"] == before


def test_unchecked_put_still_reaches_hardcover_as_the_accounts_reading(kw):
    book = kw.books[0]
    kw.seed_shared(book, 80.0, _now() - timedelta(hours=2))

    kw.put(kw.a, book, 35.0, _now() - timedelta(minutes=5))

    assert kw.hardcover == [(book.id, 35.0)]


def test_unchecked_journal_is_latest_wins_and_an_older_report_loses(kw):
    """The device's own row goes through the arbiter: a deliberate backward
    move with a newer clock is kept; a re-sent older report is not."""
    book = kw.books[0]
    now = _now()
    kw.put(kw.a, book, 60.0, now - timedelta(minutes=30), status="Finished")

    kw.put(kw.a, book, 20.0, now - timedelta(minutes=60), status="Reading")
    row = kw.journal(kw.a, book)
    assert row.progress_percent == 60.0
    assert row.location_value == "kobo.60.1"
    assert _naive(row.client_modified_at) == now - timedelta(minutes=30)
    assert row.reading_status == kw.ub.ReadBook.STATUS_FINISHED

    kw.put(kw.a, book, 25.0, now - timedelta(minutes=10), status="Reading")
    row = kw.journal(kw.a, book)
    assert row.progress_percent == 25.0
    assert _naive(row.client_modified_at) == now - timedelta(minutes=10)
    assert row.reading_status == kw.ub.ReadBook.STATUS_IN_PROGRESS


# == 3. Unchecked Kobo GET /state ============================================

@pytest.mark.parametrize("shared_exists", [True, False],
                         ids=["shared-row-newer", "no-shared-row"])
def test_unchecked_get_is_rendered_from_its_own_journal_row(kw, shared_exists):
    book = kw.books[0]
    own_clock = _now() - timedelta(minutes=60)
    if shared_exists:
        kw.seed_shared(book, 80.0, _now() - timedelta(minutes=5),
                       status=kw.ub.ReadBook.STATUS_FINISHED)
    kw.put(kw.a, book, 30.0, own_clock, status="Reading")
    before = kw.shared(book)

    served = kw.get(kw.a, book)

    assert served["LastModified"] == _kts(own_clock)
    assert served["CurrentBookmark"]["LastModified"] == _kts(own_clock)
    assert served["CurrentBookmark"]["ProgressPercent"] == 30
    assert served["CurrentBookmark"]["Location"] == {
        "Value": "kobo.30.1", "Type": "KoboSpan", "Source": "OEBPS/chap.xhtml"}
    assert served["StatusInfo"]["Status"] == "Reading"
    assert kw.shared(book) == before  # GET creates and changes nothing shared


@pytest.mark.parametrize("shared_exists", [True, False],
                         ids=["shared-row-exists", "no-shared-row"])
def test_unchecked_get_with_no_journal_row_serves_nothing_newer_than_the_device(
        kw, shared_exists):
    """Pinned shape (CWNG review of section 11): a 200 with the normal
    one-item list, rendered from a transient reading state that is never
    persisted; no CurrentBookmark; every clock at the Unix epoch, so whatever
    Nickel holds locally wins. Not an empty list, not a 404."""
    book = kw.books[0]
    if shared_exists:
        kw.seed_shared(book, 80.0, _now() - timedelta(minutes=5))
    before = kw.shared(book)
    rows_before = kw.counts()

    served = kw.get(kw.a, book)

    assert "CurrentBookmark" not in served
    assert served["LastModified"] == EPOCH
    if "PriorityTimestamp" in served:
        assert served["PriorityTimestamp"] == EPOCH
    newer = [(path, value) for path, value in _timestamps(served)
             if value != EPOCH]
    assert newer == [], "nothing newer than the epoch may be served"
    assert kw.shared(book) == before
    assert kw.counts() == rows_before
    assert kw.journal(kw.a, book) is None


# == 4. Two Kobos: A unchecked, B checked ====================================

def test_unchecked_readers_place_never_reaches_a_checked_kobo(kw):
    book = kw.books[0]
    shared_at = _now() - timedelta(hours=2)
    kw.seed_shared(book, 50.0, shared_at)
    kw.share(kw.b, True)
    kw.settle(kw.a)
    kw.settle(kw.b)

    kw.put(kw.a, book, 70.0, _now() - timedelta(minutes=5))

    assert _states(kw.sync(kw.b)) == {}
    served = kw.get(kw.b, book)
    assert served["CurrentBookmark"]["ProgressPercent"] == 50
    assert served["LastModified"] == _kts(shared_at)


def test_checked_readers_place_never_reaches_an_unchecked_kobo_but_its_frontier_advances(
        kw):
    """Standalone emission site: B's newer shared state is not sent to A, and
    A's token still moves past it, so nothing is queued for A."""
    book = kw.books[0]
    kw.seed_shared(book, 50.0, _now() - timedelta(hours=2))
    kw.share(kw.b, True)
    kw.settle(kw.a)
    kw.settle(kw.b)
    before = kw.frontier(kw.a)

    b_clock = _now() - timedelta(minutes=5)
    kw.put(kw.b, book, 70.0, b_clock)
    assert kw.shared(book)["bookmark"][0] == 70.0  # B is checked: shared moved

    response = kw.sync(kw.a)

    assert _states(response) == {}
    after = kw.frontier(kw.a)
    assert after > before
    assert after >= kw.shared(book)["state"][0]
    assert _states(kw.sync(kw.a)) == {}


def test_an_unchecked_kobos_new_entitlements_embed_no_shared_state(kw):
    """Entitlement-embedded emission site: A's first sync offers every book
    New and embeds no ReadingState; B (checked) gets the shared one embedded."""
    book = kw.books[0]
    kw.seed_shared(book, 50.0, _now() - timedelta(hours=2))
    kw.share(kw.b, True)

    first_a = kw.sync(kw.a)
    assert sum("NewEntitlement" in item for item in first_a.get_json()) == 3
    assert _states(first_a) == {}
    assert kw.frontier(kw.a) >= kw.shared(book)["state"][0]

    first_b = kw.sync(kw.b)
    assert [s["CurrentBookmark"]["ProgressPercent"]
            for s in _states(first_b)[book.uuid]] == [50]


def test_an_armed_rehydrate_latch_emits_no_shared_state_to_an_unchecked_kobo(kw):
    """Rehydrate-latch emission site."""
    book = kw.books[0]
    kw.seed_shared(book, 50.0, _now() - timedelta(hours=2))
    kw.settle(kw.a)
    from cps.services import device_reading_position as positions
    positions.mark_rehydrate_needed(kw.a.id, [book.id])
    kw.session.commit()

    assert _states(kw.sync(kw.a)) == {}
    assert _states(kw.sync(kw.a)) == {}


def test_a_confirmation_echo_emits_no_shared_state_once_the_kobo_is_unchecked(kw):
    """Confirmation-echo emission site. A checked Kobo is sent a rehydrate
    repair; the owner turns it off before the Kobo acknowledges that page.
    The acknowledging sync must not echo the shared state to it."""
    book = kw.books[0]
    kw.seed_shared(book, 50.0, _now() - timedelta(hours=2))
    kw.share(kw.a, True)
    kw.settle(kw.a)
    from cps.services import device_reading_position as positions
    positions.mark_rehydrate_needed(kw.a.id, [book.id])
    kw.session.commit()
    assert book.uuid in _states(kw.sync(kw.a))  # checked: the repair goes out

    kw.share(kw.a, False)
    acknowledging = kw.sync(kw.a)  # presents that page's token: acknowledged

    assert _states(acknowledging) == {}


# == 5. Turning the box on, and off ==========================================

def test_turning_on_emits_the_shared_place_once_only_where_it_is_newer(kw):
    """Book 1: shared newer than A's own row -> emitted. Book 2: A's own row
    newer -> not emitted (no older shared place pushed onto a Kobo that read
    further while unchecked). Book 3: A never reported it -> the shared place
    is the only report, so it is emitted. Nothing is emitted twice and B sees
    nothing from A's toggle."""
    one, two, three = kw.books
    now = _now()
    kw.share(kw.b, True)
    kw.seed_shared(one, 70.0, now - timedelta(minutes=30))
    kw.seed_shared(two, 30.0, now - timedelta(minutes=90))
    kw.seed_shared(three, 45.0, now - timedelta(minutes=40))
    kw.put(kw.a, one, 20.0, now - timedelta(minutes=90))
    kw.put(kw.a, two, 85.0, now - timedelta(minutes=30))
    kw.settle(kw.a)
    kw.settle(kw.b)
    shared_two = kw.shared(two)

    kw.share(kw.a, True)
    delivered = _states(kw.sync(kw.a))

    assert set(delivered) == {one.uuid, three.uuid}
    assert [s["CurrentBookmark"]["ProgressPercent"] for s in delivered[one.uuid]] \
        == [70]
    assert delivered[one.uuid][0]["CurrentBookmark"]["Location"]["Value"] \
        == "shared.70"
    assert [s["CurrentBookmark"]["ProgressPercent"] for s in delivered[three.uuid]] \
        == [45]
    assert kw.shared(two) == shared_two  # A's newer own place did not leak out

    assert _states(kw.sync(kw.a)) == {}
    assert _states(kw.sync(kw.b)) == {}


def test_turning_on_with_its_own_place_newer_emits_nothing(kw):
    book = kw.books[0]
    now = _now()
    kw.seed_shared(book, 60.0, now - timedelta(minutes=90))
    kw.put(kw.a, book, 15.0, now - timedelta(minutes=30))  # went back, unchecked
    kw.settle(kw.a)

    kw.share(kw.a, True)

    response = kw.sync(kw.a)
    assert book.uuid not in _states(response)
    assert _states(kw.sync(kw.a)) == {}


def test_turning_on_does_not_re_emit_to_other_kobos(kw):
    book = kw.books[0]
    kw.share(kw.b, True)
    kw.seed_shared(book, 70.0, _now() - timedelta(minutes=30))
    kw.settle(kw.a)
    kw.settle(kw.b)
    shared_before = kw.shared(book)

    kw.share(kw.a, True)

    assert _states(kw.sync(kw.b)) == {}
    assert kw.shared(book) == shared_before


def test_turning_off_rewinds_nothing(kw):
    """After A received the shared place and is turned off: the shared rows
    stay, B is sent nothing, A is sent nothing (not its older own place), and
    A's GET serves nothing newer than the place it was given."""
    book = kw.books[0]
    now = _now()
    kw.seed_shared(book, 70.0, now - timedelta(minutes=30))
    kw.put(kw.a, book, 20.0, now - timedelta(minutes=90))
    kw.share(kw.b, True)
    kw.settle(kw.a)
    kw.settle(kw.b)
    kw.share(kw.a, True)
    turned_on = _states(kw.sync(kw.a))
    assert book.uuid in turned_on, "turn-on must first deliver the shared place"
    given = turned_on[book.uuid][0]
    assert given["CurrentBookmark"]["ProgressPercent"] == 70
    kw.settle(kw.a)
    shared_before = kw.shared(book)

    kw.share(kw.a, False)

    assert _states(kw.sync(kw.a)) == {}
    assert _states(kw.sync(kw.b)) == {}
    assert kw.shared(book) == shared_before
    served = kw.get(kw.a, book)
    assert served["LastModified"] <= given["LastModified"]


# == 6. Two checked Kobos: one place, latest wins ============================

@pytest.fixture
def both_checked(kw):
    """Both Kobos checked; book 1 already has a shared place (10% two hours
    ago), as any book opened before has."""
    kw.seed_shared(kw.books[0], 10.0, _now() - timedelta(hours=2))
    kw.share(kw.a, True)
    kw.share(kw.b, True)
    kw.settle(kw.a)
    kw.settle(kw.b)
    return kw


def test_checked_backward_move_propagates(both_checked):
    kw = both_checked
    book = kw.books[0]
    now = _now()
    kw.put(kw.a, book, 70.0, now - timedelta(minutes=60))
    kw.settle(kw.a)
    kw.put(kw.b, book, 30.0, now - timedelta(minutes=30))  # back a chapter

    delivered = _states(kw.sync(kw.a))

    assert [s["CurrentBookmark"]["ProgressPercent"] for s in delivered[book.uuid]] \
        == [30]
    assert kw.get(kw.a, book)["CurrentBookmark"]["ProgressPercent"] == 30


def test_checked_stale_echo_loses(both_checked):
    kw = both_checked
    book = kw.books[0]
    now = _now()
    kw.put(kw.a, book, 70.0, now - timedelta(minutes=30))
    kw.put(kw.b, book, 30.0, now - timedelta(minutes=60))  # older, arrives later

    assert kw.shared(book)["bookmark"][0] == 70.0
    assert kw.get(kw.b, book)["CurrentBookmark"]["ProgressPercent"] == 70


def test_a_far_future_client_clock_does_not_pin_the_shared_place(both_checked):
    """Section 3 criterion 3 (clock skew): a client clock more than five
    minutes ahead is replaced by the server's receipt time, so a later honest
    report still wins."""
    kw = both_checked
    book = kw.books[0]
    kw.put(kw.a, book, 70.0, _now() + timedelta(days=1))
    kw.put(kw.b, book, 30.0, _now() + timedelta(minutes=2))

    assert kw.shared(book)["bookmark"][0] == 30.0
    assert kw.get(kw.a, book)["CurrentBookmark"]["ProgressPercent"] == 30


def test_an_honest_later_report_beats_a_day_fast_clock(both_checked):
    """Section 12: the shared row's receipt time is a real receipt. A's report
    with a clock a day fast is ordered by when it was received, so B's honest
    report received after it wins."""
    kw = both_checked
    book = kw.books[0]
    kw.put(kw.a, book, 70.0, _now() + timedelta(days=1))
    assert kw.shared(book)["bookmark"][0] == 70.0
    time.sleep(1.1)  # B's whole-second clock is after A's receipt

    kw.put(kw.b, book, 30.0, _now())

    assert kw.shared(book)["bookmark"][0] == 30.0
    assert kw.get(kw.a, book)["CurrentBookmark"]["ProgressPercent"] == 30


# == 7. Unidentified KOReader push, then Kobo: forward-only as today =========

@pytest.fixture
def kosync_world(monkeypatch, tmp_path):
    from cps import kobo, ub
    from tests.unit.koreader_library_world import LibraryWorld

    world = LibraryWorld(monkeypatch, tmp_path)
    world.reader = world.add_user("reader")
    world.add_book(1, "First Light")
    monkeypatch.setattr(world.kosync, "push_reading_state_to_hardcover",
                        lambda *_a: None)
    monkeypatch.setattr(kobo, "push_reading_state_to_hardcover", lambda *_a: None)
    # A checked Kobo on the account: unidentified writers keep today's rules
    # regardless of anyone's box.
    checked = ub.Device(
        user_id=world.reader.id, kind="kobo", display_name="Kobo Checked",
        active=True, created_by="auto", share_place=True,
        share_place_enabled_at=datetime.now(timezone.utc))
    world.session.add(checked)
    world.session.commit()
    yield world
    world.close()


def _stock_koreader_push(world, percent):
    from tests.unit.koreader_library_world import LibraryWorld
    headers = LibraryWorld.basic("reader")
    headers["Accept"] = "application/vnd.koreader.v1+json"
    response = world.client.put("/kosync/syncs/progress", json={
        "document": "0123456789abcdef0123456789abcdef",
        "progress": "/body/DocFragment[2]/body/p[1]/text().0",
        "percentage": percent / 100.0, "device": "Stock KOReader",
    }, headers=headers)
    assert response.status_code == 200, response.get_data(as_text=True)
    world.session.expire_all()


def _kobo_bookmark_percent(world):
    from cps import ub
    world.session.expire_all()
    state = world.session.query(ub.KoboReadingState).filter_by(
        user_id=world.reader.id, book_id=1).one()
    return state.current_bookmark.progress_percent


def test_unidentified_koreader_push_reaches_the_kobo_bookmark_forward_only(
        kosync_world):
    from cps.progress_syncing.models import BookFormatChecksum
    world = kosync_world
    world.session.add(BookFormatChecksum(
        book=1, format="EPUB", checksum="0123456789abcdef0123456789abcdef"))
    world.session.commit()
    world.position(world.reader, 1, 50.0,
                   at=datetime.now(timezone.utc) - timedelta(hours=1))

    _stock_koreader_push(world, 30)
    assert _kobo_bookmark_percent(world) == 50.0

    _stock_koreader_push(world, 80)
    assert _kobo_bookmark_percent(world) == pytest.approx(80.0)
