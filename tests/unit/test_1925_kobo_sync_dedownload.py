# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression coverage for fork issue #1925.

An interrupted/abnormal device sync can lose CWNG's opaque sync token.  The
same physical device then presents a fresh cursor even though its library is
already populated.  Replaying an unchanged entitlement makes Nickel mark the
local book as not downloaded; a genuine Books.last_modified change must still
be delivered.
"""

from datetime import datetime, timedelta, timezone
import logging
from types import SimpleNamespace

import pytest
from flask import Flask, g
from sqlalchemy import create_engine, event, true
from sqlalchemy.orm import sessionmaker


pytestmark = pytest.mark.unit


def _entitlements(response):
    return [
        item for item in response.get_json()
        if "NewEntitlement" in item or "ChangedEntitlement" in item
    ]


def _changed_reading_states(response):
    return [
        item["ChangedReadingState"]["ReadingState"]
        for item in response.get_json()
        if "ChangedReadingState" in item
    ]


def _add_kobo_shelf(sync_harness, *, include_book=True, date_added=None):
    from cps import ub

    shelf = ub.Shelf(
        name="Regression Kobo Shelf",
        user_id=sync_harness.user.id,
        kobo_sync=True,
        uuid="issue-1925-regression-shelf",
        is_public=0,
    )
    sync_harness.session.add(shelf)
    sync_harness.session.flush()
    link = None
    if include_book:
        link = ub.BookShelf(
            book_id=sync_harness.book.id,
            shelf=shelf.id,
            order=1,
            date_added=date_added,
        )
        link.ub_shelf = shelf
        sync_harness.session.add(link)
    sync_harness.session.commit()
    return shelf, link


def _add_reading_state(sync_harness, modified, progress=42.0):
    from cps import ub

    read = ub.ReadBook(
        user_id=sync_harness.user.id,
        book_id=sync_harness.book.id,
        read_status=ub.ReadBook.STATUS_IN_PROGRESS,
    )
    state = ub.KoboReadingState(
        user_id=sync_harness.user.id,
        book_id=sync_harness.book.id,
        priority_timestamp=modified,
    )
    state.current_bookmark = ub.KoboBookmark(
        last_modified=modified,
        progress_percent=progress,
    )
    state.statistics = ub.KoboStatistics(last_modified=modified)
    read.kobo_reading_state = state
    sync_harness.session.add(read)
    sync_harness.session.commit()
    # The before_flush listener stamps the parent when the bookmark changes.
    sync_harness.session.query(ub.KoboReadingState).filter_by(
        user_id=sync_harness.user.id,
        book_id=sync_harness.book.id,
    ).update({ub.KoboReadingState.last_modified: modified})
    sync_harness.session.commit()
    sync_harness.session.expire_all()
    return sync_harness.session.query(ub.KoboReadingState).filter_by(
        user_id=sync_harness.user.id,
        book_id=sync_harness.book.id,
    ).one()


@pytest.fixture
def sync_harness(monkeypatch):
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

    modified = datetime(2026, 8, 28, 12, 0, 0)
    book = db.Books(
        "Stable Book",
        "Stable Book",
        "Author",
        modified,
        db.Books.DEFAULT_PUBDATE,
        "1.0",
        modified,
        "stable-book",
        0,
        [],
        [],
    )
    session.add(book)
    session.flush()
    book.uuid = "00000000-0000-0000-0000-000000001925"
    session.add(db.Data(book.id, "EPUB", 1_234_567, "stable-book"))
    device = ub.Device(
        user_id=17,
        kind="kobo",
        display_name="Regression Kobo",
        model="Kobo Clara BW",
        active=True,
        created_by="auto",
    )
    session.add(device)
    session.commit()

    user = SimpleNamespace(
        id=17,
        name="issue-1925-test",
        kobo_only_shelves_sync=False,
        role_download=lambda: True,
    )
    fake_calibre_db = SimpleNamespace(
        session=session,
        reconnect_db=lambda *_args, **_kwargs: None,
        common_filters=lambda **_kwargs: true(),
        get_book=lambda book_id: session.query(db.Books).filter_by(id=book_id).one_or_none(),
    )

    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(ub, "session_commit", lambda *_args, **_kwargs: session.commit())
    monkeypatch.setattr(kobo, "calibre_db", fake_calibre_db)
    monkeypatch.setattr(kobo, "current_user", user)
    monkeypatch.setattr(kobo_sync_status, "current_user", user)
    monkeypatch.setattr(kobo.config, "config_kobo_proxy", False, raising=False)
    monkeypatch.setattr(kobo.config, "config_kobo_sync_magic_shelves", False, raising=False)
    monkeypatch.setattr(kobo.config, "config_kobo_prefer_kepub", True, raising=False)
    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", False,
        raising=False,
    )
    monkeypatch.setattr(kobo.config, "config_kepubifypath", "/usr/bin/kepubify", raising=False)
    monkeypatch.setattr(kobo.config, "config_embed_metadata", True, raising=False)
    monkeypatch.setattr(kobo.config, "config_use_google_drive", False, raising=False)
    monkeypatch.setattr(kobo.config, "get_book_path", lambda: "/nonexistent")
    monkeypatch.setattr(kobo, "get_download_url_for_book", lambda book_id, fmt: f"/download/{book_id}/{fmt}")
    monkeypatch.setattr(kobo, "get_epub_layout", lambda *_args: "reflowable")
    monkeypatch.setattr(kobo, "get_magic_shelf_book_ids_for_kobo", lambda _user_id: (set(), True))
    monkeypatch.setattr(kobo, "get_magic_shelf_membership_added_at", lambda _user_id: None)
    monkeypatch.setattr(kobo, "sync_shelves", lambda *_args, **_kwargs: None)

    app = Flask(__name__)
    app.secret_key = "issue-1925-test-key"
    app.wsgi_app = SimpleNamespace(is_proxied=True)

    def sync(token=None, *, internal_device_id=None, raw_device_id=None):
        internal_device_id = internal_device_id or device.id
        raw_device_id = raw_device_id or ("a" * 64)
        headers = {
            "x-kobo-deviceid": raw_device_id,
            "x-kobo-devicemodel": "Kobo Clara BW",
        }
        if token is not None:
            headers[kobo.SyncToken.SyncToken.SYNC_TOKEN_HEADER] = token
        with app.test_request_context("/v1/library/sync", headers=headers):
            # The auth decorator normally sets this from x-kobo-deviceid.
            g.annotation_origin_device_id = internal_device_id
            return kobo.HandleSyncRequest.__wrapped__()

    yield SimpleNamespace(
        app=app,
        book=book,
        device=device,
        calibre_db=fake_calibre_db,
        session=session,
        sync=sync,
        token_header=kobo.SyncToken.SyncToken.SYNC_TOKEN_HEADER,
        user=user,
    )

    session.close()
    engine.dispose()


def test_interrupted_sync_token_loss_does_not_redeliver_unchanged_entitlement(
    sync_harness, caplog, monkeypatch,
):
    """Layer 2 suppresses an exact replay selected by a stale valid token."""
    from cps import kobo

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    caplog.set_level(logging.DEBUG, logger="cps.kobo")
    first = sync_harness.sync()
    assert len(_entitlements(first)) == 1

    # Model the safely distinguishable interrupted-sync case: the device sends
    # a valid CWNG token, but its local book cursors are behind the payload the
    # server already delivered. An entirely absent token is deliberately not
    # eligible because it is also the factory-reset signature.
    stale_cwng_token = kobo.SyncToken.SyncToken().build_sync_token()
    second = sync_harness.sync(stale_cwng_token)

    assert _entitlements(second) == [], (
        "an unchanged entitlement replay makes Nickel flip an already-downloaded "
        "book back to Download"
    )
    summaries = [
        record.getMessage() for record in caplog.records
        if record.getMessage().startswith("Kobo Sync summary:")
    ]
    assert len(summaries) == 2
    assert "entitlements new=0 changed=0 suppressed_unchanged=1" in summaries[-1]
    assert "replay_suppression enabled=True eligible=True" in summaries[-1]
    assert "cursors in=" in summaries[-1] and " out=" in summaries[-1]


def test_suppressed_entitlement_emits_newer_reading_state_once_and_advances_cursor(
    sync_harness, monkeypatch,
):
    """Layer 2 suppression must not suppress or loop reading-state changes."""
    from cps import db, kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    monkeypatch.setattr(kobo, "SYNC_ITEM_LIMIT", 1)
    sync_harness.user.kobo_only_shelves_sync = True
    shelf, _target_link = _add_kobo_shelf(
        sync_harness,
        date_added=datetime(2026, 8, 28, 12, 5, 0),
    )

    # Seed the per-device entitlement fingerprint without a reading state.
    assert len(_entitlements(sync_harness.sync())) == 1

    # Fill the (test-sized) independent reading-state page with an older,
    # legitimate library state. Before the fix, the suppressed book relied on
    # that later paged scan; its newer state was therefore withheld until
    # another sync. Keeping this book out of Data makes it reading-state-only
    # background, not an additional base entitlement in this regression.
    background_modified = datetime(2026, 8, 28, 12, 15, 0)
    background_book = db.Books(
        "Background State",
        "Background State",
        "Author",
        background_modified,
        db.Books.DEFAULT_PUBDATE,
        "1.0",
        background_modified,
        "background-state",
        0,
        [],
        [],
    )
    background_book.uuid = "10000000-0000-0000-0000-000000000001"
    sync_harness.session.add(background_book)
    sync_harness.session.flush()
    background_state = ub.KoboReadingState(
        user_id=17,
        book_id=background_book.id,
        priority_timestamp=background_modified,
    )
    background_state.current_bookmark = ub.KoboBookmark(
        last_modified=background_modified,
        progress_percent=1.0,
    )
    background_state.statistics = ub.KoboStatistics(
        last_modified=background_modified,
    )
    background_read = ub.ReadBook(
        user_id=17,
        book_id=background_book.id,
        read_status=ub.ReadBook.STATUS_IN_PROGRESS,
    )
    background_read.kobo_reading_state = background_state
    background_link = ub.BookShelf(
        book_id=background_book.id,
        shelf=shelf.id,
        order=2,
        date_added=datetime(2026, 8, 28, 12, 6, 0),
    )
    background_link.ub_shelf = shelf
    sync_harness.session.add_all([background_read, background_link])

    state_modified = datetime(2026, 8, 28, 12, 30, 0)
    read = ub.ReadBook(
        user_id=17,
        book_id=sync_harness.book.id,
        read_status=ub.ReadBook.STATUS_IN_PROGRESS,
    )
    state = ub.KoboReadingState(
        user_id=17,
        book_id=sync_harness.book.id,
        priority_timestamp=state_modified,
    )
    state.current_bookmark = ub.KoboBookmark(
        last_modified=state_modified,
        progress_percent=42.0,
    )
    state.statistics = ub.KoboStatistics(last_modified=state_modified)
    read.kobo_reading_state = state
    sync_harness.session.add(read)
    sync_harness.session.commit()
    # The before_flush hook deliberately stamps the parent when its bookmark
    # changes. Pin the cursor carrier after the graph has been flushed.
    sync_harness.session.query(ub.KoboReadingState).filter_by(
        user_id=17,
        book_id=sync_harness.book.id,
    ).update({ub.KoboReadingState.last_modified: state_modified})
    sync_harness.session.query(ub.KoboReadingState).filter(
        ub.KoboReadingState.user_id == 17,
        ub.KoboReadingState.book_id == background_book.id,
    ).update(
        {ub.KoboReadingState.last_modified: background_modified},
        synchronize_session=False,
    )
    sync_harness.session.commit()
    sync_harness.session.expire_all()

    # A valid but stale CWNG token selects the unchanged base entitlement and
    # the newer reading state together. Layer 2 may suppress only the former.
    stale_cwng_token = kobo.SyncToken.SyncToken().build_sync_token()
    changed = sync_harness.sync(stale_cwng_token)

    assert _entitlements(changed) == []
    target_states = [
        state for state in _changed_reading_states(changed)
        if state["EntitlementId"] == sync_harness.book.uuid
    ]
    assert len(target_states) == 1
    assert target_states[0]["CurrentBookmark"]["ProgressPercent"] == 42

    advanced_token = kobo.SyncToken.SyncToken.from_headers({
        sync_harness.token_header: changed.headers[sync_harness.token_header],
    })
    assert advanced_token.reading_state_last_modified == state_modified

    unchanged = sync_harness.sync(changed.headers[sync_harness.token_header])
    target_states_again = [
        state for state in _changed_reading_states(unchanged)
        if state["EntitlementId"] == sync_harness.book.uuid
    ]
    assert target_states_again == [], (
        "the advanced reading-state cursor must not re-offer the same state "
        "on the next sync"
    )


def test_shelf_only_unchanged_library_terminates_after_first_sync(sync_harness):
    """The household's shelf-only Kobo must not loop an unchanged shelf."""
    from cps import ub

    sync_harness.user.kobo_only_shelves_sync = True
    _add_kobo_shelf(
        sync_harness,
        date_added=datetime(2026, 8, 28, 12, 5, 0),
    )

    first = sync_harness.sync()
    second = sync_harness.sync(first.headers[sync_harness.token_header])

    assert len(_entitlements(first)) == 1
    assert _entitlements(second) == []
    assert sync_harness.session.query(ub.KoboDeviceBookEntitlement).count() == 0


