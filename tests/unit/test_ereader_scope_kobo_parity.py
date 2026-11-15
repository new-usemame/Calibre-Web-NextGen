# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Kobo sync and the KOReader library hold exactly the same books.

``cps/services/ereader_scope.py`` is the one rule for which books belong on a
user's e-readers. Kobo delivers through its own cursor-shaped SQL and removes
through the shared rule, so this drives the REAL Kobo sync handler, as a
registered Kobo, in every combination of the two settings that shape the
account scope (shelf-only sync, My Library) and, from per-device scope PR B,
in every device scope mode, against one library that exercises each way a
book can be in or out:

  1  on the user's e-reader shelf, in My Library
  2  on a shelf NOT marked for e-readers, in My Library
  3  on no shelf, in My Library
  4  on the user's e-reader shelf, NOT in My Library
  5  only on an e-reader magic shelf, in My Library
  6  on the e-reader shelf, in My Library, archived by the user
  7  on the e-reader shelf, in My Library, hidden by the user
  8  only on ANOTHER user's e-reader shelf, in My Library
  9  on the e-reader shelf, in My Library, no Kobo-readable format
  10 on the e-reader shelf, in My Library, carries a tag the user may not see

Removal (sync-removal spec G2 and G8, from PR B): a Kobo that was handed
books removes exactly those of them that are no longer in
``held_book_ids`` (the device's scope, visible books only) whenever that read
is reliable. Archive keeps its own explicit path. An unreliable read removes
nothing through the scope (#468).
"""

from datetime import datetime, timedelta
import inspect
from types import SimpleNamespace

import pytest
from flask import Flask, g
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from cps import constants, db, ub
from cps.services import ereader_scope

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 28, 12, 0, 0)
ALL_BOOKS = set(range(1, 11))
MY_LIBRARY = ALL_BOOKS - {4}
KOBO_SHELF = {1, 4, 6, 7, 9, 10}
MAGIC_SHELF = {5}
KOBO_READABLE = ALL_BOOKS - {9}
VISIBLE = {1, 2, 3, 4, 5, 8, 9}     # not archived (6), hidden (7) or restricted (10)

MODES = [
    pytest.param(False, False, id="whole-library"),
    pytest.param(False, True, id="my-library"),
    pytest.param(True, False, id="shelves"),
    pytest.param(True, True, id="shelves-and-my-library"),
]

# What a device should hold, written out by hand from the rule (archived,
# hidden and tag-restricted books never; the scope bounds everything else).
EXPECTED_HELD = {
    (False, False): {1, 2, 3, 4, 5, 8},
    (False, True): {1, 2, 3, 5, 8},
    (True, False): {1, 4, 5},
    (True, True): {1, 5},
}

# Every Kobo-readable book: what a device was handed while the account synced
# everything and nothing was archived, hidden or restricted.
DELIVERED_BEFORE = KOBO_READABLE


def _expected_removed(held):
    """What leaves a device that was handed DELIVERED_BEFORE: everything it was
    handed that the rule no longer holds (``held`` includes the unreadable 9
    when in scope; it was never handed, so it never leaves)."""
    return DELIVERED_BEFORE - held


# Device scope modes (per-device scope spec, items 1-3). A device shelf is
# chosen by id, whatever its kobo_sync flag; a device scope ignores the
# account's shelf-only choice; with My Library on it is bounded by My Library.
DEVICE_MODES = [
    pytest.param("library", (), id="library"),
    pytest.param("my_library", (), id="my_library"),
    pytest.param("shelves", ("plain",), id="shelves-plain"),
    pytest.param("shelves", ("kobo", "plain"), id="shelves-kobo-and-plain"),
]


def _device_scope_held(mode, shelves, my_library):
    """The held set for a device scope, by hand."""
    if mode == "shelves":
        ids = set()
        if "kobo" in shelves:
            ids |= KOBO_SHELF
        if "plain" in shelves:
            ids |= {2}
    else:
        ids = set(ALL_BOOKS)
    if my_library or mode == "my_library":
        ids &= MY_LIBRARY
    return ids & VISIBLE


def _book(book_id):
    book = db.Books("Book %d" % book_id, "Book %d" % book_id, "Author",
                    NOW, NOW, "1.0", NOW, "book-%d" % book_id, 1, [], [])
    book.id = book_id
    book.uuid = "uuid-%d" % book_id
    return book


class _World:
    """One in-memory app.db + metadata.db wired into the real Kobo handler,
    with one registered Kobo (the requesting device)."""

    def __init__(self, monkeypatch, *, shelves, my_library, magic_reliable=True,
                 deliver_first=False):
        from cps import annotations, kobo as kobo_module, kobo_sync_status

        self.kobo = kobo_module
        self.shelves = shelves
        self.my_library = my_library
        self.engine = create_engine("sqlite://")
        event.listen(
            self.engine, "connect",
            lambda connection, _record: connection.execute(
                "ATTACH DATABASE ':memory:' AS calibre"),
        )
        ub.Base.metadata.create_all(self.engine)
        db.Base.metadata.create_all(self.engine)
        session = self.session = sessionmaker(bind=self.engine)()

        self.secret = db.Tags("secret")
        session.add(self.secret)
        for book_id in sorted(ALL_BOOKS):
            session.add(_book(book_id))
            session.add(db.Data(book_id, "AZW3" if book_id == 9 else "EPUB",
                                1, "book-%d" % book_id))

        self.user = user = ub.User(
            name="reader", email="reader@example.invalid", password="",
            has_own_library=my_library, user_library_seeded=my_library,
            default_language="all", denied_tags="secret",
            role=constants.ROLE_USER | constants.ROLE_DOWNLOAD,
            kobo_only_shelves_sync=1 if shelves else 0,
        )
        other = ub.User(name="other", email="other@example.invalid",
                        password="", default_language="all",
                        role=constants.ROLE_USER | constants.ROLE_DOWNLOAD)
        session.add_all([user, other])
        session.flush()

        self.kobo_shelf = ub.Shelf(name="E-reader", user_id=user.id, is_public=0,
                                   kobo_sync=True)
        self.plain_shelf = ub.Shelf(name="Plain", user_id=user.id, is_public=0,
                                    kobo_sync=False)
        other_shelf = ub.Shelf(name="Theirs", user_id=other.id, is_public=0,
                               kobo_sync=True)
        session.add_all([self.kobo_shelf, self.plain_shelf, other_shelf])
        session.flush()
        for shelf, book_ids in ((self.kobo_shelf, KOBO_SHELF),
                                (self.plain_shelf, {2}), (other_shelf, {8})):
            for order, book_id in enumerate(sorted(book_ids), start=1):
                link = ub.BookShelf(shelf=shelf.id, book_id=book_id, order=order,
                                    date_added=NOW)
                link.ub_shelf = shelf
                session.add(link)
        session.add_all([
            ub.UserLibraryBook(user_id=user.id, book_id=book_id)
            for book_id in sorted(MY_LIBRARY)
        ])
        self.device = ub.Device(
            user_id=user.id, kind="kobo", display_name="Kobo", active=True,
            created_by="auto", public_id="00000000-0000-4000-8000-0000000000e1",
            first_seen_at=NOW, last_seen_at=NOW)
        session.add(self.device)
        session.commit()

        cdb = self.cdb = object.__new__(db.CalibreDB)
        cdb.session = session
        cdb.config = SimpleNamespace(config_restricted_column=0)
        cdb.reconnect_db = lambda *_args, **_kwargs: None
        cdb.refresh_for_new_data = lambda: None

        monkeypatch.setattr(db.ub, "session", session)
        monkeypatch.setattr(db, "current_user", user)
        monkeypatch.setattr(ub, "session", session)
        monkeypatch.setattr(ub, "session_commit",
                            lambda *_args, **_kwargs: session.commit())
        monkeypatch.setattr(kobo_module, "calibre_db", cdb)
        monkeypatch.setattr(kobo_module, "current_user", user)
        monkeypatch.setattr(kobo_sync_status, "current_user", user)
        monkeypatch.setattr(annotations, "current_user", user)
        monkeypatch.setattr(kobo_module.config, "config_kobo_proxy", False,
                            raising=False)
        monkeypatch.setattr(kobo_module.config,
                            "config_kobo_sync_magic_shelves", True,
                            raising=False)
        monkeypatch.setattr(kobo_module, "get_download_url_for_book",
                            lambda *_args: "/download")
        # The magic shelf's rule is evaluated by magic_shelf.py; its result
        # (and whether the evaluation succeeded) is the input under test here.
        monkeypatch.setattr(kobo_module, "get_magic_shelf_book_ids_for_kobo",
                            lambda _user_id: (set(MAGIC_SHELF), magic_reliable))
        monkeypatch.setattr(kobo_module, "get_magic_shelf_membership_added_at",
                            lambda _user_id: None)
        monkeypatch.setattr(kobo_module, "sync_shelves",
                            lambda *_args, **_kwargs: None)
        monkeypatch.setattr(
            kobo_module, "create_book_entitlement",
            lambda book, archived=False: {"Id": str(book.id),
                                          "IsRemoved": archived},
        )
        monkeypatch.setattr(kobo_module, "get_metadata",
                            lambda book: {"Id": str(book.id)})
        self.app = Flask(__name__)
        self.app.wsgi_app = SimpleNamespace(is_proxied=True)
        self.app.register_blueprint(annotations.annotations_bp)
        self.token = ""

        if deliver_first:
            # The device first syncs while the account takes every book and
            # nothing is archived, hidden or restricted: it is handed every
            # Kobo-readable book. Then the world below takes effect.
            user.kobo_only_shelves_sync = 0
            user.has_own_library = False
            session.commit()
            handed = self.sync_to_end()
            assert handed == (DELIVERED_BEFORE, set())
            user.kobo_only_shelves_sync = 1 if shelves else 0
            user.has_own_library = my_library
            session.commit()
        # Archived after the device took the book, when it took it.
        self._restrict(archived_at=NOW + timedelta(days=1) if deliver_first else NOW)

    def _restrict(self, archived_at):
        session = self.session
        session.add(ub.ArchivedBook(user_id=self.user.id, book_id=6,
                                    is_archived=True, last_modified=archived_at))
        session.add(ub.UserHiddenBook(user_id=self.user.id, book_id=7))
        book = session.query(db.Books).filter(db.Books.id == 10).one()
        book.tags.append(self.secret)
        session.commit()

    def close(self):
        self.session.close()
        self.engine.dispose()

    def set_device_scope(self, mode, shelves=()):
        """The Devices page PATCH for this Kobo, as the account owner."""
        from werkzeug.exceptions import HTTPException
        chosen = {"kobo": self.kobo_shelf, "plain": self.plain_shelf}
        path = "/api/annotations/devices/%s" % self.device.public_id
        body = {"book_scope": {"mode": mode,
                               "shelf_ids": [chosen[name].id for name in shelves],
                               "magic_shelf_ids": []}}
        with self.app.test_request_context(path, method="PATCH", json=body):
            endpoint, args = self.app.url_map.bind("localhost").match(
                path, method="PATCH")
            view = inspect.unwrap(self.app.view_functions[endpoint])
            try:
                result = view(**args)
            except HTTPException as error:
                return error.code
        self.session.expire_all()
        return result[1] if isinstance(result, tuple) else result.status_code

    def _sync(self, token):
        SyncToken = self.kobo.SyncToken.SyncToken
        with self.app.test_request_context(
                "/v1/library/sync",
                headers={SyncToken.SYNC_TOKEN_HEADER: token}):
            g.annotation_origin_device_id = self.device.id
            response = self.kobo.HandleSyncRequest.__wrapped__()
        entitlements = [
            (item.get("NewEntitlement") or item.get("ChangedEntitlement"))
            ["BookEntitlement"]
            for item in response.get_json()
            if "NewEntitlement" in item or "ChangedEntitlement" in item
        ]
        self.session.expire_all()
        return response, entitlements

    def sync_to_end(self):
        """Sync the Kobo, handing back its own token, until the server stops
        and the last page is acknowledged. Returns (handed, removed)."""
        handed, removed = set(), set()
        for _page in range(20):
            response, entitlements = self._sync(self.token)
            for entitlement in entitlements:
                book_id = int(entitlement["Id"])
                if entitlement["IsRemoved"]:
                    removed.add(book_id)
                    handed.discard(book_id)
                else:
                    handed.add(book_id)
            self.token = response.headers[self.kobo.SyncToken.SyncToken.SYNC_TOKEN_HEADER]
            if (response.headers.get("x-kobo-sync") != "continue"
                    and not entitlements):
                return handed, removed
        raise AssertionError("Kobo sync never finished")

    def fresh_device_holds(self):
        """Sync a new Kobo until the server stops; return what it keeps."""
        handed, _removed = self.sync_to_end()
        return handed

    def ereader_scope_holds(self, device=None):
        scope = ereader_scope.membership(self.user, device=device,
                                         session=self.session)
        return set(ereader_scope.held_book_ids(self.user, cdb=self.cdb,
                                               session=self.session, scope=scope))


@pytest.fixture
def world(monkeypatch):
    worlds = []

    def build(**kwargs):
        built = _World(monkeypatch, **kwargs)
        worlds.append(built)
        return built

    yield build
    for built in worlds:
        built.close()


@pytest.mark.parametrize("shelves,my_library", MODES)
def test_kobo_delivers_exactly_the_ereader_scope(world, shelves, my_library):
    w = world(shelves=shelves, my_library=my_library)

    kobo_holds = w.fresh_device_holds()
    scope_holds = w.ereader_scope_holds()

    # Kobo can only be handed its own formats; KOReader's manifest applies
    # its format rule to the same set.
    assert kobo_holds == scope_holds & KOBO_READABLE
    assert kobo_holds == EXPECTED_HELD[(shelves, my_library)]


@pytest.mark.parametrize("shelves,my_library", MODES)
def test_kobo_removes_exactly_what_left_the_ereader_scope(
        world, shelves, my_library):
    """G8: hidden (7) and restricted (10) books leave too, in every mode,
    whole-library included; archived (6) leaves through its own path."""
    w = world(shelves=shelves, my_library=my_library, deliver_first=True)
    scope_holds = w.ereader_scope_holds()

    _handed, removed = w.sync_to_end()

    assert removed == _expected_removed(scope_holds)
    assert removed == _expected_removed(EXPECTED_HELD[(shelves, my_library)] | {9})


# A magic shelf whose rule failed to evaluate. The ids it did read are still
# delivered, and the scope is still the reliable one minus nothing: "unreliable"
# means neither "allow everything" nor "allow nothing". It only suspends what
# a short list would wrongly remove, on both protocols: no scope or visibility
# removal at all (hidden 7 and restricted 10 stay too). Only the explicit
# archive path may still remove the archived book (6). With shelf-only sync
# off, magic shelves do not choose the books, so nothing is suspended.
EXPECTED_REMOVED_UNRELIABLE = {
    (False, False): {6, 7, 10},
    (False, True): {4, 6, 7, 10},
}


@pytest.mark.parametrize("shelves,my_library", MODES)
def test_unreadable_magic_shelf_suspends_only_removals(world, shelves, my_library):
    # Each world wires the Kobo module to itself: one run per world.
    fresh = world(shelves=shelves, my_library=my_library, magic_reliable=False)
    assert fresh.fresh_device_holds() == EXPECTED_HELD[(shelves, my_library)]

    full = world(shelves=shelves, my_library=my_library, magic_reliable=False,
                 deliver_first=True)
    _handed, removed = full.sync_to_end()
    if shelves:
        assert removed <= {6}, "an unreliable read removed through the scope"
    else:
        assert removed == EXPECTED_REMOVED_UNRELIABLE[(shelves, my_library)]


@pytest.mark.parametrize("shelves,my_library", MODES)
def test_unreadable_magic_shelf_is_the_same_scope_for_koreader(world, shelves, my_library):
    from cps.services import koreader_library

    w = world(shelves=shelves, my_library=my_library, magic_reliable=False)
    scope = ereader_scope.membership(w.user, session=w.session)

    if shelves:
        expected = KOBO_SHELF | MAGIC_SHELF
        if my_library:
            expected &= MY_LIBRARY
        assert scope.book_ids == expected
        assert scope.reliable is False
        # Never a short list: the device keeps what it has until it reads.
        with pytest.raises(koreader_library.ScopeUnavailable):
            koreader_library.build_manifest(w.user, cdb=w.cdb, session=w.session,
                                            read_column=0)
    else:
        assert scope.reliable is True
        assert (w.ereader_scope_holds() & KOBO_READABLE
                == EXPECTED_HELD[(shelves, my_library)])


# -- per-device scope (PR B) --------------------------------------------------

def _device_params():
    for account in MODES:
        shelves, my_library = account.values
        for device in DEVICE_MODES:
            mode, chosen = device.values
            if mode == "my_library" and not my_library:
                continue   # not offered while My Library is off
            yield pytest.param(shelves, my_library, mode, chosen,
                               id="%s/%s" % (account.id, device.id))


@pytest.mark.parametrize("shelves,my_library,mode,chosen", list(_device_params()))
def test_a_scoped_kobo_delivers_exactly_its_device_scope(
        world, shelves, my_library, mode, chosen):
    w = world(shelves=shelves, my_library=my_library)
    assert w.set_device_scope(mode, chosen) == 200

    kobo_holds = w.fresh_device_holds()

    # The Kobo SQL form and the shared rule agree for the device...
    assert kobo_holds == w.ereader_scope_holds(device=w.device) & KOBO_READABLE
    # ...and both say what the spec says, by hand.
    assert kobo_holds == _device_scope_held(mode, chosen, my_library) & KOBO_READABLE


@pytest.mark.parametrize("shelves,my_library,mode,chosen", list(_device_params()))
def test_a_scoped_kobo_removes_exactly_what_left_its_device_scope(
        world, shelves, my_library, mode, chosen):
    w = world(shelves=shelves, my_library=my_library, deliver_first=True)
    assert w.set_device_scope(mode, chosen) == 200

    _handed, removed = w.sync_to_end()

    assert removed == _expected_removed(w.ereader_scope_holds(device=w.device))
    assert removed == _expected_removed(_device_scope_held(mode, chosen, my_library))
