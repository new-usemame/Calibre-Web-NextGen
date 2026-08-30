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
import re
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


def _add_kobo_shelf(
    sync_harness,
    *,
    include_book=True,
    date_added=None,
    name="Regression Kobo Shelf",
    shelf_uuid="issue-1925-regression-shelf",
):
    from cps import ub

    shelf = ub.Shelf(
        name=name,
        user_id=sync_harness.user.id,
        kobo_sync=True,
        uuid=shelf_uuid,
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
        get_book_by_uuid_for_kobo=lambda book_uuid, **_kwargs: session.query(
            db.Books,
        ).filter_by(uuid=str(book_uuid)).one_or_none(),
    )

    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(
        ub,
        "session_commit",
        lambda *_args, **_kwargs: session.commit() or True,
    )
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
    real_sync_shelves = kobo.sync_shelves
    monkeypatch.setattr(kobo, "sync_shelves", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        kobo, "push_reading_state_to_hardcover", lambda *_args: None,
    )
    monkeypatch.setattr(
        kobo, "share_kobo_progress_with_koreader", lambda *_args: None,
    )

    app = Flask(__name__)
    app.secret_key = "issue-1925-test-key"
    app.wsgi_app = SimpleNamespace(is_proxied=True)

    def sync(token=None, *, internal_device_id=None, raw_device_id=None):
        internal_device_id = internal_device_id or device.id
        raw_device_id = raw_device_id or ("a" * 64)
        speaking_device = session.get(ub.Device, internal_device_id)
        headers = {
            "x-kobo-deviceid": raw_device_id,
            "x-kobo-devicemodel": (
                speaking_device.model if speaking_device else "Kobo Clara BW"
            ),
        }
        if token is not None:
            headers[kobo.SyncToken.SyncToken.SYNC_TOKEN_HEADER] = token
        with app.test_request_context("/v1/library/sync", headers=headers):
            # The auth decorator normally sets this from x-kobo-deviceid.
            g.annotation_origin_device_id = internal_device_id
            return kobo.HandleSyncRequest.__wrapped__()

    def put_position(percent, *, clock, internal_device_id=None):
        internal_device_id = internal_device_id or device.id
        payload = {"ReadingStates": [{
            "LastModified": clock,
            "CurrentBookmark": {
                "ProgressPercent": percent,
                "ContentSourceProgressPercent": percent,
                "Location": {
                    "Value": "device.{}".format(percent),
                    "Type": "KoboSpan",
                    "Source": "kepub",
                },
            },
            "Statistics": {
                "SpentReadingMinutes": 0,
                "RemainingTimeMinutes": 100,
            },
            "StatusInfo": {"Status": "Reading"},
        }]}
        with app.test_request_context(
                "/v1/library/{}/state".format(book.uuid),
                method="PUT",
                json=payload):
            g.annotation_origin_device_id = internal_device_id
            return app.make_response(
                kobo.HandleStateRequest.__wrapped__(book.uuid),
            )

    yield SimpleNamespace(
        app=app,
        book=book,
        device=device,
        put_position=put_position,
        real_sync_shelves=real_sync_shelves,
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


def test_same_version_payload_mismatch_delivers_and_restamps(
    sync_harness, caplog, monkeypatch,
):
    """An undeclared renderer change fails open for non-bumping writers."""
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    caplog.set_level(logging.DEBUG, logger="cps.kobo")
    assert len(_entitlements(sync_harness.sync())) == 1
    before = sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).one()
    old_fingerprint = before.fingerprint

    original_get_metadata = kobo.get_metadata

    def shape_b(book):
        payload = original_get_metadata(book)
        payload["Issue1953ShapeProbe"] = "shape-b"
        return payload

    monkeypatch.setattr(kobo, "get_metadata", shape_b)
    stale_token = kobo.SyncToken.SyncToken().build_sync_token()
    replay = sync_harness.sync(stale_token)

    assert len(_entitlements(replay)) == 1
    sync_harness.session.expire_all()
    after = sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).one()
    assert after.fingerprint != old_fingerprint
    assert (
        after.payload_schema_version
        == kobo.ENTITLEMENT_PAYLOAD_SCHEMA_VERSION
    )
    assert _entitlements(sync_harness.sync(stale_token)) == []
    summaries = [
        record.getMessage() for record in caplog.records
        if record.getMessage().startswith("Kobo Sync summary:")
    ]
    assert "reseeded_shape_change=0" in summaries[-1]


def test_declared_payload_schema_transition_reseeds_without_delivery(
    sync_harness, caplog, monkeypatch,
):
    """A declared renderer transition rewrites an unchanged book in place."""
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    caplog.set_level(logging.DEBUG, logger="cps.kobo")
    assert len(_entitlements(sync_harness.sync())) == 1
    before = sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).one()
    old_fingerprint = before.fingerprint
    old_basis = before.change_basis
    next_schema = kobo.ENTITLEMENT_PAYLOAD_SCHEMA_VERSION + 1

    original_get_metadata = kobo.get_metadata

    def shape_b(book):
        payload = original_get_metadata(book)
        payload["Issue1953ShapeProbe"] = "shape-b"
        return payload

    monkeypatch.setattr(kobo, "get_metadata", shape_b)
    monkeypatch.setattr(
        kobo, "ENTITLEMENT_PAYLOAD_SCHEMA_VERSION", next_schema,
    )
    stale_token = kobo.SyncToken.SyncToken().build_sync_token()
    replay = sync_harness.sync(stale_token)

    assert _entitlements(replay) == []
    sync_harness.session.expire_all()
    after = sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).one()
    assert after.fingerprint != old_fingerprint
    assert after.change_basis == old_basis
    assert after.payload_schema_version == next_schema
    summaries = [
        record.getMessage() for record in caplog.records
        if record.getMessage().startswith("Kobo Sync summary:")
    ]
    assert "reseeded_shape_change=1" in summaries[-1]


def test_failed_live_reseed_commit_aborts_before_summary(
    sync_harness, caplog, monkeypatch,
):
    """A rolled-back live ledger update is neither returned nor reported."""
    from cps import kobo, ub
    from werkzeug.exceptions import ServiceUnavailable

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    caplog.set_level(logging.DEBUG, logger="cps.kobo")
    assert len(_entitlements(sync_harness.sync())) == 1
    before = sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).one()
    old_fingerprint = before.fingerprint
    old_version = before.payload_schema_version
    caplog.clear()

    original_get_metadata = kobo.get_metadata

    def shape_b(book):
        payload = original_get_metadata(book)
        payload["Issue1953ShapeProbe"] = "shape-b"
        return payload

    def fail_commit(*_args, **_kwargs):
        sync_harness.session.rollback()
        return False

    monkeypatch.setattr(kobo, "get_metadata", shape_b)
    monkeypatch.setattr(
        kobo,
        "ENTITLEMENT_PAYLOAD_SCHEMA_VERSION",
        kobo.ENTITLEMENT_PAYLOAD_SCHEMA_VERSION + 1,
    )
    monkeypatch.setattr(ub, "session_commit", fail_commit)
    stale_token = kobo.SyncToken.SyncToken().build_sync_token()

    with pytest.raises(ServiceUnavailable) as raised:
        sync_harness.sync(stale_token)

    assert raised.value.code == 503
    sync_harness.session.expire_all()
    after = sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).one()
    assert after.fingerprint == old_fingerprint
    assert after.payload_schema_version == old_version
    assert not any(
        record.getMessage().startswith("Kobo Sync summary:")
        for record in caplog.records
    )


