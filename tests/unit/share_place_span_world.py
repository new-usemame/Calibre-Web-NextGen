# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""One account with KOReaders and Kobos on a real book, for "Share my place"
PR 3b (exact span for checked Kobos).

The book is ``alice-pg11``: its library EPUB and the KEPUB CWNG serves a Kobo
for it (the ``kepub_alignment`` ground-truth fixtures) are on disk in the
library. KOReader pushes go through the real kosync blueprint over HTTP with
real Basic auth (``LibraryWorld``); Kobo GET /state, PUT /state and
library/sync go through the real handler bodies past ``requires_kobo_auth``,
with the Kobo named by ``g.annotation_origin_device_id`` as that decorator
sets it. The toggle goes through the real devices API route.
"""

import inspect
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from flask import g
from sqlalchemy import true

from cps import ub
from cps.progress_syncing.models import BookFormatChecksum
from cps.services import device_registry
from cps.services.koreader_position import file_digest
from tests.unit.koreader_library_world import LibraryWorld
from tests.unit.kobo_state_world import _kts, _states  # noqa: F401  (re-export)

FIXTURES = (os.path.dirname(os.path.abspath(__file__))
            + "/../fixtures/koreader_xpointer")
EPUB_FIXTURE = os.path.join(FIXTURES, "alice-pg11.epub")
KEPUB_FIXTURE = os.path.join(FIXTURES, "alice-pg11.kepub.epub")
BOOK = 1
OTHER_DIGEST = "fedcba9876543210fedcba9876543210"  # another file of the book


def span_rows():
    """Rows of (KEPUB source, Kobo span, the XPointer KOReader reports for
    the same word in the EPUB), in reading order."""
    with open(os.path.join(FIXTURES, "alice-pg11.kobo-spans.json"),
              encoding="utf-8") as stream:
        return json.load(stream)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def after_now(seconds=2):
    """A reader's clock a moment after everything the server has received so
    far (KOSync clocks are whole seconds; a report received this second has
    a receipt later than this second's start)."""
    return _now() + timedelta(seconds=seconds)


def location_of(bookmark):
    """The Kobo Location a CurrentBookmark carries, or None."""
    if not bookmark:
        return None
    location = bookmark.get("Location")
    if not location or not location.get("Value"):
        return None
    return {key: location.get(key) for key in ("Source", "Type", "Value")}


def span_location(source, span):
    return {"Source": source, "Type": "KoboSpan", "Value": span}


@pytest.fixture
def sw(monkeypatch, tmp_path):
    from cps import annotations, kobo, kobo_sync_status
    from cps.services import kobo_position_reanchor

    world = LibraryWorld(monkeypatch, tmp_path)
    world.app.register_blueprint(annotations.annotations_bp)
    reader = world.reader = world.add_user("reader")
    book = world.add_book(BOOK, "Alice", formats=("EPUB", "KEPUB"))
    world.epub = str(world.book_file(BOOK, "EPUB"))
    world.kepub = str(world.book_file(BOOK, "KEPUB"))
    shutil.copyfile(EPUB_FIXTURE, world.epub)
    shutil.copyfile(KEPUB_FIXTURE, world.kepub)
    world.digest = file_digest(world.epub)
    for digest in (world.digest, OTHER_DIGEST):
        world.session.add(BookFormatChecksum(book=BOOK, format="EPUB", checksum=digest))
    world.session.commit()
    world.book = book

    monkeypatch.setattr(annotations, "current_user", SimpleNamespace(id=reader.id))
    monkeypatch.setattr(world.kosync, "push_reading_state_to_hardcover", lambda *_a: None)

    # -- the Kobo half: the real handlers, past requires_kobo_auth ------------
    session = world.session
    from cps import db
    fake_calibre_db = SimpleNamespace(
        session=session,
        reconnect_db=lambda *_a, **_k: None,
        refresh_for_new_data=lambda: None,
        common_filters=lambda **_k: true(),
        get_book=lambda book_id: session.query(db.Books).filter_by(id=book_id).one_or_none(),
        get_book_by_uuid_for_kobo=lambda book_uuid, enforce_policy=False: (
            session.query(db.Books).filter_by(uuid=book_uuid).one_or_none()),
    )
    user = SimpleNamespace(id=reader.id, name="reader", kobo_only_shelves_sync=False,
                           role_download=lambda: True)
    monkeypatch.setattr(kobo, "calibre_db", fake_calibre_db)
    monkeypatch.setattr(kobo, "current_user", user)
    monkeypatch.setattr(kobo_sync_status, "current_user", user)
    for name, value in (("config_kobo_proxy", False),
                        ("config_kobo_suppress_replayed_entitlements", True),
                        ("config_kobo_sync_magic_shelves", False)):
        monkeypatch.setattr(kobo.config, name, value, raising=False)
    monkeypatch.setattr(kobo, "get_download_url_for_book", lambda *_a: "/download")
    monkeypatch.setattr(kobo, "get_magic_shelf_book_ids_for_kobo",
                        lambda _user_id: (set(), True))
    monkeypatch.setattr(kobo, "get_magic_shelf_membership_added_at", lambda _user_id: None)
    monkeypatch.setattr(kobo, "sync_shelves", lambda *_a, **_k: None)
    monkeypatch.setattr(kobo, "create_book_entitlement",
                        lambda book, archived=False: {"Id": str(book.id),
                                                      "IsRemoved": archived})
    monkeypatch.setattr(kobo, "get_metadata", lambda book: {"EntitlementId": str(book.id)})
    monkeypatch.setattr(kobo, "push_reading_state_to_hardcover", lambda *_a: None)
    # A Kobo whose place is missing is re-anchored by a separate job; these
    # tests are about the place KOReader shares, not that job.
    monkeypatch.setattr(kobo_position_reanchor, "reanchor_missing_position",
                        lambda *_a, **_k: None)

    header = kobo.SyncToken.SyncToken.SYNC_TOKEN_HEADER
    tokens = {}

    def kobo_device(name, *, checked):
        device = ub.Device(user_id=reader.id, kind="kobo", display_name=name,
                           active=True, created_by="auto")
        session.add(device)
        session.commit()
        if checked:
            world.share(device, True)
        return device

    def share(device, on):
        path = f"/api/annotations/devices/{device.public_id}"
        app = world.app
        with app.test_request_context(path, method="PATCH", json={"share_place": on}):
            endpoint, args = app.url_map.bind("localhost").match(path, method="PATCH")
            result = inspect.unwrap(app.view_functions[endpoint])(**args)
        status = result[1] if isinstance(result, tuple) else result.status_code
        assert status == 200
        session.expire_all()

    def kepub_written():
        return datetime.fromtimestamp(os.stat(world.kepub).st_mtime, timezone.utc).replace(
            tzinfo=None)

    def download(device, *, book_format="kepub", at=None):
        """``device`` downloaded the book as ``book_format`` at ``at`` (a minute
        after the library KEPUB was written when omitted)."""
        at = kepub_written() + timedelta(minutes=1) if at is None else at
        row = session.query(ub.KoboDeviceBookDownload).filter_by(
            device_id=device.id, book_id=BOOK).one_or_none()
        if row is None:
            row = ub.KoboDeviceBookDownload(device_id=device.id, book_id=BOOK)
            session.add(row)
        row.book_format, row.downloaded_at = book_format, at
        session.commit()

    def get(device):
        with world.app.test_request_context(f"/v1/library/{book.uuid}/state", method="GET"):
            g.annotation_origin_device_id = device.id
            response = kobo.HandleStateRequest.__wrapped__(book.uuid)
        session.expire_all()
        assert response.status_code == 200, response.get_data(as_text=True)
        body = response.get_json()
        assert isinstance(body, list) and len(body) == 1, body
        return body[0]

    def put(device, source, span, percent, clock, *, status="Reading"):
        """A Kobo's PUT /state; ``source=None`` sends a bookmark with no
        Location (what Nickel closes at when it opened at the cover)."""
        bookmark = {"ProgressPercent": percent, "ContentSourceProgressPercent": percent}
        if source is not None:
            bookmark["Location"] = span_location(source, span)
        payload = {"ReadingStates": [{
            "LastModified": _kts(clock),
            "CurrentBookmark": bookmark,
            "Statistics": {"SpentReadingMinutes": 7, "RemainingTimeMinutes": 93},
            "StatusInfo": {"Status": status},
        }]}
        with world.app.test_request_context(f"/v1/library/{book.uuid}/state",
                                            method="PUT", json=payload):
            g.annotation_origin_device_id = device.id
            response = kobo.HandleStateRequest.__wrapped__(book.uuid)
        session.expire_all()
        assert response.status_code == 200, response.get_data(as_text=True)
        assert response.get_json()["RequestResult"] == "Success"

    def sync(device):
        """One library/sync, handing back the previous token as Nickel does."""
        headers = {header: tokens[device.id]} if tokens.get(device.id) else {}
        # The sync handler reads ``wsgi_app.is_proxied``; the KOReader half
        # needs the real wsgi_app for HTTP, so stand it in only for this call.
        real_wsgi_app, world.app.wsgi_app = world.app.wsgi_app, SimpleNamespace(is_proxied=True)
        try:
            with world.app.test_request_context("/v1/library/sync", headers=headers):
                g.annotation_origin_device_id = device.id
                response = kobo.HandleSyncRequest.__wrapped__()
        finally:
            world.app.wsgi_app = real_wsgi_app
        assert response.status_code == 200, response.get_data(as_text=True)
        tokens[device.id] = response.headers[header]
        session.expire_all()
        return response

    def settle(device):
        for _ in range(4):
            if not sync(device).get_json():
                return
        raise AssertionError("sync never settled")

    def synced_location(device):
        """The Location of the book's last ReadingState in ``device``'s next
        sync (None when that state has none); fails if no state is sent."""
        states = _states(sync(device)).get(book.uuid)
        assert states, "the next sync sent no reading state for the book"
        return location_of(states[-1].get("CurrentBookmark"))

    # -- the KOReader half: the real kosync blueprint over HTTP ---------------

    def koreader(raw_id, name, *, checked):
        internal = device_registry.register_koreader_device_best_effort(
            user_id=reader.id, device_id=raw_id, device_name=name,
            secret_key=world.app.secret_key,
        )
        assert internal is not None
        device = session.get(ub.Device, internal)
        if checked:
            share(device, True)
        session.expire_all()
        return device

    def push(xpointer, percent, *, raw_id="koreader-1", name="KOReader",
             client_at=None, document=None, identified=True):
        headers = LibraryWorld.basic("reader")
        headers["Accept"] = "application/vnd.koreader.v1+json"
        if identified:
            headers["X-CWNG-Device-ID"] = raw_id
            headers["X-CWNG-Device-Name"] = name
        client_at = after_now() if client_at is None else client_at
        response = world.client.put("/kosync/syncs/progress", json={
            "document": document or world.digest, "progress": xpointer,
            "percentage": percent / 100.0, "device": name, "device_id": raw_id,
            "timestamp": int(client_at.replace(tzinfo=timezone.utc).timestamp()),
        }, headers=headers)
        assert response.status_code == 200, response.get_data(as_text=True)
        session.expire_all()

    def pull(*, raw_id="koreader-1", name="KOReader"):
        """KOReader's GET of the shared place: (percent, progress)."""
        headers = LibraryWorld.basic("reader")
        headers["Accept"] = "application/vnd.koreader.v1+json"
        headers["X-CWNG-Device-ID"] = raw_id
        headers["X-CWNG-Device-Name"] = name
        response = world.client.get(f"/kosync/syncs/progress/{world.digest}",
                                    query_string={"position_kinds": "locator,percentage"},
                                    headers=headers)
        assert response.status_code == 200, response.get_data(as_text=True)
        session.expire_all()
        body = response.get_json()
        return round(float(body["percentage"]) * 100, 3), body.get("progress")

    world.pull = pull
    world.kobo_device, world.share, world.download = kobo_device, share, download
    world.kepub_written = kepub_written
    world.get, world.put, world.sync, world.settle = get, put, sync, settle
    world.synced_location, world.koreader, world.push = synced_location, koreader, push
    try:
        yield world
    finally:
        world.close()