def test_shelf_only_membership_addition_emits_once(sync_harness):
    """Adding an old book to a Kobo shelf must move the shelf cursor once."""
    from cps import ub

    sync_harness.user.kobo_only_shelves_sync = True
    shelf, _link = _add_kobo_shelf(sync_harness, include_book=False)
    empty = sync_harness.sync()
    assert _entitlements(empty) == []

    link = ub.BookShelf(
        book_id=sync_harness.book.id,
        shelf=shelf.id,
        order=1,
        date_added=datetime(2026, 8, 28, 12, 10, 0),
    )
    link.ub_shelf = shelf
    sync_harness.session.add(link)
    sync_harness.session.commit()

    added = sync_harness.sync(empty.headers[sync_harness.token_header])
    stable = sync_harness.sync(added.headers[sync_harness.token_header])

    assert len(_entitlements(added)) == 1
    assert _entitlements(stable) == []


def test_shelf_only_removal_command_and_ledger_cleanup_are_unchanged(
    sync_harness, monkeypatch,
):
    """Removing a shelf member still emits IsRemoved and clears both markers."""
    from cps import kobo, ub

    sync_harness.user.kobo_only_shelves_sync = True
    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    _shelf, link = _add_kobo_shelf(
        sync_harness,
        date_added=datetime(2026, 8, 28, 12, 5, 0),
    )
    first = sync_harness.sync()
    assert len(_entitlements(first)) == 1
    assert sync_harness.session.query(ub.KoboDeviceBookEntitlement).count() == 1

    sync_harness.session.delete(link)
    sync_harness.session.commit()
    removed = sync_harness.sync(first.headers[sync_harness.token_header])

    envelopes = _entitlements(removed)
    assert len(envelopes) == 1
    assert "ChangedEntitlement" in envelopes[0]
    assert envelopes[0]["ChangedEntitlement"]["BookEntitlement"]["IsRemoved"] is True
    assert sync_harness.session.query(ub.KoboSyncedBooks).count() == 0
    assert sync_harness.session.query(ub.KoboDeviceBookEntitlement).count() == 0