def test_entitlement_payload_shape_matches_declared_schema_version(
    sync_harness, monkeypatch,
):
    """Pin the complete renderer output beside its declared schema version."""
    from cps import db, kobo

    book = sync_harness.book
    book.title = "Pinned Entitlement Title"
    book.sort = "Entitlement Title, Pinned"
    book.author_sort = "Author, Pinned"
    book.pubdate = datetime(2020, 2, 3, 4, 5, 6)
    book.series_index = 2.5
    book.has_cover = 1
    book.authors = [db.Authors("Pinned Author", "Author, Pinned")]
    book.publishers = [
        db.Publishers("Pinned Publisher", "Pinned Publisher"),
    ]
    book.series = [db.Series("Pinned Series", "Pinned Series")]
    book.languages = [db.Languages("eng")]
    book.comments = [db.Comments("Pinned description.", book.id)]
    sync_harness.session.commit()
    monkeypatch.setattr(
        kobo.config, "config_kobo_cover_padding_enabled", False,
        raising=False,
    )

    headers = {
        "x-kobo-deviceid": "a" * 64,
        "x-kobo-devicemodel": sync_harness.device.model,
    }
    with sync_harness.app.test_request_context(
        "/v1/library/sync", headers=headers,
    ):
        g.annotation_origin_device_id = sync_harness.device.id
        rendered = {
            "BookEntitlement": kobo.create_book_entitlement(
                book, archived=False,
            ),
            "BookMetadata": kobo.get_metadata(book),
        }
        archived_rendered = {
            "BookEntitlement": kobo.create_book_entitlement(
                book, archived=True,
            ),
            "BookMetadata": kobo.get_metadata(book),
        }
        deleted_uuid = "00000000-0000-0000-0000-deleted1953"
        deleted_at = datetime(2026, 8, 28, 13, 30, 0)
        deleted_rendered = {
            "BookEntitlement": kobo.create_deleted_book_entitlement(
                deleted_uuid, deleted_at,
            ),
            "BookMetadata": kobo.create_deleted_book_metadata(deleted_uuid),
        }

    pinned_schema_and_hashes = {
        "live": (
            1,
            "28ba9f171cd833b2779b549c9bff86347447d40c011439a06408babe656da0c2",
        ),
        "archived_live": (
            1,
            "ad2030d15995f1083b42c90f751af6e24b6a74c02cddfe0e6e5af38674cb7e02",
        ),
        "hard_delete": (
            1,
            "8d72ce590309549d65cf110a0d44b61d2145bb2401ca4d6bf204ad41f5244011",
        ),
    }
    rendered_variants = {
        "live": rendered,
        "archived_live": archived_rendered,
        "hard_delete": deleted_rendered,
    }
    for variant, payload in rendered_variants.items():
        assert (
            kobo.ENTITLEMENT_PAYLOAD_SCHEMA_VERSION,
            kobo._entitlement_fingerprint(payload),
        ) == pinned_schema_and_hashes[variant], (
            "entitlement payload shape changed: bump "
            "ENTITLEMENT_PAYLOAD_SCHEMA_VERSION and update the pinned hash together"
        )


def test_identical_payload_suppresses_and_refreshes_moved_basis(
    sync_harness, caplog, monkeypatch,
):
    """Byte-identical payloads never re-deliver, even when the basis moves."""
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    caplog.set_level(logging.DEBUG, logger="cps.kobo")
    archived = ub.ArchivedBook(
        user_id=sync_harness.user.id,
        book_id=sync_harness.book.id,
        is_archived=False,
        last_modified=sync_harness.book.last_modified,
    )
    sync_harness.session.add(archived)
    sync_harness.session.commit()
    assert len(_entitlements(sync_harness.sync())) == 1
    before = sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).one()
    original_fingerprint = before.fingerprint

    moved_basis = sync_harness.book.last_modified + timedelta(minutes=5)
    archived.last_modified = moved_basis
    sync_harness.session.commit()
    stale_token = kobo.SyncToken.SyncToken().build_sync_token()
    replay = sync_harness.sync(stale_token)

    assert _entitlements(replay) == []
    sync_harness.session.expire_all()
    after = sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).one()
    assert after.fingerprint == original_fingerprint
    assert after.change_basis == kobo._book_entitlement_change_basis(
        sync_harness.book.last_modified,
        moved_basis,
    )
    summaries = [
        record.getMessage() for record in caplog.records
        if record.getMessage().startswith("Kobo Sync summary:")
    ]
    assert "reseeded_shape_change=0" in summaries[-1]


def test_constituent_basis_detects_book_move_below_archive_max(
    sync_harness, monkeypatch,
):
    """A changed lower book clock cannot collide with the archive component."""
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    archive_clock = datetime(2026, 8, 28, 12, 10, 0)
    archived = ub.ArchivedBook(
        user_id=sync_harness.user.id,
        book_id=sync_harness.book.id,
        is_archived=False,
        last_modified=archive_clock,
    )
    sync_harness.session.add(archived)
    sync_harness.session.commit()
    assert len(_entitlements(sync_harness.sync())) == 1
    before = sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).one()
    assert before.change_basis == (
        "v1|book=2026-08-28T12:00:00.000000Z|"
        "archive=2026-08-28T12:10:00.000000Z"
    )

    sync_harness.book.last_modified = datetime(2026, 8, 28, 12, 5, 0)
    sync_harness.session.commit()
    original_get_metadata = kobo.get_metadata

    def shape_b(book):
        payload = original_get_metadata(book)
        payload["Issue1953ShapeProbe"] = "shape-b"
        return payload

    monkeypatch.setattr(kobo, "get_metadata", shape_b)
    monkeypatch.setattr(
        kobo,
        "ENTITLEMENT_PAYLOAD_SCHEMA_VERSION",
        kobo.ENTITLEMENT_PAYLOAD_SCHEMA_VERSION + 1,
    )
    stale_token = kobo.SyncToken.SyncToken().build_sync_token()
    replay = sync_harness.sync(stale_token)

    assert len(_entitlements(replay)) == 1
    sync_harness.session.expire_all()
    after = sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).one()
    assert after.change_basis == (
        "v1|book=2026-08-28T12:05:00.000000Z|"
        "archive=2026-08-28T12:10:00.000000Z"
    )


def test_constituent_basis_normalizes_aware_clocks_to_utc():
    """Equivalent instants produce one canonical byte-comparable encoding."""
    from cps import kobo

    utc_clock = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    offset_clock = datetime(
        2026, 8, 28, 14, 0,
        tzinfo=timezone(timedelta(hours=2)),
    )

    assert kobo._book_entitlement_change_basis(
        offset_clock, None,
    ) == kobo._book_entitlement_change_basis(utc_clock, None)
    assert kobo._book_entitlement_change_basis(utc_clock, None) == (
        "v1|book=2026-08-28T12:00:00.000000Z|archive=none"
    )


def test_legacy_null_basis_version_transition_delivers_then_suppresses(
    sync_harness, caplog, monkeypatch,
):
    """A legacy NULL basis is ambiguous, so its first mismatch fails open."""
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    caplog.set_level(logging.DEBUG, logger="cps.kobo")
    assert len(_entitlements(sync_harness.sync())) == 1
    modified = datetime(2026, 8, 28, 12, 30, 0)
    _add_reading_state(sync_harness, modified, progress=61.0)
    row = sync_harness.session.query(ub.KoboDeviceBookEntitlement).one()
    row.change_basis = None
    row.updated_at = sync_harness.book.last_modified + timedelta(seconds=1)
    sync_harness.session.commit()

    original_get_metadata = kobo.get_metadata

    def shape_b(book):
        payload = original_get_metadata(book)
        payload["Issue1953ShapeProbe"] = "shape-b"
        return payload

    monkeypatch.setattr(kobo, "get_metadata", shape_b)
    next_schema = kobo.ENTITLEMENT_PAYLOAD_SCHEMA_VERSION + 1
    monkeypatch.setattr(
        kobo, "ENTITLEMENT_PAYLOAD_SCHEMA_VERSION", next_schema,
    )
    stale_token = kobo.SyncToken.SyncToken().build_sync_token()
    delivered = sync_harness.sync(stale_token)

    assert len(_entitlements(delivered)) == 1
    assert _changed_reading_states(delivered) == [], (
        "even a fail-open legacy renderer delivery must not consume its latch "
        "in the same response"
    )
    sync_harness.session.expire_all()
    migrated = sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).one()
    assert migrated.change_basis == kobo._book_entitlement_change_basis(
        sync_harness.book.last_modified,
        None,
    )
    assert migrated.payload_schema_version == next_schema
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is True

    # The now-stable payload suppresses on the next request while the bounded
    # repair feed independently serves and acknowledges the pending state.
    repaired = sync_harness.sync(delivered.headers[sync_harness.token_header])
    assert _entitlements(repaired) == []
    states = _changed_reading_states(repaired)
    assert len(states) == 1
    assert states[0]["CurrentBookmark"]["ProgressPercent"] == 61
    sync_harness.session.expire_all()
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is False
    summaries = [
        record.getMessage() for record in caplog.records
        if record.getMessage().startswith("Kobo Sync summary:")
    ]
    assert "reseeded_shape_change=0" in summaries[-1]


