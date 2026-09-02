# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Forensic reproductions for whole-device Kobo ledger restamps.

These tests distinguish the response that Nickel received from the later
request that promotes the response's confirmation payload.  ``updated_at`` is
an acknowledgment clock, not a book-modification clock and not, by itself,
evidence that any entitlement crossed the wire on the acknowledging request.
"""

from datetime import datetime, timedelta, timezone
import json
import logging

import pytest

from tests.unit.test_1925_kobo_sync_dedownload import sync_harness


pytestmark = pytest.mark.unit

_HELD_BOOK_COUNT = 18
_DELETED_UUID = "00000000-0000-0000-0000-000000009999"
_OLD_DELETION = datetime(2026, 7, 8, 4, 42, 25)


def _wire_counts(response):
    counts = {
        "NewEntitlement": 0,
        "ChangedEntitlement": 0,
        "IsRemoved": 0,
    }
    for item in response.get_json():
        for kind in ("NewEntitlement", "ChangedEntitlement"):
            envelope = item.get(kind)
            if envelope is None:
                continue
            counts[kind] += 1
            if envelope.get("BookEntitlement", {}).get("IsRemoved") is True:
                counts["IsRemoved"] += 1
    return counts


def _add_incident_shape(sync_harness):
    """Build 18 held shelf books plus one old hard-delete tombstone."""
    from cps import db, ub

    sync_harness.user.kobo_only_shelves_sync = True
    modified = sync_harness.book.last_modified
    books = [sync_harness.book]
    for number in range(2, _HELD_BOOK_COUNT + 1):
        book = db.Books(
            f"Forensic Book {number}",
            f"Forensic Book {number}",
            "Author",
            modified - timedelta(days=number),
            db.Books.DEFAULT_PUBDATE,
            "1.0",
            modified - timedelta(days=number),
            f"forensic-book-{number}",
            0,
            [],
            [],
        )
        sync_harness.session.add(book)
        sync_harness.session.flush()
        book.uuid = f"00000000-0000-0000-0000-{number:012d}"
        sync_harness.session.add(db.Data(
            book.id, "EPUB", 1_000_000 + number, f"forensic-book-{number}",
        ))
        books.append(book)

    shelf = ub.Shelf(
        name="Forensic Shelf",
        user_id=sync_harness.user.id,
        kobo_sync=True,
        uuid="forensic-kobo-shelf",
        is_public=0,
    )
    sync_harness.session.add(shelf)
    sync_harness.session.flush()
    for order, book in enumerate(books, start=1):
        link = ub.BookShelf(
            book_id=book.id,
            shelf=shelf.id,
            order=order,
            date_added=modified - timedelta(days=30),
        )
        link.ub_shelf = shelf
        sync_harness.session.add(link)
    sync_harness.session.add(ub.KoboDeletedBook(
        user_id=sync_harness.user.id,
        book_uuid=_DELETED_UUID,
        deleted_at=_OLD_DELETION,
    ))
    sync_harness.session.commit()
    return books


def _establish_acknowledged_ledgers(sync_harness, monkeypatch):
    """Create acknowledged v1 fingerprints, then give each row an old clock."""
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    books = _add_incident_shape(sync_harness)
    initial = sync_harness.sync()
    assert _wire_counts(initial) == {
        "NewEntitlement": _HELD_BOOK_COUNT,
        "ChangedEntitlement": 1,
        "IsRemoved": 1,
    }

    old_clock = datetime(2026, 8, 29, 2, 19, 2)
    book_rows = sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).order_by(ub.KoboDeviceBookEntitlement.book_id).all()
    assert len(book_rows) == _HELD_BOOK_COUNT
    for offset, row in enumerate(book_rows):
        row.updated_at = old_clock + timedelta(seconds=offset)
    deleted_row = sync_harness.session.query(
        ub.KoboDeviceDeletedEntitlement,
    ).one()
    deleted_row.updated_at = old_clock + timedelta(minutes=1)
    sync_harness.session.commit()
    return books, initial.headers[sync_harness.token_header]


def _snapshot(sync_harness):
    from cps import ub

    books = {
        row.book_id: {
            "basis": row.change_basis,
            "updated_at": row.updated_at,
            "schema": row.payload_schema_version,
        }
        for row in sync_harness.session.query(
            ub.KoboDeviceBookEntitlement,
        ).filter_by(device_id=sync_harness.device.id).all()
    }
    deleted = {
        row.book_uuid: {
            "basis": row.change_basis,
            "updated_at": row.updated_at,
            "schema": row.payload_schema_version,
        }
        for row in sync_harness.session.query(
            ub.KoboDeviceDeletedEntitlement,
        ).filter_by(device_id=sync_harness.device.id).all()
    }
    return {"books": books, "deleted": deleted}


def _signature(before, after):
    book_rows = after["books"]
    deleted_rows = after["deleted"]
    book_clocks = {row["updated_at"] for row in book_rows.values()}
    return {
        "book_count": len(book_rows),
        "deleted_count": len(deleted_rows),
        "one_book_timestamp": len(book_clocks) == 1,
        "book_bases_unchanged": (
            set(book_rows) == set(before["books"])
            and all(
                book_rows[book_id]["basis"]
                == before["books"][book_id]["basis"]
                for book_id in book_rows
            )
        ),
        "all_books_restamped": (
            set(book_rows) == set(before["books"])
            and all(
                book_rows[book_id]["updated_at"]
                != before["books"][book_id]["updated_at"]
                for book_id in book_rows
            )
        ),
        "deleted_basis_unchanged": (
            set(deleted_rows) == set(before["deleted"])
            and all(
                deleted_rows[book_uuid]["basis"]
                == before["deleted"][book_uuid]["basis"]
                for book_uuid in deleted_rows
            )
        ),
        "deleted_restamped": (
            set(deleted_rows) == set(before["deleted"])
            and all(
                deleted_rows[book_uuid]["updated_at"]
                != before["deleted"][book_uuid]["updated_at"]
                for book_uuid in deleted_rows
            )
        ),
    }


def _matches_incident_signature(signature):
    return signature == {
        "book_count": _HELD_BOOK_COUNT,
        "deleted_count": 1,
        "one_book_timestamp": True,
        "book_bases_unchanged": True,
        "all_books_restamped": True,
        "deleted_basis_unchanged": True,
        "deleted_restamped": True,
    }


def _print_result(name, page, ack, signature):
    print("FORENSIC " + json.dumps({
        "candidate": name,
        "candidate_page_wire": _wire_counts(page),
        "ack_request_wire": _wire_counts(ack),
        "ledger_signature_matches": _matches_incident_signature(signature),
        "signature": signature,
    }, sort_keys=True))


def _stale_valid_token():
    from cps import kobo

    return kobo.SyncToken.SyncToken().build_sync_token()


def _partial_token_with_book_cursor(cursor):
    """Build the parser shape that preserves books but omits archive state."""
    from cps.services import SyncToken

    encoded_cursor = SyncToken.to_epoch_timestamp(cursor)
    return SyncToken.b64encode_json({
        "version": "1-0-0",
        "data": {
            "raw_kobo_store_token": "",
            "books_last_modified": encoded_cursor,
            "books_last_created": encoded_cursor,
            # archive_last_modified is deliberately absent. The permissive
            # parser resets it to datetime.min and marks this token partial.
            "reading_state_last_modified": encoded_cursor,
            "tags_last_modified": encoded_cursor,
        },
    })


def _establish_partial_cursor_shape(sync_harness, monkeypatch):
    """Create 23 recent rows, one older row, and three old tombstones."""
    from cps import db, kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    books = _add_incident_shape(sync_harness)
    shelf = sync_harness.session.query(ub.Shelf).filter_by(
        user_id=sync_harness.user.id,
        uuid="forensic-kobo-shelf",
    ).one()
    modified = sync_harness.book.last_modified
    for number in range(_HELD_BOOK_COUNT + 1, 24):
        book_modified = modified - timedelta(days=number)
        book = db.Books(
            f"Cursor Book {number}",
            f"Cursor Book {number}",
            "Author",
            book_modified,
            db.Books.DEFAULT_PUBDATE,
            "1.0",
            book_modified,
            f"cursor-book-{number}",
            0,
            [],
            [],
        )
        sync_harness.session.add(book)
        sync_harness.session.flush()
        book.uuid = f"00000000-0000-0000-0001-{number:012d}"
        sync_harness.session.add(db.Data(
            book.id, "EPUB", 2_000_000 + number, f"cursor-book-{number}",
        ))
        link = ub.BookShelf(
            book_id=book.id,
            shelf=shelf.id,
            order=number,
            date_added=modified - timedelta(days=30),
        )
        link.ub_shelf = shelf
        sync_harness.session.add(link)
        books.append(book)

    old_modified = modified - timedelta(days=60)
    old_book = db.Books(
        "Older Cursor Sentinel",
        "Older Cursor Sentinel",
        "Author",
        old_modified,
        db.Books.DEFAULT_PUBDATE,
        "1.0",
        old_modified,
        "older-cursor-sentinel",
        0,
        [],
        [],
    )
    sync_harness.session.add(old_book)
    sync_harness.session.flush()
    old_book.uuid = "00000000-0000-0000-0001-000000000024"
    sync_harness.session.add(db.Data(
        old_book.id, "EPUB", 2_000_024, "older-cursor-sentinel",
    ))
    old_link = ub.BookShelf(
        book_id=old_book.id,
        shelf=shelf.id,
        order=24,
        date_added=modified - timedelta(days=60),
    )
    old_link.ub_shelf = shelf
    sync_harness.session.add(old_link)
    books.append(old_book)

    sync_harness.session.add_all([
        ub.KoboDeletedBook(
            user_id=sync_harness.user.id,
            book_uuid="00000000-0000-0000-0000-000000009998",
            deleted_at=datetime(2026, 7, 18, 4, 42, 25),
        ),
        ub.KoboDeletedBook(
            user_id=sync_harness.user.id,
            book_uuid="00000000-0000-0000-0000-000000009997",
            deleted_at=datetime(2026, 8, 18, 4, 42, 25),
        ),
    ])
    sync_harness.session.commit()

    initial = sync_harness.sync()
    assert _wire_counts(initial) == {
        "NewEntitlement": 24,
        "ChangedEntitlement": 3,
        "IsRemoved": 3,
    }
    return books, modified - timedelta(days=24)


def test_forensic_no_sync_token_suppresses_exact_same_device_rows(
    sync_harness, monkeypatch, caplog,
):
    """An absent token cannot override acknowledged same-device evidence."""
    _establish_acknowledged_ledgers(sync_harness, monkeypatch)
    before = _snapshot(sync_harness)

    caplog.set_level(logging.INFO, logger="cps.kobo")
    page = sync_harness.sync(None, acknowledge=False)
    page_summaries = [
        record.getMessage() for record in caplog.records
        if record.getMessage().startswith("Kobo Sync summary:")
    ]
    ack = sync_harness.sync(
        page.headers[sync_harness.token_header], acknowledge=False,
    )
    after = _snapshot(sync_harness)
    signature = _signature(before, after)
    _print_result("no_sync_token", page, ack, signature)

    assert after == before
    assert _wire_counts(page) == {
        "NewEntitlement": 0,
        "ChangedEntitlement": 0,
        "IsRemoved": 0,
    }
    assert _wire_counts(ack) == {
        "NewEntitlement": 0,
        "ChangedEntitlement": 0,
        "IsRemoved": 0,
    }
    assert any(
        "suppressed_replay=19" in message
        and "suppressed_unchanged=18" in message
        and "suppressed_removed=1" in message
        and "reemit_reasons=none" in message
        and "eligible=True" in message
        for message in page_summaries
    )


def test_partial_token_honours_book_cursor_but_suppresses_same_device_replays(
    sync_harness, monkeypatch, caplog,
):
    """A partial token selects 23 recent rows and all old tombstones safely."""
    from cps.services import SyncToken

    books, cursor = _establish_partial_cursor_shape(
        sync_harness, monkeypatch,
    )
    before = _snapshot(sync_harness)
    partial_token = _partial_token_with_book_cursor(cursor)
    parsed = SyncToken.SyncToken.from_headers({
        SyncToken.SyncToken.SYNC_TOKEN_HEADER: partial_token,
    })
    assert parsed.books_last_modified == cursor
    assert parsed.archive_last_modified == datetime.min
    assert parsed.is_cwng_token is False

    caplog.set_level(logging.DEBUG, logger="cps.kobo")
    page = sync_harness.sync(partial_token, acknowledge=False)
    page_summaries = [
        record.getMessage() for record in caplog.records
        if record.getMessage().startswith("Kobo Sync summary:")
    ]
    ack = sync_harness.sync(
        page.headers[sync_harness.token_header], acknowledge=False,
    )
    after = _snapshot(sync_harness)

    # The first five selected books represent the held subset from the
    # hardware reproduction. The server does not store download status; exact
    # same-device fingerprints protect them together with every other exact
    # row in the selected page.
    held_book_ids = {book.id for book in books[:5]}
    assert len(held_book_ids) == 5
    assert any(
        record.getMessage() == (
            "Kobo Sync: candidate scan total=23 scanned=23 "
            "deliverable=0 exhausted=True"
        )
        for record in caplog.records
    )
    assert _wire_counts(page) == {
        "NewEntitlement": 0,
        "ChangedEntitlement": 0,
        "IsRemoved": 0,
    }
    assert _wire_counts(ack) == {
        "NewEntitlement": 0,
        "ChangedEntitlement": 0,
        "IsRemoved": 0,
    }
    assert any(
        "suppressed_replay=26" in message
        and "suppressed_unchanged=23" in message
        and "suppressed_removed=3" in message
        and "reemit_reasons=none" in message
        and "eligible=True" in message
        for message in page_summaries
    )
    assert after == before


def test_missing_same_device_ledger_row_is_recovered_as_new(
    sync_harness, monkeypatch,
):
    """A missing ledger row bypasses even an advanced partial book cursor."""
    from cps import ub

    books, _token = _establish_acknowledged_ledgers(
        sync_harness, monkeypatch,
    )
    missing_book = books[0]
    sync_harness.session.query(ub.KoboDeviceBookEntitlement).filter_by(
        device_id=sync_harness.device.id,
        book_id=missing_book.id,
    ).delete(synchronize_session=False)
    sync_harness.session.commit()

    page = sync_harness.sync(
        _partial_token_with_book_cursor(datetime(2027, 1, 1)),
        acknowledge=False,
    )

    assert _wire_counts(page) == {
        "NewEntitlement": 1,
        "ChangedEntitlement": 0,
        "IsRemoved": 0,
    }


def test_new_device_without_ledger_receives_books_as_new(
    sync_harness, monkeypatch,
):
    """One device's fingerprints never suppress a newly paired reader."""
    from cps import ub

    _establish_acknowledged_ledgers(sync_harness, monkeypatch)
    new_device = ub.Device(
        user_id=sync_harness.user.id,
        kind="kobo",
        display_name="New Regression Reader",
        model="Kobo Reader",
        active=True,
        created_by="auto",
    )
    sync_harness.session.add(new_device)
    sync_harness.session.commit()

    page = sync_harness.sync(
        _partial_token_with_book_cursor(datetime(2027, 1, 1)),
        internal_device_id=new_device.id,
        raw_device_id="b" * 64,
        acknowledge=False,
    )

    assert _wire_counts(page) == {
        "NewEntitlement": _HELD_BOOK_COUNT,
        "ChangedEntitlement": 1,
        "IsRemoved": 1,
    }