def test_shelf_only_magic_membership_failure_preserves_book_and_ledger(
    sync_harness, monkeypatch,
):
    """#468: an unreliable empty magic shelf must never remove a live book."""
    from cps import kobo, ub

    sync_harness.user.kobo_only_shelves_sync = True
    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    membership = {"ids": {sync_harness.book.id}, "reliable": True}
    membership_added = datetime(2026, 8, 28, 12, 20, 0)
    monkeypatch.setattr(
        kobo,
        "get_magic_shelf_book_ids_for_kobo",
        lambda _user_id: (set(membership["ids"]), membership["reliable"]),
    )
    monkeypatch.setattr(
        kobo,
        "get_magic_shelf_membership_added_at",
        lambda _user_id: membership_added,
    )

    first = sync_harness.sync()
    assert len(_entitlements(first)) == 1
    assert sync_harness.session.query(ub.KoboDeviceBookEntitlement).count() == 1

    membership["ids"] = set()
    membership["reliable"] = False
    failed_refresh = sync_harness.sync(first.headers[sync_harness.token_header])

    assert _entitlements(failed_refresh) == []
    assert sync_harness.session.query(ub.KoboSyncedBooks).count() == 1
    assert sync_harness.session.query(ub.KoboDeviceBookEntitlement).count() == 1
    assert sync_harness.session.query(ub.ArchivedBook).count() == 0