def test_legacy_null_basis_mass_transition_arms_then_drains_bounded_repairs(
        sync_harness, monkeypatch):
    """Fail-open renderer pages cannot create an unbounded repair response."""
    from cps import db, kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    monkeypatch.setattr(kobo, "SYNC_ITEM_LIMIT", 2)
    modified = sync_harness.book.last_modified
    for index in range(2):
        book = db.Books(
            "Legacy shape {}".format(index),
            "Legacy shape {}".format(index),
            "Author",
            modified,
            db.Books.DEFAULT_PUBDATE,
            "1.0",
            modified,
            "legacy-shape-{}".format(index),
            0,
            [],
            [],
        )
        sync_harness.session.add(book)
        sync_harness.session.flush()
        book.uuid = "00000000-0000-0000-0000-{:012d}".format(3000 + index)
        sync_harness.session.add(db.Data(
            book.id,
            "EPUB",
            2_000 + index,
            "legacy-shape-{}".format(index),
        ))
    sync_harness.session.commit()

    initial_a = sync_harness.sync()
    initial_b = sync_harness.sync(
        initial_a.headers[sync_harness.token_header],
    )
    assert [len(_entitlements(initial_a)), len(_entitlements(initial_b))] == [
        2, 1,
    ]
    assert sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).count() == 3

    state_clock = modified + timedelta(minutes=30)
    books = sync_harness.session.query(db.Books).order_by(db.Books.id).all()
    for index, book in enumerate(books):
        read = ub.ReadBook(
            user_id=sync_harness.user.id,
            book_id=book.id,
            read_status=ub.ReadBook.STATUS_IN_PROGRESS,
        )
        state = ub.KoboReadingState(
            user_id=sync_harness.user.id,
            book_id=book.id,
            priority_timestamp=state_clock,
        )
        state.current_bookmark = ub.KoboBookmark(
            last_modified=state_clock,
            progress_percent=60.0 + index,
        )
        state.statistics = ub.KoboStatistics(last_modified=state_clock)
        read.kobo_reading_state = state
        sync_harness.session.add(read)
    sync_harness.session.query(ub.KoboDeviceBookEntitlement).update({
        ub.KoboDeviceBookEntitlement.change_basis: None,
    })
    sync_harness.session.query(ub.DeviceReadingPosition).update({
        ub.DeviceReadingPosition.rehydrate_needed: False,
    })
    sync_harness.session.commit()
    sync_harness.session.query(ub.KoboReadingState).update(
        {ub.KoboReadingState.last_modified: state_clock},
        synchronize_session=False,
    )
    sync_harness.session.commit()

    original_get_metadata = kobo.get_metadata

    def shape_b(book):
        payload = original_get_metadata(book)
        payload["Issue1953MassShapeProbe"] = "shape-b"
        return payload

    monkeypatch.setattr(kobo, "get_metadata", shape_b)
    monkeypatch.setattr(
        kobo,
        "ENTITLEMENT_PAYLOAD_SCHEMA_VERSION",
        kobo.ENTITLEMENT_PAYLOAD_SCHEMA_VERSION + 1,
    )

    def entitlement_ids(response):
        ids = set()
        for envelope in _entitlements(response):
            payload = envelope.get("NewEntitlement") \
                or envelope["ChangedEntitlement"]
            ids.add(payload["BookEntitlement"]["Id"])
        return ids

    token = kobo.SyncToken.SyncToken(
        reading_state_last_modified=state_clock + timedelta(days=1),
    ).build_sync_token()
    transition_a = sync_harness.sync(token)
    delivered_a = entitlement_ids(transition_a)
    assert len(delivered_a) == 2
    assert _changed_reading_states(transition_a) == []
    assert sync_harness.session.query(ub.DeviceReadingPosition).filter(
        ub.DeviceReadingPosition.rehydrate_needed.is_(True),
    ).count() == 2

    transition_b = sync_harness.sync(
        transition_a.headers[sync_harness.token_header],
    )
    delivered_b = entitlement_ids(transition_b)
    repaired_b = {
        state["EntitlementId"] for state in _changed_reading_states(transition_b)
    }
    assert len(delivered_b) == 1
    assert repaired_b == delivered_a
    assert delivered_b.isdisjoint(repaired_b)
    assert len(repaired_b) <= kobo.SYNC_ITEM_LIMIT

    transition_c = sync_harness.sync(
        transition_b.headers[sync_harness.token_header],
    )
    repaired_c = {
        state["EntitlementId"] for state in _changed_reading_states(transition_c)
    }
    assert repaired_c == delivered_b
    assert _entitlements(transition_c) == []
    transition_d = sync_harness.sync(
        transition_c.headers[sync_harness.token_header],
    )
    assert _changed_reading_states(transition_d) == []
    assert sync_harness.session.query(ub.DeviceReadingPosition).filter(
        ub.DeviceReadingPosition.rehydrate_needed.is_(True),
    ).count() == 0


def test_real_last_modified_bump_under_new_payload_shape_delivers_once(
    sync_harness, monkeypatch,
):
    """A shape reseed cannot hide a later movement of the book cursor basis."""
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    assert len(_entitlements(sync_harness.sync())) == 1
    original_get_metadata = kobo.get_metadata

    def shape_b(book):
        payload = original_get_metadata(book)
        payload["Issue1953ShapeProbe"] = "shape-b"
        return payload

    monkeypatch.setattr(kobo, "get_metadata", shape_b)
    monkeypatch.setattr(
        kobo,
        "ENTITLEMENT_PAYLOAD_SCHEMA_VERSION",
        kobo.ENTITLEMENT_PAYLOAD_SCHEMA_VERSION + 1,
    )
    stale_token = kobo.SyncToken.SyncToken().build_sync_token()
    reseeded = sync_harness.sync(stale_token)
    assert _entitlements(reseeded) == []

    sync_harness.book.last_modified += timedelta(minutes=1)
    sync_harness.session.commit()
    changed = sync_harness.sync(
        reseeded.headers[sync_harness.token_header],
    )
    stable = sync_harness.sync(changed.headers[sync_harness.token_header])

    envelopes = _entitlements(changed)
    assert len(envelopes) == 1
    assert "ChangedEntitlement" in envelopes[0]
    assert (
        envelopes[0]["ChangedEntitlement"]["BookEntitlement"]["LastModified"]
        == "2026-08-28T12:01:00Z"
    )
    assert _entitlements(stable) == []
    row = sync_harness.session.query(ub.KoboDeviceBookEntitlement).one()
    assert row.change_basis == kobo._book_entitlement_change_basis(
        sync_harness.book.last_modified,
        None,
    )


