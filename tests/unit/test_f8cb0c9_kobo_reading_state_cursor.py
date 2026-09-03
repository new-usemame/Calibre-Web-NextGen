# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression coverage for F-8cb0c9's reading-state cursor gap.

The sync response may carry a reading state inside an entitlement and may also
carry a bounded page of standalone ChangedReadingState commands.  Those two
delivery paths must share one ordered frontier: otherwise a recently-read book
on the first entitlement page can move the timestamp cursor beyond standalone
states that did not fit in that response.
"""

from collections import Counter
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from flask import Flask, g
from sqlalchemy import create_engine, event, true
from sqlalchemy.orm import sessionmaker


pytestmark = pytest.mark.unit


def _state_book_ids(response, uuid_to_id):
    delivered = []
    for item in response.get_json():
        if "ChangedReadingState" in item:
            state = item["ChangedReadingState"]["ReadingState"]
        else:
            entitlement = (
                item.get("NewEntitlement") or item.get("ChangedEntitlement")
            )
            state = entitlement.get("ReadingState") if entitlement else None
        if state is not None:
            delivered.append(uuid_to_id[state["EntitlementId"]])
    return delivered


def _entitlement_count(response):
    return sum(
        "NewEntitlement" in item or "ChangedEntitlement" in item
        for item in response.get_json()
    )


def _entitlement_book_ids(response):
    return [
        int(entitlement["BookEntitlement"]["Id"])
        for item in response.get_json()
        if (entitlement := (
            item.get("NewEntitlement") or item.get("ChangedEntitlement")
        )) is not None
        and "BookMetadata" in entitlement
    ]


@pytest.fixture
def reading_state_sync(monkeypatch):
    """Real handler + SQLAlchemy harness with 250 state-bearing books."""
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

    total_books = 250
    base = datetime(2026, 8, 1)
    books = []
    for offset in range(1, total_books + 1):
        modified = base + timedelta(seconds=offset)
        book = db.Books(
            f"Book {offset}",
            f"Book {offset}",
            "Author",
            modified,
            db.Books.DEFAULT_PUBDATE,
            "1.0",
            modified,
            f"book-{offset}",
            0,
            [],
            [],
        )
        books.append(book)
    session.add_all(books)
    session.flush()

    states = []
    for book in books:
        book.uuid = f"00000000-0000-0000-0000-{book.id:012d}"
        session.add(db.Data(book.id, "EPUB", 1, f"book-{book.id}"))

        # Book 1 is the recently-read page-one trigger.  Its state is newer
        # than every other state, while the remaining clocks ascend by id.
        state_clock = (
            base + timedelta(seconds=1000)
            if book.id == 1
            else base + timedelta(seconds=book.id)
        )
        read = ub.ReadBook(
            user_id=8009,
            book_id=book.id,
            read_status=ub.ReadBook.STATUS_IN_PROGRESS,
        )
        state = ub.KoboReadingState(
            user_id=8009,
            book_id=book.id,
            priority_timestamp=state_clock,
        )
        state.current_bookmark = ub.KoboBookmark(
            last_modified=state_clock,
            progress_percent=float(book.id % 100),
        )
        state.statistics = ub.KoboStatistics(last_modified=state_clock)
        read.kobo_reading_state = state
        session.add(read)
        states.append((state, state_clock))

    device = ub.Device(
        user_id=8009,
        kind="kobo",
        display_name="Reading-state cursor Kobo",
        model="Kobo",
        active=True,
        created_by="auto",
    )
    session.add(device)
    session.commit()

    # The before_flush listener intentionally stamps a parent state whenever
    # its child rows change.  Restore the deterministic clocks after the
    # initial insert so the regression's ordering is exact.
    for state, state_clock in states:
        state.last_modified = state_clock
        state.priority_timestamp = state_clock
    session.commit()

    user = SimpleNamespace(
        id=8009,
        name="f8cb0c9-reading-state-cursor",
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
        lambda book, archived=False: {"Id": str(book.id), "IsRemoved": archived},
    )
    monkeypatch.setattr(
        kobo,
        "get_metadata",
        lambda book: {"EntitlementId": str(book.id)},
    )

    app = Flask(__name__)
    app.secret_key = "f8cb0c9-test-key"
    app.wsgi_app = SimpleNamespace(is_proxied=True)

    def sync(token=None, *, physical_device=True):
        headers = {}
        if token is not None:
            headers[kobo.SyncToken.SyncToken.SYNC_TOKEN_HEADER] = token
        with app.test_request_context("/v1/library/sync", headers=headers):
            if physical_device:
                g.annotation_origin_device_id = device.id
            response = kobo.HandleSyncRequest.__wrapped__()
            # Promote the response before the next request so the test models
            # a device that accepted each page.  In production that promotion
            # occurs when the next request presents this returned token.
            if physical_device and response.status_code == 200:
                pending = kobo_sync_status.get_pending_sync_page(device.id)
                if pending is not None:
                    assert kobo._acknowledge_pending_page(pending, device.id)
                    session.commit()
            return response

    try:
        yield SimpleNamespace(
            books=books,
            device=device,
            session=session,
            states=[state for state, _clock in states],
            sync=sync,
            token_header=kobo.SyncToken.SyncToken.SYNC_TOKEN_HEADER,
            uuid_to_id={str(book.uuid): book.id for book in books},
            user=user,
        )
    finally:
        session.close()
        engine.dispose()


def test_factory_reset_sync_delivers_every_reading_state_exactly_once(
    reading_state_sync,
):
    """The embedded page-one maximum cannot close over the last 50 states."""
    from cps import kobo

    harness = reading_state_sync
    delivered = []
    pages = []
    entitlement_pages = []
    token = None

    for _request_number in range(1, 7):
        # The no-device fallback keeps this regression focused on the cursor
        # carried by the real handler response; it still takes the exact
        # KoboSyncedBooks-empty factory-reset branch on the first request.
        response = harness.sync(token, physical_device=False)
        page_states = _state_book_ids(response, harness.uuid_to_id)
        pages.append(page_states)
        entitlement_pages.append(_entitlement_book_ids(response))
        delivered.extend(page_states)
        token = response.headers[harness.token_header]
        if not page_states and _entitlement_count(response) == 0:
            break
    else:
        pytest.fail("factory-reset sync did not drain within six requests")

    counts = Counter(delivered)
    assert len(entitlement_pages[0]) == kobo.SYNC_ITEM_LIMIT
    assert harness.books[0].id in entitlement_pages[0], (
        "the newest reading state must belong to a book on entitlement page one"
    )
    stored_ids = {state.book_id for state in harness.states}
    missing = stored_ids - counts.keys()
    duplicates = {book_id: count for book_id, count in counts.items() if count != 1}
    assert not missing and not duplicates, (
        f"every stored state must be delivered exactly once; lost={len(missing)} "
        f"missing_ids={sorted(missing)} duplicate_counts={duplicates} "
        f"pages={[page[:5] + page[-5:] for page in pages]}"
    )

    final_cursor = kobo.SyncToken.SyncToken.from_headers({
        harness.token_header: token,
    }).reading_state_last_modified
    max_delivered = max(
        state.last_modified for state in harness.states
        if state.book_id in counts
    )
    assert final_cursor == max_delivered


def test_full_standalone_page_advances_cursor_instead_of_stalling(
    reading_state_sync,
):
    """A terminal response persists the full page's own maximum timestamp."""
    from cps import kobo, ub

    harness = reading_state_sync
    # Avoid the factory-reset cursor rewrite and suppress the entitlement path;
    # this isolates two consecutive full standalone reading-state pages.
    harness.session.add(ub.KoboSyncedBooks(
        user_id=harness.user.id,
        book_id=harness.books[0].id,
        book_uuid=str(harness.books[0].uuid),
    ))
    harness.session.commit()
    initial = kobo.SyncToken.SyncToken(
        books_last_created=max(book.timestamp for book in harness.books),
        books_last_modified=max(book.last_modified for book in harness.books),
        books_last_id=harness.books[-1].id,
    ).build_sync_token()

    first = harness.sync(initial, physical_device=False)
    first_token = first.headers[harness.token_header]
    first_cursor = kobo.SyncToken.SyncToken.from_headers({
        harness.token_header: first_token,
    }).reading_state_last_modified
    second = harness.sync(first_token, physical_device=False)
    second_cursor = kobo.SyncToken.SyncToken.from_headers({
        harness.token_header: second.headers[harness.token_header],
    }).reading_state_last_modified

    assert len(_state_book_ids(first, harness.uuid_to_id)) == kobo.SYNC_ITEM_LIMIT
    assert len(_state_book_ids(second, harness.uuid_to_id)) == kobo.SYNC_ITEM_LIMIT
    assert datetime.min < first_cursor < second_cursor