@pytest.mark.parametrize("shelf_only", [False, True])
def test_magic_shelf_membership_arm_emits_once_in_both_sync_modes(
    sync_harness, monkeypatch, shelf_only,
):
    """Magic-shelf users retain the one-shot membership cursor behavior."""
    from cps import kobo, ub

    sync_harness.user.kobo_only_shelves_sync = shelf_only
    membership_added = datetime(2026, 8, 28, 12, 20, 0)
    monkeypatch.setattr(
        kobo,
        "get_magic_shelf_book_ids_for_kobo",
        lambda _user_id: ({sync_harness.book.id}, True),
    )
    monkeypatch.setattr(
        kobo,
        "get_magic_shelf_membership_added_at",
        lambda _user_id: membership_added,
    )
    # Prevent the legacy empty-marker reset so this specifically exercises the
    # magic membership arm past an already-advanced book cursor.
    sync_harness.session.add(ub.KoboSyncedBooks(
        user_id=sync_harness.user.id,
        book_id=sync_harness.book.id,
        book_uuid=sync_harness.book.uuid,
    ))
    sync_harness.session.commit()
    advanced = kobo.SyncToken.SyncToken(
        books_last_modified=datetime(2026, 8, 28, 12, 10, 0),
        books_last_created=datetime(2026, 8, 28, 12, 10, 0),
    ).build_sync_token()

    membership_sync = sync_harness.sync(advanced)
    stable = sync_harness.sync(
        membership_sync.headers[sync_harness.token_header]
    )

    assert len(_entitlements(membership_sync)) == 1
    assert _entitlements(stable) == []
    parsed = kobo.SyncToken.SyncToken.from_headers({
        sync_harness.token_header:
            membership_sync.headers[sync_harness.token_header],
    })
    assert parsed.magic_shelf_membership_at == membership_added


def test_unsuppressed_reading_state_count_and_cursor_remain_one_shot(
    sync_harness,
):
    """Layer 2's refactor must not alter the normal reading-state feed."""
    from cps import kobo, ub

    first = sync_harness.sync()
    modified = datetime(2026, 8, 28, 12, 30, 0)
    _add_reading_state(sync_harness, modified, progress=37.0)

    changed = sync_harness.sync(first.headers[sync_harness.token_header])
    unchanged = sync_harness.sync(changed.headers[sync_harness.token_header])

    states = _changed_reading_states(changed)
    assert len(states) == 1
    assert states[0]["CurrentBookmark"]["ProgressPercent"] == 37
    assert _changed_reading_states(unchanged) == []
    parsed = kobo.SyncToken.SyncToken.from_headers({
        sync_harness.token_header: changed.headers[sync_harness.token_header],
    })
    assert parsed.reading_state_last_modified == modified
    assert sync_harness.session.query(ub.KoboDeviceBookEntitlement).count() == 0


def test_payload_stabilization_replays_byte_identically_with_layer2_off(
    sync_harness,
):
    """Layer 1 is default-safe: replay unchanged, byte-identical payloads."""
    from cps import ub

    first = _entitlements(sync_harness.sync())
    second = _entitlements(sync_harness.sync())

    assert len(first) == len(second) == 1
    assert first == second
    assert sync_harness.session.query(ub.KoboDeviceBookEntitlement).count() == 0


@pytest.mark.parametrize("reset_token", [None, "not-a-token", "store.part"])
def test_factory_reset_escape_never_suppresses_without_valid_cwng_token(
    sync_harness, monkeypatch, reset_token,
):
    """Known hardware with an empty library must receive a complete replay."""
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    assert len(_entitlements(sync_harness.sync())) == 1
    assert sync_harness.session.query(ub.KoboDeviceBookEntitlement).count() == 1

    reset_response = sync_harness.sync(reset_token)

    assert len(_entitlements(reset_response)) == 1


def test_entitlement_replay_state_is_per_device(sync_harness, monkeypatch):
    """One Kobo's delivery must never suppress another Kobo's first copy."""
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    first = sync_harness.sync()
    second_device = ub.Device(
        user_id=17,
        kind="kobo",
        display_name="Regression Kobo 2",
        model="Kobo Libra Colour",
        active=True,
        created_by="auto",
    )
    sync_harness.session.add(second_device)
    sync_harness.session.commit()

    first_for_second_device = sync_harness.sync(
        kobo.SyncToken.SyncToken().build_sync_token(),
        internal_device_id=second_device.id,
        raw_device_id="b" * 64,
    )

    assert len(_entitlements(first_for_second_device)) == 1


def test_second_device_has_no_cross_device_state_when_layer2_is_off(sync_harness):
    """The default-off layer writes no ledger and does not starve device two."""
    from cps import kobo, ub

    first_device = sync_harness.sync()
    assert len(_entitlements(first_device)) == 1
    assert sync_harness.session.query(ub.KoboDeviceBookEntitlement).count() == 0

    second_device = ub.Device(
        user_id=sync_harness.user.id,
        kind="kobo",
        display_name="Household Shelf Kobo",
        model="Kobo Libra Colour",
        active=True,
        created_by="auto",
    )
    sync_harness.session.add(second_device)
    sync_harness.session.commit()
    first_for_second = sync_harness.sync(
        kobo.SyncToken.SyncToken().build_sync_token(),
        internal_device_id=second_device.id,
        raw_device_id="b" * 64,
    )
    stable_for_second = sync_harness.sync(
        first_for_second.headers[sync_harness.token_header],
        internal_device_id=second_device.id,
        raw_device_id="b" * 64,
    )

    assert len(_entitlements(first_for_second)) == 1
    assert _entitlements(stable_for_second) == []
    assert sync_harness.session.query(ub.KoboDeviceBookEntitlement).count() == 0


