# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""A two-Kobo account over the real Kobo state and sync handlers.

Shared by the Share my place PR 3a gate tests and the first-PUT clock tests.
One in-memory app.db with metadata.db attached, three KEPUB books, Kobo A and
Kobo B (both Share my place off), the real ``HandleStateRequest`` /
``HandleSyncRequest`` bodies past ``requires_kobo_auth`` and the real devices
API route for the toggle. Sync tokens are handed back as a Kobo does, so
pending pages are acknowledged inside the handler. Hardcover is recorded at
``kobo.push_reading_state_to_hardcover``.
"""

import inspect
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from flask import Flask, g
from sqlalchemy import create_engine, event, true
from sqlalchemy.orm import sessionmaker


USER_ID = 8009
READ_COLUMN_ID = 2144
EPOCH = "1970-01-01T00:00:00Z"


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _kts(clock):
    """A Kobo wire timestamp, whole seconds, as Nickel sends it."""
    return clock.strftime("%Y-%m-%dT%H:%M:%SZ")


def _naive(value):
    if value is not None and value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


@pytest.fixture(scope="module")
def read_column_class():
    """A real Calibre boolean custom column for the read-column checks."""
    from cps import db

    if READ_COLUMN_ID not in db.cc_classes:
        db.CalibreDB.setup_db_cc_classes([
            SimpleNamespace(id=READ_COLUMN_ID, datatype="bool"),
        ])
    return db.cc_classes[READ_COLUMN_ID]


# -- the world ----------------------------------------------------------------

@pytest.fixture
def kw(monkeypatch, read_column_class):
    from cps import annotations, db, helper, kobo, kobo_sync_status, ub
    from cps.services import kobo_position_reanchor
    import cps.progress_syncing.models  # noqa: F401  (registers kosync_progress)

    engine = create_engine("sqlite://")
    event.listen(engine, "connect", lambda connection, _record: connection.execute(
        "ATTACH DATABASE ':memory:' AS calibre"))
    db.Base.metadata.create_all(engine)
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    books = []
    for number in (1, 2, 3):
        modified = datetime(2026, 8, 1) + timedelta(seconds=number)
        books.append(db.Books(
            f"Book {number}", f"Book {number}", "Author", modified,
            db.Books.DEFAULT_PUBDATE, "1.0", modified, f"book-{number}", 0, [], [],
        ))
    session.add_all(books)
    session.flush()
    for book in books:
        book.uuid = f"00000000-0000-0000-0000-{book.id:012d}"
        session.add(db.Data(book.id, "KEPUB", 1, f"book-{book.id}"))
    session.add(ub.User(id=USER_ID, name="sharer", email="sharer@example.invalid",
                        role=0))
    kobo_a = ub.Device(user_id=USER_ID, kind="kobo", display_name="Kobo A",
                       active=True, created_by="auto")
    kobo_b = ub.Device(user_id=USER_ID, kind="kobo", display_name="Kobo B",
                       active=True, created_by="auto")
    session.add_all([kobo_a, kobo_b])
    session.commit()

    user = SimpleNamespace(id=USER_ID, name="sharer", kobo_only_shelves_sync=False,
                           role_download=lambda: True)
    fake_calibre_db = SimpleNamespace(
        session=session,
        reconnect_db=lambda *_a, **_k: None,
        refresh_for_new_data=lambda: None,
        common_filters=lambda **_k: true(),
        get_book=lambda book_id: session.query(db.Books).filter_by(
            id=book_id).one_or_none(),
        get_book_by_uuid_for_kobo=lambda book_uuid, enforce_policy=False: (
            session.query(db.Books).filter_by(uuid=book_uuid).one_or_none()),
    )
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(ub, "session_commit",
                        lambda *_a, **_k: session.commit() or True)
    monkeypatch.setattr(kobo, "calibre_db", fake_calibre_db)
    monkeypatch.setattr(helper, "calibre_db", fake_calibre_db)
    monkeypatch.setattr(kobo, "current_user", user)
    monkeypatch.setattr(kobo_sync_status, "current_user", user)
    monkeypatch.setattr(kobo.config, "config_kobo_proxy", False, raising=False)
    monkeypatch.setattr(kobo.config, "config_read_column", READ_COLUMN_ID,
                        raising=False)
    monkeypatch.setattr(kobo.config, "config_kobo_suppress_replayed_entitlements",
                        True, raising=False)
    monkeypatch.setattr(kobo.config, "config_kobo_sync_magic_shelves", False,
                        raising=False)
    monkeypatch.setattr(kobo, "get_download_url_for_book", lambda *_a: "/download")
    monkeypatch.setattr(kobo, "get_magic_shelf_book_ids_for_kobo",
                        lambda _user_id: (set(), True))
    monkeypatch.setattr(kobo, "get_magic_shelf_membership_added_at",
                        lambda _user_id: None)
    monkeypatch.setattr(kobo, "sync_shelves", lambda *_a, **_k: None)
    monkeypatch.setattr(kobo, "create_book_entitlement",
                        lambda book, archived=False: {"Id": str(book.id),
                                                      "IsRemoved": archived})
    monkeypatch.setattr(kobo, "get_metadata",
                        lambda book: {"EntitlementId": str(book.id)})
    # The books have no files on disk; re-anchoring has nothing to do here.
    monkeypatch.setattr(kobo_position_reanchor, "reanchor_missing_position",
                        lambda *_a, **_k: None)
    hardcover = []
    monkeypatch.setattr(kobo, "push_reading_state_to_hardcover",
                        lambda _user, book, percent: hardcover.append(
                            (book.id, percent)))
    monkeypatch.setattr(annotations, "current_user", SimpleNamespace(id=USER_ID))

    app = Flask(__name__)
    app.secret_key = "share-place-3a"
    app.wsgi_app = SimpleNamespace(is_proxied=True)
    app.register_blueprint(annotations.annotations_bp)
    header = kobo.SyncToken.SyncToken.SYNC_TOKEN_HEADER
    tokens = {}

    def sync(device):
        """One library/sync from ``device`` (None = unresolved Kobo), handing
        back the token from its previous response as Nickel does."""
        key = device.id if device is not None else None
        headers = {header: tokens[key]} if tokens.get(key) else {}
        with app.test_request_context("/v1/library/sync", headers=headers):
            if device is not None:
                g.annotation_origin_device_id = device.id
            response = kobo.HandleSyncRequest.__wrapped__()
        assert response.status_code == 200, response.get_data(as_text=True)
        tokens[key] = response.headers[header]
        session.expire_all()
        return response

    def frontier(device):
        key = device.id if device is not None else None
        return _naive(kobo.SyncToken.SyncToken.from_headers(
            {header: tokens[key]}).reading_state_last_modified)

    def settle(device):
        """Sync until a response carries nothing: the device holds everything
        the server has for it, and its last page is acknowledged."""
        for _ in range(4):
            if not sync(device).get_json():
                return
        raise AssertionError("sync never settled")

    def put(device, book, percent, clock, *, status="Reading", spent=7,
            location="default"):
        if location == "default":
            location = {"Value": f"kobo.{int(percent)}.1", "Type": "KoboSpan",
                        "Source": "OEBPS/chap.xhtml"}
        payload = {"ReadingStates": [{
            "LastModified": _kts(clock),
            "CurrentBookmark": {
                "ProgressPercent": percent,
                "ContentSourceProgressPercent": percent,
                "Location": location,
            },
            "Statistics": {"SpentReadingMinutes": spent,
                           "RemainingTimeMinutes": 100 - spent},
            "StatusInfo": {"Status": status},
        }]}
        with app.test_request_context(f"/v1/library/{book.uuid}/state",
                                      method="PUT", json=payload):
            if device is not None:
                g.annotation_origin_device_id = device.id
            response = kobo.HandleStateRequest.__wrapped__(book.uuid)
        session.expire_all()
        assert response.status_code == 200, response.get_data(as_text=True)
        body = response.get_json()
        assert body["RequestResult"] == "Success"
        return body

    def get(device, book):
        with app.test_request_context(f"/v1/library/{book.uuid}/state",
                                      method="GET"):
            if device is not None:
                g.annotation_origin_device_id = device.id
            response = kobo.HandleStateRequest.__wrapped__(book.uuid)
        session.expire_all()
        assert response.status_code == 200, response.get_data(as_text=True)
        body = response.get_json()
        assert isinstance(body, list) and len(body) == 1, body
        assert body[0]["EntitlementId"] == book.uuid
        return body[0]

    def share(device, on):
        path = f"/api/annotations/devices/{device.public_id}"
        with app.test_request_context(path, method="PATCH",
                                      json={"share_place": on}):
            endpoint, args = app.url_map.bind("localhost").match(path, method="PATCH")
            result = inspect.unwrap(app.view_functions[endpoint])(**args)
        status = result[1] if isinstance(result, tuple) else result.status_code
        assert status == 200
        session.expire_all()

    def seed_shared(book, percent, at, *, status=None):
        """The account's shared place as a checked Kobo (or today's path) left
        it, all clocks at ``at``."""
        status = ub.ReadBook.STATUS_IN_PROGRESS if status is None else status
        read = ub.ReadBook(user_id=USER_ID, book_id=book.id, read_status=status,
                           times_started_reading=1, last_modified=at)
        state = ub.KoboReadingState(user_id=USER_ID, book_id=book.id,
                                    last_modified=at, priority_timestamp=at)
        state.current_bookmark = ub.KoboBookmark(
            progress_percent=percent, content_source_progress_percent=percent,
            location_value=f"shared.{int(percent)}", location_type="KoboSpan",
            location_source="OEBPS/chap.xhtml", last_modified=at)
        state.statistics = ub.KoboStatistics(spent_reading_minutes=11,
                                             remaining_time_minutes=22,
                                             last_modified=at)
        read.kobo_reading_state = state
        session.add(read)
        session.commit()
        # The before_flush hook stamps the parent; restore the exact clocks
        # (only the parent is dirtied here, so the hook stays quiet).
        state.last_modified = at
        state.priority_timestamp = at
        session.commit()
        session.expire_all()

    def journal(device, book):
        session.expire_all()
        return session.query(ub.DeviceReadingPosition).filter_by(
            device_id=device.id, book_id=book.id).one_or_none()

    def shared(book):
        """Every shared carrier for (user, book), as plain values."""
        session.expire_all()
        read = session.query(ub.ReadBook).filter_by(
            user_id=USER_ID, book_id=book.id).one_or_none()
        state = session.query(ub.KoboReadingState).filter_by(
            user_id=USER_ID, book_id=book.id).one_or_none()
        mark = state.current_bookmark if state is not None else None
        stats = state.statistics if state is not None else None
        return {
            "read": None if read is None else (
                read.read_status, _naive(read.last_modified),
                read.times_started_reading, read.last_time_started_reading),
            "state": None if state is None else (
                _naive(state.last_modified), _naive(state.priority_timestamp)),
            "bookmark": None if mark is None else (
                mark.progress_percent, mark.content_source_progress_percent,
                mark.location_value, mark.location_type, mark.location_source,
                _naive(mark.last_modified)),
            "statistics": None if stats is None else (
                stats.spent_reading_minutes, stats.remaining_time_minutes,
                _naive(stats.last_modified)),
        }

    def counts():
        from cps.progress_syncing.models import KOSyncProgress
        session.expire_all()
        return {
            "ReadBook": session.query(ub.ReadBook).count(),
            "KoboReadingState": session.query(ub.KoboReadingState).count(),
            "KoboBookmark": session.query(ub.KoboBookmark).count(),
            "KoboStatistics": session.query(ub.KoboStatistics).count(),
            "kosync_progress": session.query(KOSyncProgress).count(),
        }

    def read_column(book):
        session.expire_all()
        return session.query(read_column_class).filter_by(book=book.id).all()

    library = tempfile.TemporaryDirectory(prefix="kobo-state-world-")

    def hold_current_kepub(device, *held):
        """``device`` completed a download of each book's current library
        KEPUB (real ``record_download``), so a span it reports, or is sent,
        addresses a file it provably holds (spec section 14c)."""
        import cps
        from cps import config
        from cps.services.kobo_post_download_restore import record_download
        monkeypatch.setattr(config, "get_book_path", lambda: library.name)
        monkeypatch.setattr(cps, "calibre_db", fake_calibre_db, raising=False)
        for book in held:
            folder = os.path.join(library.name, book.path)
            os.makedirs(folder, exist_ok=True)
            kepub = os.path.join(folder, f"book-{book.id}.kepub")
            if not os.path.exists(kepub):
                with open(kepub, "wb") as stream:
                    stream.write(b"PK\x05\x06" + bytes(18))  # an empty zip
            record_download(device_id=device.id, book_id=book.id, book_format="kepub",
                            log=logging.getLogger("kobo_state_world"))
        session.commit()
        session.expire_all()

    world = SimpleNamespace(
        a=kobo_a, b=kobo_b, books=books, session=session, kobo=kobo, ub=ub,
        sync=sync, settle=settle, frontier=frontier, put=put, get=get,
        share=share, seed_shared=seed_shared, journal=journal, shared=shared,
        counts=counts, read_column=read_column, hardcover=hardcover, tokens=tokens,
        hold_current_kepub=hold_current_kepub,
    )
    try:
        yield world
    finally:
        session.close()
        engine.dispose()
        library.cleanup()


def _states(response):
    """{book uuid: [ReadingState, ...]} for every ReadingState in a sync
    response, embedded in an entitlement or standalone."""
    found = {}
    for item in response.get_json():
        if "ChangedReadingState" in item:
            state = item["ChangedReadingState"]["ReadingState"]
        else:
            entitlement = item.get("NewEntitlement") or item.get("ChangedEntitlement")
            state = entitlement.get("ReadingState") if entitlement else None
        if state is not None:
            found.setdefault(state["EntitlementId"], []).append(state)
    return found


def _timestamps(value, path=""):
    """Every (path, value) of a key ending in LastModified/Timestamp."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key.endswith(("LastModified", "Timestamp")) and isinstance(child, str):
                yield f"{path}.{key}", child
            else:
                yield from _timestamps(child, f"{path}.{key}")
    elif isinstance(value, list):
        for child in value:
            yield from _timestamps(child, path)


EMPTY = {"read": None, "state": None, "bookmark": None, "statistics": None}


def second_after_receipts(kw, device, book):
    """The first whole client second after every receipt the server stored
    for ``book`` (``device``'s journal row and the shared bookmark).

    A report ordered at its receipt (a clock too fast to trust, or none) is
    ordered at a server time the test does not choose; a wall clock read
    before that PUT is not a safe base on a loaded worker, where the receipt
    can land seconds later. Date a report that must be newer from this.
    """
    from cps import ub
    kw.session.expire_all()
    stamps = []
    row = kw.journal(device, book)
    if row is not None:
        stamps += [row.server_modified_at, row.status_received_at]
    state = kw.session.query(ub.KoboReadingState).filter_by(
        user_id=USER_ID, book_id=book.id).one_or_none()
    if state is not None and state.current_bookmark is not None:
        stamps.append(state.current_bookmark.server_modified_at)
    stamps = [_naive(stamp) for stamp in stamps if stamp is not None]
    assert stamps, "no stored receipt for the book"
    return max(stamps).replace(microsecond=0) + timedelta(seconds=1)