def test_upgrade_seed_suppresses_confirmed_page_and_recovers_dropped_pages(
    sync_harness, caplog, monkeypatch,
):
    """Legacy Changed pages recover while the confirmed New page stays quiet."""
    from cps import db, kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    monkeypatch.setattr(
        kobo.config, "config_kobo_cover_padding_enabled", True, raising=False,
    )
    caplog.set_level(logging.DEBUG, logger="cps.kobo")

    second_device = ub.Device(
        user_id=sync_harness.user.id,
        kind="kobo",
        display_name="Existing Household Kobo",
        model="Kobo Libra Colour",
        active=True,
        created_by="auto",
    )
    sync_harness.session.add(second_device)
    sync_harness.session.flush()

    delivered = [sync_harness.book]
    modified = sync_harness.book.last_modified
    for number in range(2, 219):
        book = db.Books(
            f"Upgrade Book {number}",
            f"Upgrade Book {number}",
            "Author",
            modified,
            db.Books.DEFAULT_PUBDATE,
            "1.0",
            modified,
            f"upgrade-book-{number}",
            0,
            [],
            [],
        )
        sync_harness.session.add(book)
        sync_harness.session.flush()
        book.uuid = f"00000000-0000-0000-0000-{number:012d}"
        sync_harness.session.add(db.Data(
            book.id, "EPUB", 1_000_000 + number, f"upgrade-book-{number}",
        ))
        delivered.append(book)
    sync_harness.session.add_all([
        ub.KoboSyncedBooks(
            user_id=sync_harness.user.id,
            book_id=book.id,
            book_uuid=str(book.uuid),
        )
        for book in delivered
    ])
    sync_harness.session.commit()

    token = kobo.SyncToken.SyncToken().build_sync_token()
    responses = []
    for _page in range(3):
        response = sync_harness.sync(token)
        responses.append(response)
        token = response.headers[sync_harness.token_header]

    assert [len(_entitlements(response)) for response in responses] == [0, 100, 18]
    assert all(
        "NewEntitlement" in item
        for response in responses[1:]
        for item in _entitlements(response)
    )
    # Both devices retain the confirmed first page. Only the speaking device
    # records the 118 recovery entitlements returned by these requests.
    assert sync_harness.session.query(ub.KoboDeviceBookEntitlement).count() == 318
    assert sync_harness.session.query(ub.KoboDeviceEntitlementSeed).count() == 2
    first_book_hashes = {
        row.device_id: row.fingerprint
        for row in sync_harness.session.query(
            ub.KoboDeviceBookEntitlement,
        ).filter(
            ub.KoboDeviceBookEntitlement.book_id == sync_harness.book.id,
        ).all()
    }
    assert first_book_hashes[sync_harness.device.id] != \
        first_book_hashes[second_device.id], (
            "upgrade seeding must reproduce device-specific cover metadata, "
            "not copy the speaking Kobo's payload across the household"
        )

    summaries = [
        record.getMessage() for record in caplog.records
        if record.getMessage().startswith("Kobo Sync summary:")
    ]
    assert sum(
        int(re.search(r"suppressed_unchanged=(\d+)", line).group(1))
        for line in summaries
    ) == 100
    seed_lines = [
        record.getMessage() for record in caplog.records
        if record.getMessage().startswith("Kobo Sync ledger seed:")
    ]
    assert len(seed_lines) == 1
    assert "devices=2 books=218 deleted=0 new_devices=0" in seed_lines[0]
    assert float(re.search(r"elapsed_ms=([0-9.]+)", seed_lines[0]).group(1)) >= 0

    # Even a device whose historical ledger was seeded gets a full initial
    # library after factory reset because a tokenless request is ineligible.
    factory_reset = sync_harness.sync(
        internal_device_id=second_device.id,
        raw_device_id="b" * 64,
    )
    assert len(_entitlements(factory_reset)) == 100


def test_upgrade_seed_skips_null_archived_last_modified(
    sync_harness, monkeypatch,
):
    """Legacy NULL archive clocks do not break canonical-basis seeding."""
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    archived = ub.ArchivedBook(
        user_id=sync_harness.user.id,
        book_id=sync_harness.book.id,
        is_archived=False,
    )
    sync_harness.session.add_all([
        archived,
        ub.KoboSyncedBooks(
            user_id=sync_harness.user.id,
            book_id=sync_harness.book.id,
            book_uuid=str(sync_harness.book.uuid),
        ),
    ])
    sync_harness.session.flush()
    archived.last_modified = None
    sync_harness.session.commit()

    stale_token = kobo.SyncToken.SyncToken().build_sync_token()
    replay = sync_harness.sync(stale_token)

    assert replay.status_code == 200
    assert _entitlements(replay) == []
    row = sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).one()
    assert row.change_basis == kobo._book_entitlement_change_basis(
        sync_harness.book.last_modified,
        None,
    )


def test_two_shelf_rows_reseed_one_book_once_on_declared_transition(
    sync_harness, caplog, monkeypatch,
):
    """A per-book ledger and counter are independent of shelf-row clocks."""
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    caplog.set_level(logging.DEBUG, logger="cps.kobo")
    sync_harness.user.kobo_only_shelves_sync = True
    _add_kobo_shelf(
        sync_harness,
        date_added=datetime(2026, 8, 28, 12, 5, 0),
    )
    _add_kobo_shelf(
        sync_harness,
        date_added=datetime(2026, 8, 28, 12, 10, 0),
        name="Second Regression Kobo Shelf",
        shelf_uuid="issue-1953-second-regression-shelf",
    )
    assert _entitlements(sync_harness.sync())
    assert sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).count() == 1

    original_get_metadata = kobo.get_metadata

    def shape_b(book):
        payload = original_get_metadata(book)
        payload["Issue1953ShapeProbe"] = "shape-b"
        return payload

    monkeypatch.setattr(kobo, "get_metadata", shape_b)
    next_schema = kobo.ENTITLEMENT_PAYLOAD_SCHEMA_VERSION + 1
    monkeypatch.setattr(
        kobo, "ENTITLEMENT_PAYLOAD_SCHEMA_VERSION", next_schema,
    )
    stale_token = kobo.SyncToken.SyncToken().build_sync_token()
    replay = sync_harness.sync(stale_token)

    assert _entitlements(replay) == []
    rows = sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).all()
    assert len(rows) == 1
    assert rows[0].payload_schema_version == next_schema
    assert rows[0].change_basis == kobo._book_entitlement_change_basis(
        sync_harness.book.last_modified,
        None,
    )
    summaries = [
        record.getMessage() for record in caplog.records
        if record.getMessage().startswith("Kobo Sync summary:")
    ]
    assert "suppressed_unchanged=1" in summaries[-1]
    assert "reseeded_shape_change=1" in summaries[-1]


def test_hard_delete_entitlements_emit_once_then_suppress_exact_stale_replay(
    sync_harness, caplog, monkeypatch,
):
    """Two hard-delete probes cannot remain ChangedEntitlements forever."""
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    caplog.set_level(logging.DEBUG, logger="cps.kobo")

    # Cross the upgrade boundary before these new tombstones exist, so their
    # first delivery is real rather than migration-seeded.
    assert len(_entitlements(sync_harness.sync())) == 1
    deleted_at = datetime(2026, 8, 28, 13, 30, 0)
    sync_harness.session.add_all([
        ub.KoboDeletedBook(
            user_id=sync_harness.user.id,
            book_uuid="00000000-0000-0000-0000-deleted0001",
            deleted_at=deleted_at,
        ),
        ub.KoboDeletedBook(
            user_id=sync_harness.user.id,
            book_uuid="00000000-0000-0000-0000-deleted0002",
            deleted_at=deleted_at + timedelta(seconds=1),
        ),
    ])
    sync_harness.session.commit()

    stale_token = kobo.SyncToken.SyncToken().build_sync_token()
    first_offer = sync_harness.sync(stale_token)
    # Model request teardown. A staged-but-uncommitted deletion fingerprint
    # disappears here and makes the exact replay re-offer both tombstones.
    sync_harness.session.rollback()
    exact_replay = sync_harness.sync(stale_token)

    first_removed = [
        item["ChangedEntitlement"]
        for item in _entitlements(first_offer)
        if item.get("ChangedEntitlement", {}).get(
            "BookEntitlement", {},
        ).get("IsRemoved") is True
    ]
    assert len(first_removed) == 2
    assert _entitlements(exact_replay) == []
    assert sync_harness.session.query(
        ub.KoboDeviceDeletedEntitlement,
    ).count() == 2
    summaries = [
        record.getMessage() for record in caplog.records
        if record.getMessage().startswith("Kobo Sync summary:")
    ]
    assert "entitlements new=0 changed=0" in summaries[-1]
    assert "suppressed_unchanged=3 suppressed_removed=2" in summaries[-1]