def _seed_other_user_ledger(sync_harness):
    from cps import ub

    other_device = ub.Device(
        user_id=18,
        kind="kobo",
        display_name="Other Account Kobo",
        model="Kobo Libra Colour",
        active=True,
        created_by="auto",
    )
    sync_harness.session.add(other_device)
    sync_harness.session.flush()
    sync_harness.session.add_all([
        ub.KoboSyncedBooks(
            user_id=18,
            book_id=sync_harness.book.id,
            book_uuid=sync_harness.book.uuid,
        ),
        ub.KoboDeviceBookEntitlement(
            device_id=other_device.id,
            book_id=sync_harness.book.id,
            fingerprint="f" * 64,
        ),
    ])
    sync_harness.session.commit()
    return other_device


def _seed_same_user_device_ledger(sync_harness):
    from cps import ub

    second_device = ub.Device(
        user_id=sync_harness.user.id,
        kind="kobo",
        display_name="Second Target Kobo",
        model="Kobo Clara BW",
        active=True,
        created_by="auto",
    )
    sync_harness.session.add(second_device)
    sync_harness.session.flush()
    sync_harness.session.add(ub.KoboDeviceBookEntitlement(
        device_id=second_device.id,
        book_id=sync_harness.book.id,
        fingerprint="e" * 64,
    ))
    sync_harness.session.commit()
    return second_device


def test_full_sync_clears_only_target_users_entitlement_ledger(
    sync_harness, monkeypatch,
):
    """Full Sync clears every target device without touching another account."""
    from cps import admin, kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    first = sync_harness.sync()
    _seed_same_user_device_ledger(sync_harness)
    other_device = _seed_other_user_ledger(sync_harness)
    monkeypatch.setattr(admin, "_", lambda value: value)

    with sync_harness.app.test_request_context("/ajax/fullsync/17", method="POST"):
        response = admin.do_full_kobo_sync(sync_harness.user.id)

    assert response.status_code == 200
    rows = sync_harness.session.query(ub.KoboDeviceBookEntitlement).all()
    assert [(row.device_id, row.book_id) for row in rows] == [
        (other_device.id, sync_harness.book.id),
    ]
    assert {
        row.user_id for row in sync_harness.session.query(ub.KoboSyncedBooks)
    } == {18}

    replay = sync_harness.sync(first.headers[sync_harness.token_header])
    replay_envelopes = _entitlements(replay)
    assert len(replay_envelopes) == 1
    assert "NewEntitlement" in replay_envelopes[0]
    assert {
        row.device_id
        for row in sync_harness.session.query(ub.KoboDeviceBookEntitlement)
    } == {sync_harness.device.id, other_device.id}


def test_admin_resend_clears_target_users_entitlement_ledger(
    sync_harness, monkeypatch,
):
    """A requested resend must not be suppressed by its own stale fingerprint."""
    from cps import admin, kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    first = sync_harness.sync()
    _seed_same_user_device_ledger(sync_harness)
    other_device = _seed_other_user_ledger(sync_harness)
    monkeypatch.setattr(admin, "calibre_db", sync_harness.calibre_db)
    monkeypatch.setattr(admin, "_", lambda value: value)
    before = sync_harness.book.last_modified

    with sync_harness.app.test_request_context(
        f"/ajax/kobo_resend/{sync_harness.user.id}/{sync_harness.book.id}",
        method="POST",
    ):
        response = admin.do_kobo_resend(
            sync_harness.user.id, sync_harness.book.id,
        )

    assert response.status_code == 200
    assert sync_harness.book.last_modified > before
    rows = sync_harness.session.query(ub.KoboDeviceBookEntitlement).all()
    assert [(row.device_id, row.book_id) for row in rows] == [
        (other_device.id, sync_harness.book.id),
    ]
    assert {
        row.user_id for row in sync_harness.session.query(ub.KoboSyncedBooks)
    } == {18}

    replay = sync_harness.sync(first.headers[sync_harness.token_header])
    replay_envelopes = _entitlements(replay)
    assert len(replay_envelopes) == 1
    assert "NewEntitlement" in replay_envelopes[0]
    assert {
        row.device_id
        for row in sync_harness.session.query(ub.KoboDeviceBookEntitlement)
    } == {sync_harness.device.id, other_device.id}


def test_admin_resend_missing_book_preserves_all_sync_state(
    sync_harness, monkeypatch,
):
    """Validation must precede every ledger/marker mutation."""
    from cps import admin, kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    sync_harness.sync()
    second_device = _seed_same_user_device_ledger(sync_harness)
    monkeypatch.setattr(admin, "calibre_db", sync_harness.calibre_db)
    monkeypatch.setattr(admin, "_", lambda value: value)

    with sync_harness.app.test_request_context(
        f"/ajax/kobo_resend/{sync_harness.user.id}/999999",
        method="POST",
    ):
        response = admin.do_kobo_resend(sync_harness.user.id, 999999)

    assert response.status_code == 200
    assert response.get_json()[0]["type"] == "danger"
    assert {
        row.device_id
        for row in sync_harness.session.query(ub.KoboDeviceBookEntitlement)
    } == {sync_harness.device.id, second_device.id}
    assert sync_harness.session.query(ub.KoboSyncedBooks).count() == 1


