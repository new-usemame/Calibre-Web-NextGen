# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Behavioral regression for fork #1634 (recurrence of #347).

Kobo firmware treats ``x-kobo-sync: continue`` as an instruction to keep the
request cursor pinned.  It persists the returned ``x-kobo-synctoken`` only
after a response without ``continue``.  A server-side page that advances its
token but also emits ``continue`` therefore cannot drain: the next request
uses the old token and receives the same page again.

This test drives the real ``HandleSyncRequest`` body repeatedly, with real
SQLAlchemy queries over a small seeded library.  The item limit is patched
down so the test remains fast, while the request-token feedback loop models
the observed firmware contract rather than the server's intended contract.
"""

from datetime import datetime
from math import ceil
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine, event, true
from sqlalchemy.orm import sessionmaker


pytestmark = pytest.mark.unit


def _book_id(sync_item):
    envelope = sync_item.get("NewEntitlement") or sync_item.get("ChangedEntitlement")
    if not envelope or "BookMetadata" not in envelope:
        return None
    return int(envelope["BookEntitlement"]["Id"])


@pytest.mark.parametrize(
    "proxy_enabled",
    [False, True],
    ids=["local-only", "proxy-store-continue"],
)
def test_full_library_sync_drains_when_firmware_pins_cursor_on_continue(
    monkeypatch, proxy_enabled
):
    """Every book arrives once across bounded firmware-shaped sync sessions."""
    from cps import db, kobo as kobo_module, kobo_sync_status, ub

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

    limit = 3
    total_books = limit * 2 + 1
    shared_modified = datetime(2026, 8, 12, 5, 9, 48, 351127)
    books = [
        db.Books(
            f"Book {book_id}",
            f"Book {book_id}",
            "Author",
            shared_modified,
            db.Books.DEFAULT_PUBDATE,
            "1.0",
            shared_modified,
            f"book-{book_id}",
            0,
            [],
            [],
        )
        for book_id in range(1, total_books + 1)
    ]
    session.add_all(books)
    session.flush()
    for book in books:
        book.uuid = f"book-uuid-{book.id}"
        session.add(db.Data(book.id, "EPUB", 1, f"book-{book.id}"))
    session.commit()

    user = SimpleNamespace(
        id=17,
        name="firmware-contract-test",
        kobo_only_shelves_sync=False,
        role_download=lambda: True,
    )
    fake_calibre_db = SimpleNamespace(
        session=session,
        reconnect_db=lambda *_args, **_kwargs: None,
        refresh_for_new_data=lambda: None,
        common_filters=lambda **_kwargs: true(),
    )

    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(ub, "session_commit", lambda *_args, **_kwargs: session.commit())
    monkeypatch.setattr(kobo_module, "calibre_db", fake_calibre_db)
    monkeypatch.setattr(kobo_module, "current_user", user)
    monkeypatch.setattr(kobo_sync_status, "current_user", user)
    monkeypatch.setattr(kobo_module, "SYNC_ITEM_LIMIT", limit)
    monkeypatch.setattr(
        kobo_module.config,
        "config_kobo_proxy",
        proxy_enabled,
        raising=False,
    )
    monkeypatch.setattr(
        kobo_module.config,
        "config_kobo_sync_magic_shelves",
        False,
        raising=False,
    )
    monkeypatch.setattr(kobo_module, "get_download_url_for_book", lambda *_args: "/download")
    monkeypatch.setattr(
        kobo_module,
        "get_magic_shelf_book_ids_for_kobo",
        lambda _user_id: (set(), True),
    )
    monkeypatch.setattr(
        kobo_module,
        "get_magic_shelf_membership_added_at",
        lambda _user_id: None,
    )
    monkeypatch.setattr(kobo_module, "sync_shelves", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        kobo_module,
        "create_book_entitlement",
        lambda book, archived=False: {"Id": str(book.id), "IsRemoved": archived},
    )
    monkeypatch.setattr(
        kobo_module,
        "get_metadata",
        lambda book: {"EntitlementId": str(book.id)},
    )
    store_calls = []
    if proxy_enabled:
        store_response = SimpleNamespace(
            json=lambda: [],
            headers={
                "x-kobo-sync": "continue",
                "x-kobo-sync-mode": "test-mode",
                "x-kobo-recent-reads": "test-recent-reads",
                "x-kobo-synctoken": "test-store-token",
            },
        )

        def store_sync(token):
            store_calls.append(token)
            return store_response

        monkeypatch.setattr(kobo_module, "make_request_to_kobo_store", store_sync)

    app = Flask(__name__)
    app.wsgi_app = SimpleNamespace(is_proxied=True)
    device_token = ""
    pages = []
    deliveries = []
    delivered = set()
    request_bound = ceil(total_books / limit) + 1

    try:
        for request_number in range(1, request_bound + 1):
            sent_token = device_token
            headers = {}
            if sent_token:
                headers[kobo_module.SyncToken.SyncToken.SYNC_TOKEN_HEADER] = sent_token

            with app.test_request_context("/v1/library/sync", headers=headers):
                response = kobo_module.HandleSyncRequest.__wrapped__()

            page = tuple(
                book_id
                for item in response.get_json()
                if (book_id := _book_id(item)) is not None
            )
            if pages:
                assert page != pages[-1][0], (
                    "firmware-pinned cursor re-delivered the identical page "
                    f"{page} on requests {request_number - 1} and {request_number}; "
                    f"x-kobo-sync values were {pages[-1][1]!r} and "
                    f"{response.headers.get('x-kobo-sync')!r}"
                )

            continuation = response.headers.get("x-kobo-sync")
            pages.append((page, continuation))
            deliveries.extend(page)
            delivered.update(page)

            # Observed firmware contract: `continue` pins the request token;
            # only a terminal response commits the server's returned cursor.
            if continuation != "continue":
                device_token = response.headers[
                    kobo_module.SyncToken.SyncToken.SYNC_TOKEN_HEADER
                ]

            if len(delivered) == total_books:
                break
        else:
            pytest.fail(
                f"sync did not drain {total_books} books within {request_bound} requests; "
                f"delivered={sorted(delivered)}, pages={pages}"
            )

        assert delivered == set(range(1, total_books + 1))
        assert len(deliveries) == total_books, (
            f"each seeded book must be delivered exactly once; got {deliveries}"
        )
        assert len(pages) <= ceil(total_books / limit)
        assert len(store_calls) == (len(pages) if proxy_enabled else 0)
    finally:
        session.close()
        engine.dispose()
