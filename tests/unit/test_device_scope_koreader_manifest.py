# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-device book scope (PR A): what each KOReader device is told to hold.

Spec: state/kindle-kids/specs/2026-10-12-per-device-scope.md (with its
"Review additions") and 2026-10-12-sync-removal.md, gap G1. Contract for the
seams these tests use: CONTRACT.md at the root of the PR A branch.

Read as a spec:

* A device's scope is unset (the account's choice, exactly as today),
  ``library``, ``my_library`` or ``shelves``. Shelves are picked by id, and a
  shelf's ``kobo_sync`` mark plays no part: a device-only shelf never reaches a
  device left unset on the same account.
* ``ereader_scope.membership(user, device=...)`` is the one resolver; the
  KOReader manifest asks it for the device that is syncing.
* Visibility still applies: a restricted, hidden or archived book stays off a
  device whose shelf names it.
* The manifest revision changes when the device's scope changes, and only for
  that device.
* A chosen shelf that is deleted or no longer the owner's sends the device
  back to the account's scope: never an empty list, never an error, and the
  Devices page says it fell back and why.
* Back to unset is exactly today's behaviour; a scope change writes no place,
  read status or highlight for any device.
* The unset manifest is the one main serves, byte for byte (recorded on main
  65dce676d in ``fixtures/device_scope_koreader_unset_golden.json``; only ever
  regenerated from main's code, with ``CWNG_RECORD_KOREADER_GOLDEN=1``).

Everything runs over HTTP: the Devices API as the signed-in owner, and the
KOReader library route as the device (real Basic auth, real device headers).
"""

import json
import os
from pathlib import Path

import pytest

from cps import ub
from cps.progress_syncing.checksums.koreader import calculate_koreader_partial_md5
from cps.services import ereader_scope
from tests.unit.device_scope_world import ScopeWorld

pytestmark = pytest.mark.unit

GOLDEN = Path(__file__).parent / "fixtures" / "device_scope_koreader_unset_golden.json"
RECORD = os.environ.get("CWNG_RECORD_KOREADER_GOLDEN") == "1"

KINDLE = "kindle-a"        # the device whose scope the tests set
OTHER = "kindle-b"         # a second KOReader on the same account, left unset

TO_READ = {1}              # the account's e-reader shelf (kobo_sync on)
KINDLE_SHELF = {2, 3, 5, 6, 7}   # 5 hidden, 6 restricted, 7 archived
VISIBLE = {1, 2, 3, 4, 8}  # everything user A may see


@pytest.fixture
def world(monkeypatch, tmp_path):
    built = ScopeWorld(monkeypatch, tmp_path)
    yield built
    built.close()


def _library(world, *, shelf_only=True, my_library=False):
    """User A's library. Shelf uuids are fixed so the manifest is reproducible."""
    a = world.add_user("user-a", shelf_only=shelf_only, my_library=my_library,
                       denied_tags="secret")
    b = world.add_user("user-b")
    world.add_book(1, "On To Read")
    world.add_book(2, "Kindle One")
    world.add_book(3, "Kindle Two")
    world.add_book(4, "On No Shelf")
    world.add_book(5, "Kindle Hidden")
    world.add_book(6, "Kindle Restricted", tags=("secret",))
    world.add_book(7, "Kindle Archived")
    world.add_book(8, "Only Theirs")
    world.session.add(ub.UserHiddenBook(user_id=a.id, book_id=5))
    world.session.add(ub.ArchivedBook(user_id=a.id, book_id=7, is_archived=True))
    if my_library:
        world.session.add_all([ub.UserLibraryBook(user_id=a.id, book_id=book_id)
                               for book_id in (1, 2, 4)])
    world.session.commit()
    world.to_read = world.shelf(a, "To Read", sorted(TO_READ), kobo_sync=True,
                                uuid="shelf-to-read")
    world.kindle = world.shelf(a, "Kindle", sorted(KINDLE_SHELF), uuid="shelf-kindle")
    world.kindle_copy = world.shelf(a, "Kindle copy", [2, 3], uuid="shelf-kindle-copy")
    world.theirs = world.shelf(b, "Theirs", [8], kobo_sync=True, uuid="shelf-theirs")
    world.a, world.b = a, b
    world.device = world.koreader(a, KINDLE)
    world.other = world.koreader(a, OTHER, name="Kindle B")
    return a


def _set(world, mode, shelves=(), magic_shelves=()):
    response = world.set_scope(world.a, world.device, mode, shelves, magic_shelves)
    assert response.status_code == 200, response.get_data(as_text=True)


def _row(world, device):
    return world.device_rows(world.a)[device.public_id]


# -- the unset device is today's device ---------------------------------------

@pytest.mark.parametrize("shelf_only", [True, False], ids=["shelf-only", "whole-library"])
def test_an_unset_device_reads_the_manifest_main_serves(world, shelf_only):
    """GUARD: passes on main by construction; PR A must keep it passing."""
    key = "shelf-only" if shelf_only else "whole-library"
    a = _library(world, shelf_only=shelf_only)

    response = world.manifest(a, KINDLE)
    assert response.status_code == 200
    observed = response.get_json()
    # The test EPUBs are zipped with the current time, so their bytes differ
    # from run to run: check each checksum against its file, then compare the
    # rest of the manifest exactly.
    for book in observed["books"]:
        path = world.book_file(book["book_id"], book["format"])
        assert book["checksum"] == calculate_koreader_partial_md5(str(path))
        book["checksum"] = "<the file's checksum>"

    golden = json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}
    if RECORD:
        golden[key] = observed
        GOLDEN.write_text(json.dumps(golden, indent=1, sort_keys=True) + "\n")
    assert observed == golden[key]


