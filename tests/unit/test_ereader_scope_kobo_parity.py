# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Kobo sync and the KOReader library hold exactly the same books.

``cps/services/ereader_scope.py`` is the one rule for which books belong on a
user's e-readers. Kobo delivers through its own cursor-shaped SQL and removes
through the shared rule, so this drives the REAL Kobo sync handler in every
combination of the two settings that shape the scope (shelf-only sync, My
Library) against one library that exercises each way a book can be in or out:

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
"""

from datetime import datetime
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from cps import constants, db, ub
from cps.services import ereader_scope

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 28, 12, 0, 0)
FAR_FUTURE = datetime(2100, 1, 1)
ALL_BOOKS = set(range(1, 11))
MY_LIBRARY = ALL_BOOKS - {4}
KOBO_SHELF = {1, 4, 6, 7, 9, 10}
MAGIC_SHELF = {5}
KOBO_READABLE = ALL_BOOKS - {9}

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
# What leaves a device that holds every book. Whole-library mode never removes.
EXPECTED_REMOVED = {
    (False, False): set(),
    (False, True): {4},
    (True, False): {2, 3, 8},
    (True, True): {2, 3, 4, 8},
}


def _book(book_id):
    book = db.Books("Book %d" % book_id, "Book %d" % book_id, "Author",
                    NOW, NOW, "1.0", NOW, "book-%d" % book_id, 1, [], [])
    book.id = book_id
    book.uuid = "uuid-%d" % book_id
    return book


class _World:
    """One in-memory app.db + metadata.db wired into the real Kobo handler."""

    def __init__(self, monkeypatch, *, shelves, my_library, magic_reliable=True):
        from cps import kobo as kobo_module, kobo_sync_status

        self.kobo = kobo_module
        self.engine = create_engine("sqlite://")
        event.listen(
            self.engine, "connect",
            lambda connection, _record: connection.execute(
                "ATTACH DATABASE ':memory:' AS calibre"),
        )
        ub.Base.metadata.create_all(self.engine)
        db.Base.metadata.create_all(self.engine)
        session = self.session = sessionmaker(bind=self.engine)()

        secret = db.Tags("secret")
        for book_id in sorted(ALL_BOOKS):
            book = _book(book_id)
            if book_id == 10:
                book.tags.append(secret)
            session.add(book)
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

        kobo_shelf = ub.Shelf(name="E-reader", user_id=user.id, is_public=0,
                              kobo_sync=True)
        plain_shelf = ub.Shelf(name="Plain", user_id=user.id, is_public=0,
                               kobo_sync=False)
        other_shelf = ub.Shelf(name="Theirs", user_id=other.id, is_public=0,
                               kobo_sync=True)
        session.add_all([kobo_shelf, plain_shelf, other_shelf])
        session.flush()
        for shelf, book_ids in ((kobo_shelf, KOBO_SHELF), (plain_shelf, {2}),
                                (other_shelf, {8})):
            for order, book_id in enumerate(sorted(book_ids), start=1):
                link = ub.BookShelf(shelf=shelf.id, book_id=book_id, order=order)
                link.ub_shelf = shelf
                session.add(link)
        session.add_all([
            ub.UserLibraryBook(user_id=user.id, book_id=book_id)
            for book_id in sorted(MY_LIBRARY)
        ])
        session.add(ub.ArchivedBook(user_id=user.id, book_id=6,
                                    is_archived=True, last_modified=NOW))
        session.add(ub.UserHiddenBook(user_id=user.id, book_id=7))
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

    def close(self):
        self.session.close()
        self.engine.dispose()

    def _sync(self, token):
        SyncToken = self.kobo.SyncToken.SyncToken
        with self.app.test_request_context(
                "/v1/library/sync",
                headers={SyncToken.SYNC_TOKEN_HEADER: token}):
            response = self.kobo.HandleSyncRequest.__wrapped__()
        entitlements = [
            (item.get("NewEntitlement") or item.get("ChangedEntitlement"))
            ["BookEntitlement"]
            for item in response.get_json()
            if "NewEntitlement" in item or "ChangedEntitlement" in item
        ]
        return response, entitlements

    def fresh_device_holds(self):
        """Sync a new Kobo until the server stops; return what it keeps."""
        held = set()
        token = ""
        for _page in range(20):
            response, entitlements = self._sync(token)
            for entitlement in entitlements:
                book_id = int(entitlement["Id"])
                if entitlement["IsRemoved"]:
                    held.discard(book_id)
                else:
                    held.add(book_id)
            token = response.headers[self.kobo.SyncToken.SyncToken.SYNC_TOKEN_HEADER]
            if response.headers.get("x-kobo-sync") != "continue":
                return held
        raise AssertionError("Kobo sync never finished")

    def full_device_removals(self):
        """A Kobo that already holds every book syncs once; return removals."""
        self.session.add_all([
            ub.KoboSyncedBooks(user_id=self.user.id, book_id=book_id,
                               book_uuid="uuid-%d" % book_id)
            for book_id in sorted(ALL_BOOKS)
        ])
        self.session.commit()
        token = self.kobo.SyncToken.SyncToken(
            books_last_created=FAR_FUTURE,
            books_last_modified=FAR_FUTURE,
            archive_last_modified=FAR_FUTURE,
            reading_state_last_modified=FAR_FUTURE,
            books_last_id=max(ALL_BOOKS),
        ).build_sync_token()
        _response, entitlements = self._sync(token)
        return {int(e["Id"]) for e in entitlements if e["IsRemoved"]}

    def ereader_scope_holds(self):
        return set(ereader_scope.held_book_ids(self.user, cdb=self.cdb))


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
    w = world(shelves=shelves, my_library=my_library)
    scope_holds = w.ereader_scope_holds()

    removed = w.full_device_removals()

    assert removed == EXPECTED_REMOVED[(shelves, my_library)]
    # For every book the user can see, "removed from the Kobo" and "absent
    # from the KOReader library" are the same statement.
    visible = {1, 2, 3, 4, 5, 8}
    assert removed & visible == visible - scope_holds


# A magic shelf whose rule failed to evaluate. The ids it did read are still
# delivered, and the scope is still the reliable one minus nothing: "unreliable"
# means neither "allow everything" nor "allow nothing". It only suspends what
# a short list would wrongly remove, on both protocols. With shelf-only sync
# off, magic shelves do not choose the books, so nothing is suspended.
EXPECTED_REMOVED_UNRELIABLE = {
    (False, False): set(),
    (False, True): {4},
    (True, False): set(),
    (True, True): set(),
}


@pytest.mark.parametrize("shelves,my_library", MODES)
def test_unreadable_magic_shelf_suspends_only_removals(world, shelves, my_library):
    # Each world wires the Kobo module to itself: one sync per world.
    fresh = world(shelves=shelves, my_library=my_library, magic_reliable=False)
    assert fresh.fresh_device_holds() == EXPECTED_HELD[(shelves, my_library)]

    full = world(shelves=shelves, my_library=my_library, magic_reliable=False)
    assert full.full_device_removals() == EXPECTED_REMOVED_UNRELIABLE[(shelves, my_library)]


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
