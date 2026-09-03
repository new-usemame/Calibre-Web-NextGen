# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Replay suppression must be transparent to Kobo page capacity."""

from datetime import datetime, timedelta

import pytest

from tests.unit.test_1925_kobo_sync_dedownload import sync_harness


pytestmark = pytest.mark.unit


def _wire_entitlements(response):
    return [
        (kind, payload["BookEntitlement"])
        for item in response.get_json()
        for kind, payload in item.items()
        if kind in {"NewEntitlement", "ChangedEntitlement"}
    ]


def _add_library_book(sync_harness, sequence, modified):
    from cps import db

    label = sequence + 1
    book = db.Books(
        f"Pagination Book {label}",
        f"Pagination Book {label}",
        "Author",
        modified,
        db.Books.DEFAULT_PUBDATE,
        "1.0",
        modified,
        f"pagination-book-{label}",
        0,
        [],
        [],
    )
    sync_harness.session.add(book)
    sync_harness.session.flush()
    book.uuid = f"00000000-0000-0000-0002-{book.id:012d}"
    sync_harness.session.add(db.Data(
        book.id,
        "EPUB",
        3_000_000 + sequence,
        f"pagination-book-{label}",
    ))
    return book


def _populate_library(sync_harness, count, *, tied=False):
    """Create a stable id-ordered candidate set of exactly ``count`` books."""
    base = datetime(2026, 1, 1)
    sync_harness.book.timestamp = base
    sync_harness.book.last_modified = base
    books = [sync_harness.book]
    books.extend(
        _add_library_book(
            sync_harness,
            sequence,
            base if tied else base + timedelta(seconds=sequence),
        )
        for sequence in range(1, count)
    )
    sync_harness.session.commit()
    return books


def _mutate_after_first_candidate_page(monkeypatch, mutate):
    """Run ``mutate`` after the real handler finishes its first query page."""
    from cps import kobo

    original_pages = kobo._bounded_query_pages
    mutation_state = {"calls": 0, "page_sizes": []}

    def pages_with_interleaved_mutation(*args, **kwargs):
        for page in original_pages(*args, **kwargs):
            mutation_state["page_sizes"].append(len(page))
            yield page
            if mutation_state["calls"] == 0:
                mutate()
                mutation_state["calls"] += 1

    monkeypatch.setattr(
        kobo, "_bounded_query_pages", pages_with_interleaved_mutation,
    )
    return mutation_state


def _acknowledge_all_live_books(sync_harness, books):
    """Walk returned cursors until every live entitlement is acknowledged."""
    from cps import kobo, ub

    token = None
    responses = []
    max_pages = (
        len(books) + kobo.SYNC_ITEM_LIMIT - 1
    ) // kobo.SYNC_ITEM_LIMIT
    for _page in range(max_pages):
        response = sync_harness.sync(token)
        responses.append(response)
        token = response.headers[sync_harness.token_header]
    assert sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).filter_by(device_id=sync_harness.device.id).count() == len(books)
    return responses


def test_exact_page_prefix_does_not_hide_changed_book_on_first_response(
    sync_harness, monkeypatch,
):
    """One changed book behind 100 exact rows is delivered immediately."""
    from cps import kobo

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    books = _populate_library(sync_harness, kobo.SYNC_ITEM_LIMIT + 1)
    _acknowledge_all_live_books(sync_harness, books)

    changed = books[-1]
    changed.last_modified = datetime(2027, 1, 1)
    sync_harness.session.commit()

    response = sync_harness.sync(None, acknowledge=False)
    entitlements = _wire_entitlements(response)

    assert len(entitlements) == 1
    assert entitlements[0][0] == "ChangedEntitlement"
    assert entitlements[0][1]["Id"] == str(changed.uuid)


def test_exact_page_prefix_does_not_hide_missing_ledger_book_on_first_response(
    sync_harness, monkeypatch,
):
    """One never-received row behind 100 exact rows remains New immediately."""
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    books = _populate_library(sync_harness, kobo.SYNC_ITEM_LIMIT + 1)
    _acknowledge_all_live_books(sync_harness, books)

    missing = books[-1]
    sync_harness.session.query(ub.KoboDeviceBookEntitlement).filter_by(
        device_id=sync_harness.device.id,
        book_id=missing.id,
    ).delete(synchronize_session=False)
    sync_harness.session.commit()

    response = sync_harness.sync(None, acknowledge=False)
    entitlements = _wire_entitlements(response)

    assert len(entitlements) == 1
    assert entitlements[0][0] == "NewEntitlement"
    assert entitlements[0][1]["Id"] == str(missing.uuid)