# -- each mode -----------------------------------------------------------------

@pytest.mark.parametrize("mode,shelves,expected", [
    ("library", (), VISIBLE),
    ("shelves", ("kindle",), {2, 3}),
    ("shelves", ("kindle", "to_read"), {1, 2, 3}),
], ids=["library", "one-shelf", "two-shelves"])
def test_each_device_scope_gives_its_own_books(world, mode, shelves, expected):
    _library(world)
    _set(world, mode, [getattr(world, name) for name in shelves])

    held, body = world.held(world.a, KINDLE)

    assert held == expected
    if mode == "shelves":
        assert body["scope"] == "shelves"
        assert {s["id"] for s in body["scope_shelves"]} == {
            getattr(world, name).uuid for name in shelves}


def test_my_library_scope_gives_the_my_library_books(world):
    # Shelf-only account scope is "To Read" within My Library: book 1 only.
    a = _library(world, shelf_only=True, my_library=True)
    assert world.held(a, KINDLE)[0] == {1}

    _set(world, "my_library")

    assert world.held(a, KINDLE)[0] == {1, 2, 4}


def test_a_device_shelf_reaches_only_that_device(world):
    """The "Kindle" shelf has kobo_sync off; the other device stays on the
    account's "To Read" scope, and its manifest does not move at all."""
    a = _library(world)
    before = world.manifest(a, OTHER).get_json()

    _set(world, "shelves", [world.kindle])

    assert world.held(a, KINDLE)[0] == {2, 3}
    after, body = world.held(a, OTHER)
    assert after == TO_READ
    assert body == before
    assert _row(world, world.other)["book_scope"]["mode"] == "unset"


def test_a_restricted_hidden_or_archived_book_stays_off_a_device_naming_its_shelf(world):
    _library(world)
    _set(world, "shelves", [world.kindle])

    held, _body = world.held(world.a, KINDLE)

    assert {2, 3} <= held
    assert not held & {5, 6, 7}


def test_the_resolver_answers_for_the_device_and_falls_back_to_the_account(world):
    a = _library(world)
    account = ereader_scope.membership(a)

    assert ereader_scope.membership(a, device=None) == account
    assert ereader_scope.membership(a, device=world.device) == account

    _set(world, "shelves", [world.kindle])
    world.session.expire_all()
    device = world.session.get(ub.Device, world.device.id)
    scoped = ereader_scope.membership(a, device=device)
    assert scoped.book_ids == frozenset(KINDLE_SHELF)
    assert scoped.reliable is True
    # The account, and the device left unset, are untouched.
    assert ereader_scope.membership(a) == account
    other = world.session.get(ub.Device, world.other.id)
    assert ereader_scope.membership(a, device=other) == account

    _set(world, "library")
    world.session.expire_all()
    device = world.session.get(ub.Device, world.device.id)
    assert ereader_scope.membership(a, device=device).book_ids is None


