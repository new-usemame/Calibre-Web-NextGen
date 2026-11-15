# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Two Kobos on one account over the real Kobo sync, state and annotation routes.

Shared by the per-device scope PR B tests (Kobo half) and the sync-removal
tests (G2, G3, G4, G7, G8). Specs: state/kindle-kids/specs/
2026-10-12-per-device-scope.md and 2026-10-12-sync-removal.md.

One in-memory app.db with metadata.db attached and a real CalibreDB over it,
so hidden, archived and tag-restricted books go through the real
``common_filters``. Two Kobos (A and B) and a KOReader on user A's account;
another account (user B) exists so nothing leaks across accounts. The real
handlers run past their auth decorators with the decorator's identity on
``g``: ``HandleSyncRequest`` (each device hands back its own last token, so
pending pages are acknowledged inside the handler as a Kobo does),
``HandleStateRequest``, ``readingservices.handle_annotations`` (GET and
PATCH) and the devices API (``book_scope``, ``share_place``). A Kobo download
is recorded through ``kobo_post_download_restore.record_download``, the seam
the Kobo download route calls.

Rendering is stubbed exactly as in test_ereader_scope_kobo_parity.py: which
books are delivered or removed, and to which device, is what is under test.

Contract: state/kindle-kids/legs/device-scope-b/CONTRACT.md.
"""

import inspect
import json
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from flask import Flask, g
from sqlalchemy import create_engine, event, inspect as sa_inspect
from sqlalchemy.orm import sessionmaker

from cps import constants, db, ub

NOW = datetime(2026, 8, 28, 12, 0, 0)
FAR_FUTURE = datetime(2100, 1, 1)
EPOCH = "1970-01-01T00:00:00Z"

# The library. "To Read" is the account's e-reader shelf (kobo_sync on), the
# common case; "Kindle" is a shelf picked for one device only (kobo_sync off).
ALL_BOOKS = (1, 2, 3, 4, 5, 6)
TO_READ = (1, 2, 3, 4)
KINDLE = (2, 5)


def kts(clock):
    """A Kobo wire timestamp, whole seconds, as Nickel sends it."""
    return clock.strftime("%Y-%m-%dT%H:%M:%SZ")


def naive(value):
    if value is not None and value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _book(book_id):
    book = db.Books("Book %d" % book_id, "Book %d" % book_id, "Author",
                    NOW, NOW, "1.0", NOW, "book-%d" % book_id, 1, [], [])
    book.id = book_id
    book.uuid = "00000000-0000-4000-8000-%012d" % book_id
    return book


def entitlements(response):
    """[(kind, book id, IsRemoved)] for every entitlement in a sync response."""
    found = []
    for item in response.get_json() or []:
        for kind in ("NewEntitlement", "ChangedEntitlement"):
            if kind in item:
                entitlement = item[kind]["BookEntitlement"]
                found.append((kind, int(entitlement["Id"]),
                              bool(entitlement["IsRemoved"])))
    return found


def removed(responses):
    """Book ids a run of sync responses told the device to remove."""
    if not isinstance(responses, (list, tuple)):
        responses = [responses]
    return {book_id for response in responses
            for _kind, book_id, is_removed in entitlements(response) if is_removed}


def delivered(responses):
    """Book ids a run of sync responses handed the device (not removed)."""
    if not isinstance(responses, (list, tuple)):
        responses = [responses]
    return {book_id for response in responses
            for _kind, book_id, is_removed in entitlements(response)
            if not is_removed}


def _plain(row):
    return {column.key: getattr(row, column.key)
            for column in sa_inspect(row).mapper.column_attrs}


class KoboRemovalWorld:
    def __init__(self, monkeypatch, *, shelf_only=True):
        from cps import annotations, kobo as kobo_module, kobo_sync_status, helper
        import cps.readingservices as rs
        from cps.services import kobo_annotation_authority as authority
        from cps.services import kobo_position_reanchor

        self.kobo = kobo_module
        self.rs = rs
        self.monkeypatch = monkeypatch
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
        for book_id in ALL_BOOKS:
            session.add(_book(book_id))
            session.add(db.Data(book_id, "EPUB", 1, "book-%d" % book_id))

        self.user = user = ub.User(
            name="user-a", email="user-a@example.invalid", password="",
            default_language="all", denied_tags="secret",
            role=constants.ROLE_USER | constants.ROLE_DOWNLOAD,
            kobo_only_shelves_sync=1 if shelf_only else 0,
        )
        user.kobo_two_way_annotation_sync = True
        other = ub.User(name="user-b", email="user-b@example.invalid",
                        password="", default_language="all",
                        role=constants.ROLE_USER | constants.ROLE_DOWNLOAD)
        session.add_all([user, other])
        session.flush()
        self.to_read = ub.Shelf(name="To Read", user_id=user.id, is_public=0,
                                kobo_sync=True, uuid="shelf-to-read")
        self.kindle = ub.Shelf(name="Kindle", user_id=user.id, is_public=0,
                               kobo_sync=False, uuid="shelf-kindle")
        session.add_all([self.to_read, self.kindle])
        session.flush()
        for shelf, book_ids in ((self.to_read, TO_READ), (self.kindle, KINDLE)):
            for book_id in book_ids:
                self.shelve(shelf, book_id, at=NOW, commit=False)
        self.a = ub.Device(
            user_id=user.id, kind="kobo", display_name="Kobo A", active=True,
            created_by="auto", public_id="00000000-0000-4000-8000-0000000000a1",
            first_seen_at=NOW, last_seen_at=NOW)
        self.b = ub.Device(
            user_id=user.id, kind="kobo", display_name="Kobo B", active=True,
            created_by="auto", public_id="00000000-0000-4000-8000-0000000000b2",
            first_seen_at=NOW, last_seen_at=NOW)
        self.koreader = ub.Device(
            user_id=user.id, kind="koreader", display_name="Kindle", active=True,
            created_by="auto", public_id="00000000-0000-4000-8000-0000000000c3",
            first_seen_at=NOW, last_seen_at=NOW)
        session.add_all([self.a, self.b, self.koreader])
        session.commit()

        cdb = self.cdb = object.__new__(db.CalibreDB)
        cdb.session = session
        cdb.config = SimpleNamespace(config_restricted_column=0)
        cdb.reconnect_db = lambda *_args, **_kwargs: None
        cdb.refresh_for_new_data = lambda: None
        cdb.ensure_session = lambda: None

        monkeypatch.setattr(db.ub, "session", session)
        monkeypatch.setattr(db, "current_user", user)
        monkeypatch.setattr(ub, "session", session)
        monkeypatch.setattr(ub, "session_commit",
                            lambda *_args, **_kwargs: session.commit() or True)
        monkeypatch.setattr(kobo_module, "calibre_db", cdb)
        monkeypatch.setattr(helper, "calibre_db", cdb)
        import cps
        monkeypatch.setattr(cps, "calibre_db", cdb, raising=False)
        monkeypatch.setattr(rs, "calibre_db", cdb, raising=False)
        for module in (kobo_module, kobo_sync_status, annotations, rs):
            monkeypatch.setattr(module, "current_user", user)
        monkeypatch.setattr(kobo_module.config, "config_kobo_proxy", False,
                            raising=False)
        monkeypatch.setattr(kobo_module.config, "config_read_column", 0,
                            raising=False)
        # No book files exist; a path that holds nothing.
        monkeypatch.setattr(kobo_module.config, "config_calibre_split", False,
                            raising=False)
        monkeypatch.setattr(kobo_module.config, "config_calibre_dir",
                            "/nonexistent-kobo-removal-library", raising=False)
        monkeypatch.setattr(kobo_module.config,
                            "config_kobo_sync_magic_shelves", False,
                            raising=False)
        monkeypatch.setattr(kobo_module.config,
                            "config_kobo_suppress_replayed_entitlements", True,
                            raising=False)
        monkeypatch.setattr(rs.config, "config_kobo_two_way_annotation_sync",
                            True, raising=False)
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
        monkeypatch.setattr(kobo_position_reanchor, "reanchor_missing_position",
                            lambda *_a, **_k: None)
        monkeypatch.setattr(kobo_module, "push_reading_state_to_hardcover",
                            lambda *_a, **_k: None)
        # The sync token's delivery epoch is a random nonce; count instead.
        epochs = iter(range(1, 100000))
        monkeypatch.setattr(kobo_module.secrets, "token_hex",
                            lambda nbytes=32: "%0*x" % (2 * nbytes, next(epochs)))
        # Reading services: CWNG answers; nothing reaches the Kobo cloud.
        monkeypatch.setattr("cps.services.kobo_annotation_stage0.schema_capable",
                            lambda _engine: True)
        monkeypatch.setattr(rs, "_begin_exchange_capture", lambda *_a, **_k: None)
        monkeypatch.setattr(rs, "_stage_patch_for_recovery", lambda *_a, **_k: None)
        monkeypatch.setattr(
            rs, "resolve_entitlement_ownership",
            lambda content_id: session.query(db.Books).filter(
                db.Books.uuid == content_id).one_or_none())
        monkeypatch.setattr(
            "cps.services.annotation_sync.dispatch_annotation_sync",
            lambda *_a, **_k: True)

        def no_cloud(**_kwargs):
            raise AssertionError("this request must be answered by CWNG")
        monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", no_cloud)
        authority.reset_skip_log_for_testing()

        self.app = Flask(__name__)
        self.app.secret_key = "kobo-removal-world"
        self.app.wsgi_app = SimpleNamespace(is_proxied=True)
        self.app.register_blueprint(annotations.annotations_bp)
        self.header = kobo_module.SyncToken.SyncToken.SYNC_TOKEN_HEADER
        self.tokens = {}

    def close(self):
        self.session.close()
        self.engine.dispose()

    # -- library changes ------------------------------------------------------

    def book(self, book_id):
        return self.session.query(db.Books).filter(db.Books.id == book_id).one()

    def shelve(self, shelf, book_id, *, at=None, commit=True):
        order = 1 + self.session.query(ub.BookShelf).filter(
            ub.BookShelf.shelf == shelf.id).count()
        link = ub.BookShelf(shelf=shelf.id, book_id=book_id, order=order,
                            date_added=at or utcnow())
        link.ub_shelf = shelf
        self.session.add(link)
        if commit:
            self.session.commit()

    def unshelve(self, shelf, book_id):
        self.session.query(ub.BookShelf).filter(
            ub.BookShelf.shelf == shelf.id,
            ub.BookShelf.book_id == book_id).delete()
        self.session.commit()

    def hide(self, book_id):
        self.session.add(ub.UserHiddenBook(user_id=self.user.id, book_id=book_id))
        self.session.commit()

    def restrict(self, book_id):
        """Give the book a tag the user may not see."""
        book = self.book(book_id)
        book.tags.append(self.secret)
        self.session.commit()

    def set_scope(self, device, mode, shelves=()):
        """The Devices page PATCH, as the account owner. Returns the status."""
        return self._devices_patch(device, {"book_scope": {
            "mode": mode, "shelf_ids": [shelf.id for shelf in shelves],
            "magic_shelf_ids": []}})

    def share(self, device, on):
        assert self._devices_patch(device, {"share_place": bool(on)}) == 200

    def _devices_patch(self, device, body):
        from werkzeug.exceptions import HTTPException
        path = "/api/annotations/devices/%s" % device.public_id
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

    # -- Kobo sync --------------------------------------------------------------

    def sync(self, device, token=None):
        """One library/sync from ``device``, handing back the token from its
        own previous response as Nickel does (or ``token`` when given)."""
        if token is None:
            token = self.tokens.get(device.id)
        headers = {self.header: token} if token else {}
        with self.app.test_request_context("/v1/library/sync", headers=headers):
            g.annotation_origin_device_id = device.id
            response = self.kobo.HandleSyncRequest.__wrapped__()
        assert response.status_code == 200, response.get_data(as_text=True)
        self.tokens[device.id] = response.headers[self.header]
        self.session.expire_all()
        return response

    def settle(self, device):
        """Sync until a response carries nothing: every page the device was
        sent is acknowledged. Returns every response, the empty one last."""
        responses = []
        for _ in range(12):
            response = self.sync(device)
            responses.append(response)
            if (not response.get_json()
                    and response.headers.get("x-kobo-sync") != "continue"):
                return responses
        raise AssertionError("Kobo sync never settled")

    def old_cursor(self, device):
        """A token from a Kobo that synced long ago and holds everything up to
        now: every cursor at its far end except the one under test."""
        return self.kobo.SyncToken.SyncToken(
            books_last_created=FAR_FUTURE,
            books_last_modified=FAR_FUTURE,
            archive_last_modified=FAR_FUTURE,
            reading_state_last_modified=FAR_FUTURE,
            books_last_id=max(ALL_BOOKS),
        ).build_sync_token()

    # -- reading state ---------------------------------------------------------

    def put_state(self, device, book_id, percent, clock, *, status="Reading"):
        book = self.book(book_id)
        payload = {"ReadingStates": [{
            "LastModified": kts(clock),
            "CurrentBookmark": {
                "ProgressPercent": percent,
                "ContentSourceProgressPercent": percent,
                "Location": {"Value": "kobo.%d.1" % int(percent),
                             "Type": "KoboSpan", "Source": "OEBPS/chap.xhtml"},
            },
            "Statistics": {"SpentReadingMinutes": 7, "RemainingTimeMinutes": 93},
            "StatusInfo": {"Status": status},
        }]}
        with self.app.test_request_context("/v1/library/%s/state" % book.uuid,
                                           method="PUT", json=payload):
            g.annotation_origin_device_id = device.id
            response = self.kobo.HandleStateRequest.__wrapped__(book.uuid)
        self.session.expire_all()
        assert response.status_code == 200, response.get_data(as_text=True)
        return response.get_json()

    def get_state(self, device, book_id):
        book = self.book(book_id)
        with self.app.test_request_context("/v1/library/%s/state" % book.uuid,
                                           method="GET"):
            g.annotation_origin_device_id = device.id
            response = self.kobo.HandleStateRequest.__wrapped__(book.uuid)
        self.session.expire_all()
        assert response.status_code == 200, response.get_data(as_text=True)
        body = response.get_json()
        assert isinstance(body, list) and len(body) == 1, body
        return body[0]

    def seed_shared(self, book_id, percent, at):
        """The account's shared place, every clock at ``at``."""
        read = ub.ReadBook(user_id=self.user.id, book_id=book_id,
                           read_status=ub.ReadBook.STATUS_IN_PROGRESS,
                           times_started_reading=1, last_modified=at)
        state = ub.KoboReadingState(user_id=self.user.id, book_id=book_id,
                                    last_modified=at, priority_timestamp=at)
        state.current_bookmark = ub.KoboBookmark(
            progress_percent=percent, content_source_progress_percent=percent,
            location_value="shared.%d" % int(percent), location_type="KoboSpan",
            location_source="OEBPS/chap.xhtml", last_modified=at)
        state.statistics = ub.KoboStatistics(spent_reading_minutes=11,
                                             remaining_time_minutes=22,
                                             last_modified=at)
        read.kobo_reading_state = state
        self.session.add(read)
        self.session.commit()
        state.last_modified = at
        state.priority_timestamp = at
        self.session.commit()
        self.session.expire_all()

    def journal(self, device, book_id):
        self.session.expire_all()
        return self.session.query(ub.DeviceReadingPosition).filter_by(
            device_id=device.id, book_id=book_id).one_or_none()

    # -- annotations -----------------------------------------------------------

    def make_authoritative(self, book_id):
        """CWNG is this book's annotation authority (the usual steady state)."""
        state = ub.KoboAnnotationBookState(
            user_id=self.user.id, book_id=book_id, content_id=self.book(book_id).uuid,
            authority_status="authoritative", authority_revision=1,
            generation_id="00000000-0000-0000-0000-%012d" % book_id,
            opaque_content_status="absent",
            seeded_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        )
        state.ever_authoritative = True
        self.session.add(state)
        self.session.commit()

    def add_annotation(self, book_id, annotation_id, origin, *, source="kobo",
                       note=None):
        uuid = self.book(book_id).uuid
        row = ub.Annotation(
            user_id=self.user.id, book_id=book_id, annotation_id=annotation_id,
            source=source, origin_device_id=None if origin is None else origin.id,
            annotation_type="note" if note else "highlight",
            highlighted_text="passage " + annotation_id, highlight_color="#F6F3B3",
            note_text=note, content_id="%s!!OEBPS/chap.xhtml" % uuid,
            chapter_progress=0.25, start_container_path="span#kobo\\.3\\.1",
            end_container_path="span#kobo\\.3\\.1", start_offset=0, end_offset=12,
            context_string="", client_modified_at=datetime(2026, 10, 1, 8, 0, 0),
            hidden=False, content_revision=1,
        )
        self.session.add(row)
        self.session.commit()
        return row

    def mark_delivered(self, device, annotation_row):
        """Delivery telemetry says the device already holds this row."""
        self.session.add(ub.AnnotationDeviceState(
            annotation_id=annotation_row.id, device_id=device.id, desired=True,
            delivery_status="delivered", first_seen_revision=1,
            last_delivered_revision=1, last_ack_revision=1,
            last_seen_present_at=datetime(2026, 10, 1, 9, 0, 0)))
        self.session.commit()

    def get_annotations(self, device, book_id):
        """The Kobo annotation GET; ``device`` None is an unresolved Kobo."""
        uuid = self.book(book_id).uuid
        with self.app.test_request_context(
                "/api/v3/content/%s/annotations?limit=100" % uuid):
            if device is not None:
                g.annotation_origin_device_id = device.id
            response = self.rs.handle_annotations.__wrapped__(uuid)
        self.session.expire_all()
        return response

    def annotation_ids(self, device, book_id):
        response = self.get_annotations(device, book_id)
        assert response.status_code == 200, response.get_data(as_text=True)
        payload = json.loads(response.get_data())
        return {row["id"] for row in payload["annotations"]}

    def upload_annotation(self, device, book_id, annotation_id):
        """The device uploads one highlight (the Kobo annotation PATCH)."""
        uuid = self.book(book_id).uuid
        payload = {"updatedAnnotations": [{
            "id": annotation_id, "type": "highlight",
            "highlightedText": "uploaded " + annotation_id,
            "highlightColor": "#F6F3B3",
            "clientLastModifiedUtc": kts(utcnow()),
            "location": {"span": {
                "chapterFilename": "OEBPS/chap.xhtml", "chapterProgress": 0.5,
                "chapterTitle": "One", "startPath": "span#kobo\\.4\\.1",
                "startChar": 0, "endPath": "span#kobo\\.4\\.1", "endChar": 9}},
        }]}
        with self.app.test_request_context(
                "/api/v3/content/%s/annotations" % uuid, method="PATCH",
                json=payload):
            g.annotation_origin_device_id = device.id
            g.annotation_upload_device_id = device.id
            response = self.rs.handle_annotations.__wrapped__(uuid)
        self.session.expire_all()
        assert response.status_code < 300, response.get_data(as_text=True)
        return response

    # -- downloads ---------------------------------------------------------------

    def download(self, device, book_id):
        """The device downloads the book's file (the Kobo download route's
        seam, ``record_download``)."""
        from cps.services.kobo_post_download_restore import record_download
        row = record_download(device_id=device.id, book_id=book_id,
                              book_format="kepub",
                              log=logging.getLogger("kobo_removal_world"))
        assert row is not None
        self.session.expire_all()

    # -- evidence -----------------------------------------------------------------

    def device_rows(self, device):
        """Every app.db row that belongs to ``device``: every table with a
        ``device_id`` column, plus annotations it originated, as plain values."""
        self.session.expire_all()
        rows = {}
        for mapper in ub.Base.registry.mappers:
            model = mapper.class_
            table = mapper.local_table
            if "device_id" in table.c:
                found = self.session.query(model).filter(
                    table.c.device_id == device.id).all()
            elif model is ub.Annotation:
                found = self.session.query(model).filter(
                    ub.Annotation.origin_device_id == device.id).all()
            else:
                continue
            rows[table.name] = sorted((_plain(row) for row in found),
                                      key=lambda value: repr(sorted(value.items())))
        return rows

    def synced_marker(self, book_id):
        self.session.expire_all()
        return self.session.query(ub.KoboSyncedBooks).filter_by(
            user_id=self.user.id, book_id=book_id).one_or_none() is not None

    def ledger(self, device):
        self.session.expire_all()
        return {row.book_id for row in self.session.query(
            ub.KoboDeviceBookEntitlement).filter_by(device_id=device.id)}


def observation(row):
    """A journal row's place and status observation with its clocks."""
    assert row is not None, "the device's own place row is gone"
    return {
        "location_value": row.location_value,
        "location_type": row.location_type,
        "location_source": row.location_source,
        "progress_percent": row.progress_percent,
        "content_source_progress_percent": row.content_source_progress_percent,
        "client_modified_at": naive(row.client_modified_at),
        "server_modified_at": naive(row.server_modified_at),
        "reading_status": row.reading_status,
        "status_modified_at": naive(row.status_modified_at),
        "status_received_at": naive(row.status_received_at),
    }


def an_hour_ago():
    return utcnow() - timedelta(hours=1)
