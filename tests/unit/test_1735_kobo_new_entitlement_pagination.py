# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression coverage for Kobo entitlement classification (#1735)."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from flask import Flask, g
from sqlalchemy import create_engine, event, true
from sqlalchemy.orm import sessionmaker


pytestmark = pytest.mark.unit


def _entitlements(response):
    return [
        (kind, int(payload["BookEntitlement"]["Id"]))
        for item in response.get_json()
        for kind, payload in item.items()
        if kind in {"NewEntitlement", "ChangedEntitlement"}
        and "BookMetadata" in payload
    ]


@pytest.fixture
def large_library_sync(monkeypatch):
    """Drive the real handler over 101 books with opposing creation/change clocks."""
    from cps import db, kobo, kobo_sync_status, ub

    engine = create_engine("sqlite://")
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

    total_books = kobo.SYNC_ITEM_LIMIT + 1
    base = datetime(2026, 1, 1)
    books = []
    for offset in range(total_books):
        # The page cursor walks last_modified forwards while date-added walks
        # backwards. Page one's newest timestamp therefore poisons the legacy
        # watermark for the only book on page two.
        last_modified = base + timedelta(seconds=offset)
        timestamp = base + timedelta(seconds=total_books - offset)
        book = db.Books(
            f"Book {offset + 1}",
            f"Book {offset + 1}",
            "Author",
            timestamp,
            db.Books.DEFAULT_PUBDATE,
            "1.0",
            last_modified,
            f"book-{offset + 1}",
            0,
            [],
            [],
        )
        books.append(book)
    session.add_all(books)
    session.flush()
    for book in books:
        book.uuid = f"00000000-0000-0000-0000-{book.id:012d}"
        session.add(db.Data(book.id, "EPUB", 1, f"book-{book.id}"))

    device = ub.Device(
        user_id=1735,
        kind="kobo",
        display_name="Regression Kobo",
        model="Kobo",
        active=True,
        created_by="auto",
    )
    session.add(device)
    session.commit()

    user = SimpleNamespace(
        id=1735,
        name="issue-1735",
        kobo_only_shelves_sync=False,
        role_download=lambda: True,
    )
    fake_calibre_db = SimpleNamespace(
        session=session,
        reconnect_db=lambda *_args, **_kwargs: None,
        common_filters=lambda **_kwargs: true(),
        get_book=lambda book_id: session.query(db.Books).filter_by(
            id=book_id
        ).one_or_none(),
    )

    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(ub, "session_commit", lambda *_args, **_kwargs: session.commit())
    monkeypatch.setattr(kobo, "calibre_db", fake_calibre_db)
    monkeypatch.setattr(kobo, "current_user", user)
    monkeypatch.setattr(kobo_sync_status, "current_user", user)
    monkeypatch.setattr(kobo.config, "config_kobo_proxy", False, raising=False)
    monkeypatch.setattr(
        kobo.config,
        "config_kobo_suppress_replayed_entitlements",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        kobo.config,
        "config_kobo_sync_magic_shelves",
        False,
        raising=False,
    )
    monkeypatch.setattr(kobo, "get_download_url_for_book", lambda *_args: "/download")
    monkeypatch.setattr(
        kobo,
        "get_magic_shelf_book_ids_for_kobo",
        lambda _user_id: (set(), True),
    )
    monkeypatch.setattr(
        kobo,
        "get_magic_shelf_membership_added_at",
        lambda _user_id: None,
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
        kobo,
        "get_metadata",
        lambda book: {"EntitlementId": str(book.id)},
    )

    app = Flask(__name__)
    app.secret_key = "issue-1735-test-key"
    app.wsgi_app = SimpleNamespace(is_proxied=True)

    def sync(token=None):
        headers = {
            "x-kobo-deviceid": "a" * 64,
            "x-kobo-devicemodel": device.model,
        }
        if token is not None:
            headers[kobo.SyncToken.SyncToken.SYNC_TOKEN_HEADER] = token
        with app.test_request_context("/v1/library/sync", headers=headers):
            g.annotation_origin_device_id = device.id
            return kobo.HandleSyncRequest.__wrapped__()

    try:
        yield SimpleNamespace(
            books=books,
            device=device,
            session=session,
            sync=sync,
            token_header=kobo.SyncToken.SyncToken.SYNC_TOKEN_HEADER,
            user=user,
        )
    finally:
        session.close()
        engine.dispose()


def test_every_never_delivered_book_is_new_across_multiple_pages(large_library_sync):
    """All 101 books are New even though page two has an older date-added."""
    harness = large_library_sync

    first = harness.sync()
    first_entitlements = _entitlements(first)
    assert len(first_entitlements) == 100
    assert {kind for kind, _book_id in first_entitlements} == {"NewEntitlement"}

    second = harness.sync(first.headers[harness.token_header])
    assert _entitlements(second) == [("NewEntitlement", 101)], (
        "a book absent from this device's delivery record must not become a "
        "ChangedEntitlement merely because its date-added is old"
    )


def test_already_delivered_book_is_changed_not_new_on_later_sync(large_library_sync):
    """The per-device delivery record prevents repeated NewEntitlements."""
    harness = large_library_sync
    first = harness.sync()
    second = harness.sync(first.headers[harness.token_header])

    harness.books[-1].last_modified = datetime(2027, 1, 1)
    harness.session.commit()
    changed = harness.sync(second.headers[harness.token_header])

    assert _entitlements(changed) == [("ChangedEntitlement", 101)]


def test_classification_ledger_is_written_when_replay_suppression_is_disabled(
        large_library_sync, monkeypatch):
    """The classification ledger is core state, not an optional replay cache."""
    from cps import kobo, ub

    harness = large_library_sync
    monkeypatch.setattr(
        kobo.config,
        "config_kobo_suppress_replayed_entitlements",
        False,
    )

    first = harness.sync()
    assert len(_entitlements(first)) == 100
    assert harness.session.query(ub.KoboDeviceBookEntitlement).count() == 100

    second = harness.sync(first.headers[harness.token_header])
    assert _entitlements(second) == [("NewEntitlement", 101)]
    assert harness.session.query(ub.KoboDeviceBookEntitlement).count() == 101

    harness.books[-1].last_modified = datetime(2027, 1, 1)
    harness.session.commit()
    changed = harness.sync(second.headers[harness.token_header])

    assert _entitlements(changed) == [("ChangedEntitlement", 101)]


def test_legacy_stuck_cursor_recovers_missing_page_without_reset(large_library_sync):
    """An old complete cursor cannot hide books the device never received."""
    from cps import kobo, ub

    harness = large_library_sync
    harness.session.add_all([
        ub.KoboSyncedBooks(
            user_id=harness.user.id,
            book_id=book.id,
            book_uuid=str(book.uuid),
        )
        for book in harness.books
    ])
    harness.session.commit()

    # This is the state left by the old handler: the flat server marker and
    # device cursor claim every book was sent, while the actual device received
    # only the first NewEntitlement page. No token or marker is cleared here.
    poisoned = kobo.SyncToken.SyncToken(
        books_last_created=max(book.timestamp for book in harness.books),
        books_last_modified=max(book.last_modified for book in harness.books),
        books_last_id=harness.books[-1].id,
    ).build_sync_token()

    recovered = harness.sync(poisoned)

    assert _entitlements(recovered) == [("NewEntitlement", 101)]
    assert harness.session.query(ub.KoboSyncedBooks).count() == 101
    assert harness.session.query(ub.KoboDeviceBookEntitlement).count() == 101