# -- magic shelves -------------------------------------------------------------

def _magic(world, user, name, book_ids, *, reliable=True, kobo_sync=False,
           global_setting=True):
    """A smart shelf whose rule the library evaluates to ``book_ids``; the
    evaluator is the one Kobo sync uses today. ``global_setting`` is the
    admin's "Sync smart shelves to e-readers" switch."""
    from cps import config, magic_shelf
    shelf = ub.MagicShelf(name=name, user_id=user.id, rules={}, kobo_sync=kobo_sync)
    world.session.add(shelf)
    world.session.commit()
    world.monkeypatch.setattr(config, "config_kobo_sync_magic_shelves", global_setting,
                              raising=False)
    results = getattr(world, "_magic_results", {})
    results[shelf.id] = (set(book_ids), reliable)
    world._magic_results = results
    real = magic_shelf.get_book_ids_for_magic_shelf

    def evaluate(shelf_id, *args, **kwargs):
        if shelf_id not in results:
            return real(shelf_id, *args, **kwargs)
        ids, ok = results[shelf_id]
        if not ok:
            raise RuntimeError("magic shelf rule could not be read")
        return list(sorted(ids)), len(ids)

    world.monkeypatch.setattr(magic_shelf, "get_book_ids_for_magic_shelf", evaluate)
    return shelf


def test_a_smart_shelf_picked_for_a_device_gives_its_books(world):
    a = _library(world)
    smart = _magic(world, a, "Smart", {3, 4})

    _set(world, "shelves", [world.kindle_copy], [smart])

    assert world.held(a, KINDLE)[0] == {2, 3, 4}
    # Not marked for e-readers, so the unset device never sees it.
    assert world.held(a, OTHER)[0] == TO_READ


def test_an_unreadable_smart_shelf_answers_unavailable_not_a_short_list(world):
    a = _library(world)
    smart = _magic(world, a, "Smart", {3, 4}, reliable=False)
    _set(world, "shelves", [world.kindle_copy], [smart])

    response = world.manifest(a, KINDLE)

    assert response.status_code == 503
    assert response.get_json()["error"] == "scope_unavailable"
    # The device left unset is not affected by a shelf it never chose.
    assert world.held(a, OTHER)[0] == TO_READ


@pytest.mark.parametrize("with_shelf", [True, False], ids=["shelf-and-smart", "smart-only"])
def test_a_smart_shelf_with_smart_shelf_sync_off_does_what_the_account_does(world, with_shelf):
    """With the admin's smart-shelf switch off, the account scope today leaves
    a smart shelf's books out (#359: reliable, nothing delivered). A device
    that picks the same shelves gets exactly what an unset device on an
    account with those shelves gets."""
    a = _library(world)
    smart = _magic(world, a, "Smart", {3, 4}, kobo_sync=True, global_setting=False)
    if not with_shelf:
        shelf = world.session.get(ub.Shelf, world.to_read.id)
        shelf.kobo_sync = False
        world.session.commit()
    # The account's own answer on main's code: the unset device.
    account = world.manifest(a, OTHER)
    assert account.status_code == 200
    account_books = {book["book_id"] for book in account.get_json()["books"]}
    assert account_books == (TO_READ if with_shelf else set())

    _set(world, "shelves", [world.to_read] if with_shelf else [], [smart])

    scoped = world.manifest(a, KINDLE)
    assert scoped.status_code == account.status_code
    assert {book["book_id"] for book in scoped.get_json()["books"]} == account_books


# -- a scope change reaches the device on its next sync ------------------------

