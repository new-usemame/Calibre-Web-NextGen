# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""A held app.db writer must not turn a finished sync response into a 500 (#2562).

The sync stages a replayable copy of its response as its final write. When another
connection holds the app.db write lock at that moment — on the reporting install a
library task sweeping the library, reproduced here by holding ``BEGIN IMMEDIATE``
from a second connection to the same database — that write used to fail the whole
request with ``database is locked``, discarding a response that had already been
built.

The staged page only makes the *next* sync a delta, so losing it is safe: with no
stored page the next request skips the replay block in ``HandleSyncRequest`` and
does a fresh full sync. The handler now treats the staging write and its commit as
best-effort: it retries briefly, and if the writer is still held it rolls back,
logs the degraded delivery and returns the response.
"""

import sqlite3
from datetime import datetime
from types import SimpleNamespace

import pytest
from flask import Flask, g
from sqlalchemy import create_engine, event, true
from sqlalchemy.orm import sessionmaker


pytestmark = pytest.mark.unit


@pytest.fixture
def held_writer_sync(monkeypatch, tmp_path):
    """Drive the real handler over a file-backed app.db a second connection can lock."""
    from cps import db, kobo, kobo_sync_status, ub

    db_path = tmp_path / "app.db"
    engine = create_engine("sqlite:///" + str(db_path))
    event.listen(
        engine,
        "connect",
        lambda connection, _record: connection.execute(
            "ATTACH DATABASE ':memory:' AS calibre"
        ),
    )
    db.Base.metadata.create_all(engine)
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    book = db.Books(
        "Held Writer",
        "Held Writer",
        "Author",
        datetime(2026, 1, 1),
        db.Books.DEFAULT_PUBDATE,
        "1.0",
        datetime(2026, 1, 1, 12, 0, 0),
        "book-1",
        0,
        [],
        [],
    )
    session.add(book)
    session.flush()
    book.uuid = "00000000-0000-0000-0000-000000000001"
    session.add(db.Data(book.id, "EPUB", 1, "book-1"))

    device = ub.Device(
        user_id=2562,
        kind="kobo",
        display_name="Held Writer Kobo",
        model="Kobo",
        active=True,
        created_by="auto",
    )
    session.add(device)
    session.commit()

    user = SimpleNamespace(
        id=2562,
        name="issue-2562",
        kobo_only_shelves_sync=False,
        role_download=lambda: True,
    )
    fake_calibre_db = SimpleNamespace(
        session=session,
        reconnect_db=lambda *_args, **_kwargs: None,
        refresh_for_new_data=lambda: None,
        common_filters=lambda **_kwargs: true(),
        get_book=lambda book_id: session.query(db.Books).filter_by(
            id=book_id
        ).one_or_none(),
    )

    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(
        ub, "session_commit", lambda *_args, **_kwargs: session.commit() or True
    )
    monkeypatch.setattr(kobo, "calibre_db", fake_calibre_db)
    monkeypatch.setattr(kobo, "current_user", user)
    monkeypatch.setattr(kobo_sync_status, "current_user", user)
    monkeypatch.setattr(kobo.config, "config_kobo_proxy", False, raising=False)
    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True, raising=False
    )
    monkeypatch.setattr(
        kobo.config, "config_kobo_sync_magic_shelves", False, raising=False
    )
    monkeypatch.setattr(kobo, "get_download_url_for_book", lambda *_args: "/download")
    monkeypatch.setattr(
        kobo, "get_magic_shelf_book_ids_for_kobo", lambda _user_id: (set(), True)
    )
    monkeypatch.setattr(
        kobo, "get_magic_shelf_membership_added_at", lambda _user_id: None
    )
    monkeypatch.setattr(kobo, "sync_shelves", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        kobo,
        "create_book_entitlement",
        lambda book, archived=False: {
            "Id": str(book.id),
            "IsRemoved": archived,
            "LastModified": book.last_modified.isoformat(),
        },
    )
    monkeypatch.setattr(
        kobo, "get_metadata", lambda book: {"EntitlementId": str(book.id)}
    )

    # Both are module constants the helper reads when it runs, so the best-effort
    # window can be shortened for the test instead of sleeping for seconds.
    monkeypatch.setattr(kobo, "_PENDING_STAGE_RETRY_BUDGET_S", 0.3, raising=False)
    monkeypatch.setattr(kobo, "_PENDING_STAGE_ATTEMPT_TIMEOUT_MS", 100, raising=False)

    app = Flask(__name__)
    app.secret_key = "issue-2562-test-key"
    app.wsgi_app = SimpleNamespace(is_proxied=True)

    def sync(token=None):
        headers = {
            "x-kobo-deviceid": "a" * 64,
            "x-kobo-devicemodel": device.model,
        }
        if token:
            headers["x-kobo-synctoken"] = token
        with app.test_request_context("/v1/library/sync", headers=headers):
            g.annotation_origin_device_id = device.id
            return kobo.HandleSyncRequest.__wrapped__()

    def hold_writer():
        """Hold the app.db write lock from a second connection, as a task would."""
        locker = sqlite3.connect(str(db_path), isolation_level=None, timeout=0)
        locker.execute("BEGIN IMMEDIATE")
        return locker

    def sync_with_writer_held_at_staging(token=None):
        """Hold the writer exactly while the sync's final staging write runs.

        This change is about the sync's LAST write: persisting the replayable copy
        of a response that is already built. Holding the writer for the whole
        request fails an earlier bookkeeping write first
        (``device_reading_position``) — see the marked test at the bottom, which
        documents that remaining exposure rather than hiding it.
        """
        original = kobo_sync_status.stage_pending_sync_page

        def locked_stage(*args, **kwargs):
            locker = sqlite3.connect(str(db_path), isolation_level=None, timeout=0)
            locker.execute("BEGIN IMMEDIATE")
            try:
                return original(*args, **kwargs)
            finally:
                locker.rollback()
                locker.close()

        with monkeypatch.context() as patch_context:
            patch_context.setattr(
                kobo_sync_status, "stage_pending_sync_page", locked_stage,
            )
            return sync(token)

    def returning_device_without_page():
        """A seeded device with recorded deliveries and no page to acknowledge.

        This is the state in which the staging upsert is the request's first
        write. A first sync writes its seed rows early, and a sync that
        acknowledges a page writes the acknowledgment early; either way that
        request already holds the writer and its staging cannot be refused.
        """
        first = sync()
        second = sync(first.headers["x-kobo-synctoken"])
        assert second.status_code == 200
        session.query(ub.KoboDevicePendingSyncPage).delete()
        session.commit()
        return second.headers["x-kobo-synctoken"]

    def add_book(title, book_uuid):
        new_book = db.Books(
            title, title, "Author", datetime(2026, 1, 2),
            db.Books.DEFAULT_PUBDATE, "1.0", datetime(2026, 1, 2, 12, 0, 0),
            title, 0, [], [],
        )
        session.add(new_book)
        session.flush()
        new_book.uuid = book_uuid
        session.add(db.Data(new_book.id, "EPUB", 1, title))
        session.commit()
        return new_book

    try:
        yield SimpleNamespace(
            book=book,
            returning_device_without_page=returning_device_without_page,
            add_book=add_book,
            device=device,
            session=session,
            sync=sync,
            hold_writer=hold_writer,
            sync_with_writer_held_at_staging=sync_with_writer_held_at_staging,
            pending_page=lambda: kobo_sync_status.get_pending_sync_page(device.id),
        )
    finally:
        session.close()
        engine.dispose()


def test_control_free_writer_stores_the_page(held_writer_sync):
    """Control: with the writer free the page is stored, so deltas keep working."""
    response = held_writer_sync.sync()

    assert response.status_code == 200
    assert held_writer_sync.pending_page() is not None


def test_sync_survives_a_writer_held_at_staging(held_writer_sync, caplog):
    """A writer held while the response is staged must not fail the sync."""
    token = held_writer_sync.returning_device_without_page()
    with caplog.at_level("WARNING", logger="cps.kobo"):
        response = held_writer_sync.sync_with_writer_held_at_staging(token)

    assert response.status_code == 200, (
        "a writer held at the staging write must not turn an already-built "
        "response into a 500"
    )
    assert held_writer_sync.pending_page() is None, (
        "the page could not be stored, so nothing it delivered is recorded"
    )
    assert any(
        "without a staged page" in record.getMessage() for record in caplog.records
    ), "the degraded delivery must be logged rather than silent"


def test_a_delivery_that_could_not_be_recorded_is_delivered_again(
    held_writer_sync,
):
    """The degraded response must not make the server forget what it sent.

    The held sync delivers a new book and a new token, but its page (the record
    of that delivery) was rolled back. The next sync must deliver the book again
    and, with the writer free, record it.
    """
    from cps import ub

    session = held_writer_sync.session
    synctoken = "x-kobo-synctoken"
    token = held_writer_sync.returning_device_without_page()
    added = held_writer_sync.add_book(
        "Added While Held", "00000000-0000-0000-0000-000000000002",
    )

    def delivered(response):
        return [
            entry["NewEntitlement"]["BookEntitlement"]["Id"]
            for entry in response.get_json()
            if "NewEntitlement" in entry
        ]

    held = held_writer_sync.sync_with_writer_held_at_staging(token)
    assert held.status_code == 200
    assert delivered(held) == [str(added.id)]
    session.expire_all()
    assert session.query(ub.KoboSyncedBooks).filter_by(book_id=added.id).count() == 0

    again = held_writer_sync.sync(held.headers[synctoken])
    assert again.status_code == 200
    assert delivered(again) == [str(added.id)], (
        "a delivery whose record was rolled back must be delivered again"
    )

    settled = held_writer_sync.sync(again.headers[synctoken])
    assert settled.status_code == 200
    session.expire_all()
    assert session.query(ub.KoboSyncedBooks).filter_by(book_id=added.id).count() == 1


def test_waiting_for_the_writer_keeps_serving_other_requests(
    held_writer_sync, monkeypatch,
):
    """The retry pause must yield to other greenlets, not park the hub.

    The app runs gevent without monkey-patching, so a stdlib ``time.sleep`` in
    the retry loop would freeze every other request for the whole wait.
    """
    from cps import kobo

    gevent = pytest.importorskip("gevent")
    # Long enough for a pause between attempts: SQLite's busy wait can overrun
    # a short timeout on platforms that sleep in coarse steps.
    monkeypatch.setattr(kobo, "_PENDING_STAGE_RETRY_BUDGET_S", 1.5)
    ticks = []

    def other_request():
        while True:
            ticks.append(1)
            gevent.sleep(0.01)

    token = held_writer_sync.returning_device_without_page()
    ticker = gevent.spawn(other_request)
    try:
        response = gevent.spawn(
            held_writer_sync.sync_with_writer_held_at_staging, token,
        ).get(timeout=30)
    finally:
        ticker.kill()

    assert response.status_code == 200
    assert len(ticks) >= 5, (
        "other greenlets must keep running while the sync waits for the writer"
    )


@pytest.mark.xfail(
    reason="a writer held for the whole request still fails earlier bookkeeping "
           "writes (device_reading_position); out of scope for this change",
    strict=False,
)
def test_writer_held_for_the_whole_request(held_writer_sync):
    """Documents the remaining exposure: earlier writes are not best-effort."""
    locker = held_writer_sync.hold_writer()
    try:
        response = held_writer_sync.sync()
    finally:
        locker.rollback()
        locker.close()

    assert response.status_code == 200

