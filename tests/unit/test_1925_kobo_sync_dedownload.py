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
        book=book,
        device=device,
        session=session,
        sync=sync,
        token_header=kobo.SyncToken.SyncToken.SYNC_TOKEN_HEADER,
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
    sync_harness.sync()
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
