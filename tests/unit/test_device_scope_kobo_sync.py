# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-device scope PR B: a Kobo syncs the books its own scope chooses.

Spec: state/kindle-kids/specs/2026-10-12-per-device-scope.md, items 1-3 and
"Scope change": Kobo sync passes the device to ``membership(user, device=)``;
when a device's scope changes, the books newly in scope are delivered even
though their shelf membership predates the device's cursor; the books that
left follow the removal rule (sync-removal spec); a scope change writes no
place, read status or annotation row, and touches no other device.

Which books each mode selects, against the shared rule, is pinned in
test_ereader_scope_kobo_parity.py. The byte-identical unset guard is in
test_device_scope_kobo_unset_guard.py.
"""

import pytest

from cps import ub
from tests.unit.kobo_removal_world import (
    KoboRemovalWorld, TO_READ, an_hour_ago, delivered, removed,
)

pytestmark = pytest.mark.unit


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


def test_books_newly_in_scope_reach_a_kobo_past_its_cursor(world):
    """Kobo A holds "To Read" (1-4). Its owner picks the "Kindle" shelf
    (2, 5) for it; book 5 was shelved long before A's cursor."""
    w = world(shelf_only=True)
    assert delivered(w.settle(w.a)) == set(TO_READ)

    assert w.set_scope(w.a, "shelves", [w.kindle]) == 200
    responses = w.settle(w.a)

    assert 5 in delivered(responses)
    assert removed(responses) == {1, 3, 4}


def test_going_back_to_the_account_setting_brings_the_account_books_back(world):
    w = world(shelf_only=True)
    assert w.set_scope(w.a, "shelves", [w.kindle]) == 200
    assert delivered(w.settle(w.a)) == {2, 5}

    assert w.set_scope(w.a, "unset") == 200
    responses = w.settle(w.a)

    assert delivered(responses) >= {1, 3, 4}
    assert removed(responses) == {5}


def test_widening_to_the_whole_library_delivers_past_the_cursor(world):
    w = world(shelf_only=True)
    w.settle(w.a)

    assert w.set_scope(w.a, "library") == 200
    responses = w.settle(w.a)

    assert delivered(responses) == {5, 6}
    assert removed(responses) == set()


def test_a_scope_change_on_one_kobo_leaves_the_other_kobo_alone(world):
    """Kobo B stays on the account setting: it neither gains the Kindle
    shelf's book nor loses a To Read book, and none of its rows move."""
    w = world(shelf_only=True)
    w.settle(w.a)
    w.settle(w.b)
    before = w.device_rows(w.b)

    assert w.set_scope(w.a, "shelves", [w.kindle]) == 200
    w.settle(w.a)
    assert w.device_rows(w.b) == before

    responses = w.settle(w.b)
    assert delivered(responses) == set()
    assert removed(responses) == set()
    assert w.ledger(w.b) == set(TO_READ)


def test_a_scope_change_writes_no_place_status_or_annotation(world):
    w = world(shelf_only=True)
    w.settle(w.a)
    w.settle(w.b)
    w.put_state(w.a, 2, 30, an_hour_ago())
    w.put_state(w.b, 2, 60, an_hour_ago())
    w.make_authoritative(2)
    w.add_annotation(2, "a-2", w.a)
    w.add_annotation(2, "b-2", w.b, note="a note")
    w.session.expire_all()

    def reading_rows():
        w.session.expire_all()
        return {
            "places": sorted(
                (row.device_id, row.book_id, row.progress_percent,
                 row.location_value, row.reading_status)
                for row in w.session.query(ub.DeviceReadingPosition)),
            "read": sorted((row.book_id, row.read_status)
                           for row in w.session.query(ub.ReadBook)),
            "annotations": sorted((row.annotation_id, row.hidden, row.note_text)
                                  for row in w.session.query(ub.Annotation)),
        }

    before = reading_rows()
    assert w.set_scope(w.a, "shelves", [w.kindle]) == 200
    assert reading_rows() == before
