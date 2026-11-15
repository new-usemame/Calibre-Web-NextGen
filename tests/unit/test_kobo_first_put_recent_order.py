# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
""""Recently read" order around the first-PUT clock fix.

Spec: state/kindle-kids/specs/2026-10-11-share-my-place.md, section 12
("First-Kobo-PUT stamp bug"), with the decided Recent-sort rule: a Kobo
position's activity is the later of its device clock and its server receipt
(``max(last_modified, COALESCE(server_modified_at, last_modified))``). The
upgrade backfills each legacy receipt as ``min(clock, upgrade time)``, so on
rows that existed before the upgrade the order is exactly today's.

* Across the upgrade: an existing library's Recent order is the same before
  and after the receipt column is added and backfilled, including a legacy
  Kobo clock a little in the future.
* A Kobo whose clock runs a few seconds slow making the first report for a
  book: that book is the newest activity (its receipt is now), above a
  KOReader sync received just before it; and a first report of Finished
  lands in ReadBook.

Runs with the Share my place switch off: this fix is not behind it.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from cps import db, sort_orders, ub
from tests.unit.kobo_state_world import (  # noqa: F401  (fixtures)
    USER_ID, _now, kw, read_column_class,
)
from tests.unit.test_library_recent_sort import (  # noqa: F401  (fixture)
    _koreader_sync, _kobo_progress_put, _web_reader_save, library,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def gates_off(monkeypatch):
    monkeypatch.setenv("CWNG_SHARE_PLACE_GATES", "0")


# == Order across the upgrade =================================================

def test_an_existing_librarys_recent_order_is_unchanged_by_the_upgrade(
        library, monkeypatch):
    """Book 1: Kobo, its clock three minutes in the future. Book 4: Kobo,
    months ago. Book 2: KOReader. Book 3: web reader, last. Then the rows
    are made pre-upgrade: positions created long ago, no receipts."""
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    _kobo_progress_put(library, 1, 50.0,
                       datetime(2026, 6, 1, tzinfo=timezone.utc))
    _kobo_progress_put(library, 4, 20.0,
                       datetime(2026, 6, 1, tzinfo=timezone.utc))
    _koreader_sync(library, 2, 10.0, monkeypatch)
    _web_reader_save(library, 3, percentage=30.0)
    clocks = {1: now + timedelta(minutes=3), 4: datetime(2026, 7, 1, 9, 0)}
    with library.engine.begin() as conn:
        for book_id, clock in clocks.items():
            conn.execute(text(
                "UPDATE kobo_bookmark SET last_modified = :clock "
                "WHERE kobo_reading_state_id IN (SELECT id FROM "
                "kobo_reading_state WHERE book_id = :book)"),
                {"clock": clock, "book": book_id})
        conn.execute(text("UPDATE kobo_bookmark SET "
                          "created_at = '2026-05-01 00:00:00.000000', "
                          "server_modified_at = NULL"))
    library.session.expire_all()
    before = library.ids(user=library.reader)
    assert before == [1, 3, 2, 4]

    ub.migrate_kobo_bookmark_receipt(library.engine, None)
    library.session.expire_all()
    receipts = library.session.query(ub.KoboBookmark.server_modified_at).all()
    assert all(receipt is not None for (receipt,) in receipts)

    assert library.ids(user=library.reader) == before


# == Slow-clock first PUT =====================================================

def _recent_ids(kw):
    kw.session.expire_all()
    order = sort_orders.book_sort_order("recent", user_id=USER_ID)
    return [row[0] for row in kw.session.query(db.Books.id).order_by(*order)]


def _koreader_mirror(kw, book, percent, monkeypatch):
    import sys
    import cps.progress_syncing.protocols.kosync  # noqa: F401
    kosync = sys.modules["cps.progress_syncing.protocols.kosync"]
    monkeypatch.setattr(kosync, "push_reading_state_to_hardcover",
                        lambda *_a, **_k: None, raising=False)
    kosync.update_book_read_status(
        kw.session.get(ub.User, USER_ID), book.id, percent)
    kw.session.commit()


def test_a_slow_kobos_first_report_is_the_newest_activity(kw, monkeypatch):
    """KOReader syncs book 2; a moment later a Kobo whose clock runs five
    seconds slow makes the first report for book 1. Book 1 was read last."""
    one, two, _ = kw.books
    _koreader_mirror(kw, two, 10.0, monkeypatch)

    kw.put(kw.a, one, 30.0, _now() - timedelta(seconds=5))

    assert _recent_ids(kw)[:2] == [one.id, two.id]


def test_a_slow_kobos_first_report_of_finished_lands_in_readbook(kw):
    book = kw.books[0]

    kw.put(kw.a, book, 100.0, _now() - timedelta(seconds=5), status="Finished")

    assert kw.shared(book)["read"][0] == ub.ReadBook.STATUS_FINISHED