def test_hard_delete_payload_shape_change_reseeds_without_delivery(
    sync_harness, caplog, monkeypatch,
):
    """IsRemoved tombstones use the same declared-transition reseed path."""
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    caplog.set_level(logging.DEBUG, logger="cps.kobo")
    assert len(_entitlements(sync_harness.sync())) == 1
    deleted_at = datetime(2026, 8, 28, 13, 30, 0)
    book_uuid = "00000000-0000-0000-0000-deleted1953"
    sync_harness.session.add(ub.KoboDeletedBook(
        user_id=sync_harness.user.id,
        book_uuid=book_uuid,
        deleted_at=deleted_at,
    ))
    sync_harness.session.commit()
    stale_token = kobo.SyncToken.SyncToken().build_sync_token()
    first_offer = sync_harness.sync(stale_token)
    assert any(
        item.get("ChangedEntitlement", {}).get(
            "BookEntitlement", {},
        ).get("IsRemoved") is True
        for item in _entitlements(first_offer)
    )
    before = sync_harness.session.query(
        ub.KoboDeviceDeletedEntitlement,
    ).filter_by(book_uuid=book_uuid).one()
    old_fingerprint = before.fingerprint

    original_deleted_metadata = kobo.create_deleted_book_metadata

    def shape_b(deleted_uuid):
        payload = original_deleted_metadata(deleted_uuid)
        payload["Issue1953ShapeProbe"] = "shape-b"
        return payload

    monkeypatch.setattr(kobo, "create_deleted_book_metadata", shape_b)
    next_schema = kobo.ENTITLEMENT_PAYLOAD_SCHEMA_VERSION + 1
    monkeypatch.setattr(
        kobo, "ENTITLEMENT_PAYLOAD_SCHEMA_VERSION", next_schema,
    )
    replay = sync_harness.sync(stale_token)

    assert _entitlements(replay) == []
    sync_harness.session.expire_all()
    after = sync_harness.session.query(
        ub.KoboDeviceDeletedEntitlement,
    ).filter_by(book_uuid=book_uuid).one()
    assert after.fingerprint != old_fingerprint
    assert after.change_basis == kobo._deleted_entitlement_change_basis(
        deleted_at,
    )
    assert after.payload_schema_version == next_schema
    summaries = [
        record.getMessage() for record in caplog.records
        if record.getMessage().startswith("Kobo Sync summary:")
    ]
    assert "reseeded_shape_change=1" in summaries[-1]
    assert "suppressed_removed=1" in summaries[-1]


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
    assert sync_harness.session.query(ub.KoboDeviceBookEntitlement).count() == 1


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


@pytest.mark.parametrize("removed_book_was_sole_marker", [False, True])
def test_failed_request_retains_shelf_removal_for_retry(
    sync_harness, monkeypatch, removed_book_was_sole_marker,
):
    """A 503 cannot consume an IsRemoved command that was never returned."""
    from cps import db, kobo, ub
    from werkzeug.exceptions import ServiceUnavailable

    sync_harness.user.kobo_only_shelves_sync = True
    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    shelf, removed_link = _add_kobo_shelf(
        sync_harness,
        date_added=datetime(2026, 8, 28, 12, 5, 0),
    )

    def add_shelf_book(number, title, modified):
        book = db.Books(
            title,
            title,
            "Author",
            modified,
            db.Books.DEFAULT_PUBDATE,
            "1.0",
            modified,
            f"issue-1953-{number}",
            0,
            [],
            [],
        )
        sync_harness.session.add(book)
        sync_harness.session.flush()
        book.uuid = f"00000000-0000-0000-1953-{number:012d}"
        link = ub.BookShelf(
            book_id=book.id,
            shelf=shelf.id,
            order=number,
            date_added=modified,
        )
        link.ub_shelf = shelf
        sync_harness.session.add_all([
            db.Data(book.id, "EPUB", 1_000_000 + number, book.path),
            link,
        ])
        sync_harness.session.commit()
        return book

    if not removed_book_was_sole_marker:
        add_shelf_book(
            1,
            "Retained Shelf Book",
            datetime(2026, 8, 28, 12, 1, 0),
        )
    first = sync_harness.sync()
    expected_initial = 1 if removed_book_was_sole_marker else 2
    assert len(_entitlements(first)) == expected_initial

    sync_harness.session.delete(removed_link)
    later_book = add_shelf_book(
        2,
        "Later Live Book",
        datetime(2026, 8, 28, 12, 20, 0),
    )
    sync_harness.session.commit()

    def fail_request_commit(*_args, **_kwargs):
        sync_harness.session.rollback()
        return False

    monkeypatch.setattr(ub, "session_commit", fail_request_commit)
    with pytest.raises(ServiceUnavailable) as raised:
        sync_harness.sync(first.headers[sync_harness.token_header])
    assert raised.value.code == 503

    # The removal command was discarded with the 503, so both sources needed
    # to reconstruct it must still be durable. In the sole-marker case this
    # also prevents the next request's zero-marker token-reset branch.
    assert sync_harness.session.query(ub.KoboSyncedBooks).filter_by(
        user_id=sync_harness.user.id,
        book_id=sync_harness.book.id,
    ).count() == 1
    assert sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).filter_by(book_id=sync_harness.book.id).count() == 1
    assert sync_harness.session.query(ub.KoboSyncedBooks).filter_by(
        user_id=sync_harness.user.id,
        book_id=later_book.id,
    ).count() == 0

    monkeypatch.setattr(
        ub,
        "session_commit",
        lambda *_args, **_kwargs: sync_harness.session.commit(),
    )
    retry = sync_harness.sync(first.headers[sync_harness.token_header])
    removals = [
        item["ChangedEntitlement"]["BookEntitlement"]
        for item in _entitlements(retry)
        if item.get("ChangedEntitlement", {}).get(
            "BookEntitlement", {},
        ).get("IsRemoved") is True
    ]

    assert [item["Id"] for item in removals] == [
        str(sync_harness.book.uuid),
    ]
    assert any(
        envelope.get("NewEntitlement", {}).get(
            "BookEntitlement", {},
        ).get("Id") == str(later_book.uuid)
        or envelope.get("ChangedEntitlement", {}).get(
            "BookEntitlement", {},
        ).get("Id") == str(later_book.uuid)
        for envelope in _entitlements(retry)
    )
    assert sync_harness.session.query(ub.KoboSyncedBooks).filter_by(
        user_id=sync_harness.user.id,
        book_id=sync_harness.book.id,
    ).count() == 0


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
    assert sync_harness.session.query(ub.KoboDeviceBookEntitlement).count() == 1


def test_rehydrate_emits_past_advanced_cursor_clears_atomically_and_ignores_exact_replay(
    sync_harness, monkeypatch,
):
    """M3's device latch repairs a download without reopening #1925/#1953."""
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config,
        "config_kobo_suppress_replayed_entitlements",
        True,
    )
    modified = datetime(2026, 8, 28, 12, 30, 0)
    _add_reading_state(sync_harness, modified, progress=48.0)
    first = sync_harness.sync()
    assert len(_entitlements(first)) == 1
    assert _changed_reading_states(first) == [], (
        "a state attached to the newly delivered entitlement must not consume "
        "the repair latch in the offering response"
    )
    position = sync_harness.session.query(ub.DeviceReadingPosition).one()
    assert position.device_id == sync_harness.device.id
    assert position.rehydrate_needed is True

    cursor_ahead = modified + timedelta(days=1)
    advanced = kobo.SyncToken.SyncToken.from_headers({
        sync_harness.token_header: first.headers[sync_harness.token_header],
    })
    advanced.reading_state_last_modified = cursor_ahead

    rehydrated = sync_harness.sync(advanced.build_sync_token())
    states = _changed_reading_states(rehydrated)
    assert len(states) == 1
    assert states[0]["EntitlementId"] == sync_harness.book.uuid
    assert states[0]["CurrentBookmark"]["ProgressPercent"] == 48
    sync_harness.session.expire_all()
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is False

    response_token = kobo.SyncToken.SyncToken.from_headers({
        sync_harness.token_header:
            rehydrated.headers[sync_harness.token_header],
    })
    assert response_token.reading_state_last_modified == cursor_ahead
    assert _changed_reading_states(sync_harness.sync(
        rehydrated.headers[sync_harness.token_header],
    )) == []

    # Select the book again with a stale but valid CWNG cursor. Layer 2
    # suppresses the exact entitlement; that suppression must not re-arm every
    # device position during a #1953-style renderer replay.
    stale = kobo.SyncToken.SyncToken(
        reading_state_last_modified=cursor_ahead,
    ).build_sync_token()
    replay = sync_harness.sync(stale)
    assert _entitlements(replay) == []
    sync_harness.session.expire_all()
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is False