def test_explicit_full_sync_clears_ledger_and_redelivers_new(
    sync_harness, monkeypatch,
):
    """Full Sync remains the deliberate escape from exact suppression."""
    from cps import admin

    _establish_acknowledged_ledgers(sync_harness, monkeypatch)
    monkeypatch.setattr(admin, "_", lambda value: value)
    with sync_harness.app.test_request_context(
        "/ajax/fullsync/17", method="POST",
    ):
        response = admin.do_full_kobo_sync(sync_harness.user.id)
    assert response.status_code == 200

    page = sync_harness.sync(
        _partial_token_with_book_cursor(datetime(2027, 1, 1)),
        acknowledge=False,
    )

    assert _wire_counts(page) == {
        "NewEntitlement": _HELD_BOOK_COUNT,
        "ChangedEntitlement": 1,
        "IsRemoved": 1,
    }


def test_forensic_incident_order_expired_page_prune_cannot_restamp(
    sync_harness, monkeypatch,
):
    """The pre-#2113 prune-before-ack order discards confirmation, not promotes it."""
    from cps import kobo_sync_status, ub

    _establish_acknowledged_ledgers(sync_harness, monkeypatch)
    before = _snapshot(sync_harness)
    page = sync_harness.sync(None, acknowledge=False)
    pending = sync_harness.session.query(
        ub.KoboDevicePendingSyncPage,
    ).filter_by(device_id=sync_harness.device.id).one()
    pending.created_at = (
        datetime.now(timezone.utc)
        - kobo_sync_status.PENDING_SYNC_PAGE_TTL
        - timedelta(seconds=1)
    )
    outgoing = pending.outgoing_token
    sync_harness.session.commit()

    # Reproduce the incident image's ordering explicitly: that handler called
    # prune before comparing the incoming token with pending.outgoing_token.
    assert kobo_sync_status.prune_expired_pending_sync_pages(
        sync_harness.user.id,
    ) == 1
    sync_harness.session.commit()
    ack = sync_harness.sync(outgoing, acknowledge=False)
    after = _snapshot(sync_harness)
    signature = _signature(before, after)
    _print_result("expired_page_pruned_before_ack", page, ack, signature)

    assert not _matches_incident_signature(signature)
    assert signature["all_books_restamped"] is False
    assert signature["deleted_restamped"] is False
    assert _wire_counts(ack) == {
        "NewEntitlement": 0,
        "ChangedEntitlement": 0,
        "IsRemoved": 0,
    }