def test_the_revision_changes_when_the_device_scope_changes(world):
    a = _library(world)
    first = world.manifest(a, KINDLE).get_json()["revision"]
    other_first = world.manifest(a, OTHER).get_json()["revision"]

    _set(world, "shelves", [world.kindle])
    answer = world.manifest(a, KINDLE, if_revision=first).get_json()
    assert "unchanged" not in answer
    assert answer["revision"] != first

    # The same books from a different shelf is still a different scope.
    _set(world, "shelves", [world.kindle_copy])
    again = world.manifest(a, KINDLE, if_revision=answer["revision"]).get_json()
    assert "unchanged" not in again
    assert {book["book_id"] for book in again["books"]} == {2, 3}

    # The other device has nothing new to read.
    assert world.manifest(a, OTHER, if_revision=other_first).get_json() == {
        "unchanged": True, "revision": other_first}


def test_a_sync_paging_through_the_old_scope_does_not_mix_in_the_new_one(world):
    """Pages after the first come from the manifest the walk started with; a
    scope change in between is the NEXT sync's news, never half of this one."""
    a = _library(world, shelf_only=False)
    first = world.manifest(a, KINDLE, limit=1).get_json()
    assert first["next_cursor"]

    _set(world, "shelves", [world.kindle])
    seen = {book["book_id"] for book in first["books"]}
    cursor = first["next_cursor"]
    while cursor:
        page = world.manifest(a, KINDLE, limit=1, cursor=cursor).get_json()
        assert page["revision"] == first["revision"]
        seen |= {book["book_id"] for book in page["books"]}
        cursor = page["next_cursor"]
    assert seen == VISIBLE

    fresh = world.manifest(a, KINDLE, if_revision=first["revision"]).get_json()
    assert {book["book_id"] for book in fresh["books"]} == {2, 3}


# -- rollback ------------------------------------------------------------------

@pytest.mark.parametrize("shelf_only", [True, False], ids=["shelf-only", "whole-library"])
def test_back_to_unset_is_the_account_manifest_again(world, shelf_only):
    a = _library(world, shelf_only=shelf_only)
    before = world.manifest(a, KINDLE).get_json()

    _set(world, "shelves", [world.kindle])
    assert world.held(a, KINDLE)[0] == {2, 3}
    _set(world, "unset")

    assert world.manifest(a, KINDLE).get_json() == before
    assert _row(world, world.device)["book_scope"]["mode"] == "unset"


# -- the chosen shelf disappears -----------------------------------------------

def _assert_account_scope(world, a):
    """The scoped device now reads what the unset device reads."""
    unset = world.manifest(a, OTHER)
    scoped = world.manifest(a, KINDLE)
    assert scoped.status_code == 200, scoped.get_data(as_text=True)
    unset, scoped = unset.get_json(), scoped.get_json()
    assert scoped["books"] == unset["books"]
    assert scoped["books"], "never an empty device"
    assert (scoped["scope"], scoped["scope_shelves"]) == (unset["scope"], unset["scope_shelves"])


def test_a_deleted_device_shelf_falls_back_to_the_account_scope(world):
    a = _library(world)
    _set(world, "shelves", [world.kindle])
    assert world.held(a, KINDLE)[0] == {2, 3}

    deleted = world.web(a).post("/api/v1/shelves/%d/delete" % world.kindle.id)
    assert deleted.status_code == 204

    _assert_account_scope(world, a)
    scope = _row(world, world.device)["book_scope"]
    assert scope["fallback"] == {"reason": "shelf_deleted"}
    assert scope["effective_mode"] == scope["account_mode"] == "shelves"


def test_a_device_shelf_the_owner_lost_falls_back_to_the_account_scope(world):
    a = _library(world)
    _set(world, "shelves", [world.kindle])
    assert world.held(a, KINDLE)[0] == {2, 3}

    # The shelf now belongs to someone else (as an admin reassignment leaves it).
    shelf = world.session.get(ub.Shelf, world.kindle.id)
    shelf.user_id = world.b.id
    world.session.commit()

    _assert_account_scope(world, a)
    held, _body = world.held(a, KINDLE)
    assert not held & {2, 3}
    scope = _row(world, world.device)["book_scope"]
    assert scope["fallback"] == {"reason": "shelf_unavailable"}
    assert scope["effective_mode"] == scope["account_mode"]