@pytest.mark.parametrize(
    "ordering",
    ["download_then_sync", "sync_then_download"],
)
def test_real_download_and_sync_orderings_converge(
        sync_harness, ordering):
    """Both legal offer/download orderings repair the simulated device."""
    from cps import kobo, ub
    from cps.services import device_reading_position as positions

    modified = datetime(2026, 8, 28, 12, 30, 0)
    _add_reading_state(sync_harness, modified, progress=80.0)
    cursor_ahead = modified + timedelta(days=1)
    advanced = kobo.SyncToken.SyncToken(
        books_last_modified=cursor_ahead,
        books_last_created=cursor_ahead,
        reading_state_last_modified=cursor_ahead,
    ).build_sync_token()

    if ordering == "download_then_sync":
        # A prior non-identical offer armed the device; its byte replacement
        # and cover PUT happen before the repairing sync request.
        sync_harness.session.add(ub.KoboSyncedBooks(
            user_id=sync_harness.user.id,
            book_id=sync_harness.book.id,
            book_uuid=sync_harness.book.uuid,
        ))
        positions.mark_rehydrate_needed(
            sync_harness.device.id, [sync_harness.book.id],
        )
        sync_harness.session.commit()
        repair_token = advanced
    else:
        # The sync offers bytes first. Even though it attaches the current
        # state, this response only arms the latch; it cannot acknowledge it.
        offered = sync_harness.sync()
        assert len(_entitlements(offered)) == 1
        assert _changed_reading_states(offered) == []
        assert sync_harness.session.query(
            ub.DeviceReadingPosition.rehydrate_needed,
        ).scalar() is True
        repair_token = offered.headers[sync_harness.token_header]

    reset = sync_harness.put_position(
        0.0, clock="2026-08-29T15:00:00Z",
    )
    assert reset.status_code == 200
    sync_harness.session.expire_all()
    resolved = sync_harness.session.query(ub.KoboReadingState).one()
    journal = sync_harness.session.query(ub.DeviceReadingPosition).one()
    assert resolved.current_bookmark.progress_percent == 80.0
    assert journal.progress_percent == 0.0
    assert journal.rehydrate_needed is True

    repaired = sync_harness.sync(repair_token)
    repairs = _changed_reading_states(repaired)
    assert len(repairs) == 1
    assert repairs[0]["CurrentBookmark"]["ProgressPercent"] == 80
    simulated_device_progress = 80.0
    sync_harness.session.expire_all()
    journal = sync_harness.session.query(ub.DeviceReadingPosition).one()
    assert journal.progress_percent == 0.0
    assert journal.rehydrate_needed is False

    # Nickel confirms the state it applied; both orderings now have identical
    # server rows, journal state, and simulated device position.
    sync_harness.put_position(
        simulated_device_progress,
        clock="2026-08-29T16:00:00Z",
    )
    sync_harness.session.expire_all()
    resolved = sync_harness.session.query(ub.KoboReadingState).one()
    journal = sync_harness.session.query(ub.DeviceReadingPosition).one()
    assert resolved.current_bookmark.progress_percent == 80.0
    assert journal.progress_percent == 80.0
    assert journal.rehydrate_needed is False
    assert simulated_device_progress == 80.0

    following_token = kobo.SyncToken.SyncToken.from_headers({
        sync_harness.token_header: repaired.headers[sync_harness.token_header],
    })
    following_token.reading_state_last_modified = datetime(2026, 8, 30)
    following = sync_harness.sync(following_token.build_sync_token())
    assert _changed_reading_states(following) == []


def test_rehydrate_backlog_is_capped_and_drains_without_duplicates_or_starvation(
        sync_harness, monkeypatch):
    """Repair work stays bounded while unrelated entitlement recovery runs."""
    from cps import db, kobo, ub

    monkeypatch.setattr(kobo, "SYNC_ITEM_LIMIT", 2)
    old_clock = datetime(2026, 8, 1, 12, 0, 0)
    cursor_clock = datetime(2026, 8, 2, 12, 0, 0)
    ordinary_clock = datetime(2026, 8, 3, 12, 0, 0)
    repair_ids = []
    repair_uuids = set()
    ordinary_uuid = None

    for index in range(6):
        book = db.Books(
            "Repair {}".format(index),
            "Repair {}".format(index),
            "Author",
            old_clock,
            db.Books.DEFAULT_PUBDATE,
            "1.0",
            old_clock,
            "repair-{}".format(index),
            0,
            [],
            [],
        )
        sync_harness.session.add(book)
        sync_harness.session.flush()
        book.uuid = "00000000-0000-0000-0000-{:012d}".format(2000 + index)
        sync_harness.session.add(db.Data(
            book.id, "EPUB", 1_000 + index, "repair-{}".format(index),
        ))
        read = ub.ReadBook(
            user_id=sync_harness.user.id,
            book_id=book.id,
            read_status=ub.ReadBook.STATUS_IN_PROGRESS,
        )
        state = ub.KoboReadingState(
            user_id=sync_harness.user.id,
            book_id=book.id,
            priority_timestamp=old_clock,
        )
        state.current_bookmark = ub.KoboBookmark(
            last_modified=(ordinary_clock if index == 5 else old_clock),
            progress_percent=50.0 + index,
        )
        state.statistics = ub.KoboStatistics(last_modified=old_clock)
        read.kobo_reading_state = state
        sync_harness.session.add(read)
        if index < 5:
            repair_ids.append(book.id)
            repair_uuids.add(str(book.uuid))
            sync_harness.session.add(ub.DeviceReadingPosition(
                device_id=sync_harness.device.id,
                book_id=book.id,
                server_modified_at=old_clock,
                rehydrate_needed=True,
            ))
        else:
            ordinary_uuid = str(book.uuid)

    sync_harness.session.add_all([
        ub.KoboSyncedBooks(
            user_id=sync_harness.user.id,
            book_id=sync_harness.book.id,
            book_uuid=sync_harness.book.uuid,
        ),
        # #1735 makes this per-device ledger the source of truth for whether
        # an entitlement was delivered. The five pending repair rows model
        # books already present on this Kobo, so record that fact explicitly.
        # Deliberately leave the ordinary sixth book absent: its recovery arm
        # runs concurrently and proves it cannot starve or duplicate repairs.
        ub.KoboDeviceEntitlementSeed(
            device_id=sync_harness.device.id,
            classification_version=kobo.ENTITLEMENT_CLASSIFICATION_VERSION,
        ),
        *[
            ub.KoboDeviceBookEntitlement(
                device_id=sync_harness.device.id,
                book_id=book_id,
                fingerprint="f" * 64,
            )
            for book_id in [sync_harness.book.id, *repair_ids]
        ],
    ])
    sync_harness.session.commit()
    sync_harness.session.query(ub.KoboReadingState).filter(
        ub.KoboReadingState.book_id.in_(repair_ids),
    ).update(
        {ub.KoboReadingState.last_modified: old_clock},
        synchronize_session=False,
    )
    ordinary_id = sync_harness.session.query(db.Books.id).filter_by(
        uuid=ordinary_uuid,
    ).scalar()
    sync_harness.session.query(ub.KoboReadingState).filter_by(
        book_id=ordinary_id,
    ).update({ub.KoboReadingState.last_modified: ordinary_clock})
    sync_harness.session.commit()

    token = kobo.SyncToken.SyncToken(
        books_last_modified=datetime(2027, 1, 1),
        books_last_created=datetime(2027, 1, 1),
        reading_state_last_modified=cursor_clock,
    ).build_sync_token()
    seen_repairs = []
    repair_page_sizes = []
    ordinary_seen = False
    ordinary_recovery_seen = False
    for _round in range(4):
        response = sync_harness.sync(token)
        states = _changed_reading_states(response)
        uuids = [state["EntitlementId"] for state in states]
        if ordinary_uuid in uuids:
            ordinary_seen = True
        if any(
            envelope.get("NewEntitlement", {})
            .get("BookEntitlement", {})
            .get("Id") == ordinary_uuid
            for envelope in response.get_json()
        ):
            ordinary_recovery_seen = True
        page_repairs = [uuid for uuid in uuids if uuid in repair_uuids]
        repair_page_sizes.append(len(page_repairs))
        seen_repairs.extend(page_repairs)
        token = response.headers[sync_harness.token_header]

    assert ordinary_seen, "ordinary cursor work must not be starved by repairs"
    assert ordinary_recovery_seen, (
        "the missing-ledger recovery arm must be active during backlog drain"
    )
    assert repair_page_sizes == [2, 2, 1, 0]
    assert len(seen_repairs) == len(set(seen_repairs)) == 5
    assert set(seen_repairs) == repair_uuids
    assert sync_harness.session.query(ub.DeviceReadingPosition).filter_by(
        rehydrate_needed=True,
    ).count() == 0