def test_unsync_scopes_ledger_to_current_user_and_all_mode_clears_everyone(
    sync_harness, monkeypatch,
):
    """Ordinary unsync is account-scoped; all=True remains the global escape."""
    from cps import kobo, kobo_sync_status, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    sync_harness.sync()
    _seed_same_user_device_ledger(sync_harness)
    other_device = _seed_other_user_ledger(sync_harness)

    kobo_sync_status.remove_synced_book(
        sync_harness.book.id,
        all=False,
        session=sync_harness.session,
    )
    rows = sync_harness.session.query(ub.KoboDeviceBookEntitlement).all()
    assert [(row.device_id, row.book_id) for row in rows] == [
        (other_device.id, sync_harness.book.id),
    ]
    assert {
        row.user_id for row in sync_harness.session.query(ub.KoboSyncedBooks)
    } == {18}

    kobo_sync_status.remove_synced_book(
        sync_harness.book.id,
        all=True,
        session=sync_harness.session,
    )
    assert sync_harness.session.query(ub.KoboDeviceBookEntitlement).count() == 0
    assert sync_harness.session.query(ub.KoboSyncedBooks).count() == 0


def test_real_last_modified_bump_still_emits_changed_entitlement(
    sync_harness, monkeypatch,
):
    """Per-device replay suppression must not mask a real library change."""
    from cps import kobo

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    first = sync_harness.sync()
    first_token = first.headers[sync_harness.token_header]
    original_last_modified = sync_harness.book.last_modified

    sync_harness.book.last_modified = original_last_modified + timedelta(minutes=1)
    sync_harness.session.commit()
    changed = sync_harness.sync(first_token)

    envelopes = _entitlements(changed)
    assert len(envelopes) == 1
    assert "ChangedEntitlement" in envelopes[0]
    assert (
        envelopes[0]["ChangedEntitlement"]["BookEntitlement"]["LastModified"]
        == "2026-08-28T12:01:00Z"
    )


def test_entitlement_declared_fields_are_byte_stable_for_unchanged_book(
    sync_harness, monkeypatch,
):
    """No wall-clock field may mutate an unchanged entitlement payload."""
    from cps import kobo

    class AdvancingClock:
        calls = 0
        min = datetime.min

        @classmethod
        def now(cls, _tz=None):
            cls.calls += 1
            return datetime(2026, 8, 28, 13, cls.calls, tzinfo=timezone.utc)

    # Before the fix, ActivePeriod called datetime.now() and these two calls
    # differed. The stable implementation does not consult this clock.
    monkeypatch.setattr(kobo, "datetime", AdvancingClock)
    with Flask(__name__).test_request_context("/v1/library/sync"):
        first = kobo.create_book_entitlement(sync_harness.book, archived=False)
        second = kobo.create_book_entitlement(sync_harness.book, archived=False)

    assert first == second
    assert first["ActivePeriod"]["From"] == first["Created"]


def test_invalid_legacy_timestamp_fallback_is_byte_stable():
    """A malformed unchanged row must not inherit response wall-clock time."""
    from cps import kobo

    assert kobo.convert_to_kobo_timestamp_string(None) == "1970-01-01T00:00:00Z"


def test_generated_kepub_does_not_declare_source_epub_size(sync_harness):
    """A download-time generated KEPUB must not advertise the EPUB's size."""
    from cps import kobo

    app = Flask(__name__)
    app.wsgi_app = SimpleNamespace(is_proxied=True)
    with app.test_request_context("/v1/library/sync"):
        download = kobo.get_metadata(sync_harness.book)["DownloadUrls"][0]

    assert download["Format"] == "KEPUB"
    assert download["Url"] == f"/download/{sync_harness.book.id}/kepub"
    assert download["Platform"] == "Generic"
    assert download["DrmType"] == "None"
    assert "Size" not in download, (
        "the source EPUB size is not the size of the KEPUB bytes served after "
        "download-time conversion/metadata rewriting"
    )


def test_exact_stored_epub_keeps_truthful_declared_size(sync_harness, monkeypatch):
    """Only generated artifacts lose Size; exact stored downloads retain it."""
    from cps import kobo

    monkeypatch.setattr(kobo.config, "config_embed_metadata", False, raising=False)
    stored_epub = SimpleNamespace(format="EPUB", uncompressed_size=321)
    with Flask(__name__).test_request_context("/v1/library/sync"):
        download = kobo.build_download_url(
            sync_harness.book, stored_epub, "epub", "EPUB3",
        )

    assert download["Size"] == 321


def test_metadata_rewritten_epub_does_not_declare_stored_size(
    sync_harness, monkeypatch,
):
    """Metadata embedding makes an EPUB Data-row size inexact as well."""
    from cps import kobo

    monkeypatch.setattr(kobo.config, "config_embed_metadata", True, raising=False)
    stored_epub = SimpleNamespace(format="EPUB", uncompressed_size=321)
    with Flask(__name__).test_request_context("/v1/library/sync"):
        download = kobo.build_download_url(
            sync_harness.book, stored_epub, "epub", "EPUB3",
        )

    assert "Size" not in download
    assert download == {
        "Format": "EPUB3",
        "Url": f"/download/{sync_harness.book.id}/epub",
        "Platform": "Generic",
        "DrmType": "None",
    }