def test_forensic_empty_flat_markers_reset_suppresses_without_restamp(
    sync_harness, monkeypatch,
):
    """The flat-marker reset rewinds cursors but retains valid-token suppression."""
    from cps import kobo, ub

    _establish_acknowledged_ledgers(sync_harness, monkeypatch)
    before = _snapshot(sync_harness)
    sync_harness.session.query(ub.KoboSyncedBooks).filter_by(
        user_id=sync_harness.user.id,
    ).delete(synchronize_session=False)
    sync_harness.session.commit()
    advanced = kobo.SyncToken.SyncToken(
        books_last_modified=datetime(2027, 1, 1),
        books_last_created=datetime(2027, 1, 1),
        books_last_id=999_999,
    ).build_sync_token()

    page = sync_harness.sync(advanced, acknowledge=False)
    ack = sync_harness.sync(
        page.headers[sync_harness.token_header], acknowledge=False,
    )
    after = _snapshot(sync_harness)
    signature = _signature(before, after)
    _print_result("empty_kobo_synced_books", page, ack, signature)

    assert not _matches_incident_signature(signature)
    assert _wire_counts(page) == {
        "NewEntitlement": 0,
        "ChangedEntitlement": 0,
        "IsRemoved": 0,
    }


def test_forensic_classification_migration_restamps_all_and_emits_new(
    sync_harness, monkeypatch,
):
    """The v0-to-v1 audit deletes uncertain rows, then reannounces them New."""
    from cps import ub

    _establish_acknowledged_ledgers(sync_harness, monkeypatch)
    before = _snapshot(sync_harness)
    seed = sync_harness.session.get(
        ub.KoboDeviceEntitlementSeed, sync_harness.device.id,
    )
    seed.classification_version = 0
    sync_harness.session.commit()

    page = sync_harness.sync(_stale_valid_token(), acknowledge=False)
    assert sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).filter_by(device_id=sync_harness.device.id).count() == 0
    assert sync_harness.session.query(
        ub.KoboDeviceDeletedEntitlement,
    ).filter_by(device_id=sync_harness.device.id).count() == 0
    assert sync_harness.session.get(
        ub.KoboDeviceEntitlementSeed, sync_harness.device.id,
    ).classification_version == 1

    ack = sync_harness.sync(
        page.headers[sync_harness.token_header], acknowledge=False,
    )
    after = _snapshot(sync_harness)
    signature = _signature(before, after)
    _print_result("classification_v0_to_v1", page, ack, signature)

    assert _matches_incident_signature(signature)
    assert _wire_counts(page) == {
        "NewEntitlement": _HELD_BOOK_COUNT,
        "ChangedEntitlement": 1,
        "IsRemoved": 1,
    }