def test_sync_has_one_checked_commit_after_all_response_state_is_staged(
        sync_harness, monkeypatch):
    """Shelf work and M2/M3 ledgers share the final checked commit."""
    from cps import kobo, ub

    calls = []

    def counted_commit(*_args, **_kwargs):
        calls.append("checked")
        sync_harness.session.commit()
        return True

    monkeypatch.setattr(kobo, "sync_shelves", sync_harness.real_sync_shelves)
    monkeypatch.setattr(ub, "session_commit", counted_commit)
    response = sync_harness.sync()

    assert len(_entitlements(response)) == 1
    assert calls == ["checked"]
    assert sync_harness.session.query(ub.KoboSyncedBooks).count() == 1
    assert sync_harness.session.query(ub.DeviceReadingPosition).count() == 1


def test_failed_only_sync_commit_leaves_all_response_state_retryable(
        sync_harness, monkeypatch):
    """A 503 cannot durably suppress an entitlement the device never got."""
    from werkzeug.exceptions import ServiceUnavailable
    from cps import kobo, ub

    calls = []

    def reject_commit(*_args, **_kwargs):
        calls.append("rejected")
        sync_harness.session.rollback()
        return False

    monkeypatch.setattr(kobo, "sync_shelves", sync_harness.real_sync_shelves)
    monkeypatch.setattr(ub, "session_commit", reject_commit)
    with pytest.raises(ServiceUnavailable):
        sync_harness.sync()

    assert calls == ["rejected"]
    assert sync_harness.session.query(ub.KoboSyncedBooks).count() == 0
    assert sync_harness.session.query(ub.KoboDeviceBookEntitlement).count() == 0
    assert sync_harness.session.query(ub.KoboDeviceEntitlementSeed).count() == 0
    assert sync_harness.session.query(ub.DeviceReadingPosition).count() == 0

    monkeypatch.setattr(
        ub,
        "session_commit",
        lambda *_args, **_kwargs: sync_harness.session.commit() or True,
    )
    retried = sync_harness.sync()
    assert len(_entitlements(retried)) == 1
    assert sync_harness.session.query(ub.KoboSyncedBooks).count() == 1
    seed = sync_harness.session.query(ub.KoboDeviceEntitlementSeed).one()
    assert seed.classification_version == kobo.ENTITLEMENT_CLASSIFICATION_VERSION
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is True


def test_rehydrate_latch_survives_checked_sync_commit_failure(
    sync_harness, monkeypatch,
):
    """A response that cannot commit must leave the repair queued."""
    from werkzeug.exceptions import ServiceUnavailable
    from cps import kobo, ub

    first = sync_harness.sync()
    assert len(_entitlements(first)) == 1
    modified = datetime(2026, 8, 28, 12, 30, 0)
    _add_reading_state(sync_harness, modified, progress=52.0)
    advanced = kobo.SyncToken.SyncToken.from_headers({
        sync_harness.token_header: first.headers[sync_harness.token_header],
    })
    advanced.reading_state_last_modified = modified + timedelta(days=1)

    def reject_commit(*_args, **_kwargs):
        sync_harness.session.rollback()
        return False

    monkeypatch.setattr(ub, "session_commit", reject_commit)
    with pytest.raises(ServiceUnavailable):
        sync_harness.sync(advanced.build_sync_token())

    sync_harness.session.expire_all()
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is True


def test_payload_stabilization_replays_byte_identically_with_layer2_off(
    sync_harness,
):
    """Layer 1 is default-safe: replay unchanged, byte-identical payloads."""
    from cps import ub

    first = _entitlements(sync_harness.sync())
    second = _entitlements(sync_harness.sync())

    assert len(first) == len(second) == 1
    assert first == second
    assert sync_harness.session.query(ub.KoboDeviceBookEntitlement).count() == 1


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
    """Core classification state stays isolated when replay suppression is off."""
    from cps import kobo, ub

    first_device = sync_harness.sync()
    assert len(_entitlements(first_device)) == 1
    assert sync_harness.session.query(ub.KoboDeviceBookEntitlement).count() == 1

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
    rows = sync_harness.session.query(ub.KoboDeviceBookEntitlement).all()
    assert {row.device_id for row in rows} == {
        sync_harness.device.id, second_device.id,
    }


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
    second_target_device = _seed_same_user_device_ledger(sync_harness)
    other_device = _seed_other_user_ledger(sync_harness)
    sync_harness.session.add_all([
        ub.KoboDeviceDeletedEntitlement(
            device_id=sync_harness.device.id,
            book_uuid="target-deleted",
            fingerprint="a" * 64,
        ),
        ub.KoboDeviceDeletedEntitlement(
            device_id=second_target_device.id,
            book_uuid="target-deleted",
            fingerprint="b" * 64,
        ),
        ub.KoboDeviceDeletedEntitlement(
            device_id=other_device.id,
            book_uuid="other-deleted",
            fingerprint="c" * 64,
        ),
        ub.KoboDeviceEntitlementSeed(device_id=second_target_device.id),
        ub.KoboDeviceEntitlementSeed(device_id=other_device.id),
    ])
    sync_harness.session.commit()
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
    assert {
        row.device_id for row in
        sync_harness.session.query(ub.KoboDeviceDeletedEntitlement)
    } == {other_device.id}
    assert {
        row.device_id for row in
        sync_harness.session.query(ub.KoboDeviceEntitlementSeed)
    } == {other_device.id}

    replay = sync_harness.sync(first.headers[sync_harness.token_header])
    replay_envelopes = _entitlements(replay)
    assert len(replay_envelopes) == 1
    assert "NewEntitlement" in replay_envelopes[0]
    assert {
        row.device_id
        for row in sync_harness.session.query(ub.KoboDeviceBookEntitlement)
    } == {sync_harness.device.id, other_device.id}
    assert {
        row.device_id for row in
        sync_harness.session.query(ub.KoboDeviceEntitlementSeed)
    } == {sync_harness.device.id, second_target_device.id, other_device.id}


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


def test_self_resend_clears_only_callers_entitlement_ledger(
    sync_harness, monkeypatch,
):
    """A non-admin can resend their own book without touching another user."""
    from cps import admin, ub

    sync_harness.sync()
    second_target_device = _seed_same_user_device_ledger(sync_harness)
    other_device = _seed_other_user_ledger(sync_harness)
    monkeypatch.setattr(admin, "calibre_db", sync_harness.calibre_db)
    monkeypatch.setattr(admin, "_", lambda value: value)
    monkeypatch.setattr(admin, "current_user", SimpleNamespace(
        id=sync_harness.user.id,
        role_admin=lambda: False,
    ))

    with sync_harness.app.test_request_context(
        f"/ajax/kobo_resend/{sync_harness.user.id}/{sync_harness.book.id}",
        method="POST",
    ):
        response = admin.ajax_kobo_resend.__wrapped__(
            sync_harness.user.id, sync_harness.book.id,
        )

    assert response.status_code == 200
    assert sync_harness.session.query(ub.KoboSyncedBooks).filter_by(
        user_id=sync_harness.user.id, book_id=sync_harness.book.id,
    ).count() == 0
    assert sync_harness.session.query(ub.KoboSyncedBooks).filter_by(
        user_id=18, book_id=sync_harness.book.id,
    ).count() == 1
    assert {
        row.device_id
        for row in sync_harness.session.query(ub.KoboDeviceBookEntitlement)
    } == {other_device.id}
    assert second_target_device.id != other_device.id


def test_user_cannot_resend_or_clear_another_users_ledger(
    sync_harness, monkeypatch,
):
    """The route rejects the forged user ID before any resend write occurs."""
    from werkzeug.exceptions import Forbidden

    from cps import admin, ub

    sync_harness.sync()
    other_device = _seed_other_user_ledger(sync_harness)
    monkeypatch.setattr(admin, "calibre_db", sync_harness.calibre_db)
    monkeypatch.setattr(admin, "current_user", SimpleNamespace(
        id=sync_harness.user.id,
        role_admin=lambda: False,
    ))
    before_modified = sync_harness.book.last_modified
    before_synced = {
        (row.user_id, row.book_id)
        for row in sync_harness.session.query(ub.KoboSyncedBooks)
    }
    before_ledgers = {
        (row.device_id, row.book_id)
        for row in sync_harness.session.query(ub.KoboDeviceBookEntitlement)
    }

    with sync_harness.app.test_request_context(
        f"/ajax/kobo_resend/18/{sync_harness.book.id}", method="POST",
    ):
        with pytest.raises(Forbidden) as raised:
            admin.ajax_kobo_resend.__wrapped__(18, sync_harness.book.id)

    assert raised.value.code == 403
    assert sync_harness.book.last_modified == before_modified
    assert {
        (row.user_id, row.book_id)
        for row in sync_harness.session.query(ub.KoboSyncedBooks)
    } == before_synced
    assert {
        (row.device_id, row.book_id)
        for row in sync_harness.session.query(ub.KoboDeviceBookEntitlement)
    } == before_ledgers
    assert (other_device.id, sync_harness.book.id) in before_ledgers


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