def test_exact_tombstone_prefix_does_not_hide_new_tombstone_on_first_response(
    sync_harness, monkeypatch,
):
    """One new deletion behind 100 acknowledged tombstones emits immediately."""
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    deleted_base = datetime(2026, 1, 1)
    for offset in range(kobo.SYNC_ITEM_LIMIT):
        sync_harness.session.add(ub.KoboDeletedBook(
            user_id=sync_harness.user.id,
            book_uuid=f"00000000-0000-0000-0003-{offset:012d}",
            deleted_at=deleted_base + timedelta(seconds=offset),
        ))
    sync_harness.session.commit()

    initial = sync_harness.sync()
    assert sum(
        entitlement["IsRemoved"] is True
        for _kind, entitlement in _wire_entitlements(initial)
    ) == kobo.SYNC_ITEM_LIMIT

    new_uuid = "00000000-0000-0000-0003-000000000100"
    sync_harness.session.add(ub.KoboDeletedBook(
        user_id=sync_harness.user.id,
        book_uuid=new_uuid,
        deleted_at=deleted_base + timedelta(seconds=kobo.SYNC_ITEM_LIMIT),
    ))
    sync_harness.session.commit()

    response = sync_harness.sync(None, acknowledge=False)
    removed = [
        entitlement for _kind, entitlement in _wire_entitlements(response)
        if entitlement["IsRemoved"] is True
    ]

    assert len(removed) == 1
    assert removed[0]["Id"] == new_uuid


def test_fully_suppressed_250_book_scan_is_bounded_and_terminal(
    sync_harness, monkeypatch,
):
    """A finite exact-only snapshot drains without continuation or over-scan."""
    from cps import kobo

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    books = _populate_library(sync_harness, 250)
    _acknowledge_all_live_books(sync_harness, books)

    original_fingerprint = kobo._entitlement_fingerprint
    rendered = []

    def counted_fingerprint(entitlement):
        rendered.append(entitlement["BookEntitlement"]["Id"])
        return original_fingerprint(entitlement)

    monkeypatch.setattr(kobo, "_entitlement_fingerprint", counted_fingerprint)
    response = sync_harness.sync(None, acknowledge=False)

    assert response.get_json() == []
    assert response.headers.get("x-kobo-sync") is None
    assert len(rendered) == len(set(rendered)) == 250


def test_candidate_growth_after_first_chunk_cannot_enter_captured_frontier(
    sync_harness, monkeypatch,
):
    """Rows inserted after identity capture are deferred to another request."""
    from cps import kobo

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    books = _populate_library(sync_harness, 250)
    _acknowledge_all_live_books(sync_harness, books)
    captured_uuids = {str(book.uuid) for book in books}

    original_fingerprint = kobo._entitlement_fingerprint
    rendered = []

    def counted_fingerprint(entitlement):
        rendered.append(entitlement["BookEntitlement"]["Id"])
        return original_fingerprint(entitlement)

    monkeypatch.setattr(kobo, "_entitlement_fingerprint", counted_fingerprint)

    def insert_later_candidates():
        later = datetime(2027, 1, 1)
        for sequence in range(10_000, 10_100):
            _add_library_book(
                sync_harness,
                sequence,
                later + timedelta(seconds=sequence),
            )
        sync_harness.session.commit()

    mutation = _mutate_after_first_candidate_page(
        monkeypatch, insert_later_candidates,
    )
    response = sync_harness.sync(None, acknowledge=False)

    assert mutation["calls"] == 1
    assert response.get_json() == []
    assert response.headers.get("x-kobo-sync") is None
    assert len(rendered) == len(set(rendered)) == 250
    assert set(rendered) == captured_uuids
    assert mutation["page_sizes"] == [100, 100, 50]


def test_candidate_shrink_after_first_chunk_cannot_skip_captured_row(
    sync_harness, monkeypatch,
):
    """Deleting an early candidate cannot shift row 101 past the next fetch."""
    from cps import db, kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    books = _populate_library(
        sync_harness, kobo.SYNC_ITEM_LIMIT + 1, tied=True,
    )
    _acknowledge_all_live_books(sync_harness, books)
    deliverable = books[-1]
    deliverable_uuid = str(deliverable.uuid)
    sync_harness.session.query(ub.KoboDeviceBookEntitlement).filter_by(
        device_id=sync_harness.device.id,
        book_id=deliverable.id,
    ).delete(synchronize_session=False)
    sync_harness.session.commit()

    def remove_early_candidate():
        sync_harness.session.query(db.Data).filter_by(
            book=books[0].id,
            format="EPUB",
        ).delete(synchronize_session=False)
        sync_harness.session.commit()

    mutation = _mutate_after_first_candidate_page(
        monkeypatch, remove_early_candidate,
    )
    response = sync_harness.sync(None, acknowledge=False)
    entitlements = _wire_entitlements(response)

    assert mutation["calls"] == 1
    assert len(entitlements) == 1
    assert entitlements[0][0] == "NewEntitlement"
    assert entitlements[0][1]["Id"] == deliverable_uuid
    assert mutation["page_sizes"] == [100, 1]
