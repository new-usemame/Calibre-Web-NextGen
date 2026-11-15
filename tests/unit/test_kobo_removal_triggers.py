# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""What sends a Kobo a removal, to which Kobo, and when it waits.

Spec: state/kindle-kids/specs/2026-10-12-sync-removal.md, "CWNG review" G2,
G7 and G8, for EVERY Kobo.

* G2: a removal is the requesting Kobo's own; its acknowledgement clears only
  that Kobo's delivery rows. Kobo B is never touched by A's removal and gets
  its own when it syncs.
* G8: a book that leaves what the Kobo should hold (a shelf, the device's own
  scope, hidden, restricted, deleted) is removed, but only on a reliable read
  of that set. An unreliable read never removes (#468).
* G7: while the Kobo's reading activity on the book is newer than its last
  annotation upload, the removal waits (the annotations may still be on the
  device). An upload releases it. After a capped number of syncs the removal
  goes anyway and is logged. The cap is ``cps.kobo.KOBO_REMOVAL_CAPTURE_HOLD_SYNCS``;
  this file sets it, and never pins its default.

Activity and upload are spaced by more than a second: the comparison may be at
whole-second granularity.
"""

import logging
import time
from datetime import datetime, timezone

import pytest

import cps.kobo as kobo_module
from cps import db, ub
from tests.unit.kobo_removal_world import (
    KoboRemovalWorld, TO_READ, delivered, entitlements, removed, utcnow,
)

pytestmark = pytest.mark.unit

CAP = "KOBO_REMOVAL_CAPTURE_HOLD_SYNCS"


@pytest.fixture
def world(monkeypatch):
    worlds = []

    def build(**kwargs):
        built = KoboRemovalWorld(monkeypatch, **kwargs)
        worlds.append(built)
        return built

    yield build
    for built in worlds:
        built.close()


def _both_hold_to_read(w):
    assert delivered(w.settle(w.a)) == set(TO_READ)
    assert delivered(w.settle(w.b)) == set(TO_READ)


def _removed_for_a_only(w, book_id):
    """A gets the removal and acknowledges it; B's rows are byte-unchanged and
    B then gets its own removal."""
    before = w.device_rows(w.b)
    assert removed(w.settle(w.a)) == {book_id}
    assert w.device_rows(w.b) == before
    assert book_id not in w.ledger(w.a)
    assert book_id in w.ledger(w.b)
    assert removed(w.settle(w.b)) == {book_id}


# == G8 + G2: each trigger ===================================================

def test_leaving_the_shelf_removes_from_each_kobo_separately(world):
    w = world(shelf_only=True)
    _both_hold_to_read(w)
    w.unshelve(w.to_read, 3)
    _removed_for_a_only(w, 3)


def test_hiding_a_book_removes_it(world):
    w = world(shelf_only=True)
    _both_hold_to_read(w)
    w.hide(3)
    _removed_for_a_only(w, 3)


def test_restricting_a_book_removes_it(world):
    w = world(shelf_only=True)
    _both_hold_to_read(w)
    w.restrict(3)
    _removed_for_a_only(w, 3)


def test_hiding_removes_on_a_whole_library_account_too(world):
    w = world(shelf_only=False)
    assert removed(w.settle(w.a)) == set()
    w.hide(5)
    assert removed(w.settle(w.a)) == {5}


def test_restricting_removes_on_a_whole_library_account_too(world):
    w = world(shelf_only=False)
    w.settle(w.a)
    w.restrict(5)
    assert removed(w.settle(w.a)) == {5}


def test_leaving_a_devices_own_scope_removes_from_that_device_only(world):
    """Whole-library account. A is scoped to "To Read"; B keeps the account
    setting and loses nothing."""
    w = world(shelf_only=False)
    w.settle(w.a)
    w.settle(w.b)
    before = w.device_rows(w.b)

    assert w.set_scope(w.a, "shelves", [w.to_read]) == 200
    assert removed(w.settle(w.a)) == {5, 6}
    assert w.device_rows(w.b) == before
    assert removed(w.settle(w.b)) == set()


def test_a_deleted_book_is_removed_from_each_kobo_separately(world):
    w = world(shelf_only=True)
    _both_hold_to_read(w)
    book = w.book(4)
    uuid = book.uuid
    w.session.query(db.Data).filter(db.Data.book == 4).delete()
    w.session.query(ub.BookShelf).filter(ub.BookShelf.book_id == 4).delete()
    w.session.delete(book)
    w.session.add(ub.KoboDeletedBook(
        user_id=w.user.id, book_uuid=uuid,
        deleted_at=datetime.now(timezone.utc).replace(tzinfo=None)))
    w.session.commit()

    def removes_deleted(responses):
        return any(
            entry.get("ChangedEntitlement", {}).get("BookEntitlement", {})
            .get("Id") == uuid
            and entry["ChangedEntitlement"]["BookEntitlement"]["IsRemoved"]
            for response in responses for entry in (response.get_json() or []))

    before = w.device_rows(w.b)
    assert removes_deleted(w.settle(w.a))
    assert w.device_rows(w.b) == before
    assert removes_deleted(w.settle(w.b))


# == G8 guards: no removal without a reliable read ===========================

def test_an_unreliable_scope_read_removes_nothing(world, monkeypatch):
    """A magic shelf's rule failed to evaluate: the hidden and unshelved books
    stay on the device until a reliable read."""
    w = world(shelf_only=True)
    monkeypatch.setattr(kobo_module.config, "config_kobo_sync_magic_shelves",
                        True, raising=False)
    monkeypatch.setattr(kobo_module, "get_magic_shelf_book_ids_for_kobo",
                        lambda _user_id: (set(), False))
    monkeypatch.setattr(kobo_module, "get_magic_shelf_membership_added_at",
                        lambda _user_id: None, raising=False)
    _both_hold_to_read(w)
    w.hide(2)
    w.unshelve(w.to_read, 3)

    assert removed(w.settle(w.a)) == set()
    assert w.ledger(w.a) == set(TO_READ)


def test_nothing_left_means_nothing_removed(world):
    w = world(shelf_only=True)
    _both_hold_to_read(w)
    assert removed(w.settle(w.a)) == set()
    assert removed(w.settle(w.b)) == set()


# == G7: hold while the device may still have unsent annotations =============

def _read_now(w, device, book_id, percent=40):
    w.put_state(device, book_id, percent, utcnow())


def test_a_book_read_since_its_last_upload_is_held_and_others_still_go(world):
    w = world(shelf_only=True)
    _both_hold_to_read(w)
    _read_now(w, w.a, 1)
    w.unshelve(w.to_read, 1)
    w.unshelve(w.to_read, 2)

    assert removed(w.settle(w.a)) == {2}
    assert 1 in w.ledger(w.a)


def test_a_held_removal_on_one_kobo_does_not_hold_the_other(world):
    w = world(shelf_only=True)
    _both_hold_to_read(w)
    _read_now(w, w.a, 1)
    w.unshelve(w.to_read, 1)

    assert removed(w.settle(w.a)) == set()
    assert removed(w.settle(w.b)) == {1}
    assert 1 in w.ledger(w.a)


def test_reading_after_the_last_upload_still_holds(world):
    w = world(shelf_only=True)
    _both_hold_to_read(w)
    w.make_authoritative(1)
    w.upload_annotation(w.a, 1, "early")
    time.sleep(1.1)
    _read_now(w, w.a, 1)
    w.unshelve(w.to_read, 1)

    assert removed(w.settle(w.a)) == set()


def test_an_upload_releases_the_hold(world):
    w = world(shelf_only=True)
    _both_hold_to_read(w)
    w.make_authoritative(1)
    _read_now(w, w.a, 1)
    w.unshelve(w.to_read, 1)
    assert removed(w.settle(w.a)) == set()

    time.sleep(1.1)
    w.upload_annotation(w.a, 1, "captured")

    assert removed(w.settle(w.a)) == {1}
    assert 1 not in w.ledger(w.a)


def test_after_the_cap_the_removal_goes_and_is_logged(world, monkeypatch, caplog):
    monkeypatch.setattr(kobo_module, CAP, 2, raising=False)
    w = world(shelf_only=True)
    _both_hold_to_read(w)
    _read_now(w, w.a, 1)
    w.unshelve(w.to_read, 1)

    caplog.set_level(logging.DEBUG, logger="cps.kobo")
    assert entitlements(w.sync(w.a)) == []
    assert entitlements(w.sync(w.a)) == []
    assert not [r for r in caplog.records
                if r.levelno >= logging.WARNING and "book_id=1" in r.getMessage()]

    assert entitlements(w.sync(w.a)) == [("ChangedEntitlement", 1, True)]
    assert [r for r in caplog.records
            if r.levelno == logging.WARNING and "book_id=1" in r.getMessage()]


def test_the_cap_is_read_at_sync_time(world, monkeypatch):
    """A cap of 1: the first sync that would remove holds, the second removes."""
    monkeypatch.setattr(kobo_module, CAP, 1, raising=False)
    w = world(shelf_only=True)
    _both_hold_to_read(w)
    _read_now(w, w.a, 1)
    w.unshelve(w.to_read, 1)

    assert entitlements(w.sync(w.a)) == []
    assert entitlements(w.sync(w.a)) == [("ChangedEntitlement", 1, True)]
