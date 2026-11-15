# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Remove, acknowledge, re-add: a Kobo gets its own notes and place back.

Spec: state/kindle-kids/specs/2026-10-12-sync-removal.md, G2, G3, G4 and
Tests 1 and 6, approved by CWNG for EVERY Kobo (scoped or not). The operator's
rule: "notes and highlights are not deleted, and if that book is resynced and
readded to that same device ... add back the notes and highlights for that
device".

The world: a shelf-only account, "To Read" on two Kobos (A and
B), both holding book 1. Kobo A reads book 1 and uploads a highlight, then the
book leaves "To Read". A syncs (the removal), syncs again (its token
acknowledges that page), the book returns to "To Read", A syncs it back and
downloads it. B never syncs in between: it still holds book 1.

* G2: the removal and its acknowledgement are A's alone. B's rows are
  byte-unchanged, B still gets its own removal later, and the account marker
  (``KoboSyncedBooks``) goes only when no Kobo holds the book.
* G3: A's place, status and their observation clocks survive.
* G4: the first state exchange after the re-download serves A's own place,
  with Share my place off and on; a newer shared place on a checked Kobo
  follows today's arbitration.
* A gets its own annotations back, even when delivery telemetry says it
  already has them, and never B's.
"""

import time
from datetime import timedelta

import pytest

from cps import ub
from tests.unit.kobo_removal_world import (
    KoboRemovalWorld, NOW, an_hour_ago, delivered, entitlements, observation,
    removed, utcnow,
)

pytestmark = pytest.mark.unit

BOOK = 1
A_OWN = {"a-1", "a-note", "legacy-kobo"}
B_OWN = {"b-1", "legacy-kobo"}
EVERY_VISIBLE = {"a-1", "a-note", "b-1", "kindle-1", "legacy-kobo", "legacy-web"}


@pytest.fixture
def world(monkeypatch):
    built = KoboRemovalWorld(monkeypatch, shelf_only=True)
    yield built
    built.close()


def _held_by_both(w, *, a_read_at=None):
    """A and B hold book 1 and every To Read book; each has read book 1 and
    uploaded its annotations after that reading, so no removal waits (G7)."""
    w.settle(w.a)
    w.settle(w.b)
    w.make_authoritative(BOOK)
    w.add_annotation(BOOK, "a-1", w.a)
    w.add_annotation(BOOK, "a-note", w.a, note="my note")
    w.add_annotation(BOOK, "b-1", w.b)
    w.add_annotation(BOOK, "kindle-1", w.koreader, source="koreader")
    w.add_annotation(BOOK, "legacy-kobo", None, source="kobo")
    w.add_annotation(BOOK, "legacy-web", None, source="webreader")
    w.put_state(w.a, BOOK, 40, a_read_at or an_hour_ago())
    w.put_state(w.b, BOOK, 75, an_hour_ago())
    # Nickel uploads annotations after reading; a second later on the server
    # clock, so "reading newer than the last upload" (G7) is not the case.
    time.sleep(1.1)
    w.upload_annotation(w.a, BOOK, "a-uploaded")
    w.upload_annotation(w.b, BOOK, "b-uploaded")


def _remove_and_acknowledge(w):
    w.unshelve(w.to_read, BOOK)
    removal = w.sync(w.a)
    assert entitlements(removal) == [("ChangedEntitlement", BOOK, True)]
    w.settle(w.a)                      # its token acknowledges the removal page


def _re_add_and_download(w):
    w.shelve(w.to_read, BOOK)
    assert BOOK in delivered(w.settle(w.a))
    w.download(w.a, BOOK)


# == G2 ======================================================================

def test_a_removal_and_its_acknowledgement_leave_the_other_kobo_byte_unchanged(world):
    w = world
    _held_by_both(w)
    before = w.device_rows(w.b)

    _remove_and_acknowledge(w)

    assert w.device_rows(w.b) == before
    assert BOOK in w.ledger(w.b)
    assert BOOK not in w.ledger(w.a)
    assert w.synced_marker(BOOK), "Kobo B still holds the book"


def test_the_other_kobo_still_gets_its_own_removal(world):
    w = world
    _held_by_both(w)
    _remove_and_acknowledge(w)

    responses = w.settle(w.b)

    assert removed(responses) == {BOOK}
    assert BOOK not in w.ledger(w.b)
    assert not w.synced_marker(BOOK), "no Kobo holds the book any more"


def test_the_whole_lifecycle_leaves_every_row_of_the_other_kobo_byte_unchanged(world):
    w = world
    _held_by_both(w)
    before = w.device_rows(w.b)

    _remove_and_acknowledge(w)
    _re_add_and_download(w)
    w.get_state(w.a, BOOK)
    w.annotation_ids(w.a, BOOK)

    assert w.device_rows(w.b) == before


# == G3 ======================================================================

def test_removal_keeps_the_devices_place_status_and_their_clocks(world):
    w = world
    _held_by_both(w)
    before = observation(w.journal(w.a, BOOK))

    _remove_and_acknowledge(w)
    assert observation(w.journal(w.a, BOOK)) == before

    _re_add_and_download(w)
    assert observation(w.journal(w.a, BOOK)) == before


def test_removal_deletes_no_annotation_and_no_shared_reading_row(world):
    w = world
    _held_by_both(w)

    def kept():
        w.session.expire_all()
        return {
            "annotations": sorted(
                (row.annotation_id, row.origin_device_id, row.hidden, row.note_text,
                 row.highlighted_text) for row in w.session.query(ub.Annotation)),
            "read": sorted((row.book_id, row.read_status)
                           for row in w.session.query(ub.ReadBook)),
            "kobo_reading_state": sorted(
                row.book_id for row in w.session.query(ub.KoboReadingState)),
        }

    before = kept()
    _remove_and_acknowledge(w)
    _re_add_and_download(w)

    assert kept() == before


# == annotations on re-add (holds today: regression) =========================

def test_re_add_serves_the_devices_own_annotations_even_when_telemetry_says_delivered(
        world):
    w = world
    _held_by_both(w)
    for row in w.session.query(ub.Annotation).filter(
            ub.Annotation.origin_device_id == w.a.id):
        w.mark_delivered(w.a, row)

    _remove_and_acknowledge(w)
    _re_add_and_download(w)
    served = w.annotation_ids(w.a, BOOK)

    assert A_OWN <= served
    assert not served & {"b-1", "b-uploaded", "kindle-1", "legacy-web"}


# == G4 ======================================================================

def _place(state):
    mark = state.get("CurrentBookmark") or {}
    return mark.get("ProgressPercent"), (mark.get("Location") or {}).get("Value")


def test_re_download_serves_the_devices_own_place_share_off(world):
    w = world
    _held_by_both(w)
    assert _place(w.get_state(w.a, BOOK)) == (40, "kobo.40.1")

    _remove_and_acknowledge(w)
    _re_add_and_download(w)

    assert _place(w.get_state(w.a, BOOK)) == (40, "kobo.40.1")


def test_re_download_serves_the_devices_own_place_share_on(world):
    """A read with Share my place off, then turned it on: the account's shared
    place (10%, older) is not newer than A's own, so A's own is served."""
    w = world
    w.seed_shared(BOOK, 10, an_hour_ago() - timedelta(days=30))
    _held_by_both(w)
    w.share(w.a, True)
    assert _place(w.get_state(w.a, BOOK)) == (40, "kobo.40.1")

    _remove_and_acknowledge(w)
    _re_add_and_download(w)

    assert _place(w.get_state(w.a, BOOK)) == (40, "kobo.40.1")


def test_a_newer_shared_place_still_wins_on_a_checked_kobo(world):
    """Today's arbitration: A (checked) read to 40% an hour ago; the shared
    place was moved to 70% since. A is served the shared 70% before and after
    the lifecycle."""
    w = world
    _held_by_both(w, a_read_at=utcnow() - timedelta(hours=2))
    w.seed_shared(BOOK, 70, utcnow() - timedelta(minutes=30))
    w.share(w.a, True)
    assert _place(w.get_state(w.a, BOOK))[0] == 70

    _remove_and_acknowledge(w)
    _re_add_and_download(w)

    # The shared place's progress wins; which Location carries it is today's
    # delivery-proof rule, not pinned here.
    assert _place(w.get_state(w.a, BOOK))[0] == 70


# == re-add past an old cursor (holds today: regression) =====================

def test_re_add_past_an_old_cursor_is_a_new_entitlement(world):
    """The book returns to the shelf with a membership time no later than A's
    cursor (the book and shelf clocks both sit at the cursor): only the
    missing delivery row can admit it, as New."""
    w = world
    _held_by_both(w)
    _remove_and_acknowledge(w)

    w.shelve(w.to_read, BOOK, at=NOW)
    responses = w.settle(w.a)

    assert ("NewEntitlement", BOOK, False) in [
        item for response in responses for item in entitlements(response)]


# == device matching for the Kobo annotation GET (CWNG note) =================
# Driven through the real route and the real authority decision: nothing on
# the decision path is stubbed (prepare_authoritative_device_get included).

def test_each_kobo_is_served_its_own_rows_and_never_the_other_kobos(world):
    w = world
    _held_by_both(w)

    assert w.annotation_ids(w.a, BOOK) >= A_OWN
    assert not w.annotation_ids(w.a, BOOK) & {"b-1", "b-uploaded", "kindle-1", "legacy-web"}
    assert w.annotation_ids(w.b, BOOK) == B_OWN


def test_after_a_download_only_the_downloading_kobos_rows_are_restored(world):
    w = world
    _held_by_both(w)
    w.download(w.a, BOOK)

    assert not w.annotation_ids(w.a, BOOK) & {"b-1", "b-uploaded", "kindle-1", "legacy-web"}
    assert w.annotation_ids(w.a, BOOK) >= A_OWN
    assert w.annotation_ids(w.b, BOOK) == B_OWN


def test_an_unresolved_kobo_keeps_todays_every_origin_answer(world):
    """No registered device behind the request: today's path, which replays
    the last complete every-origin set (G5: unchanged in this work)."""
    import json
    import logging
    from cps.services import kobo_annotation_authority as authority
    w = world
    _held_by_both(w)
    with w.app.app_context():
        rendered = authority.render_owned_annotations(
            user_id=w.user.id, book_id=BOOK, entitlement_id=w.book(BOOK).uuid,
            page_limit=100, device_id=None, log=logging.getLogger("test"))
    assert rendered is not None
    body, _etag = rendered

    response = w.get_annotations(None, BOOK)

    assert response.status_code == 200
    assert response.get_data() == body
    assert {row["id"] for row in json.loads(body)["annotations"]} >= EVERY_VISIBLE