def test_rewritten_stored_epub_and_kepub_keep_complete_download_fields(
    sync_harness, monkeypatch,
):
    """Omitting inexact Size must not damage any URL/format/DRM field."""
    from cps import db, kobo

    monkeypatch.setattr(kobo.config, "config_embed_metadata", True, raising=False)
    monkeypatch.setattr(kobo.config, "config_kobo_prefer_kepub", False, raising=False)
    with Flask(__name__).test_request_context("/v1/library/sync"):
        epub_urls = kobo.get_metadata(sync_harness.book)["DownloadUrls"]
    assert epub_urls == [
        {
            "Format": "EPUB3",
            "Url": f"/download/{sync_harness.book.id}/epub",
            "Platform": "Generic",
            "DrmType": "None",
        },
        {
            "Format": "EPUB",
            "Url": f"/download/{sync_harness.book.id}/epub",
            "Platform": "Generic",
            "DrmType": "None",
        },
    ]

    sync_harness.session.add(db.Data(
        sync_harness.book.id, "KEPUB", 1_345_678, "stable-book",
    ))
    sync_harness.session.commit()
    sync_harness.session.expire(sync_harness.book, ["data"])
    monkeypatch.setattr(kobo.config, "config_kobo_prefer_kepub", True, raising=False)
    with Flask(__name__).test_request_context("/v1/library/sync"):
        kepub_urls = kobo.get_metadata(sync_harness.book)["DownloadUrls"]
    assert kepub_urls == [{
        "Format": "KEPUB",
        "Url": f"/download/{sync_harness.book.id}/kepub",
        "Platform": "Generic",
        "DrmType": "None",
    }]


@pytest.mark.parametrize("network_share_mode", [False, True])
@pytest.mark.parametrize("download_case", [
    "deferred_epub_to_kepub",
    "rewritten_stored_epub",
    "rewritten_stored_kepub",
])
def test_size_omission_paths_still_serve_the_kobo_download_route(
    tmp_path, monkeypatch, network_share_mode, download_case,
):
    """Every artifact whose entitlement omits Size still reaches the wire."""
    import inspect

    from cps import helper, kobo

    if network_share_mode:
        monkeypatch.setenv("NETWORK_SHARE_MODE", "true")
    else:
        monkeypatch.delenv("NETWORK_SHARE_MODE", raising=False)

    library = tmp_path / "library"
    book_dir = library / "Author" / "Book"
    book_dir.mkdir(parents=True)
    book = SimpleNamespace(
        id=1925,
        uuid="route-1925",
        title="Route Book",
        path="Author/Book",
        authors=[SimpleNamespace(name="Author")],
    )
    epub = SimpleNamespace(format="EPUB", name="stable-book", uncompressed_size=11)
    kepub = SimpleNamespace(format="KEPUB", name="stable-book", uncompressed_size=13)
    converted = {"ready": False}

    if download_case == "deferred_epub_to_kepub":
        requested_format = "kepub"
        expected_bytes = b"deferred-kepub-bytes"
        (book_dir / "stable-book.epub").write_bytes(b"source-epub-bytes")

        def get_format(_book_id, fmt):
            if fmt == "EPUB":
                return epub
            if fmt == "KEPUB" and converted["ready"]:
                return kepub
            return None

        def convert(*_args, **kwargs):
            assert kwargs == {"blocking": True, "timeout": 25}
            (book_dir / "stable-book.kepub").write_bytes(expected_bytes)
            converted["ready"] = True
            return None

        monkeypatch.setattr(helper, "convert_book_format", convert)
        embed_metadata = False
    elif download_case == "rewritten_stored_epub":
        requested_format = "epub"
        expected_bytes = b"rewritten-epub-bytes"
        (book_dir / "stable-book.epub").write_bytes(expected_bytes)
        get_format = lambda _book_id, fmt: epub if fmt == "EPUB" else None
        monkeypatch.setattr(
            helper,
            "do_calibre_export",
            lambda *_args, **_kwargs: (str(book_dir), "stable-book"),
        )
        embed_metadata = True
    else:
        requested_format = "kepub"
        expected_bytes = b"rewritten-kepub-bytes"
        (book_dir / "stable-book.kepub").write_bytes(expected_bytes)
        get_format = lambda _book_id, fmt: kepub if fmt == "KEPUB" else None
        monkeypatch.setattr(
            helper,
            "do_kepubify_metadata_replace",
            lambda *_args, **_kwargs: (str(book_dir), "stable-book"),
        )
        embed_metadata = True

    monkeypatch.setattr(
        helper.calibre_db,
        "get_filtered_book",
        lambda *_args, **_kwargs: book,
    )
    monkeypatch.setattr(helper.calibre_db, "get_book_format", get_format)
    monkeypatch.setattr(
        helper,
        "current_user",
        SimpleNamespace(is_authenticated=False, role_admin=lambda: False),
    )
    monkeypatch.setattr(helper.config, "config_use_google_drive", False, raising=False)
    monkeypatch.setattr(helper.config, "config_embed_metadata", embed_metadata, raising=False)
    monkeypatch.setattr(helper.config, "config_binariesdir", "/bin", raising=False)
    monkeypatch.setattr(helper.config, "config_kepubifypath", "/bin/kepubify", raising=False)
    monkeypatch.setattr(helper.config, "config_kobo_prefer_kepub", True, raising=False)
    monkeypatch.setattr(helper.config, "config_unicode_filename", False, raising=False)
    monkeypatch.setattr(helper.config, "get_book_path", lambda: str(library), raising=False)

    app = Flask(__name__)
    with app.test_request_context(
        f"/kobo/token/download/{book.id}/{requested_format}"
    ):
        response = inspect.unwrap(kobo.download_book)(
            str(book.id), requested_format,
        )

    assert response.status_code == 200
    response.direct_passthrough = False
    assert response.get_data() == expected_bytes
    assert "attachment" in response.headers["Content-Disposition"]
    if requested_format == "kepub":
        assert ".kepub.epub" in response.headers["Content-Disposition"]


