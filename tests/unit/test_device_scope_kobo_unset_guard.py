# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""A Kobo left on "Same as my account setting" syncs exactly as main has it.

Spec: state/kindle-kids/specs/2026-10-12-per-device-scope.md, "What it must not
touch" and "Review additions": PR A (model + UI + KOReader) must not change the
Kobo path, and a device left on "Same as my account setting" gets the same Kobo
sync response, byte for byte, as main gives it. That is the shelf-only Kobo's
guarantee (the household's "To Read" Kobo) and it is checked here for a
shelf-only account and for a whole-library account.

The real Kobo sync handler runs a fresh device to the end. Every response
(body bytes, sync token, the continue flag) is compared with a recording made
by this same file on main 65dce676d (``fixtures/device_scope_kobo_unset_golden.json``).
The recording is only ever regenerated from main's code:
``CWNG_RECORD_KOBO_GOLDEN=1``.

PR B (sync-removal spec, "CWNG review") changes the remove/re-add lifecycle for
every Kobo (G2, G3, G4, G7) and removes hidden, restricted and whole-library
departures (G8). So from PR B the byte-identical guarantee covers ORDINARY sync
responses, the ones with no removal or re-add in them: a device that already
held every book and is told to remove some is a G8 case
(test_kobo_device_removal.py), no longer a byte-for-byte one.

Metadata rendering is stubbed exactly as in test_ereader_scope_kobo_parity.py:
which books are delivered or removed, in which order, under which token, is
what a scope change could disturb.
"""

import inspect
import json
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask, g
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from cps import constants, db, ub

pytestmark = pytest.mark.unit

GOLDEN = Path(__file__).parent / "fixtures" / "device_scope_kobo_unset_golden.json"
RECORD = os.environ.get("CWNG_RECORD_KOBO_GOLDEN") == "1"

NOW = datetime(2026, 8, 28, 12, 0, 0)
ALL_BOOKS = set(range(1, 11))
TO_READ = {1, 4, 6, 7, 9, 10}    # the account's e-reader shelf (kobo_sync on)
KINDLE = {2, 3}                  # a shelf for one KOReader device (kobo_sync off)


def _book(book_id):
    book = db.Books("Book %d" % book_id, "Book %d" % book_id, "Author",
                    NOW, NOW, "1.0", NOW, "book-%d" % book_id, 1, [], [])
    book.id = book_id
    book.uuid = "uuid-%d" % book_id
    return book


class _KoboWorld:
    """One in-memory app.db + metadata.db wired into the real Kobo handler,
    with a registered Kobo (scope never set) and a KOReader on the same account."""

    def __init__(self, monkeypatch, *, shelf_only):
        from cps import annotations, kobo as kobo_module, kobo_sync_status

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
            name="user-a", email="user-a@example.invalid", password="",
            default_language="all", denied_tags="secret",
            role=constants.ROLE_USER | constants.ROLE_DOWNLOAD,
            kobo_only_shelves_sync=1 if shelf_only else 0,
        )
        session.add(user)
        session.flush()
        self.to_read = ub.Shelf(name="To Read", user_id=user.id, is_public=0,
                                kobo_sync=True, uuid="shelf-to-read")
        self.kindle = ub.Shelf(name="Kindle", user_id=user.id, is_public=0,
                               kobo_sync=False, uuid="shelf-kindle")
        session.add_all([self.to_read, self.kindle])
        session.flush()
        for shelf, book_ids in ((self.to_read, TO_READ), (self.kindle, KINDLE)):
            for order, book_id in enumerate(sorted(book_ids), start=1):
                link = ub.BookShelf(shelf=shelf.id, book_id=book_id, order=order,
                                    date_added=NOW)
                link.ub_shelf = shelf
                session.add(link)
        session.add(ub.ArchivedBook(user_id=user.id, book_id=6,
                                    is_archived=True, last_modified=NOW))
        session.add(ub.UserHiddenBook(user_id=user.id, book_id=7))
        self.kobo_device = ub.Device(
            user_id=user.id, kind="kobo", display_name="Kobo", active=True,
            created_by="auto", public_id="00000000-0000-4000-8000-00000000000b",
            first_seen_at=NOW, last_seen_at=NOW)
        self.koreader_device = ub.Device(
            user_id=user.id, kind="koreader", display_name="Kindle", active=True,
            created_by="auto", public_id="00000000-0000-4000-8000-00000000000c",
            first_seen_at=NOW, last_seen_at=NOW)
        session.add_all([self.kobo_device, self.koreader_device])
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
                            "config_kobo_sync_magic_shelves", False,
                            raising=False)
        monkeypatch.setattr(kobo_module, "get_download_url_for_book",
                            lambda *_args: "/download")
        monkeypatch.setattr(kobo_module, "sync_shelves",
                            lambda *_args, **_kwargs: None)
        monkeypatch.setattr(
            kobo_module, "create_book_entitlement",
            lambda book, archived=False: {"Id": str(book.id),
                                          "IsRemoved": archived},
        )
        monkeypatch.setattr(kobo_module, "get_metadata",
                            lambda book: {"Id": str(book.id)})
        # The sync token's delivery epoch is a random nonce; count instead.
        epochs = iter(range(1, 1000))
        monkeypatch.setattr(kobo_module.secrets, "token_hex",
                            lambda nbytes=32: "%0*x" % (2 * nbytes, next(epochs)))
        self.app = Flask(__name__)
        self.app.register_blueprint(annotations.annotations_bp)
        # The handler asks whether the app sits behind a proxy (as the parity test).
        self.app.wsgi_app = SimpleNamespace(is_proxied=True)

    def close(self):
        self.session.close()
        self.engine.dispose()

    def set_koreader_scope(self, mode, shelves=()):
        """The Devices page PATCH, as the account owner (CONTRACT.md)."""
        from werkzeug.exceptions import HTTPException
        path = "/api/annotations/devices/%s" % self.koreader_device.public_id
        body = {"book_scope": {"mode": mode,
                               "shelf_ids": [shelf.id for shelf in shelves],
                               "magic_shelf_ids": []}}
        with self.app.test_request_context(path, method="PATCH", json=body):
            endpoint, args = self.app.url_map.bind("localhost").match(
                path, method="PATCH")
            view = inspect.unwrap(self.app.view_functions[endpoint])
            try:
                result = view(**args)
            except HTTPException as error:
                return error.code
        return result[1] if isinstance(result, tuple) else result.status_code

    def _sync(self, token):
        SyncToken = self.kobo.SyncToken.SyncToken
        with self.app.test_request_context(
                "/v1/library/sync",
                headers={SyncToken.SYNC_TOKEN_HEADER: token}):
            g.annotation_origin_device_id = self.kobo_device.id
            response = self.kobo.HandleSyncRequest.__wrapped__()
        return {
            "status": response.status_code,
            "body": response.get_data(as_text=True),
            "token": response.headers.get(SyncToken.SYNC_TOKEN_HEADER),
            "continue": response.headers.get("x-kobo-sync"),
        }

    def add_scoped_kobo(self, mode, shelves=()):
        """A second Kobo on the account, given its own scope on the Devices page."""
        from werkzeug.exceptions import HTTPException
        device = ub.Device(
            user_id=self.user.id, kind="kobo", display_name="Kobo B", active=True,
            created_by="auto", public_id="00000000-0000-4000-8000-00000000000d",
            first_seen_at=NOW, last_seen_at=NOW)
        self.session.add(device)
        self.session.commit()
        path = "/api/annotations/devices/%s" % device.public_id
        body = {"book_scope": {"mode": mode,
                               "shelf_ids": [shelf.id for shelf in shelves],
                               "magic_shelf_ids": []}}
        with self.app.test_request_context(path, method="PATCH", json=body):
            endpoint, args = self.app.url_map.bind("localhost").match(
                path, method="PATCH")
            view = inspect.unwrap(self.app.view_functions[endpoint])
            try:
                result = view(**args)
            except HTTPException as error:
                return error.code
        return result[1] if isinstance(result, tuple) else result.status_code

    def fresh_device(self):
        """A new Kobo syncs until the server says it is done."""
        pages, token = [], ""
        for _page in range(20):
            page = self._sync(token)
            pages.append(page)
            token = page["token"]
            if page["continue"] != "continue":
                return pages
        raise AssertionError("Kobo sync never finished")


ACCOUNTS = [pytest.param(True, id="shelf-only"),
            pytest.param(False, id="whole-library")]


@pytest.fixture
def kobo_world(monkeypatch):
    worlds = []

    def build(**kwargs):
        built = _KoboWorld(monkeypatch, **kwargs)
        worlds.append(built)
        return built

    yield build
    for built in worlds:
        built.close()


def _recorded(key, observed):
    golden = json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}
    if RECORD:
        golden[key] = observed
        GOLDEN.write_text(json.dumps(golden, indent=1, sort_keys=True) + "\n")
    assert key in golden, "no recording on main for %s" % key
    return golden[key]


def _assert_same_as_main(key, observed):
    expected = _recorded(key, observed)
    assert len(observed) == len(expected)
    for page, (got, want) in enumerate(zip(observed, expected)):
        assert got == want, "Kobo response %d of %s differs from main" % (page, key)


@pytest.mark.parametrize("shelf_only", ACCOUNTS)
def test_an_unset_kobo_syncs_byte_for_byte_as_on_main(kobo_world, shelf_only):
    """GUARD: passes on main by construction; PR A and PR B keep it passing."""
    key = "shelf-only" if shelf_only else "whole-library"

    _assert_same_as_main(key + "/fresh", kobo_world(shelf_only=shelf_only).fresh_device())


@pytest.mark.parametrize("shelf_only", ACCOUNTS)
def test_a_scoped_koreader_on_the_account_leaves_the_unset_kobo_as_on_main(
        kobo_world, shelf_only):
    """The household case: the Kindle gets its own "Kindle" shelf (kobo_sync off)
    and the Kobo, left unset, still syncs exactly as main does."""
    key = "shelf-only" if shelf_only else "whole-library"

    fresh = kobo_world(shelf_only=shelf_only)
    assert fresh.set_koreader_scope("shelves", [fresh.kindle]) == 200
    _assert_same_as_main(key + "/fresh", fresh.fresh_device())


@pytest.mark.parametrize("shelf_only", ACCOUNTS)
def test_a_scoped_kobo_on_the_account_leaves_the_unset_kobo_as_on_main(
        kobo_world, shelf_only):
    """GUARD (PR B): another Kobo on the account picks its own shelf; the
    unset Kobo's ordinary sync is still byte for byte main's."""
    key = "shelf-only" if shelf_only else "whole-library"

    world = kobo_world(shelf_only=shelf_only)
    assert world.add_scoped_kobo("shelves", [world.kindle]) == 200
    _assert_same_as_main(key + "/fresh", world.fresh_device())