def _two_shelves(world):
    """The device picks "Kindle" (2, 3) and "Extra" (4). The account scope is
    "To Read" (1), so keeping a shelf and falling back are told apart."""
    a = _library(world)
    world.extra = world.shelf(a, "Extra", [4], uuid="shelf-extra")
    _set(world, "shelves", [world.kindle, world.extra])
    assert world.held(a, KINDLE)[0] == {2, 3, 4}
    return a


def _assert_kept_extra(world, a):
    held, body = world.held(a, KINDLE)
    assert held == {4}
    assert body["scope"] == "shelves"
    assert [s["id"] for s in body["scope_shelves"]] == ["shelf-extra"]
    scope = _row(world, world.device)["book_scope"]
    assert scope["fallback"] is None, "no notice while a chosen shelf remains"
    assert scope["effective_mode"] == "shelves"


def test_a_device_keeps_its_other_shelf_when_one_is_deleted(world):
    a = _two_shelves(world)

    deleted = world.web(a).post("/api/v1/shelves/%d/delete" % world.kindle.id)
    assert deleted.status_code == 204

    _assert_kept_extra(world, a)


def test_a_device_keeps_its_other_shelf_when_the_owner_loses_one(world):
    a = _two_shelves(world)

    shelf = world.session.get(ub.Shelf, world.kindle.id)
    shelf.user_id = world.b.id
    world.session.commit()

    _assert_kept_extra(world, a)


def test_a_device_falls_back_only_when_its_last_shelf_goes(world):
    a = _two_shelves(world)
    assert world.web(a).post("/api/v1/shelves/%d/delete" % world.kindle.id).status_code == 204
    _assert_kept_extra(world, a)

    assert world.web(a).post("/api/v1/shelves/%d/delete" % world.extra.id).status_code == 204

    _assert_account_scope(world, a)
    assert _row(world, world.device)["book_scope"]["fallback"] == {"reason": "shelf_deleted"}


def test_a_device_that_did_not_fall_back_reports_no_fallback(world):
    _library(world)
    _set(world, "shelves", [world.kindle])

    scope = _row(world, world.device)["book_scope"]

    assert scope["fallback"] is None
    assert scope["effective_mode"] == "shelves"
    assert _row(world, world.other)["book_scope"]["fallback"] is None


# -- a scope change writes no reading data -------------------------------------

def _reading_rows(world):
    world.session.expire_all()
    tables = (ub.DeviceReadingPosition, ub.ReadBook, ub.Annotation,
              ub.KoboReadingState)
    snapshot = {}
    for table in tables:
        columns = [column.name for column in table.__table__.columns]
        snapshot[table.__tablename__] = sorted(
            tuple(repr(getattr(row, name)) for name in columns)
            for row in world.session.query(table).all())
    return snapshot


def test_a_scope_change_writes_no_place_status_or_highlight_for_any_device(world):
    a = _library(world)
    for device, book_id, percent in ((world.device, 1, 40.0), (world.other, 1, 70.0),
                                     (world.device, 2, 12.5)):
        world.session.add(ub.DeviceReadingPosition(
            device_id=device.id, book_id=book_id,
            progress_percent=percent, reading_status=ub.ReadBook.STATUS_IN_PROGRESS))
        world.session.add(ub.Annotation(
            user_id=a.id, book_id=book_id, annotation_id="hl-%d-%d" % (device.id, book_id),
            source="koreader", origin_device_id=device.id, highlighted_text="words",
            highlight_color="yellow", annotation_type="highlight"))
    world.position(a, 1, 55.0)
    world.session.commit()
    world.manifest(a, KINDLE)
    world.manifest(a, OTHER)
    before = _reading_rows(world)

    _set(world, "shelves", [world.kindle])
    world.manifest(a, KINDLE)
    world.manifest(a, OTHER)
    _set(world, "library")
    world.manifest(a, KINDLE)
    _set(world, "unset")
    world.manifest(a, KINDLE)

    assert _reading_rows(world) == before