def test_forensic_payload_schema_transition_restamps_without_wire_entitlement(
    sync_harness, monkeypatch,
):
    """A declared ledger-schema refresh has the timestamp shape but no wire replay."""
    from cps import ub

    _establish_acknowledged_ledgers(sync_harness, monkeypatch)
    for row in sync_harness.session.query(
            ub.KoboDeviceBookEntitlement).filter_by(
                device_id=sync_harness.device.id):
        row.payload_schema_version = 0
    for row in sync_harness.session.query(
            ub.KoboDeviceDeletedEntitlement).filter_by(
                device_id=sync_harness.device.id):
        row.payload_schema_version = 0
    sync_harness.session.commit()
    before = _snapshot(sync_harness)

    page = sync_harness.sync(_stale_valid_token(), acknowledge=False)
    ack = sync_harness.sync(
        page.headers[sync_harness.token_header], acknowledge=False,
    )
    after = _snapshot(sync_harness)
    signature = _signature(before, after)
    _print_result("payload_schema_transition", page, ack, signature)

    assert _matches_incident_signature(signature)
    assert _wire_counts(page) == {
        "NewEntitlement": 0,
        "ChangedEntitlement": 0,
        "IsRemoved": 0,
    }
    assert {row["schema"] for row in after["books"].values()} == {1}
    assert {row["schema"] for row in after["deleted"].values()} == {1}


def test_forensic_older_valid_sync_token_version_suppresses_without_restamp(
    sync_harness, monkeypatch,
):
    """The token's transport/schema version does not refresh entitlement rows."""
    from cps.services import SyncToken

    _establish_acknowledged_ledgers(sync_harness, monkeypatch)
    before = _snapshot(sync_harness)
    minimum = SyncToken.to_epoch_timestamp(datetime.min)
    older_token = SyncToken.b64encode_json({
        "version": "1-1-0",
        "data": {
            "raw_kobo_store_token": "",
            "books_last_modified": minimum,
            "books_last_created": minimum,
            "archive_last_modified": minimum,
            "reading_state_last_modified": minimum,
            "tags_last_modified": minimum,
        },
    })

    page = sync_harness.sync(older_token, acknowledge=False)
    ack = sync_harness.sync(
        page.headers[sync_harness.token_header], acknowledge=False,
    )
    after = _snapshot(sync_harness)
    signature = _signature(before, after)
    _print_result("older_valid_sync_token_version", page, ack, signature)

    assert not _matches_incident_signature(signature)
    assert _wire_counts(page) == {
        "NewEntitlement": 0,
        "ChangedEntitlement": 0,
        "IsRemoved": 0,
    }