def test_generated_kepub_restores_stable_v4142_source_size(sync_harness):
    """Generated KEPUB metadata retains v4.1.42's nonzero stable Size."""
    from cps import kobo

    app = Flask(__name__)
    app.wsgi_app = SimpleNamespace(is_proxied=True)
    with app.test_request_context("/v1/library/sync"):
        download = kobo.get_metadata(sync_harness.book)["DownloadUrls"][0]

    assert download["Format"] == "KEPUB"
    assert download["Url"] == f"/download/{sync_harness.book.id}/kepub"
    assert download["Platform"] == "Generic"
    assert download["DrmType"] == "None"
    assert download["Size"] == 1_234_567


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


def test_metadata_rewritten_epub_restores_stable_stored_size(
    sync_harness, monkeypatch,
):
    """Metadata embedding still declares the stable v4.1.42 Data-row size."""
    from cps import kobo

    monkeypatch.setattr(kobo.config, "config_embed_metadata", True, raising=False)
    stored_epub = SimpleNamespace(format="EPUB", uncompressed_size=321)
    with Flask(__name__).test_request_context("/v1/library/sync"):
        download = kobo.build_download_url(
            sync_harness.book, stored_epub, "epub", "EPUB3",
        )

    assert download == {
        "Format": "EPUB3",
        "Url": f"/download/{sync_harness.book.id}/epub",
        "Platform": "Generic",
        "DrmType": "None",
        "Size": 321,
    }


def test_rewritten_stored_epub_and_kepub_keep_v4142_download_fields(
    sync_harness, monkeypatch,
):
    """Rewritten routes retain all URL/format/DRM/Size fields."""
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
            "Size": 1_234_567,
        },
        {
            "Format": "EPUB",
            "Url": f"/download/{sync_harness.book.id}/epub",
            "Platform": "Generic",
            "DrmType": "None",
            "Size": 1_234_567,
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
        "Size": 1_345_678,
    }]


@pytest.mark.parametrize("network_share_mode", [False, True])
@pytest.mark.parametrize("download_case", [
    "deferred_epub_to_kepub",
    "rewritten_stored_epub",
    "rewritten_stored_kepub",
])
def test_restored_size_paths_still_serve_the_kobo_download_route(
    tmp_path, monkeypatch, network_share_mode, download_case,
):
    """Generated/rewritten artifacts retain their working download routes."""
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


def test_device_entitlement_tables_are_created_by_app_db_migration_path():
    """An existing app.db receives every replay ledger table at startup."""
    from cps import ub
    from sqlalchemy import inspect as sa_inspect

    engine = create_engine("sqlite:///:memory:")
    session = sessionmaker(bind=engine)()
    try:
        # Create the existing referenced table but deliberately omit the new
        # ledger, then exercise the same additive path migrate_Database calls.
        ub.Device.__table__.create(bind=engine)
        expected = {
            "kobo_device_book_entitlement",
            "kobo_device_deleted_entitlement",
            "kobo_device_entitlement_seed",
        }
        assert expected.isdisjoint(sa_inspect(engine).get_table_names())
        ub.add_missing_tables(engine, session)
        assert expected.issubset(sa_inspect(engine).get_table_names())
    finally:
        session.close()
        engine.dispose()


def test_existing_entitlement_ledgers_receive_additive_provenance_and_classification_columns(
    monkeypatch,
):
    """A migrated #1925 app.db accepts provenance and #1735 state."""
    from cps import kobo_sync_status, ub
    from sqlalchemy import inspect as sa_inspect, text

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE kobo_device_book_entitlement ("
            "id INTEGER PRIMARY KEY, device_id INTEGER NOT NULL, "
            "book_id INTEGER NOT NULL, fingerprint VARCHAR(64) NOT NULL, "
            "updated_at DATETIME NOT NULL, "
            "UNIQUE (device_id, book_id))"
        ))
        connection.execute(text(
            "CREATE TABLE kobo_device_deleted_entitlement ("
            "id INTEGER PRIMARY KEY, device_id INTEGER NOT NULL, "
            "book_uuid VARCHAR(64) NOT NULL, fingerprint VARCHAR(64) NOT NULL, "
            "updated_at DATETIME NOT NULL, "
            "UNIQUE (device_id, book_uuid))"
        ))
        connection.execute(text(
            "CREATE TABLE kobo_device_entitlement_seed ("
            "device_id INTEGER PRIMARY KEY, seeded_at DATETIME NOT NULL)"
        ))
        connection.execute(text(
            "INSERT INTO kobo_device_book_entitlement "
            "(device_id, book_id, fingerprint, updated_at) "
            "VALUES (7, 19, :fingerprint, :updated_at)"
        ), {"fingerprint": "a" * 64, "updated_at": "2026-08-28 12:00:01"})
    session = sessionmaker(bind=engine)()
    try:
        monkeypatch.setattr(ub, "session", session)
        ub.migrate_kobo_entitlement_ledger_columns(engine, session)
        ub.migrate_kobo_entitlement_ledger_columns(engine, session)
        for table_name in (
            "kobo_device_book_entitlement",
            "kobo_device_deleted_entitlement",
        ):
            columns = {
                column["name"]: column
                for column in sa_inspect(engine).get_columns(table_name)
            }
            assert {"payload_schema_version", "change_basis"} <= set(columns)
            assert str(columns["change_basis"]["type"]) == "TEXT"

        seed_columns = {
            column["name"]: column
            for column in sa_inspect(engine).get_columns(
                "kobo_device_entitlement_seed"
            )
        }
        assert "classification_version" in seed_columns
        assert seed_columns["classification_version"]["nullable"] is False

        row = session.query(ub.KoboDeviceBookEntitlement).one()
        assert row.payload_schema_version == 1
        assert row.change_basis is None

        book_basis = (
            "v1|book=2026-08-28T12:05:00.000000Z|archive=none"
        )
        deleted_basis = "v1|deleted=2026-08-28T12:05:00.000000Z"
        kobo_sync_status.stage_device_entitlement_fingerprints(
            7, {19: "b" * 64}, {19: book_basis}, 2,
        )
        kobo_sync_status.stage_device_deleted_entitlement_fingerprints(
            7,
            {"deleted-1953": "c" * 64},
            {"deleted-1953": deleted_basis},
            2,
        )
        kobo_sync_status.stage_device_deleted_entitlement_fingerprints(
            7,
            {"deleted-1953": "d" * 64},
            {"deleted-1953": deleted_basis},
            2,
        )
        session.commit()
        session.expire_all()

        row = session.query(ub.KoboDeviceBookEntitlement).one()
        assert row.fingerprint == "b" * 64
        assert row.payload_schema_version == 2
        assert row.change_basis == book_basis
        deleted = session.query(ub.KoboDeviceDeletedEntitlement).one()
        assert deleted.fingerprint == "d" * 64
        assert deleted.payload_schema_version == 2
        assert deleted.change_basis == deleted_basis
    finally:
        session.close()
        engine.dispose()


def test_replay_suppression_config_migrates_and_defaults_on():
    """Hardware-proven replay suppression defaults on for upgrades and fresh installs."""
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
        )).scalar() == 1

        fresh_engine = create_engine("sqlite:///:memory:")
        try:
            config_sql._Base.metadata.create_all(fresh_engine)
            fresh_session = sessionmaker(bind=fresh_engine)()
            fresh_session.add(config_sql._Settings())
            fresh_session.commit()
            assert (
                fresh_session.query(config_sql._Settings).one()
                .config_kobo_suppress_replayed_entitlements is True
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