def test_device_entitlement_table_is_created_by_app_db_migration_path():
    """An existing app.db missing the new ledger receives it at startup."""
    from cps import ub
    from sqlalchemy import inspect as sa_inspect

    engine = create_engine("sqlite:///:memory:")
    session = sessionmaker(bind=engine)()
    try:
        # Create the existing referenced table but deliberately omit the new
        # ledger, then exercise the same additive path migrate_Database calls.
        ub.Device.__table__.create(bind=engine)
        assert "kobo_device_book_entitlement" not in sa_inspect(engine).get_table_names()
        ub.add_missing_tables(engine, session)
        assert "kobo_device_book_entitlement" in sa_inspect(engine).get_table_names()
    finally:
        session.close()
        engine.dispose()


def test_replay_suppression_config_migrates_and_defaults_off():
    """Layer 2 must remain dormant on both upgrades and fresh installs."""
    from cps import config_sql
    from sqlalchemy import text

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE settings (id INTEGER PRIMARY KEY)"))
        connection.execute(text("INSERT INTO settings (id) VALUES (1)"))
    session = sessionmaker(bind=engine)()
    try:
        config_sql._migrate_table(session, config_sql._Settings)
        assert session.execute(text(
            "SELECT config_kobo_suppress_replayed_entitlements FROM settings"
        )).scalar() == 0

        fresh_engine = create_engine("sqlite:///:memory:")
        try:
            config_sql._Base.metadata.create_all(fresh_engine)
            fresh_session = sessionmaker(bind=fresh_engine)()
            fresh_session.add(config_sql._Settings())
            fresh_session.commit()
            assert (
                fresh_session.query(config_sql._Settings).one()
                .config_kobo_suppress_replayed_entitlements is False
            )
            fresh_session.close()
        finally:
            fresh_engine.dispose()
    finally:
        session.close()
        engine.dispose()


def test_layer2_provenance_requires_cwng_core_cursor_fields():
    """Permissive legacy-token fallback is not suppression authorization."""
    from cps.services import SyncToken

    emitted = SyncToken.SyncToken().build_sync_token()
    parsed_emitted = SyncToken.SyncToken.from_headers({
        SyncToken.SyncToken.SYNC_TOKEN_HEADER: emitted,
    })
    permissive_legacy = SyncToken.b64encode_json({
        "version": SyncToken.SyncToken.VERSION,
        "data": {},
    })
    parsed_legacy = SyncToken.SyncToken.from_headers({
        SyncToken.SyncToken.SYNC_TOKEN_HEADER: permissive_legacy,
    })

    assert parsed_emitted.is_cwng_token is True
    assert parsed_legacy.is_cwng_token is False


def test_legacy_token_missing_additive_fields_keeps_old_cursors_sane():
    """Pre-books-id/magic tokens remain valid and receive safe defaults."""
    from cps.services import SyncToken

    legacy = SyncToken.b64encode_json({
        "version": "1-1-0",
        "data": {
            "raw_kobo_store_token": "",
            "books_last_modified": 1735689600.0,
            "books_last_created": 1735689600.0,
            "archive_last_modified": 1735689600.0,
            "reading_state_last_modified": 1735689600.0,
            "tags_last_modified": 1735689600.0,
            # No books_last_id, magic_shelf_last_id, or membership timestamp.
        },
    })

    parsed = SyncToken.SyncToken.from_headers({
        SyncToken.SyncToken.SYNC_TOKEN_HEADER: legacy,
    })

    assert parsed.is_cwng_token is True
    assert parsed.books_last_modified == datetime(2025, 1, 1)
    assert parsed.books_last_id == -1
    assert parsed.magic_shelf_last_id == -1
    assert parsed.magic_shelf_membership_at == datetime.min


def test_partial_legacy_and_store_tokens_degrade_without_exception():
    """Missing legacy cursors and official-store tokens are tolerant but unsafe to suppress."""
    from cps.services import SyncToken

    partial = SyncToken.b64encode_json({
        "version": "1-0-0",
        "data": {
            "raw_kobo_store_token": "",
            "books_last_modified": 1735689600.0,
            # Older/partial shape: no remaining core cursors.
        },
    })
    parsed_partial = SyncToken.SyncToken.from_headers({
        SyncToken.SyncToken.SYNC_TOKEN_HEADER: partial,
    })
    parsed_store = SyncToken.SyncToken.from_headers({
        SyncToken.SyncToken.SYNC_TOKEN_HEADER: "official.store-token",
    })

    assert parsed_partial.books_last_modified == datetime(2025, 1, 1)
    assert parsed_partial.reading_state_last_modified == datetime.min
    assert parsed_partial.books_last_id == -1
    assert parsed_partial.is_cwng_token is False
    assert parsed_store.raw_kobo_store_token == "official.store-token"
    assert parsed_store.books_last_modified == datetime.min
    assert parsed_store.is_cwng_token is False


def test_sync_summary_handles_store_min_and_nullable_cursor_shapes(
    sync_harness, caplog,
):
    """The permanent DEBUG diagnostic must never become a sync failure."""
    from cps import kobo

    caplog.set_level(logging.DEBUG, logger="cps.kobo")
    response = sync_harness.sync("official.store-token")
    assert response.status_code == 200
    summaries = [
        record.getMessage() for record in caplog.records
        if record.getMessage().startswith("Kobo Sync summary:")
    ]
    assert len(summaries) == 1
    assert "entitlements new=1 changed=0" in summaries[0]
    assert "cursors in=" in summaries[0] and " out=" in summaries[0]

    nullable = SimpleNamespace(
        books_last_modified=None,
        books_last_id=None,
        books_last_created=datetime.min,
        archive_last_modified=None,
        reading_state_last_modified=datetime.min,
        tags_last_modified=None,
        magic_shelf_last_id=None,
        magic_shelf_membership_at=datetime.min,
    )
    assert kobo._sync_cursor_summary(nullable) == (
        None, None, datetime.min, None, datetime.min, None, None, datetime.min,
    )
