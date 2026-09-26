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
import gzip
import hashlib
import inspect
import json
import logging
from types import SimpleNamespace

import pytest
from flask import Flask, g
from sqlalchemy import create_engine, event, true
from sqlalchemy.orm import sessionmaker


pytestmark = pytest.mark.unit


class _ProxiedTestWsgi:
    """Keep the harness proxy flag while allowing real Flask dispatch."""

    is_proxied = True

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        return Flask.wsgi_app(self.app, environ, start_response)


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


def _response_wire(response):
    """The review contract is status plus exact response bytes."""
    return response.status_code, response.get_data()


def _discard_pending_page(sync_harness):
    from cps import kobo_sync_status

    kobo_sync_status.delete_pending_sync_page(sync_harness.device.id)
    sync_harness.session.commit()


def _private_capture_records(root):
    return [
        json.loads(gzip.decompress(path.read_bytes()))
        for path in sorted(root.glob("exchange-*.json.gz"))
    ]


def _enable_private_sync_capture(monkeypatch, tmp_path):
    from cps.services import kobo_exchange_capture as capture

    root = tmp_path / "private-kobo-exchanges"
    monkeypatch.setenv(
        capture.ENABLE_ENV, capture.ENABLE_ACKNOWLEDGEMENT,
    )
    monkeypatch.setattr(capture, "_capture_root", lambda: root)
    monkeypatch.setattr(
        capture,
        "_run_off_hub_bounded",
        lambda _scope, function: function(),
    )
    return capture, root


def _sync_through_response_pipeline(
    sync_harness,
    *,
    token=None,
    raw_device_id="b" * 64,
    x_kobo_sync=None,
):
    """Dispatch the real handler and run Flask's after-response observers."""
    from cps import kobo

    headers = {
        "x-kobo-deviceid": raw_device_id,
        "x-kobo-devicemodel": sync_harness.device.model,
    }
    if token is not None:
        headers[kobo.SyncToken.SyncToken.SYNC_TOKEN_HEADER] = token
    if x_kobo_sync is not None:
        headers["x-kobo-sync"] = x_kobo_sync
    with sync_harness.app.test_request_context(
        "/v1/library/sync", headers=headers,
    ):
        g.annotation_origin_device_id = sync_harness.device.id
        response = sync_harness.app.make_response(
            kobo.HandleSyncRequest.__wrapped__(),
        )
        return sync_harness.app.process_response(response)


def _sync_through_flask_error_pipeline(sync_harness, *, token=None):
    """Exercise Flask's request and HTTP-exception handling around sync."""
    from cps import kobo

    headers = {
        "x-kobo-deviceid": "a" * 64,
        "x-kobo-devicemodel": sync_harness.device.model,
    }
    if token is not None:
        headers[kobo.SyncToken.SyncToken.SYNC_TOKEN_HEADER] = token
    return sync_harness.app.test_client().get(
        "/v1/library/sync", headers=headers,
    )


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
        refresh_for_new_data=lambda: None,
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
    app.wsgi_app = _ProxiedTestWsgi(app)

    def dispatched_sync():
        # The production auth decorator resolves this before entering the
        # handler. Keep that boundary while exercising Flask's real dispatch.
        g.annotation_origin_device_id = device.id
        return kobo._run_sync_with_pending_page_reset_boundary(
            kobo.HandleSyncRequest.__wrapped__,
        )

    app.add_url_rule(
        "/v1/library/sync",
        endpoint="issue_1925_dispatched_sync",
        view_func=dispatched_sync,
    )

    def sync(
        token=None,
        *,
        internal_device_id=None,
        raw_device_id=None,
        acknowledge=True,
    ):
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
            response = kobo.HandleSyncRequest.__wrapped__()
            # Most tests model a response the device received successfully.
            # Promote that page directly so their next assertion observes the
            # same state as the next real request's token acknowledgment. Tests
            # for the lost-response boundary opt out explicitly.
            if acknowledge and getattr(response, "status_code", None) == 200:
                pending = kobo_sync_status.get_pending_sync_page(
                    internal_device_id,
                )
                if pending is not None:
                    assert kobo._acknowledge_pending_page(
                        pending, internal_device_id,
                    )
                    session.commit()
            return response

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


def test_library_sync_private_capture_records_exact_response_and_summary_link(
    sync_harness, monkeypatch, tmp_path, caplog,
):
    from cps import kobo

    _capture, root = _enable_private_sync_capture(monkeypatch, tmp_path)
    caplog.set_level(logging.INFO, logger="cps.kobo")
    incoming_token = kobo.SyncToken.SyncToken().build_sync_token()
    raw_device_id = "capture-device-id"

    response = _sync_through_response_pipeline(
        sync_harness,
        token=incoming_token,
        raw_device_id=raw_device_id,
        x_kobo_sync="continue",
    )

    assert response.status_code == 200
    [record] = _private_capture_records(root)
    assert record["schema_version"] == 3
    assert record["exchange"] == "library_sync"
    assert record["device_request"]["path"] == "/v1/library/sync"
    assert record["device_request"]["body"]["data"] == ""
    request_headers = dict(record["device_request"]["headers"])
    expected_device_hash = hashlib.sha256(
        ("header:" + raw_device_id).encode("utf-8"),
    ).hexdigest()[:12]
    assert request_headers == {
        "x-kobo-synctoken": "***REDACTED***",
        "x-cwng-device-hash": expected_device_hash,
        "x-kobo-sync": "continue",
    }
    sync_exchange = record["sync_exchange"]
    assert sync_exchange["request"] == {
        "incoming_token": incoming_token,
        "x_kobo_sync": "continue",
        "device_hash": expected_device_hash,
    }
    assert sync_exchange["response"]["outgoing_token"] == response.headers[
        sync_harness.token_header
    ]
    assert record["device_response"]["body"]["data"].encode() \
        == response.get_data()
    assert record["upstream_request"] is None
    assert record["upstream_response"] is None
    assert raw_device_id not in json.dumps(record)
    raw_capture = json.dumps(record)
    assert sync_harness.book.title in raw_capture
    assert "/v1/library/sync" in raw_capture
    assert "/download/" in raw_capture
    assert incoming_token in raw_capture
    assert response.headers[sync_harness.token_header] in raw_capture
    info_summary = sync_exchange["info_summary"]
    assert info_summary["log_event"] == "Kobo Sync summary"
    assert info_summary["capture_id"] == record["capture_id"]
    assert info_summary["response_mode"] == "new_page"
    assert info_summary["counters"]["new"] == 1
    assert info_summary["counters"]["changed"] == 0
    assert info_summary["counters"]["removed"] == 0
    assert info_summary["counters"]["suppressed_replay"] == 0
    assert info_summary["counters"]["suppressed_unchanged"] == 0
    summaries = [
        item.getMessage() for item in caplog.records
        if item.getMessage().startswith("Kobo Sync summary:")
    ]
    assert "capture_id={}".format(record["capture_id"]) in summaries[-1]
    assert "device={}".format(expected_device_hash) in summaries[-1]
    assert "entitlements new=1 changed=0 removed=0" in summaries[-1]
    for raw_value in (
        raw_device_id,
        sync_harness.user.name,
        sync_harness.book.title,
        "/v1/library/sync",
        "/download/",
        incoming_token,
        response.headers[sync_harness.token_header],
    ):
        assert raw_value not in summaries[-1]


def test_library_sync_private_capture_reuses_authenticated_retention(
    sync_harness, monkeypatch, tmp_path,
):
    capture, root = _enable_private_sync_capture(monkeypatch, tmp_path)
    monkeypatch.setattr(capture, "MAX_FILES", 2)
    monkeypatch.setattr(capture, "MAX_TOTAL_BYTES", 1024 * 1024)
    incoming_token = kobo_token = None

    # Repeating the same unacknowledged request exercises the real pending-page
    # replay path while producing four distinct exchange records.
    for _index in range(4):
        response = _sync_through_response_pipeline(
            sync_harness,
            token=incoming_token,
            raw_device_id="retention-device-id",
        )
        assert response.status_code == 200
        if incoming_token is None:
            kobo_token = response.headers[sync_harness.token_header]

    records = _private_capture_records(root)
    assert len(records) == 2
    assert all(record["exchange"] == "library_sync" for record in records)
    assert all(
        record["sync_exchange"]["response"]["outgoing_token"] == kobo_token
        for record in records
    )
    assert sum(
        path.stat().st_size for path in root.glob("exchange-*.json.gz")
    ) <= capture.MAX_TOTAL_BYTES


def test_library_sync_capture_persistence_failure_never_changes_response(
    sync_harness, monkeypatch, tmp_path,
):
    from cps.services import kobo_exchange_capture as capture

    monkeypatch.delenv(capture.ENABLE_ENV, raising=False)
    request_token = None
    baseline = _sync_through_response_pipeline(
        sync_harness,
        token=request_token,
        raw_device_id="failure-device-id",
    )
    baseline_wire = (
        baseline.status_code,
        baseline.get_data(),
        baseline.headers[sync_harness.token_header],
        baseline.headers.get("x-kobo-sync"),
    )

    _capture, root = _enable_private_sync_capture(monkeypatch, tmp_path)
    monkeypatch.setattr(
        capture.CaptureSession,
        "_persist",
        lambda _self: (_ for _ in ()).throw(OSError("capture unavailable")),
    )
    observed = _sync_through_response_pipeline(
        sync_harness,
        token=request_token,
        raw_device_id="failure-device-id",
    )

    assert (
        observed.status_code,
        observed.get_data(),
        observed.headers[sync_harness.token_header],
        observed.headers.get("x-kobo-sync"),
    ) == baseline_wire
    assert not list(root.glob("exchange-*.json.gz")) if root.exists() else True


def test_sync_summary_emission_failure_preserves_pending_replay_bytes(
    sync_harness, monkeypatch, caplog,
):
    from cps import kobo

    caplog.set_level(logging.WARNING, logger="cps.kobo")
    sync_harness.sync(acknowledge=False)
    baseline = sync_harness.sync(acknowledge=False)

    def fail_cursor_format(_cursor):
        raise RuntimeError("injected summary formatter failure")

    monkeypatch.setattr(kobo, "_sync_cursor_log_value", fail_cursor_format)
    observed = sync_harness.sync(acknowledge=False)

    assert _response_wire(observed) == _response_wire(baseline)
    assert any(
        "reason=summary_emission_failed" in record.getMessage()
        for record in caplog.records
    )


def test_sync_capture_summary_failure_preserves_pending_replay_bytes(
    sync_harness, monkeypatch, caplog,
):
    from cps.services import kobo_exchange_capture as capture

    class CaptureProbe:
        capture_id = "capture-probe"

        def __init__(self, fail):
            self.fail = fail

        def record_sync_request(self, **_kwargs):
            return None

        def record_sync_info_summary(self, **_kwargs):
            if self.fail:
                raise RuntimeError("injected capture summary failure")

        def record_sync_response(self, **_kwargs):
            return None

        def finish(self, **_kwargs):
            return True

    caplog.set_level(logging.WARNING, logger="cps.kobo")
    sync_harness.sync(acknowledge=False)
    should_fail = {"value": False}
    monkeypatch.setattr(
        capture,
        "begin_capture",
        lambda **_kwargs: CaptureProbe(should_fail["value"]),
    )
    baseline = sync_harness.sync(acknowledge=False)
    should_fail["value"] = True
    observed = sync_harness.sync(acknowledge=False)

    assert _response_wire(observed) == _response_wire(baseline)
    assert any(
        "reason=capture_summary_record_failed" in record.getMessage()
        for record in caplog.records
    )


def test_reemit_reason_failure_preserves_non_cwng_reset_bytes(
    sync_harness, monkeypatch, caplog,
):
    from cps import kobo

    caplog.set_level(logging.WARNING, logger="cps.kobo")
    sync_harness.sync()
    monkeypatch.setattr(kobo.secrets, "token_hex", lambda _size: "a" * 32)
    baseline = sync_harness.sync("official.store-token", acknowledge=False)
    _discard_pending_page(sync_harness)

    def fail_reason(*_args, **_kwargs):
        raise RuntimeError("injected reason classification failure")

    monkeypatch.setattr(kobo, "_entitlement_reemit_reason", fail_reason)
    observed = sync_harness.sync("official.store-token", acknowledge=False)

    assert _response_wire(observed) == _response_wire(baseline)
    assert any(
        "reason=reason_classification_failed" in record.getMessage()
        for record in caplog.records
    )


def test_pending_observability_serialization_failure_preserves_response_bytes(
    sync_harness, monkeypatch, caplog,
):
    from cps import kobo

    caplog.set_level(logging.WARNING, logger="cps.kobo")
    sync_harness.sync()
    monkeypatch.setattr(kobo.secrets, "token_hex", lambda _size: "b" * 32)
    baseline = sync_harness.sync("official.store-token", acknowledge=False)
    _discard_pending_page(sync_harness)
    real_observability = kobo._sync_observability

    def unserializable_observability(*args, **kwargs):
        counters = real_observability(*args, **kwargs)
        counters["injected_unserializable"] = {"not-json"}
        return counters

    monkeypatch.setattr(
        kobo, "_sync_observability", unserializable_observability,
    )
    observed = sync_harness.sync("official.store-token", acknowledge=False)

    assert _response_wire(observed) == _response_wire(baseline)
    assert any(
        "reason=pending_observability_serialization_failed"
        in record.getMessage()
        for record in caplog.records
    )


def test_diagnostic_tombstone_ledger_failure_preserves_response_bytes(
    sync_harness, monkeypatch, caplog,
):
    from cps import kobo, kobo_sync_status, ub

    caplog.set_level(logging.WARNING, logger="cps.kobo")
    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    sync_harness.session.add(ub.KoboDeletedBook(
        user_id=sync_harness.user.id,
        book_uuid="00000000-0000-0000-0000-000000002128",
        deleted_at=datetime(2026, 8, 31, 12, 0, 0),
    ))
    sync_harness.session.commit()
    sync_harness.sync()
    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", False,
    )
    monkeypatch.setattr(kobo.secrets, "token_hex", lambda _size: "c" * 32)
    baseline = sync_harness.sync("official.store-token", acknowledge=False)
    _discard_pending_page(sync_harness)

    def fail_diagnostic_read(_device_id, _book_uuids, *, _session=None):
        assert _session is not None
        raise RuntimeError("injected diagnostic ledger failure")

    monkeypatch.setattr(
        kobo_sync_status,
        "get_device_deleted_entitlement_fingerprints",
        fail_diagnostic_read,
    )
    observed = sync_harness.sync("official.store-token", acknowledge=False)

    assert _response_wire(observed) == _response_wire(baseline)
    assert any(
        "reason=diagnostic_ledger_read_failed "
        "scope=removed_non_suppressing" in record.getMessage()
        for record in caplog.records
    )


def test_live_entitlement_ledger_read_failure_returns_retryable_503(
    sync_harness, monkeypatch, caplog,
):
    from cps import kobo, kobo_sync_status, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    assert len(_entitlements(sync_harness.sync())) == 1
    before = sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).one()
    before_state = (
        before.fingerprint,
        before.payload_schema_version,
        before.change_basis,
        before.updated_at,
    )

    sync_harness.book.title = "Changed after acknowledged delivery"
    sync_harness.book.sort = "Changed after acknowledged delivery"
    sync_harness.book.last_modified += timedelta(minutes=1)
    sync_harness.session.commit()
    real_lookup = kobo_sync_status.get_device_entitlement_fingerprints

    def fail_critical_read(*_args, **_kwargs):
        raise RuntimeError("injected live entitlement ledger failure")

    monkeypatch.setattr(
        kobo_sync_status,
        "get_device_entitlement_fingerprints",
        fail_critical_read,
    )
    caplog.set_level(logging.INFO, logger="cps.kobo")
    failed = _sync_through_flask_error_pipeline(sync_harness)

    assert failed.status_code == 503
    assert b"Entitlement" not in failed.get_data()
    assert kobo_sync_status.get_pending_sync_page(
        sync_harness.device.id,
    ) is None
    sync_harness.session.expire_all()
    after = sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).one()
    assert (
        after.fingerprint,
        after.payload_schema_version,
        after.change_basis,
        after.updated_at,
    ) == before_state
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "reason=delivery_ledger_read_failed scope=live_entitlement"
        in message for message in messages
    )
    assert any(
        "response_mode=live_entitlement_ledger_read_failed" in message
        for message in messages
    )

    monkeypatch.setattr(
        kobo_sync_status, "get_device_entitlement_fingerprints", real_lookup,
    )
    retry = sync_harness.sync()
    [delivered] = _entitlements(retry)
    assert "ChangedEntitlement" in delivered


def test_ledger_read_failure_with_failed_logging_backend_still_returns_503(
    sync_harness, monkeypatch,
):
    """A logger that stays broken through the failure path must not turn the
    retryable 503 into a 500 (the failure-only boundary cannot rely on
    logging to reach ``abort``)."""
    from cps import kobo, kobo_sync_status, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    assert len(_entitlements(sync_harness.sync())) == 1
    sync_harness.book.title = "Changed while logging is broken"
    sync_harness.book.sort = "Changed while logging is broken"
    sync_harness.book.last_modified += timedelta(minutes=1)
    sync_harness.session.commit()

    def broken_logging_backend(*_args, **_kwargs):
        raise RuntimeError("injected persistent logging backend failure")

    def fail_critical_read(*_args, **_kwargs):
        # The backend dies at the moment of the ledger failure and stays dead
        # through every log call on the failure path that follows.
        for level in ("info", "warning", "error"):
            monkeypatch.setattr(kobo.log, level, broken_logging_backend)
        raise RuntimeError("injected live entitlement ledger failure")

    monkeypatch.setattr(
        kobo_sync_status,
        "get_device_entitlement_fingerprints",
        fail_critical_read,
    )
    failed = _sync_through_flask_error_pipeline(sync_harness)

    assert failed.status_code == 503
    assert b"Entitlement" not in failed.get_data()
    assert kobo_sync_status.get_pending_sync_page(
        sync_harness.device.id,
    ) is None
    sync_harness.session.expire_all()
    assert sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).count() == 1


def test_download_forbidden_with_failed_logging_backend_still_returns_403(
    sync_harness, monkeypatch,
):
    from cps import kobo, kobo_sync_status

    def broken_logging_backend(*_args, **_kwargs):
        raise RuntimeError("injected persistent logging backend failure")

    def deny_download():
        for level in ("info", "warning", "error", "exception"):
            monkeypatch.setattr(kobo.log, level, broken_logging_backend)
        return False

    monkeypatch.setattr(sync_harness.user, "role_download", deny_download)
    failed = _sync_through_flask_error_pipeline(sync_harness)

    assert failed.status_code == 403
    assert b"Entitlement" not in failed.get_data()
    assert kobo_sync_status.get_pending_sync_page(
        sync_harness.device.id,
    ) is None


def test_pending_ack_failure_with_failed_logging_backend_still_returns_503(
    sync_harness, monkeypatch,
):
    from cps import kobo, kobo_sync_status

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    first = sync_harness.sync(acknowledge=False)
    assert len(_entitlements(first)) == 1
    pending = kobo_sync_status.get_pending_sync_page(sync_harness.device.id)
    assert pending is not None
    pending_state = (pending.outgoing_token, pending.confirmation_json)

    def broken_logging_backend(*_args, **_kwargs):
        raise RuntimeError("injected persistent logging backend failure")

    def fail_staging(*_args, **_kwargs):
        for level in ("info", "warning", "error", "exception"):
            monkeypatch.setattr(kobo.log, level, broken_logging_backend)
        raise RuntimeError("injected acknowledgment staging failure")

    monkeypatch.setattr(
        kobo_sync_status,
        "stage_device_entitlement_fingerprints",
        fail_staging,
    )
    failed = _sync_through_flask_error_pipeline(
        sync_harness, token=pending.outgoing_token,
    )

    assert failed.status_code == 503
    assert b"Entitlement" not in failed.get_data()
    sync_harness.session.expire_all()
    still_pending = kobo_sync_status.get_pending_sync_page(
        sync_harness.device.id,
    )
    assert still_pending is not None
    assert (
        still_pending.outgoing_token, still_pending.confirmation_json,
    ) == pending_state


def test_page_commit_failure_with_failed_logging_backend_still_returns_503(
    sync_harness, monkeypatch,
):
    from cps import kobo, kobo_sync_status, ub

    def broken_logging_backend(*_args, **_kwargs):
        raise RuntimeError("injected persistent logging backend failure")

    def reject_commit(*_args, **_kwargs):
        for level in ("info", "warning", "error", "exception"):
            monkeypatch.setattr(kobo.log, level, broken_logging_backend)
        ub.session.rollback()
        return False

    monkeypatch.setattr(ub, "session_commit", reject_commit)
    failed = _sync_through_flask_error_pipeline(sync_harness)

    assert failed.status_code == 503
    assert b"Entitlement" not in failed.get_data()
    assert kobo_sync_status.get_pending_sync_page(
        sync_harness.device.id,
    ) is None


def test_pending_page_with_wrong_shaped_headers_is_corrupt_not_a_500(
    sync_harness, monkeypatch, caplog,
):
    from cps import kobo, kobo_sync_status

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    first = sync_harness.sync(acknowledge=False)
    assert len(_entitlements(first)) == 1
    pending = kobo_sync_status.get_pending_sync_page(sync_harness.device.id)
    # Valid JSON of the wrong shape: json.loads succeeds, .items() would not.
    pending.response_headers_json = "[]"
    sync_harness.session.commit()
    caplog.set_level(logging.INFO, logger="cps.kobo")
    # Replay is keyed on the incoming token; repeat the same tokenless request.
    replayed = _sync_through_flask_error_pipeline(sync_harness)

    assert replayed.status_code == 503
    assert b"Entitlement" not in replayed.get_data()
    assert any(
        "response_mode=pending_response_corrupt" in record.getMessage()
        for record in caplog.records
    )


def test_ledger_seed_failure_with_failed_rollback_still_returns_503(
    sync_harness, monkeypatch,
):
    from cps import kobo_sync_status, ub

    def fail_seed(*_args, **_kwargs):
        raise RuntimeError("injected seed failure")

    def fail_rollback(*_args, **_kwargs):
        raise RuntimeError("injected rollback failure")

    monkeypatch.setattr(
        kobo_sync_status, "mark_device_entitlement_ledgers_seeded", fail_seed,
    )
    monkeypatch.setattr(ub.session, "rollback", fail_rollback)
    failed = _sync_through_flask_error_pipeline(sync_harness)

    assert failed.status_code == 503
    assert b"Entitlement" not in failed.get_data()


def test_deleted_entitlement_ledger_read_failure_returns_retryable_503(
    sync_harness, monkeypatch, caplog,
):
    from cps import kobo, kobo_sync_status, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    assert len(_entitlements(sync_harness.sync())) == 1
    tombstone = ub.KoboDeletedBook(
        user_id=sync_harness.user.id,
        book_uuid="00000000-0000-0000-0000-000000002135",
        deleted_at=datetime(2026, 8, 31, 12, 0, 0),
    )
    sync_harness.session.add(tombstone)
    sync_harness.session.commit()
    sync_harness.sync()

    before_live = [
        (row.id, row.fingerprint, row.change_basis, row.updated_at)
        for row in sync_harness.session.query(
            ub.KoboDeviceBookEntitlement,
        ).order_by(ub.KoboDeviceBookEntitlement.id)
    ]
    before_deleted = [
        (row.id, row.fingerprint, row.change_basis, row.updated_at)
        for row in sync_harness.session.query(
            ub.KoboDeviceDeletedEntitlement,
        ).order_by(ub.KoboDeviceDeletedEntitlement.id)
    ]
    tombstone.deleted_at += timedelta(minutes=1)
    sync_harness.session.commit()
    real_lookup = (
        kobo_sync_status.get_device_deleted_entitlement_fingerprints
    )

    def fail_critical_read(*_args, **_kwargs):
        raise RuntimeError("injected deleted entitlement ledger failure")

    monkeypatch.setattr(
        kobo_sync_status,
        "get_device_deleted_entitlement_fingerprints",
        fail_critical_read,
    )
    caplog.set_level(logging.INFO, logger="cps.kobo")
    failed = _sync_through_flask_error_pipeline(sync_harness)

    assert failed.status_code == 503
    assert b"Entitlement" not in failed.get_data()
    assert kobo_sync_status.get_pending_sync_page(
        sync_harness.device.id,
    ) is None
    sync_harness.session.expire_all()
    assert [
        (row.id, row.fingerprint, row.change_basis, row.updated_at)
        for row in sync_harness.session.query(
            ub.KoboDeviceBookEntitlement,
        ).order_by(ub.KoboDeviceBookEntitlement.id)
    ] == before_live
    assert [
        (row.id, row.fingerprint, row.change_basis, row.updated_at)
        for row in sync_harness.session.query(
            ub.KoboDeviceDeletedEntitlement,
        ).order_by(ub.KoboDeviceDeletedEntitlement.id)
    ] == before_deleted
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "reason=delivery_ledger_read_failed scope=deleted_entitlement"
        in message for message in messages
    )
    assert any(
        "response_mode=deleted_entitlement_ledger_read_failed" in message
        for message in messages
    )

    monkeypatch.setattr(
        kobo_sync_status,
        "get_device_deleted_entitlement_fingerprints",
        real_lookup,
    )
    retry = sync_harness.sync()
    removed = [
        item for item in _entitlements(retry)
        if item.get("ChangedEntitlement", {}).get(
            "BookEntitlement", {},
        ).get("IsRemoved") is True
    ]
    assert len(removed) == 1


def test_live_removal_diagnostic_failure_preserves_staged_acknowledgement(
    sync_harness, monkeypatch, caplog,
):
    from cps import kobo_sync_status, ub

    caplog.set_level(logging.WARNING, logger="cps.kobo")
    sync_harness.user.kobo_only_shelves_sync = True
    _shelf, link = _add_kobo_shelf(sync_harness)
    offered = sync_harness.sync(acknowledge=False)
    offered_token = offered.headers[sync_harness.token_header]
    sync_harness.session.delete(link)
    sync_harness.session.commit()

    real_lookup = kobo_sync_status.get_device_entitlement_fingerprints

    def fail_diagnostic_read(device_id, book_ids, *, _session=None):
        if _session is not None:
            raise RuntimeError("injected live-removal ledger failure")
        return real_lookup(device_id, book_ids)

    monkeypatch.setattr(
        kobo_sync_status,
        "get_device_entitlement_fingerprints",
        fail_diagnostic_read,
    )
    observed = sync_harness.sync(offered_token, acknowledge=False)

    assert observed.status_code == 200
    assert sync_harness.session.query(ub.KoboSyncedBooks).count() == 1
    assert sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).count() == 1
    pending = kobo_sync_status.get_pending_sync_page(sync_harness.device.id)
    assert pending is not None
    assert pending.response_body.encode() == observed.get_data()
    assert any(
        "reason=diagnostic_ledger_read_failed "
        "scope=live_scope_removal" in record.getMessage()
        for record in caplog.records
    )

    monkeypatch.setattr(
        kobo_sync_status,
        "get_device_entitlement_fingerprints",
        real_lookup,
    )
    no_fault_replay = sync_harness.sync(offered_token, acknowledge=False)
    assert _response_wire(observed) == _response_wire(no_fault_replay)


def test_sync_refresh_preserves_a_concurrent_library_session(
    sync_harness, monkeypatch,
):
    """A library sync must not dispose the engine under another request.

    The old reconnect path disposed the shared StaticPool connection. Keep a
    second SQLAlchemy session in a live transaction while the real Kobo sync
    body runs; that session must remain usable after the refresh.
    """
    from cps import db

    engine = sync_harness.session.get_bind()
    concurrent_session = sessionmaker(bind=engine)()
    dispose_calls = []

    def destructive_reconnect(*_args, **_kwargs):
        dispose_calls.append(True)
        engine.dispose()

    def nondisposing_refresh():
        sync_harness.session.expire_all()
        sync_harness.session.rollback()

    monkeypatch.setattr(
        sync_harness.calibre_db, "reconnect_db", destructive_reconnect
    )
    monkeypatch.setattr(
        sync_harness.calibre_db, "refresh_for_new_data", nondisposing_refresh
    )

    try:
        assert concurrent_session.query(db.Books).count() == 1
        response = sync_harness.sync()

        assert response.status_code == 200
        assert dispose_calls == [], (
            "Kobo sync invoked the destructive reconnect path and disposed "
            "the class-level library engine"
        )
        assert concurrent_session.query(db.Books).count() == 1, (
            "Kobo sync invalidated a concurrent library session"
        )
    finally:
        concurrent_session.close()


def test_sync_refresh_failure_logs_kobo_error_and_aborts_503(
    sync_harness, monkeypatch, caplog,
):
    """An incomplete pre-sync refresh is loud and has a stable HTTP status."""
    from werkzeug.exceptions import ServiceUnavailable

    def fail_refresh():
        raise RuntimeError("library refresh unavailable")

    monkeypatch.setattr(
        sync_harness.calibre_db, "refresh_for_new_data", fail_refresh
    )

    with caplog.at_level(logging.ERROR, logger="cps.kobo"):
        with pytest.raises(ServiceUnavailable) as exc_info:
            sync_harness.sync()

    assert exc_info.value.code == 503
    assert any(
        record.name == "cps.kobo"
        and "Kobo Sync: failed to refresh the library database"
        in record.getMessage()
        and record.exc_info is not None
        for record in caplog.records
    ), "refresh failure must produce a Kobo-side exception log before aborting"


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

    # Model an interrupted-sync case where the device sends a valid CWNG token,
    # but its local book cursors are behind the payload already delivered. The
    # acknowledged same-device ledger is equally authoritative for absent,
    # foreign, and partial tokens; dedicated regressions cover those shapes.
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
    assert "entitlements new=0 changed=0 removed=0" in summaries[-1]
    assert "suppressed_replay=1" in summaries[-1]
    assert "suppressed_unchanged=1" in summaries[-1]
    assert "replay_suppression enabled=True eligible=True" in summaries[-1]
    assert "cursors in=" in summaries[-1] and " out=" in summaries[-1]


def test_expired_pending_page_ack_is_honoured_before_ttl_prune(
    sync_harness, monkeypatch,
):
    """An idle Kobo's returned token remains authoritative after the TTL."""
    from cps import kobo, kobo_sync_status, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )

    first = sync_harness.sync(acknowledge=False)
    pending = sync_harness.session.query(
        ub.KoboDevicePendingSyncPage,
    ).filter_by(device_id=sync_harness.device.id).one()
    outgoing_token = pending.outgoing_token
    pending.created_at = (
        datetime.now(timezone.utc)
        - kobo_sync_status.PENDING_SYNC_PAGE_TTL
        - timedelta(seconds=1)
    )
    sync_harness.session.commit()

    acknowledged = sync_harness.sync(
        outgoing_token,
        acknowledge=False,
    )

    assert len(_entitlements(first)) == 1
    assert _entitlements(acknowledged) == [], (
        "the final page must not be re-emitted after its returned token "
        "acknowledges delivery"
    )
    ledger = sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).filter_by(
        device_id=sync_harness.device.id,
        book_id=sync_harness.book.id,
    ).one()
    assert ledger.fingerprint
    assert sync_harness.session.query(ub.KoboSyncedBooks).filter_by(
        user_id=sync_harness.user.id,
        book_id=sync_harness.book.id,
    ).count() == 1


def test_expired_orphan_pending_page_is_pruned_after_device_moves_on(
    sync_harness,
):
    """A never-acknowledged page expires when the Kobo presents another token."""
    from cps import kobo, kobo_sync_status, ub

    first = sync_harness.sync(acknowledge=False)
    pending = sync_harness.session.query(
        ub.KoboDevicePendingSyncPage,
    ).filter_by(device_id=sync_harness.device.id).one()
    expired_at = (
        datetime.now(timezone.utc)
        - kobo_sync_status.PENDING_SYNC_PAGE_TTL
        - timedelta(seconds=1)
    )
    pending.created_at = expired_at
    sync_harness.session.commit()

    moved_on_token = kobo.SyncToken.SyncToken(
        books_last_id=sync_harness.book.id + 100,
    ).build_sync_token()
    assert moved_on_token != pending.outgoing_token
    rebuilt = sync_harness.sync(moved_on_token, acknowledge=False)
    replacement = sync_harness.session.query(
        ub.KoboDevicePendingSyncPage,
    ).filter_by(device_id=sync_harness.device.id).one()

    assert first.status_code == rebuilt.status_code == 200
    assert len(_entitlements(rebuilt)) == 1
    assert replacement.created_at > expired_at.replace(tzinfo=None)
    assert sync_harness.session.query(ub.KoboDeviceBookEntitlement).count() == 0


def test_pending_page_replay_logs_the_stored_response_counts(
    sync_harness, caplog,
):
    """A byte replay gets its own INFO incident line with original counts."""
    caplog.set_level(logging.INFO, logger="cps.kobo")

    offered = sync_harness.sync(acknowledge=False)
    replayed = sync_harness.sync(acknowledge=False)

    assert replayed.get_data() == offered.get_data()
    summaries = [
        record.getMessage() for record in caplog.records
        if record.getMessage().startswith("Kobo Sync summary:")
    ]
    assert len(summaries) == 2
    assert "response_mode=pending_replay" in summaries[-1]
    assert "entitlements new=1 changed=0 removed=0" in summaries[-1]
    assert "suppressed_replay=0" in summaries[-1]
    assert "fingerprint_mismatch_reemitted=0" in summaries[-1]
    assert "reemit_reasons=none" in summaries[-1]
    assert "cursors in=" in summaries[-1] and " out=" in summaries[-1]
    assert "a" * 64 not in summaries[-1]


def test_upgrade_reading_state_frontier_keeps_base_fingerprint_stable(
    sync_harness, monkeypatch,
):
    """#2107 may move state between envelopes, never re-emit the base book."""
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    first = sync_harness.sync()
    assert len(_entitlements(first)) == 1
    ledger = sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).one()
    original_fingerprint = ledger.fingerprint
    sync_harness.session.query(ub.DeviceReadingPosition).update({
        ub.DeviceReadingPosition.rehydrate_needed: False,
    })
    sync_harness.session.commit()

    modified = datetime(2026, 8, 28, 12, 30, 0)
    state = _add_reading_state(sync_harness, modified, progress=42.0)
    with sync_harness.app.test_request_context("/v1/library/sync"):
        base_entitlement = {
            "BookEntitlement": kobo.create_book_entitlement(
                sync_harness.book, archived=False,
            ),
            "BookMetadata": kobo.get_metadata(sync_harness.book),
        }
        state_bearing_entitlement = dict(base_entitlement)
        state_bearing_entitlement["ReadingState"] = (
            kobo.get_kobo_reading_state_response(sync_harness.book, state)
        )

    assert kobo._entitlement_fingerprint(base_entitlement) == original_fingerprint
    assert (
        kobo._entitlement_fingerprint(state_bearing_entitlement)
        != original_fingerprint
    ), "the wire envelope changes when ReadingState moves, by design"

    stale_token = kobo.SyncToken.SyncToken().build_sync_token()
    upgraded = sync_harness.sync(stale_token)

    assert _entitlements(upgraded) == [], (
        "a reading-state placement change must not re-offer an already-held book"
    )
    states = _changed_reading_states(upgraded)
    assert len(states) == 1
    assert states[0]["EntitlementId"] == sync_harness.book.uuid
    sync_harness.session.expire_all()
    assert sync_harness.session.query(
        ub.KoboDeviceBookEntitlement.fingerprint,
    ).scalar() == original_fingerprint


def test_failed_book_delete_rolls_back_archive_and_preserves_device_ledger(
    sync_harness, monkeypatch,
):
    """#2102 cannot leak a staged removal into the following library sync."""
    from werkzeug.exceptions import ServiceUnavailable
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    first = sync_harness.sync()
    ledger = sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).one()
    original_fingerprint = ledger.fingerprint
    sync_harness.user.check_visibility = lambda _permission: True

    def reject_commit(*_args, **_kwargs):
        sync_harness.session.rollback()
        return False

    monkeypatch.setattr(ub, "session_commit", reject_commit)
    with sync_harness.app.test_request_context(
        "/v1/library/{}".format(sync_harness.book.uuid), method="DELETE",
    ):
        g.annotation_origin_device_id = sync_harness.device.id
        with pytest.raises(ServiceUnavailable) as raised:
            inspect.unwrap(kobo.HandleBookDeletionRequest)(
                sync_harness.book.uuid,
            )
    assert raised.value.code == 503

    sync_harness.session.expire_all()
    assert sync_harness.session.query(ub.ArchivedBook).count() == 0
    assert sync_harness.session.query(ub.KoboSyncedBooks).count() == 1
    assert sync_harness.session.query(
        ub.KoboDeviceBookEntitlement.fingerprint,
    ).scalar() == original_fingerprint

    monkeypatch.setattr(
        ub, "session_commit",
        lambda *_args, **_kwargs: sync_harness.session.commit() or True,
    )
    following = sync_harness.sync(first.headers[sync_harness.token_header])
    assert _entitlements(following) == []


def test_annotation_get_503_does_not_mutate_book_delivery_state(
    sync_harness, monkeypatch,
):
    """#2108's annotation-only 503 cannot queue a library removal response."""
    from cps import kobo, readingservices, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    first = sync_harness.sync()
    ledger = sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).one()
    original_fingerprint = ledger.fingerprint

    monkeypatch.setattr(
        readingservices,
        "current_user",
        SimpleNamespace(id=sync_harness.user.id, is_authenticated=True),
    )
    monkeypatch.setattr(
        readingservices.config, "config_kobo_sync", True, raising=False,
    )
    monkeypatch.setattr(
        readingservices, "_begin_exchange_capture", lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        readingservices,
        "resolve_entitlement_ownership",
        lambda _entitlement_id: readingservices.OWNERSHIP_UNKNOWN,
    )
    monkeypatch.setattr(
        readingservices,
        "_possible_annotation_ownership",
        lambda *_a, **_k: readingservices.POSSIBLE_OWNERSHIP_LOOKUP_FAILED,
    )
    with sync_harness.app.test_request_context(
        "/api/v3/content/{}/annotations".format(sync_harness.book.uuid),
        method="GET",
    ):
        response = inspect.unwrap(readingservices.handle_annotations)(
            sync_harness.book.uuid,
        )

    assert response.status_code == 503
    sync_harness.session.expire_all()
    assert sync_harness.session.query(ub.ArchivedBook).count() == 0
    assert sync_harness.session.query(ub.KoboDeletedBook).count() == 0
    assert sync_harness.session.query(ub.KoboSyncedBooks).count() == 1
    assert sync_harness.session.query(
        ub.KoboDeviceBookEntitlement.fingerprint,
    ).scalar() == original_fingerprint

    following = sync_harness.sync(first.headers[sync_harness.token_header])
    assert _entitlements(following) == []


def test_furthest_wins_parent_clock_emits_state_without_entitlement(
    sync_harness, monkeypatch,
):
    """#2118 may advance KoboReadingState, never Books or its fingerprint."""
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    first = sync_harness.sync()
    ledger = sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).one()
    original_fingerprint = ledger.fingerprint
    original_book_clock = sync_harness.book.last_modified
    original_state_clock = kobo.get_or_create_reading_state(
        sync_harness.book.id,
    ).last_modified
    device_clock = (
        original_state_clock + timedelta(seconds=1)
    ).replace(microsecond=0)
    assert device_clock > original_state_clock
    sync_harness.session.query(ub.DeviceReadingPosition).update({
        ub.DeviceReadingPosition.rehydrate_needed: False,
    })
    sync_harness.session.commit()

    written = sync_harness.put_position(
        45.0, clock=device_clock.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    assert written.status_code == 200
    sync_harness.session.expire_all()
    state = sync_harness.session.query(ub.KoboReadingState).one()
    assert state.last_modified == device_clock
    assert sync_harness.session.get(
        type(sync_harness.book), sync_harness.book.id,
    ).last_modified == original_book_clock

    stale_token = kobo.SyncToken.SyncToken().build_sync_token()
    following = sync_harness.sync(stale_token)

    assert _entitlements(following) == []
    states = _changed_reading_states(following)
    assert len(states) == 1
    assert states[0]["CurrentBookmark"]["ProgressPercent"] == 45
    sync_harness.session.expire_all()
    assert sync_harness.session.query(
        ub.KoboDeviceBookEntitlement.fingerprint,
    ).scalar() == original_fingerprint


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
    mismatch_summaries = [
        summary for summary in summaries
        if "fingerprint_mismatch_reemitted=1" in summary
    ]
    assert len(mismatch_summaries) == 1
    assert "entitlements new=0 changed=1 removed=0" in mismatch_summaries[0]
    assert "suppressed_replay=0" in mismatch_summaries[0]
    assert (
        "reemit_reasons=live_fingerprint_mismatch_same_basis:1"
        in mismatch_summaries[0]
    )
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


def test_failed_live_reseed_commit_rolls_back_pending_page_and_aborts(
    sync_harness, caplog, monkeypatch,
):
    """A rolled-back exact response is not returned or left replayable."""
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
    assert any(
        record.getMessage().startswith("Kobo Sync summary:")
        for record in caplog.records
    )
    assert sync_harness.session.query(
        ub.KoboDevicePendingSyncPage,
    ).count() == 0


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
        # v2: DownloadUrls[].Size is excluded from the fingerprint so that an
        # on-demand KEPUB materialised from the stored EPUB does not re-send.
        # v3: so are BookMetadata.CoverImageId and DownloadUrls[].Url, which
        # follow file times and the address the device uses, not the book.
        "live": (
            3,
            "284c2a6d36293818b56ded3f4180fd297cde29fa93e0464c4a00b0f6f3680c81",
        ),
        "archived_live": (
            3,
            "a94235b4ea56106d2fcbdbcf6b777fee0c751df7d257a9d2debb3832698110e1",
        ),
        "hard_delete": (
            3,
            "01783c900c2983c507c1ae9d8830d9b3a8f5817c262496c41febf4401740b847",
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


def test_upgrade_reannounces_a_household_seed_copy_without_starvation(
    sync_harness, caplog, monkeypatch,
):
    """A v4.1.43 seed copy no download vouches for becomes New per Kobo."""
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
    # The copy v4.1.43's boundary seed wrote: the flat table above, staged
    # onto both Kobos just before one call sealed them at the same instant.
    # No download or reading report vouches for any of it.
    sealed_at = modified + timedelta(days=1)
    sync_harness.session.add_all([
        *[
            ub.KoboDeviceEntitlementSeed(
                device_id=device.id,
                seeded_at=sealed_at,
                classification_version=0,
            )
            for device in (sync_harness.device, second_device)
        ],
        *[
            ub.KoboDeviceBookEntitlement(
                device_id=device.id,
                book_id=book.id,
                fingerprint=fingerprint * 64,
                updated_at=sealed_at - timedelta(seconds=1),
            )
            for device, fingerprint in (
                (sync_harness.device, "a"), (second_device, "b"),
            )
            for book in delivered
        ],
    ])
    sync_harness.session.commit()

    poisoned_token = kobo.SyncToken.SyncToken(
        books_last_modified=modified + timedelta(days=1),
        books_last_created=modified + timedelta(days=1),
        books_last_id=999_999,
    ).build_sync_token()

    pages_by_device = {}
    for device, raw_id in (
        (sync_harness.device, "a" * 64),
        (second_device, "b" * 64),
    ):
        token = poisoned_token
        responses = []
        for _page in range(3):
            response = sync_harness.sync(
                token,
                internal_device_id=device.id,
                raw_device_id=raw_id,
            )
            responses.append(response)
            token = response.headers[sync_harness.token_header]
        pages_by_device[device.id] = responses

    assert {
        device_id: [len(_entitlements(response)) for response in responses]
        for device_id, responses in pages_by_device.items()
    } == {
        sync_harness.device.id: [100, 100, 18],
        second_device.id: [100, 100, 18],
    }
    assert all(
        "NewEntitlement" in item
        for responses in pages_by_device.values()
        for response in responses
        for item in _entitlements(response)
    )
    assert sync_harness.session.query(ub.KoboDeviceBookEntitlement).count() == 436
    assert sync_harness.session.query(ub.KoboDeviceEntitlementSeed).count() == 2
    first_book_hashes = {
        row.device_id: row.fingerprint
        for row in sync_harness.session.query(
            ub.KoboDeviceBookEntitlement,
        ).filter(
            ub.KoboDeviceBookEntitlement.book_id == sync_harness.book.id,
        ).all()
    }
    # Both Kobos hold the same book, so since schema 3 (the per-model cover
    # id is not fingerprinted) the payloads they were sent hash the same.
    assert set(first_book_hashes.values()).isdisjoint({"a" * 64, "b" * 64}), (
        "confirmed hashes must come from each device's acknowledged "
        "payload, not copied user-wide upgrade history"
    )

    summaries = [
        record.getMessage() for record in caplog.records
        if record.getMessage().startswith("Kobo Sync summary:")
    ]
    assert all("suppressed_unchanged=0" in line for line in summaries)
    seed_lines = [
        record.getMessage() for record in caplog.records
        if record.getMessage().startswith("Kobo Sync ledger seed:")
    ]
    # Pre-existing version-0 markers bypass the initial marker insert, but the
    # one classification audit must still have cleared both copies.
    assert seed_lines == []
    migration_lines = [
        record.getMessage() for record in caplog.records
        if record.getMessage().startswith(
            "Kobo Sync classification migration:"
        )
    ]
    assert len(migration_lines) == 1
    assert "devices=2 device_proven=0 unproven=436" in migration_lines[0]


def test_upgrade_seed_skips_null_archived_last_modified(
    sync_harness, monkeypatch,
):
    """Legacy NULL archive clocks do not break conservative reannouncement."""
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
    assert len(_entitlements(replay)) == 1
    assert "NewEntitlement" in _entitlements(replay)[0]
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
    assert "suppressed_replay=1" in summaries[-1]
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
    assert "suppressed_replay=3" in summaries[-1]
    assert "suppressed_unchanged=1" in summaries[-1]
    assert "suppressed_removed=2" in summaries[-1]


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


def test_suppressed_entitlement_drains_newer_reading_state_after_older_full_page(
    sync_harness, monkeypatch,
):
    """Layer 2 suppression preserves the ordered reading-state frontier."""
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
    # This test isolates replay suppression + cursor pagination. The separate
    # re-download repair channel intentionally has its own delivery semantics.
    sync_harness.session.query(ub.DeviceReadingPosition).update({
        ub.DeviceReadingPosition.rehydrate_needed: False,
    })
    sync_harness.session.commit()

    # Fill the (test-sized) independent reading-state page with an older,
    # legitimate library state. Keeping this book out of Data makes it
    # reading-state-only background, not an additional base entitlement in
    # this regression. The target's newer state must wait for the next ordered
    # page; embedding it now would move the timestamp cursor past this row.
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

    # A valid but stale CWNG token selects the unchanged base entitlement, but
    # the older background state owns this response's one-row state frontier.
    stale_cwng_token = kobo.SyncToken.SyncToken().build_sync_token()
    changed = sync_harness.sync(stale_cwng_token)

    assert _entitlements(changed) == []
    target_states = [
        state for state in _changed_reading_states(changed)
        if state["EntitlementId"] == sync_harness.book.uuid
    ]
    assert target_states == []
    assert [
        state["EntitlementId"] for state in _changed_reading_states(changed)
    ] == [background_book.uuid]

    advanced_token = kobo.SyncToken.SyncToken.from_headers({
        sync_harness.token_header: changed.headers[sync_harness.token_header],
    })
    assert advanced_token.reading_state_last_modified == background_modified

    target_page = sync_harness.sync(changed.headers[sync_harness.token_header])
    target_states = [
        state for state in _changed_reading_states(target_page)
        if state["EntitlementId"] == sync_harness.book.uuid
    ]
    assert len(target_states) == 1
    assert target_states[0]["CurrentBookmark"]["ProgressPercent"] == 42
    target_token = kobo.SyncToken.SyncToken.from_headers({
        sync_harness.token_header: target_page.headers[sync_harness.token_header],
    })
    assert target_token.reading_state_last_modified == state_modified

    unchanged = sync_harness.sync(target_page.headers[sync_harness.token_header])
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
    sync_harness, monkeypatch, caplog,
):
    """Removing a shelf member still emits IsRemoved and clears both markers."""
    from cps import kobo, ub

    sync_harness.user.kobo_only_shelves_sync = True
    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    caplog.set_level(logging.INFO, logger="cps.kobo")
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
    summaries = [
        record.getMessage() for record in caplog.records
        if record.getMessage().startswith("Kobo Sync summary:")
    ]
    assert "entitlements new=0 changed=1 removed=1" in summaries[-1]
    assert "fingerprint_mismatch_reemitted=1" in summaries[-1]
    assert "reemit_reasons=live_scope_removal:1" in summaries[-1]


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


def test_lost_rehydrate_page_replays_and_clears_only_after_token_ack(
    sync_harness,
):
    """The #1942 repair latch follows the same response acknowledgment."""
    from cps import ub

    modified = datetime(2026, 8, 28, 12, 30, 0)
    _add_reading_state(sync_harness, modified, progress=63.0)
    entitlement = sync_harness.sync()

    repair_incoming = entitlement.headers[sync_harness.token_header]
    offered = sync_harness.sync(repair_incoming, acknowledge=False)
    assert len(_changed_reading_states(offered)) == 1
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is True

    retried = sync_harness.sync(repair_incoming, acknowledge=False)
    assert offered.get_data() == retried.get_data()
    assert offered.headers[sync_harness.token_header] == retried.headers[
        sync_harness.token_header
    ]
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is True

    sync_harness.sync(
        offered.headers[sync_harness.token_header], acknowledge=False,
    )
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is False


def test_rehydrate_ack_request_echoes_state_after_download_materialization(
    sync_harness,
):
    """A pre-download repair gets one post-download confirmation echo."""
    from cps import kobo, ub

    modified = datetime(2026, 8, 28, 12, 30, 0)
    _add_reading_state(sync_harness, modified, progress=80.0)
    entitlement = sync_harness.sync()
    cursor_ahead = modified + timedelta(days=1)
    advanced = kobo.SyncToken.SyncToken.from_headers({
        sync_harness.token_header:
            entitlement.headers[sync_harness.token_header],
    })
    advanced.reading_state_last_modified = cursor_ahead

    # Firmware can request and acknowledge the repair before it installs the
    # offered bytes. Keep this page pending so the next real request performs
    # the acknowledgment inside HandleSyncRequest.
    pre_download_repair = sync_harness.sync(
        advanced.build_sync_token(), acknowledge=False,
    )
    assert len(_changed_reading_states(pre_download_repair)) == 1
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is True

    # The download itself is device-local and sends no state PUT. Its next
    # sync presents the repair token; the server clears the latch atomically
    # and echoes the authoritative state after all entitlements.
    post_download_sync = sync_harness.sync(
        pre_download_repair.headers[sync_harness.token_header],
        acknowledge=False,
    )
    states = _changed_reading_states(post_download_sync)
    assert len(states) == 1
    assert states[0]["CurrentBookmark"]["ProgressPercent"] == 80
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is False

    # The echo is itself replayable while pending, but acknowledging it does
    # not create an unbounded repair loop.
    replay = sync_harness.sync(
        pre_download_repair.headers[sync_harness.token_header],
        acknowledge=False,
    )
    assert replay.get_data() == post_download_sync.get_data()
    terminal = sync_harness.sync(
        post_download_sync.headers[sync_harness.token_header],
        acknowledge=False,
    )
    assert _changed_reading_states(terminal) == []


def test_rehydrate_confirmation_echo_failure_is_retryable_and_atomic(
    sync_harness, monkeypatch,
):
    """A failed confirmation echo retains the pending repair for retry."""
    from cps import kobo, kobo_sync_status, ub
    from werkzeug.exceptions import ServiceUnavailable

    modified = datetime(2026, 8, 28, 12, 30, 0)
    _add_reading_state(sync_harness, modified, progress=80.0)
    entitlement = sync_harness.sync()
    advanced = kobo.SyncToken.SyncToken.from_headers({
        sync_harness.token_header:
            entitlement.headers[sync_harness.token_header],
    })
    advanced.reading_state_last_modified = modified + timedelta(days=1)
    repair = sync_harness.sync(
        advanced.build_sync_token(), acknowledge=False,
    )
    repair_token = repair.headers[sync_harness.token_header]
    original_renderer = kobo.get_kobo_reading_state_response

    def fail_renderer(*_args, **_kwargs):
        raise RuntimeError("injected confirmation echo failure")

    monkeypatch.setattr(
        kobo, "get_kobo_reading_state_response", fail_renderer,
    )
    with pytest.raises(ServiceUnavailable) as failed:
        sync_harness.sync(repair_token, acknowledge=False)
    assert failed.value.code == 503
    sync_harness.session.expire_all()
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is True
    assert kobo_sync_status.get_pending_sync_page(
        sync_harness.device.id,
    ) is not None

    monkeypatch.setattr(
        kobo, "get_kobo_reading_state_response", original_renderer,
    )
    retried = sync_harness.sync(repair_token, acknowledge=False)
    assert retried.status_code == 200
    assert len(_changed_reading_states(retried)) == 1
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
    """Classification changes New to Changed without changing payload bytes."""
    from cps import ub

    first = _entitlements(sync_harness.sync())
    second = _entitlements(sync_harness.sync())

    assert len(first) == len(second) == 1
    assert set(first[0]) == {"NewEntitlement"}
    assert set(second[0]) == {"ChangedEntitlement"}
    assert first[0]["NewEntitlement"] == second[0]["ChangedEntitlement"]
    assert sync_harness.session.query(ub.KoboDeviceBookEntitlement).count() == 1


@pytest.mark.parametrize("reset_token", [None, "not-a-token", "store.part"])
def test_known_device_exact_replay_is_suppressed_for_any_token_shape(
    sync_harness, monkeypatch, reset_token,
):
    """Token shape cannot override an acknowledged same-device fingerprint."""
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    assert len(_entitlements(sync_harness.sync())) == 1
    assert sync_harness.session.query(ub.KoboDeviceBookEntitlement).count() == 1

    reset_response = sync_harness.sync(reset_token)

    assert _entitlements(reset_response) == []


def test_entitlement_replay_state_is_per_device(sync_harness, monkeypatch):
    """One Kobo's delivery must never suppress another Kobo's first copy."""
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    _first = sync_harness.sync()
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
    first = sync_harness.sync(acknowledge=False)
    assert sync_harness.session.query(
        ub.KoboDevicePendingSyncPage,
    ).filter_by(device_id=sync_harness.device.id).count() == 1
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
    assert sync_harness.session.query(
        ub.KoboDevicePendingSyncPage,
    ).filter_by(device_id=sync_harness.device.id).count() == 0
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
    # The reset leaves the target's Kobos audited, so no later upgrade audit
    # can rewrite their emptied ledgers; the other account's marker is as it was.
    assert {
        row.device_id: row.classification_version for row in
        sync_harness.session.query(ub.KoboDeviceEntitlementSeed)
    } == {
        sync_harness.device.id: kobo.ENTITLEMENT_CLASSIFICATION_VERSION,
        second_target_device.id: kobo.ENTITLEMENT_CLASSIFICATION_VERSION,
        other_device.id: 0,
    }

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
    first = sync_harness.sync(acknowledge=False)
    assert sync_harness.session.query(
        ub.KoboDevicePendingSyncPage,
    ).filter_by(device_id=sync_harness.device.id).count() == 1
    _seed_same_user_device_ledger(sync_harness)
    other_device = _seed_other_user_ledger(sync_harness)
    monkeypatch.setattr(admin, "calibre_db", sync_harness.calibre_db)
    monkeypatch.setattr(admin, "_", lambda value: value)

    with sync_harness.app.test_request_context(
        f"/ajax/kobo_resend/{sync_harness.user.id}/{sync_harness.book.id}",
        method="POST",
    ):
        response = admin.do_kobo_resend(
            sync_harness.user.id, sync_harness.book.id,
        )

    assert response.status_code == 200
    assert sync_harness.session.query(
        ub.KoboDevicePendingSyncPage,
    ).filter_by(device_id=sync_harness.device.id).count() == 0
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


def test_resend_reaches_only_the_requesting_accounts_kobos(
    sync_harness, monkeypatch,
):
    """Resend is one account's request: another account's Kobo that holds the
    book must hear nothing.  It used to bump the book's ``last_modified``, a
    library-wide clock, so every other account's Kobo holding the book got it
    as Changed (#2207's gate: five other-account Kobos).  The cleared ledger
    row is what reselects the book, whatever the reader's cursor."""
    from cps import admin, kobo, ub

    h = sync_harness
    monkeypatch.setattr(kobo.config, "config_kobo_suppress_replayed_entitlements", True)
    monkeypatch.setattr(admin, "calibre_db", h.calibre_db)
    monkeypatch.setattr(admin, "_", lambda value: value)
    other = ub.Device(user_id=18, kind="kobo", display_name="Other Account Kobo",
                      model="Kobo Libra Colour", active=True, created_by="auto")
    h.session.add(other)
    h.session.commit()

    def sync_as(user_id, token, device_id):
        monkeypatch.setattr(h.user, "id", user_id)
        return h.sync(token, internal_device_id=device_id,
                      raw_device_id=("a" if user_id == 17 else "c") * 64)

    def kinds(response):
        return [sorted(item) for item in _entitlements(response)]

    mine = sync_as(17, None, h.device.id)
    theirs = sync_as(18, None, other.id)
    assert kinds(mine) == kinds(theirs) == [["NewEntitlement"]]

    monkeypatch.setattr(h.user, "id", 17)
    with h.app.test_request_context(f"/ajax/kobo_resend/17/{h.book.id}", method="POST"):
        assert admin.do_kobo_resend(17, h.book.id).status_code == 200

    assert kinds(sync_as(18, theirs.headers[h.token_header], other.id)) == []
    assert kinds(sync_as(18, None, other.id)) == []
    assert kinds(sync_as(17, mine.headers[h.token_header], h.device.id)) == [["NewEntitlement"]]


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
        def get_format(_book_id, fmt):
            return epub if fmt == "EPUB" else None
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
        def get_format(_book_id, fmt):
            return kepub if fmt == "KEPUB" else None
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
            "kobo_device_pending_sync_page",
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


def test_pending_page_provenance_requires_cwng_core_cursor_fields():
    """Only a complete cursor set proves a token belongs to a CWNG page chain."""
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
    """Partial legacy and official-store shapes preserve tolerant cursor parsing."""
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
    """The permanent INFO diagnostic must never become a sync failure."""
    from cps import kobo

    caplog.set_level(logging.INFO, logger="cps.kobo")
    response = sync_harness.sync("official.store-token")
    assert response.status_code == 200
    summaries = [
        record.getMessage() for record in caplog.records
        if record.getMessage().startswith("Kobo Sync summary:")
    ]
    assert len(summaries) == 1
    assert "entitlements new=1 changed=0 removed=0" in summaries[0]
    assert "suppressed_replay=0" in summaries[0]
    assert "fingerprint_mismatch_reemitted=0" in summaries[0]
    assert "reemit_reasons=none" in summaries[0]
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


# ---------------------------------------------------------------------------
# Stranded-fix investigation (state/release/v4.1.44/BRIEF-kobo-stranded-fix.md)
# ---------------------------------------------------------------------------


def _legacy_classification_replay(books, page_size):
    """Replay the pre-#2025 New/Changed state machine over a book list.

    The legacy handler emitted ``NewEntitlement`` when a book's created clock
    exceeded the INCOMING token's ``books_last_created``, and set the outgoing
    token to that page's maximum. Reproduced here from
    ``cps/kobo.py`` at 0e3b86005a^ lines 1129-1131 and 1176 so the test states
    its own premise instead of assuming it.
    """
    ordered = sorted(books, key=lambda book: (book.last_modified, book.id))
    new_ids = set()
    watermark = datetime.min
    for offset in range(0, len(ordered), page_size):
        page = ordered[offset:offset + page_size]
        page_max = watermark
        for book in page:
            created = book.timestamp
            if created > watermark:
                new_ids.add(book.id)
            page_max = max(page_max, created)
        watermark = page_max
    return new_ids


def _seed_legacy_install(
        sync_harness, count=218, stagger=True, record_flat=True,
        downloaded=False):
    """A pre-#1925 install: flat KoboSyncedBooks, no per-device ledger rows.

    With ``downloaded`` each book also has the ``Downloads`` row its
    reader's download recorded (``helper.get_download_link``).
    """
    from cps import db, ub

    delivered = [sync_harness.book]
    base = sync_harness.book.last_modified
    sync_harness.book.timestamp = base
    for number in range(2, count + 1):
        clock = base + timedelta(seconds=number - 1) if stagger else base
        book = db.Books(
            f"Held Book {number}",
            f"Held Book {number}",
            "Author",
            clock,
            db.Books.DEFAULT_PUBDATE,
            "1.0",
            clock,
            f"held-book-{number}",
            0,
            [],
            [],
        )
        sync_harness.session.add(book)
        sync_harness.session.flush()
        book.uuid = f"00000000-0000-0000-0003-{number:012d}"
        sync_harness.session.add(db.Data(
            book.id, "EPUB", 3_000_000 + number, f"held-book-{number}",
        ))
        delivered.append(book)
    if record_flat:
        sync_harness.session.add_all([
            ub.KoboSyncedBooks(
                user_id=sync_harness.user.id,
                book_id=book.id,
                book_uuid=str(book.uuid),
            )
            for book in delivered
        ])
    if downloaded:
        sync_harness.session.add_all([
            ub.Downloads(user_id=sync_harness.user.id, book_id=book.id)
            for book in delivered
        ])
    sync_harness.session.commit()
    return delivered


def _v4_1_43_fingerprint(entitlement):
    """The entitlement hash v4.1.43 shipped (``cps/kobo.py`` at tag v4.1.43).

    It hashes the whole payload, ``DownloadUrls[].Size`` included.  Today's
    schema-2 fingerprint hashes a projection without Size, so a v4.1.43 row
    never equals the current fingerprint of a book that has a download.
    """
    return hashlib.sha256(json.dumps(
        entitlement, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()


def _render_book_entitlement(sync_harness, book, *, device=None, archived=None):
    """Render ``book``'s base entitlement as the sync handler does for ``device``."""
    from cps import kobo, ub

    device = device or sync_harness.device
    if archived is None:
        archived = bool(sync_harness.session.query(
            ub.ArchivedBook.is_archived,
        ).filter_by(
            user_id=sync_harness.user.id, book_id=book.id,
        ).scalar())
    with sync_harness.app.test_request_context(
            "/v1/library/sync",
            headers={"x-kobo-devicemodel": device.model or "Kobo Clara BW"}):
        g.annotation_origin_device_id = device.id
        return {
            "BookEntitlement": kobo.create_book_entitlement(
                book, archived=archived,
            ),
            "BookMetadata": kobo.get_metadata(book),
        }


def _rewrite_ledger_as_v4_1_43(sync_harness, *, device=None):
    """Give acknowledged ledger rows the shape a real v4.1.43 app.db holds.

    v4.1.43 stored ``(device_id, book_id, fingerprint, updated_at)`` and wrote
    the row when it SENT an entitlement, New and Changed alike.  Its
    fingerprint hashed ``DownloadUrls[].Size``; ``payload_schema_version`` and
    ``change_basis`` only arrive with the upgrade's ALTER TABLE, as
    ``DEFAULT 1`` and NULL.  Each row written here by current code is rehashed
    with the v4.1.43 formula over the very payload it describes (checked), and
    ``updated_at`` keeps the clock the row was written at.  Deleted-book rows
    get the same shape.  Returns how many book rows now differ from today's
    fingerprint of the same payload.
    """
    from cps import db, kobo, ub

    device = device or sync_harness.device
    session = sync_harness.session
    differing = 0
    for row in session.query(ub.KoboDeviceBookEntitlement).filter_by(
            device_id=device.id).all():
        payload = _render_book_entitlement(
            sync_harness, session.get(db.Books, row.book_id), device=device,
        )
        current = kobo._entitlement_fingerprint(payload)
        assert current == row.fingerprint, (
            f"fixture premise: the row for book {row.book_id} must describe "
            "the payload it is rewritten from"
        )
        row.fingerprint = _v4_1_43_fingerprint(payload)
        differing += row.fingerprint != current
        row.payload_schema_version = 1
        row.change_basis = None
    for row in session.query(ub.KoboDeviceDeletedEntitlement).filter_by(
            device_id=device.id).all():
        tombstone = session.query(ub.KoboDeletedBook).filter_by(
            user_id=sync_harness.user.id, book_uuid=row.book_uuid,
        ).one()
        row.fingerprint = _v4_1_43_fingerprint({
            "BookEntitlement": kobo.create_deleted_book_entitlement(
                row.book_uuid, tombstone.deleted_at,
            ),
            "BookMetadata": kobo.create_deleted_book_metadata(row.book_uuid),
        })
        row.payload_schema_version = 1
        row.change_basis = None
    session.commit()
    return differing


def _mark_v4_1_43_upgrade(sync_harness):
    """The upgrade's ALTER TABLE: every existing seed marker reads version 0."""
    from cps import ub

    for seed in sync_harness.session.query(ub.KoboDeviceEntitlementSeed).all():
        seed.classification_version = 0
    sync_harness.session.commit()


def _record_downloads(sync_harness, response):
    """The reader fetches every book a response announced New.

    Each Kobo download goes through the download route, which records a
    ``Downloads`` row for the account (``helper.get_download_link``).
    """
    from cps import db, ub

    uuids = _entitlement_ids(response, kind="NewEntitlement")
    for book in sync_harness.session.query(db.Books).filter(
            db.Books.uuid.in_(uuids)).all():
        if sync_harness.session.query(ub.Downloads).filter_by(
                user_id=sync_harness.user.id, book_id=book.id).first() is None:
            sync_harness.session.add(ub.Downloads(
                user_id=sync_harness.user.id, book_id=book.id,
            ))
    sync_harness.session.commit()


def _drain_library(sync_harness, books, *, device=None, raw_device_id=None):
    """Deliver ``books`` to one Kobo through acknowledged syncs.

    The reader downloads every book it is sent New.  A Kobo not yet sealed is
    sealed at its first sync.  Returns its settled token.
    """
    from cps import kobo, ub

    device = device or sync_harness.device
    token = kobo.SyncToken.SyncToken().build_sync_token()
    for _page in range(len(books) // kobo.SYNC_ITEM_LIMIT + 2):
        response = sync_harness.sync(
            token, internal_device_id=device.id, raw_device_id=raw_device_id,
        )
        _record_downloads(sync_harness, response)
        token = response.headers[sync_harness.token_header]
        if not _entitlements(response):
            break
    assert sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).filter_by(device_id=device.id).count() == len(books)
    return token


def _deliver_as_v4_1_43(sync_harness, books):
    """Deliver ``books`` for real, then age the ledger into v4.1.43's shape.

    Every book goes out through acknowledged syncs, so every row names a book
    that was sent.  The ledger then takes v4.1.43's row shape and the seed
    marker the upgrade's version 0.  Returns the reader's own settled token.
    """
    token = _drain_library(sync_harness, books)
    assert _rewrite_ledger_as_v4_1_43(sync_harness) == len(books), (
        "fixture premise: no v4.1.43 row may equal today's fingerprint"
    )
    _mark_v4_1_43_upgrade(sync_harness)
    return token


def _seal_as_v4_1_43_boundary_copy(sync_harness, devices, books):
    """Give ``devices``' rows the provenance of v4.1.43's boundary seed copy.

    Upgrading to v4.1.43, the seed staged the account's flat history onto
    every Kobo still unseeded, then one call sealed them all at one instant:
    each copied row predates its device's seal, and devices sealed together
    share it.  The rows here are dated after every book's last change, as
    the seed rendered them.  Returns the seal instant.
    """
    from cps import ub

    copied_at = max(book.last_modified for book in books) + timedelta(hours=1)
    sealed_at = copied_at + timedelta(seconds=1)
    for device in devices:
        sync_harness.session.get(
            ub.KoboDeviceEntitlementSeed, device.id,
        ).seeded_at = sealed_at
        sync_harness.session.query(ub.KoboDeviceBookEntitlement).filter_by(
            device_id=device.id,
        ).update({"updated_at": copied_at}, synchronize_session=False)
    sync_harness.session.commit()
    return sealed_at


def _pair_kobo(sync_harness, display_name, *, user_id=None, active=True):
    """A Kobo paired to the account (or ``user_id``'s), not yet seeded."""
    from cps import ub

    device = ub.Device(
        user_id=user_id or sync_harness.user.id,
        kind="kobo",
        display_name=display_name,
        model="Kobo Libra Colour",
        active=active,
        created_by="auto",
    )
    sync_harness.session.add(device)
    sync_harness.session.commit()
    return device


def _v4_1_43_install(sync_harness, *, count):
    """A one-Kobo v4.1.43 install whose reader holds ``count`` books.

    Returns the books and the reader's own settled token.
    """
    books = _seed_legacy_install(sync_harness, count=count, record_flat=False)
    return books, _deliver_as_v4_1_43(sync_harness, books)


def _legacy_paged_library(sync_harness):
    """#1735's shape: a full first page, then one book called Changed.

    Last-modified ascends while date-added descends, so under v4.1.43's
    creation-time watermark the first ``SYNC_ITEM_LIMIT`` books were New and
    the last one was announced as ChangedEntitlement to a reader that did not
    have it.  Returns every book and that last one.
    """
    from cps import db, kobo

    total = kobo.SYNC_ITEM_LIMIT + 1
    base = sync_harness.book.last_modified
    books = [sync_harness.book]
    sync_harness.book.timestamp = base + timedelta(seconds=total)
    for offset in range(1, total):
        book = db.Books(
            f"Legacy Paged Book {offset + 1}",
            f"Legacy Paged Book {offset + 1}",
            "Author",
            base + timedelta(seconds=total - offset),   # date added
            db.Books.DEFAULT_PUBDATE,
            "1.0",
            base + timedelta(seconds=offset),           # last modified
            f"legacy-paged-book-{offset + 1}",
            0,
            [],
            [],
        )
        sync_harness.session.add(book)
        sync_harness.session.flush()
        book.uuid = f"00000000-0000-0000-0008-{offset + 1:012d}"
        sync_harness.session.add(db.Data(
            book.id, "EPUB", 8_000_000 + offset,
            f"legacy-paged-book-{offset + 1}",
        ))
        books.append(book)
    sync_harness.session.commit()
    return books, books[-1]


def _entitlement_ids(response, *, kind=None):
    """The book ids a response announced, optionally of one envelope kind."""
    ids = []
    for item in _entitlements(response):
        for envelope_kind in ("NewEntitlement", "ChangedEntitlement"):
            if envelope_kind in item and kind in (None, envelope_kind):
                ids.append(item[envelope_kind]["BookEntitlement"]["Id"])
    return ids


def _removal_ids(response):
    """The UUIDs a response told the reader to remove."""
    return [
        item[kind]["BookEntitlement"]["Id"]
        for item in response.get_json()
        for kind in ("NewEntitlement", "ChangedEntitlement")
        if kind in item
        and item[kind].get("BookEntitlement", {}).get("IsRemoved") is True
    ]


def _announced(response):
    """Split what a response tells the reader into New, Changed and removals."""
    new_ids, changed_ids, removed_ids = [], [], []
    for item in _entitlements(response):
        kind = (
            "NewEntitlement" if "NewEntitlement" in item
            else "ChangedEntitlement"
        )
        entitlement = item[kind]["BookEntitlement"]
        if entitlement.get("IsRemoved") is True:
            removed_ids.append(entitlement["Id"])
        elif kind == "NewEntitlement":
            new_ids.append(entitlement["Id"])
        else:
            changed_ids.append(entitlement["Id"])
    return new_ids, changed_ids, removed_ids


_QUIET = {"new": [], "changed": [], "removed": []}


def _sync_session(sync_harness, token, device, raw_device_id, *, pages=6):
    """One Kobo's sync session: pages until one announces nothing.

    The reader downloads every book it is sent New.  Returns its last token
    and what the session announced, as ``{"new", "changed", "removed"}``
    lists of book ids.
    """
    announced = {"new": [], "changed": [], "removed": []}
    for _page in range(pages):
        response = sync_harness.sync(
            token, internal_device_id=device.id, raw_device_id=raw_device_id,
        )
        assert response.status_code == 200
        _record_downloads(sync_harness, response)
        for key, ids in zip(("new", "changed", "removed"), _announced(response)):
            announced[key] += ids
        token = response.headers[sync_harness.token_header]
        if not _entitlements(response):
            break
    return token, announced


def _brief(announced):
    """How many books a session announced, per kind, leaving out none."""
    return {kind: len(ids) for kind, ids in announced.items() if ids}


def _tally(results):
    """``_brief`` for every step of every Kobo."""
    return {
        device_id: {step: _brief(announced) for step, announced in steps.items()}
        for device_id, steps in results.items()
    }


def _join_magic_shelf(sync_harness, monkeypatch):
    """A long-held library book joins a Kobo-synced magic shelf.

    The shelf holds the whole library, and its cache rebuild dates the
    membership after every Kobo's cursor, so the next sync selects every
    member again (#359).  Returns the book that joined.
    """
    from cps import db

    library = sync_harness.session.query(db.Books).all()
    clock = min(book.last_modified for book in library) - timedelta(days=30)
    member = db.Books(
        "Magic Shelf Newcomer", "Magic Shelf Newcomer", "Author", clock,
        db.Books.DEFAULT_PUBDATE, "1.0", clock, "magic-shelf-newcomer",
        0, [], [],
    )
    sync_harness.session.add(member)
    sync_harness.session.flush()
    member.uuid = "00000000-0000-0000-0005-000000000001"
    sync_harness.session.add(db.Data(
        member.id, "EPUB", 5_000_001, "magic-shelf-newcomer",
    ))
    sync_harness.session.commit()
    _rebuild_magic_shelf_cache(
        monkeypatch, [*library, member],
        max(book.last_modified for book in library) + timedelta(days=1),
    )
    return member


def _run_upgrade_sequence(sync_harness, monkeypatch, kobos):
    """Drive the real-upgrade gate's sequence and record each Kobo's syncs.

    ``kobos`` lists ``(device, raw_device_id, token)``.  Each step runs for
    every Kobo in turn: the first sync session after the upgrade, the next
    one, one with no token at all (as after a USB eject, #2131), one after a
    book joins a Kobo-synced magic shelf (``_join_magic_shelf``), and one
    more.  Returns ``{device_id: {step: announced}}`` and that book.
    """
    tokens = {device.id: token for device, _raw, token in kobos}
    results = {device.id: {} for device, _raw, _token in kobos}

    def step(name, *, tokenless=False):
        for device, raw_device_id, _token in kobos:
            tokens[device.id], results[device.id][name] = _sync_session(
                sync_harness,
                None if tokenless else tokens[device.id],
                device,
                raw_device_id,
            )

    step("first")
    step("second")
    step("tokenless", tokenless=True)
    member = _join_magic_shelf(sync_harness, monkeypatch)
    step("magic")
    step("after")
    return results, member


def _settled_legacy_token(books):
    """The token a Kobo holds once it has walked past ``books``."""
    from cps import kobo

    last = max(books, key=lambda book: (book.last_modified, book.id))
    return kobo.SyncToken.SyncToken(
        books_last_modified=last.last_modified,
        books_last_created=max(book.timestamp for book in books),
        books_last_id=last.id,
    ).build_sync_token()


def _give_v4_1_43_boundary_copy(sync_harness, device, books):
    """Stage the rows v4.1.43's boundary seed copied onto ``device``.

    Its seal marker reads classification version 0 after the upgrade's
    ALTER TABLE, and each row has v4.1.43's shape (``_v4_1_43_fingerprint``,
    payload schema 1, no change basis).  ``_seal_as_v4_1_43_boundary_copy``
    then dates the copy and the seal.
    """
    from cps import ub

    sync_harness.session.add(ub.KoboDeviceEntitlementSeed(
        device_id=device.id, classification_version=0,
    ))
    sync_harness.session.add_all([
        ub.KoboDeviceBookEntitlement(
            device_id=device.id,
            book_id=book.id,
            fingerprint=_v4_1_43_fingerprint(
                _render_book_entitlement(sync_harness, book, device=device),
            ),
            payload_schema_version=1,
            change_basis=None,
        )
        for book in books
    ])
    sync_harness.session.commit()


def _v4_1_43_account(sync_harness, topology, *, count):
    """One of the real-upgrade gate's v4.1.43 account shapes.

    Every Kobo that syncs afterwards holds every book, downloaded.  Returns
    the books and those Kobos as ``(device, raw_device_id, token)``.

    ``single``: sealed at its first v4.1.43 sync, holding its own
    deliveries.  ``single_sealed_with_copy``: sealed alone by v4.1.43's
    boundary seed with a copy of the account's flat history.
    ``household_sealed_together``: two Kobos sealed at one instant with that
    copy, the household's union.  ``household_paired_separately``: the
    second Kobo sealed later on its own and sent its own deliveries.
    ``retired_household_kobo``: sealed together, then one of them retired.
    """
    if topology == "household_paired_separately":
        books = _seed_legacy_install(sync_harness, count=count, record_flat=False)
        first_token = _drain_library(sync_harness, books)
        second = _pair_kobo(sync_harness, "Second Household Kobo")
        second_token = _drain_library(
            sync_harness, books, device=second, raw_device_id="b" * 64,
        )
        for device in (sync_harness.device, second):
            assert _rewrite_ledger_as_v4_1_43(
                sync_harness, device=device,
            ) == len(books)
        _mark_v4_1_43_upgrade(sync_harness)
        return books, [
            (sync_harness.device, "a" * 64, first_token),
            (second, "b" * 64, second_token),
        ]

    books, token = _v4_1_43_install(sync_harness, count=count)
    kobos = [(sync_harness.device, "a" * 64, token)]
    if topology == "single":
        return books, kobos
    if topology == "single_sealed_with_copy":
        _seal_as_v4_1_43_boundary_copy(
            sync_harness, (sync_harness.device,), books,
        )
        return books, kobos
    assert topology in (
        "household_sealed_together", "retired_household_kobo",
    ), topology
    second = _pair_kobo(
        sync_harness, "Second Household Kobo",
        active=topology == "household_sealed_together",
    )
    _give_v4_1_43_boundary_copy(sync_harness, second, books)
    _seal_as_v4_1_43_boundary_copy(
        sync_harness, (sync_harness.device, second), books,
    )
    if topology == "household_sealed_together":
        kobos.append((second, "b" * 64, _settled_legacy_token(books)))
    return books, kobos


def _pre_ledger_account(sync_harness, topology, *, count, lag=5):
    """One of the real-upgrade gate's pre-v4.1.43 account shapes.

    Every book is in the account's flat ``KoboSyncedBooks`` history and was
    downloaded; no Kobo has a ledger row or a seal.  Returns the books and
    the Kobos that sync afterwards as ``(device, raw_device_id, token,
    lacking)``, ``lacking`` being the books that Kobo does not hold.

    ``single``; ``household``: two Kobos, both past every book;
    ``household_lagging_kobo``: the second last synced before the newest
    ``lag`` books, which the first one fetched; ``retired_household_kobo``:
    the second is retired and never syncs again.  Another account on the
    server already has a sealed Kobo, from long before this one's were seen.
    """
    from cps import ub

    neighbour = _pair_kobo(
        sync_harness, "Another Account's Kobo",
        user_id=sync_harness.user.id + 1,
    )
    sync_harness.session.add(ub.KoboDeviceEntitlementSeed(
        device_id=neighbour.id,
        seeded_at=datetime(2026, 1, 1),
        classification_version=1,
    ))
    sync_harness.session.commit()
    books = _seed_legacy_install(sync_harness, count=count, downloaded=True)
    kobos = [(sync_harness.device, "a" * 64, _settled_legacy_token(books), [])]
    if topology == "single":
        return books, kobos
    second = _pair_kobo(
        sync_harness, "Second Household Kobo",
        active=topology != "retired_household_kobo",
    )
    if topology == "household":
        kobos.append((second, "b" * 64, _settled_legacy_token(books), []))
    elif topology == "household_lagging_kobo":
        kobos.append((
            second, "b" * 64, _settled_legacy_token(books[:-lag]), books[-lag:],
        ))
    else:
        assert topology == "retired_household_kobo", topology
    return books, kobos


def _add_book_after_every_sync(sync_harness, books):
    """A book added to the library after every Kobo last synced."""
    from cps import db

    clock = max(book.last_modified for book in books) + timedelta(hours=1)
    book = db.Books(
        "Added After The Last Sync", "Added After The Last Sync", "Author",
        clock, db.Books.DEFAULT_PUBDATE, "1.0", clock,
        "added-after-the-last-sync", 0, [], [],
    )
    sync_harness.session.add(book)
    sync_harness.session.flush()
    book.uuid = "00000000-0000-0000-0006-000000000001"
    sync_harness.session.add(db.Data(
        book.id, "EPUB", 6_000_001, "added-after-the-last-sync",
    ))
    sync_harness.session.commit()
    return book


def _add_tombstone_after_every_sync(sync_harness, books):
    """A book hard-deleted after every Kobo last synced; returns its UUID."""
    from cps import ub

    book_uuid = "00000000-0000-0000-0009-000000000003"
    sync_harness.session.add(ub.KoboDeletedBook(
        user_id=sync_harness.user.id,
        book_uuid=book_uuid,
        deleted_at=max(book.last_modified for book in books)
        + timedelta(hours=2),
    ))
    sync_harness.session.commit()
    return book_uuid


def test_upgrade_with_a_valid_token_does_not_reannounce_a_held_library(
    sync_harness, caplog, monkeypatch,
):
    """An existing, fully-downloaded Kobo must not be told its library is New.

    Models the ordinary upgrade of a pre-#1925 install.  Every book was
    emitted to the device as a NewEntitlement by the legacy handler (asserted
    below, not assumed) and downloaded, so the physical Kobo holds all 218
    files, and it presents its own valid token whose cursor is already past
    the library.  Nickel treats a NewEntitlement for a book it already holds
    as "not downloaded" (#1925), so a full re-announce is a full
    re-download.  The account shapes and syncs of the real-upgrade gate are
    ``test_pre_ledger_upgrade_resends_no_held_book``.
    """
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    caplog.set_level(logging.DEBUG, logger="cps.kobo")

    delivered = _seed_legacy_install(sync_harness, downloaded=True)

    # Premise: under the legacy classifier every one of these 218 books was a
    # NewEntitlement, so an empty Kobo could accept every one of them. This is
    # the shape #1735 does NOT describe; nothing here was silently dropped.
    legacy_new = _legacy_classification_replay(delivered, kobo.SYNC_ITEM_LIMIT)
    assert len(legacy_new) == 218, (
        "test premise broken: the legacy handler would not have delivered "
        f"all 218 books as New (only {len(legacy_new)})"
    )
    assert sync_harness.session.query(ub.KoboSyncedBooks).count() == 218
    assert sync_harness.session.query(ub.KoboDeviceBookEntitlement).count() == 0

    # The device's own token, exactly where a fully drained legacy sync left
    # it: past every book by (last_modified, id) and by created clock.
    settled_token = kobo.SyncToken.SyncToken(
        books_last_modified=max(book.last_modified for book in delivered),
        books_last_created=max(book.timestamp for book in delivered),
        books_last_id=max(book.id for book in delivered),
    ).build_sync_token()

    token = settled_token
    page_sizes = []
    kinds = set()
    for _page in range(4):
        response = sync_harness.sync(token)
        assert response.status_code == 200
        items = _entitlements(response)
        page_sizes.append(len(items))
        kinds.update(
            "New" if "NewEntitlement" in item else "Changed" for item in items
        )
        token = response.headers[sync_harness.token_header]

    assert page_sizes == [0, 0, 0, 0], (
        "a Kobo that holds every book and presents a valid token must be "
        f"sent nothing; it was sent {sum(page_sizes)} entitlements "
        f"({sorted(kinds)}) across pages {page_sizes}"
    )


def test_upgrade_still_removes_a_book_hard_deleted_before_the_upgrade(
    sync_harness, monkeypatch,
):
    """A pre-upgrade hard delete must still reach the Kobo that holds it.

    The admin deleted a book on the old server, which recorded a
    ``KoboDeletedBook`` tombstone.  The device was offline and never received
    the removal, so its token's archive cursor still predates the deletion.
    After the upgrade the removal must still be delivered, or the file stays
    on the Kobo forever with no server-side way to dislodge it.
    """
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )

    delivered = _seed_legacy_install(sync_harness, count=3)
    modified = max(book.last_modified for book in delivered)
    orphan_uuid = "00000000-0000-0000-0009-000000000001"
    deleted_at = modified + timedelta(hours=1)
    sync_harness.session.add(ub.KoboDeletedBook(
        user_id=sync_harness.user.id,
        book_uuid=orphan_uuid,
        deleted_at=deleted_at,
    ))
    sync_harness.session.commit()
    assert sync_harness.session.query(
        ub.KoboDeviceDeletedEntitlement,
    ).count() == 0

    # The offline device's token: past every book, but its archive cursor is
    # still before the deletion it never heard about.
    stale_token = kobo.SyncToken.SyncToken(
        books_last_modified=modified,
        books_last_created=max(book.timestamp for book in delivered),
        books_last_id=max(book.id for book in delivered),
        archive_last_modified=modified,
    ).build_sync_token()

    response = sync_harness.sync(stale_token)
    assert response.status_code == 200
    removed = [
        item[kind]["BookEntitlement"]["Id"]
        for item in response.get_json()
        for kind in ("NewEntitlement", "ChangedEntitlement")
        if kind in item
        and item[kind].get("BookEntitlement", {}).get("IsRemoved") is True
    ]
    assert removed == [orphan_uuid], (
        "the upgrade sync swallowed a hard delete the device never received; "
        f"removals emitted: {removed}"
    )


def test_a_book_the_device_never_received_survives_one_library_deletion(
    sync_harness, monkeypatch,
):
    """#1735's undelivered book must still arrive after an unrelated delete.

    Legacy shape: last_modified ascending, date-added descending.  The old
    handler sent books 1-100 as New and book 101 as Changed, which an empty
    Kobo drops (#1735) - so the device does NOT hold book 101.  The admin then
    deletes one unrelated book.  Any upgrade path that reconstructs "what the
    legacy handler classified as New" from CURRENT library rows now re-pages
    the history and can mistake book 101 for a delivered book.  The reader
    downloaded the books it was sent New and nothing downloaded book 101, so
    the upgrade must announce book 101 New and no other book.
    """
    from cps import db, kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )

    base = sync_harness.book.last_modified
    total = kobo.SYNC_ITEM_LIMIT + 1
    books = [sync_harness.book]
    sync_harness.book.last_modified = base
    sync_harness.book.timestamp = base + timedelta(seconds=total)
    for offset in range(1, total):
        book = db.Books(
            f"Legacy Paged Book {offset + 1}",
            f"Legacy Paged Book {offset + 1}",
            "Author",
            base + timedelta(seconds=total - offset),   # date added
            db.Books.DEFAULT_PUBDATE,
            "1.0",
            base + timedelta(seconds=offset),           # last modified
            f"legacy-paged-book-{offset + 1}",
            0,
            [],
            [],
        )
        sync_harness.session.add(book)
        sync_harness.session.flush()
        book.uuid = f"00000000-0000-0000-0007-{offset + 1:012d}"
        sync_harness.session.add(db.Data(
            book.id, "EPUB", 7_000_000 + offset,
            f"legacy-paged-book-{offset + 1}",
        ))
        books.append(book)
    undelivered = books[-1]
    sync_harness.session.add_all([
        ub.KoboSyncedBooks(
            user_id=sync_harness.user.id,
            book_id=book.id,
            book_uuid=str(book.uuid),
        )
        for book in books
    ])
    sync_harness.session.commit()

    # Premise: under the legacy classifier only page one was New, so the
    # physical Kobo never received the last book.
    legacy_new = _legacy_classification_replay(books, kobo.SYNC_ITEM_LIMIT)
    assert undelivered.id not in legacy_new
    assert len(legacy_new) == kobo.SYNC_ITEM_LIMIT
    sync_harness.session.add_all([
        ub.Downloads(user_id=sync_harness.user.id, book_id=book_id)
        for book_id in legacy_new
    ])

    # One unrelated book leaves the library, as books routinely do.
    dropped = books[10]
    sync_harness.session.query(db.Data).filter_by(book=dropped.id).delete()
    sync_harness.session.query(db.Books).filter_by(id=dropped.id).delete()
    sync_harness.session.commit()

    settled_token = kobo.SyncToken.SyncToken(
        books_last_modified=max(b.last_modified for b in books),
        books_last_created=max(b.timestamp for b in books),
        books_last_id=max(b.id for b in books),
    ).build_sync_token()

    _token, announced = _sync_session(
        sync_harness, settled_token, sync_harness.device, "a" * 64,
    )

    assert announced == {
        "new": [str(undelivered.uuid)], "changed": [], "removed": [],
    }, (
        "expected only the book the device never received, New; got "
        f"{ {kind: len(ids) for kind, ids in announced.items()} }"
    )


def test_v4_1_43_install_upgrading_is_not_told_its_whole_library_is_new(
    sync_harness, monkeypatch,
):
    """The shipped v4.1.43 -> next-release upgrade must be silent.

    v4.1.43 (tag bdead55920) seeded and kept a per-device ledger but wrote no
    classification stamp, so after the upgrade's ALTER TABLE every existing
    install reads ``classification_version`` 0 and holds ledger rows in
    v4.1.43's shape: fingerprints that hash ``DownloadUrls[].Size`` (today's do
    not), ``payload_schema_version`` 1 and ``change_basis`` NULL.  Here the
    reader received all 218 books through real acknowledged syncs, and the
    ledger was then rewritten into exactly that shape (``_v4_1_43_install``).
    The reader presents its own settled token.  Nickel re-downloads a held
    book it is told about again (#1925), so the upgrade sync and the pages
    after it must carry nothing.  Syncs that reselect the held books are
    ``test_v4_1_43_held_books_stay_silent_when_a_sync_reselects_them``.
    """
    from cps import kobo

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    _books, token = _v4_1_43_install(sync_harness, count=218)

    upgrade_pages = []
    for _page in range(4):
        response = sync_harness.sync(token)
        upgrade_pages.append(len(_entitlements(response)))
        token = response.headers[sync_harness.token_header]

    assert upgrade_pages == [0, 0, 0, 0], (
        "upgrading a v4.1.43 install re-announced "
        f"{sum(upgrade_pages)} of its 218 already-downloaded books "
        f"across pages {upgrade_pages}"
    )


@pytest.mark.parametrize("topology", (
    "single",
    "single_sealed_with_copy",
    "household_sealed_together",
    "household_paired_separately",
    "retired_household_kobo",
    "new_kobo_syncs_first",
    "new_kobo_syncs_first_after_copy",
))
def test_v4_1_43_upgrade_resends_no_held_book(
    sync_harness, monkeypatch, topology,
):
    """No Kobo is told again about a book it holds, as New or as Changed.

    The real-upgrade gate's bar for a v4.1.43 install, in its account shapes
    (``_v4_1_43_account``): every Kobo holds every book, and after the
    upgrade it runs the first sync session, the next one, one with no
    token, and one after a magic-shelf membership change selects the whole
    library again (``_run_upgrade_sequence``).  Nickel re-downloads a held
    book it is told about again (#1925), so none of those syncs may carry
    one; only the book that joined the shelf goes out, New, once.  The
    ``new_kobo_syncs_first`` shapes add a Kobo paired after the upgrade whose
    first sync runs the one-time audit before the old reader syncs; it holds
    nothing and is sent the library New.  Fails if the upgrade clears a
    ledger whose books the account downloaded (they come back New) or keeps
    it without vouching for it (they come back Changed when selected again).
    """
    from cps import kobo

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    shape = {
        "new_kobo_syncs_first": "single",
        "new_kobo_syncs_first_after_copy": "single_sealed_with_copy",
    }.get(topology, topology)
    books, kobos = _v4_1_43_account(sync_harness, shape, count=120)
    if shape != topology:
        new_kobo = _pair_kobo(sync_harness, "Kobo Paired After The Upgrade")
        token, initial = _sync_session(sync_harness, None, new_kobo, "c" * 64)
        assert sorted(initial["new"]) == sorted(str(b.uuid) for b in books)
        assert initial["changed"] == []
        kobos.append((new_kobo, "c" * 64, token))

    results, member = _run_upgrade_sequence(sync_harness, monkeypatch, kobos)

    assert _tally(results) == {
        device.id: {
            "first": {}, "second": {}, "tokenless": {},
            "magic": {"new": 1}, "after": {},
        }
        for device, _raw, _token in kobos
    }, f"{topology}: held books were announced again: {_tally(results)}"
    assert all(
        steps["magic"]["new"] == [str(member.uuid)]
        for steps in results.values()
    )


@pytest.mark.parametrize("topology", (
    "single",
    "single_sealed_with_copy",
    "household_sealed_together",
    "household_paired_separately",
    "retired_household_kobo",
))
def test_v4_1_43_kobo_whose_first_sync_has_no_token_resends_no_held_book(
    sync_harness, monkeypatch, topology,
):
    """A v4.1.43 Kobo that comes back without a token still holds its books.

    A Kobo can make its first sync after the upgrade with no token, as after
    a USB eject (#2131), and that sync selects the whole library.  Unlike a
    Kobo from before v4.1.43, which then has no cursor to vouch for its
    history, a v4.1.43 Kobo brings its ledger across the upgrade: the audit
    keeps and stamps the rows its downloads vouch for whatever the request
    carries.  Fails if the audit or the stamping depends on the cursor.
    """
    from cps import kobo

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    _books, kobos = _v4_1_43_account(sync_harness, topology, count=120)

    announced = {
        device.id: _brief(
            _sync_session(sync_harness, None, device, raw_device_id)[1],
        )
        for device, raw_device_id, _token in kobos
    }

    assert announced == {device.id: {} for device, _raw, _token in kobos}, (
        f"{topology}: {announced}"
    )


@pytest.mark.parametrize("kobos", (1, 2))
def test_v4_1_43_book_no_kobo_downloaded_is_announced_new_once(
    sync_harness, monkeypatch, kobos,
):
    """A book v4.1.43 sent that nothing downloaded goes out New, once.

    v4.1.43 wrote a ledger row when it sent a book, not when the reader
    stored it.  Its creation-time classifier announced page two of a large
    first sync as ChangedEntitlement, which a reader without the book drops
    (#1735, the "stops at 100 books" reports); a response lost in transit
    ends the same way.  Either way the account has no download of the book.
    The upgrade announces it New to each Kobo -- one reader, or a household
    whose readers v4.1.43's boundary seed copied that row onto -- and every
    book they hold stays silent, tokenless sync included.  Fails if the
    upgrade keeps a row nothing vouches for (the book is never sent again)
    or clears the rows the account downloaded.
    """
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    books, never_stored = _legacy_paged_library(sync_harness)
    # Premise: v4.1.43's classifier announced this book Changed, not New.
    assert never_stored.id not in _legacy_classification_replay(
        books, kobo.SYNC_ITEM_LIMIT,
    )
    readers = [(sync_harness.device, "a" * 64, _deliver_as_v4_1_43(
        sync_harness, books,
    ))]
    # The reader dropped that ChangedEntitlement: nothing downloaded it.
    sync_harness.session.query(ub.Downloads).filter_by(
        user_id=sync_harness.user.id, book_id=never_stored.id,
    ).delete()
    sync_harness.session.commit()
    if kobos == 2:
        second = _pair_kobo(sync_harness, "Second Household Kobo")
        _give_v4_1_43_boundary_copy(sync_harness, second, books)
        _seal_as_v4_1_43_boundary_copy(
            sync_harness, (sync_harness.device, second), books,
        )
        readers.append((second, "b" * 64, _settled_legacy_token(books)))

    announced = {}
    for device, raw_device_id, token in readers:
        token, first = _sync_session(sync_harness, token, device, raw_device_id)
        _token, second_session = _sync_session(
            sync_harness, token, device, raw_device_id,
        )
        _token, tokenless = _sync_session(
            sync_harness, None, device, raw_device_id,
        )
        announced[device.id] = [first, second_session, tokenless]
    assert announced == {
        device.id: [
            {"new": [str(never_stored.uuid)], "changed": [], "removed": []},
            _QUIET,
            _QUIET,
        ]
        for device, _raw, _token in readers
    }, {
        device_id: [_brief(session) for session in sessions]
        for device_id, sessions in announced.items()
    }


def test_v4_1_43_upgrade_keeps_a_held_row_over_the_position_sentinel(
    sync_harness, monkeypatch,
):
    """A kept row already proves delivery; the sentinel must not clobber it.

    The reading-position sentinel exists for books the device demonstrably
    holds but that have no ledger row.  Writing it over a row that survived
    the audit would force a pointless ChangedEntitlement for exactly the
    books whose delivery is best evidenced.
    """
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    delivered = _seed_legacy_install(sync_harness, count=3, record_flat=False)

    token = kobo.SyncToken.SyncToken().build_sync_token()
    for _page in range(2):
        response = sync_harness.sync(token)
        token = response.headers[sync_harness.token_header]
    assert sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).count() == 3

    position = sync_harness.session.query(ub.DeviceReadingPosition).filter_by(
        device_id=sync_harness.device.id, book_id=delivered[0].id,
    ).one_or_none()
    if position is None:
        position = ub.DeviceReadingPosition(
            device_id=sync_harness.device.id,
            book_id=delivered[0].id,
            server_modified_at=delivered[0].last_modified,
        )
        sync_harness.session.add(position)
    position.client_modified_at = delivered[0].last_modified
    seed = sync_harness.session.get(
        ub.KoboDeviceEntitlementSeed, sync_harness.device.id,
    )
    seed.classification_version = 0
    sync_harness.session.commit()
    before = {
        row.book_id: row.fingerprint
        for row in sync_harness.session.query(ub.KoboDeviceBookEntitlement)
    }

    response = sync_harness.sync(token)
    assert _entitlements(response) == []
    after = {
        row.book_id: row.fingerprint
        for row in sync_harness.session.query(ub.KoboDeviceBookEntitlement)
    }
    assert after == before


def test_v4_1_43_upgrade_still_delivers_a_book_v4_1_43_never_sent(
    sync_harness, monkeypatch,
):
    """Keeping the ledger must not suppress a book that is not in it.

    v4.1.43 could leave a book behind its reader's cursor without ever
    sending it: its "only sync selected shelves" mode folded a newer shelf
    date into the cursor after the first page and then went silent (#2201).
    v4.1.43 wrote ledger rows only for books it sent, so such a book has no
    row, and the cursor-independent recovery arm must announce it New across
    the upgrade -- keeping the rows for held books may not cost it its repair.

    This covers books v4.1.43 never SENT.  A book it sent that nothing
    downloaded is ``test_v4_1_43_book_no_kobo_downloaded_is_announced_new_once``;
    one a download elsewhere vouches for is the documented gap in
    ``test_v4_1_43_book_downloaded_elsewhere_is_recovered_by_full_sync_or_resend``.
    """
    from cps import db, kobo

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    books, token = _v4_1_43_install(sync_harness, count=3)

    # A book whose clock is already behind the reader's cursor, with no flat
    # marker and no ledger row: v4.1.43's cursor never selected it.
    behind = min(book.last_modified for book in books) - timedelta(days=1)
    never_sent = db.Books(
        "Never Sent", "Never Sent", "Author", behind, db.Books.DEFAULT_PUBDATE,
        "1.0", behind, "never-sent", 0, [], [],
    )
    sync_harness.session.add(never_sent)
    sync_harness.session.flush()
    never_sent.uuid = "00000000-0000-0000-0003-999999999999"
    sync_harness.session.add(db.Data(
        never_sent.id, "EPUB", 3_999_999, "never-sent",
    ))
    sync_harness.session.commit()

    response = sync_harness.sync(token)
    assert _entitlement_ids(response) == [str(never_sent.uuid)], (
        _entitlements(response)
    )
    assert _entitlement_ids(response, kind="NewEntitlement") == [
        str(never_sent.uuid),
    ]
    assert _entitlements(sync_harness.sync(
        response.headers[sync_harness.token_header],
    )) == []


def _rebuild_magic_shelf_cache(monkeypatch, books, rebuilt_at):
    """A magic-shelf cache rebuild: every member is selected again (#359)."""
    from cps import kobo

    member_ids = {book.id for book in books}
    monkeypatch.setattr(
        kobo, "get_magic_shelf_book_ids_for_kobo",
        lambda _user_id: (set(member_ids), True),
    )
    monkeypatch.setattr(
        kobo, "get_magic_shelf_membership_added_at",
        lambda _user_id: rebuilt_at,
    )


@pytest.mark.parametrize("reselection", (
    "stale_cursor_on_the_upgrade_sync",
    "tokenless_sync_after_the_upgrade",
    "magic_shelf_rebuild_after_the_upgrade",
))
def test_v4_1_43_held_books_stay_silent_when_a_sync_reselects_them(
    sync_harness, monkeypatch, reselection,
):
    """A kept v4.1.43 ledger must suppress, not merely go unconsulted.

    A v4.1.43 row hashes ``DownloadUrls[].Size`` and has no change basis, so
    on its own it never matches a current payload: the first sync that
    selects a held book again would announce it ChangedEntitlement, and
    Nickel de-downloads a held book on Changed (#1925).  Three ordinary
    things select a whole library again -- a valid token whose cursor trails
    the reader's ledger, a request with no token at all (USB eject, #2131),
    and a magic-shelf cache rebuild, which happens every 30 minutes (#359).
    Each must announce nothing, and each row must come out re-fingerprinted
    at the current schema so the next reselection is an exact-bytes match.
    One held book was restored from the archive long before v4.1.43 sent it,
    so its change basis carries an archive clock too.  Fails if the upgrade
    keeps v4.1.43 rows without proving them current (they fail open as
    Changed) or clears them (they come back New).
    """
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    books = _seed_legacy_install(sync_harness, count=218, record_flat=False)
    restored = books[5]
    archive_clocks = {
        restored.id: min(book.last_modified for book in books)
        - timedelta(days=7),
    }
    sync_harness.session.add(ub.ArchivedBook(
        user_id=sync_harness.user.id,
        book_id=restored.id,
        is_archived=False,
        last_modified=archive_clocks[restored.id],
    ))
    sync_harness.session.commit()
    settled_token = _deliver_as_v4_1_43(sync_harness, books)

    if reselection == "stale_cursor_on_the_upgrade_sync":
        reselecting = sync_harness.sync(
            kobo.SyncToken.SyncToken().build_sync_token(),
        )
    else:
        upgrade = sync_harness.sync(settled_token)
        assert _entitlements(upgrade) == []
        if reselection == "tokenless_sync_after_the_upgrade":
            reselecting = sync_harness.sync(None)
        else:
            _rebuild_magic_shelf_cache(
                monkeypatch, books,
                max(book.last_modified for book in books) + timedelta(days=1),
            )
            reselecting = sync_harness.sync(
                upgrade.headers[sync_harness.token_header],
            )
    following = sync_harness.sync(
        reselecting.headers[sync_harness.token_header],
    )

    announced = [
        len(_entitlements(response)) for response in (reselecting, following)
    ]
    assert announced == [0, 0], (
        f"{reselection}: a reader holding all {len(books)} books was told "
        f"about {announced} of them again, which de-downloads each one"
    )

    sync_harness.session.expire_all()
    rows = {
        row.book_id: row for row in sync_harness.session.query(
            ub.KoboDeviceBookEntitlement,
        ).filter_by(device_id=sync_harness.device.id).all()
    }
    assert set(rows) == {book.id for book in books}
    not_current = [
        book.id for book in books
        if (
            rows[book.id].payload_schema_version,
            rows[book.id].change_basis,
            rows[book.id].fingerprint,
        ) != (
            kobo.ENTITLEMENT_PAYLOAD_SCHEMA_VERSION,
            kobo._book_entitlement_change_basis(
                book.last_modified, archive_clocks.get(book.id),
            ),
            kobo._entitlement_fingerprint(
                _render_book_entitlement(sync_harness, book),
            ),
        )
    ]
    assert not_current == [], (
        f"{len(not_current)} reselected rows were not re-fingerprinted at the "
        f"current schema, e.g. book ids {not_current[:5]}"
    )


def test_v4_1_43_book_edited_after_the_upgrade_is_still_delivered_as_changed(
    sync_harness, monkeypatch,
):
    """Keeping the ledger must not swallow a real edit.

    The upgrade vouches for a kept row only while its book is unchanged.  An
    edit after the upgrade moves the book's change basis, so the row no longer
    qualifies for the silent re-fingerprint, and the edit reaches the reader
    once, as ChangedEntitlement, with no other book.  Fails if kept rows are
    trusted whatever the book's clock says.
    """
    from cps import kobo

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    books, token = _v4_1_43_install(sync_harness, count=30)
    upgrade = sync_harness.sync(token)
    assert _entitlements(upgrade) == []

    edited = books[7]
    edited.title = f"{edited.title} (revised)"
    edited.last_modified = (
        max(book.last_modified for book in books) + timedelta(hours=1)
    )
    sync_harness.session.commit()

    changed = sync_harness.sync(upgrade.headers[sync_harness.token_header])
    envelopes = _entitlements(changed)
    assert _entitlement_ids(changed, kind="ChangedEntitlement") == [
        str(edited.uuid),
    ], envelopes
    assert len(envelopes) == 1, envelopes
    assert (
        envelopes[0]["ChangedEntitlement"]["BookMetadata"]["Title"]
        == edited.title
    )
    assert _entitlements(sync_harness.sync(
        changed.headers[sync_harness.token_header],
    )) == []


@pytest.mark.parametrize("moved_clock", ("book_edited", "unarchived"))
def test_v4_1_43_row_older_than_its_books_last_change_is_still_delivered(
    sync_harness, monkeypatch, moved_clock,
):
    """A kept row vouches only for the payload it was written for.

    v4.1.43 wrote the row when it sent the book.  If the book changed after
    that and before the upgrade, the reader holds the older version and
    v4.1.43 would have sent the change at its next sync.  The upgrade must
    not stamp such a row as current: it stays unproven, and the change goes
    out once as ChangedEntitlement when the book is next selected, while the
    held books around it stay silent.  Both clocks in the change basis count:
    an edit moves ``Books.last_modified``; un-archiving through the book
    editor moves only ``ArchivedBook.last_modified`` and leaves the row, which
    still describes the archived payload.  Fails if the audit stamps a row
    without comparing the book's clocks with the row's.
    """
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    books, token = _v4_1_43_install(sync_harness, count=30)
    target = books[3]
    row = sync_harness.session.query(ub.KoboDeviceBookEntitlement).filter_by(
        device_id=sync_harness.device.id, book_id=target.id,
    ).one()
    written_at = datetime(2026, 8, 29, 9, 0, 0)
    changed_at = written_at + timedelta(days=1)
    row.updated_at = written_at
    if moved_clock == "book_edited":
        target.title = f"{target.title} (revised)"
        target.last_modified = changed_at
    else:
        # v4.1.43 last sent this book while it was archived; the editor's
        # un-archive (editbooks ``is_archived`` = False) leaves ledger rows.
        row.fingerprint = _v4_1_43_fingerprint(
            _render_book_entitlement(sync_harness, target, archived=True),
        )
        sync_harness.session.add(ub.ArchivedBook(
            user_id=sync_harness.user.id,
            book_id=target.id,
            is_archived=False,
            last_modified=changed_at,
        ))
    sync_harness.session.commit()

    upgrade = sync_harness.sync(token)
    reselected = sync_harness.sync(None)

    announced = _entitlements(upgrade) + _entitlements(reselected)
    assert [sorted(item) for item in announced] == [["ChangedEntitlement"]], (
        f"{moved_clock}: expected exactly one ChangedEntitlement for the "
        f"changed book, got {announced}"
    )
    entitlement = announced[0]["ChangedEntitlement"]
    assert entitlement["BookEntitlement"]["Id"] == str(target.uuid)
    assert entitlement["BookEntitlement"]["IsRemoved"] is False
    assert entitlement["BookMetadata"]["Title"] == target.title


@pytest.mark.parametrize("kobos", (1, 2))
def test_v4_1_43_upgrade_delivers_a_removal_its_seed_marked_acknowledged(
    sync_harness, monkeypatch, kobos,
):
    """A hard delete the reader never heard about must still reach it once.

    v4.1.43's seed wrote every tombstone of the user into every Kobo's
    deleted-book ledger as if acknowledged, so a removal an offline reader
    never received was suppressed and its archive cursor moved past it: the
    deleted book stays on the reader with no server-side way to dislodge it.
    The upgrade clears those rows for a single Kobo as for a household, so
    the removal goes out exactly once.  The price is resending removals a
    reader already applied, as main always did; that this is harmless
    (Nickel has nothing left to remove) is ASSUMED, not observed on hardware.
    """
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    books, _token = _v4_1_43_install(sync_harness, count=3)
    devices = [sync_harness.device]
    if kobos == 2:
        second = ub.Device(
            user_id=sync_harness.user.id,
            kind="kobo",
            display_name="Second Household Kobo",
            model="Kobo Libra Colour",
            active=True,
            created_by="auto",
        )
        sync_harness.session.add(second)
        sync_harness.session.flush()
        sync_harness.session.add(ub.KoboDeviceEntitlementSeed(
            device_id=second.id, classification_version=0,
        ))
        devices.append(second)

    orphan = "00000000-0000-0000-0009-000000000002"
    deleted_at = max(book.last_modified for book in books) + timedelta(hours=1)
    sync_harness.session.add(ub.KoboDeletedBook(
        user_id=sync_harness.user.id, book_uuid=orphan, deleted_at=deleted_at,
    ))
    seeded_fingerprint = _v4_1_43_fingerprint({
        "BookEntitlement": kobo.create_deleted_book_entitlement(
            orphan, deleted_at,
        ),
        "BookMetadata": kobo.create_deleted_book_metadata(orphan),
    })
    sync_harness.session.add_all([
        ub.KoboDeviceDeletedEntitlement(
            device_id=device.id,
            book_uuid=orphan,
            fingerprint=seeded_fingerprint,
            payload_schema_version=1,
            change_basis=None,
        )
        for device in devices
    ])
    sync_harness.session.commit()

    # v4.1.43 suppressed the removal and moved the archive cursor past it.
    token = kobo.SyncToken.SyncToken(
        books_last_modified=max(book.last_modified for book in books),
        books_last_created=max(book.timestamp for book in books),
        books_last_id=max(book.id for book in books),
        archive_last_modified=deleted_at + timedelta(minutes=1),
    ).build_sync_token()
    removed = []
    for _sync in range(3):
        response = sync_harness.sync(token)
        removed += _removal_ids(response)
        token = response.headers[sync_harness.token_header]

    assert removed == [orphan], (
        f"{kobos} Kobo(s): a removal the reader never received was delivered "
        f"{removed.count(orphan)} times across three syncs"
    )


@pytest.mark.parametrize("recovery", ("full_sync", "per_book_resend"))
def test_v4_1_43_book_downloaded_elsewhere_is_recovered_by_full_sync_or_resend(
    sync_harness, monkeypatch, recovery,
):
    """The documented gap, and the recovery that reaches it.

    v4.1.43 wrote a ledger row when it SENT an entitlement, New and Changed
    alike.  Its creation-time classifier announced page two of a large first
    sync as ChangedEntitlement, which a reader that does not hold the book
    ignores (#1735, the "stops at 100 books" reports).  The upgrade keeps a
    row whose book the account downloaded, and a ``Downloads`` row does not
    say who downloaded: a browser, or another Kobo of the account, vouches
    for the book this reader dropped (``_migrate_device_entitlement_
    classification`` says why).  So the upgrade stays silent about it even
    when the library is selected again -- asserted first, so a change that
    starts guessing shows up here.  The recovery the product offers must
    still reach the book: Full Sync announces the library New, and a
    per-book resend announces exactly that book New.  The same book with no
    download is ``test_v4_1_43_book_no_kobo_downloaded_is_announced_new_once``.
    """
    from cps import admin, kobo

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    monkeypatch.setattr(admin, "calibre_db", sync_harness.calibre_db)
    monkeypatch.setattr(admin, "_", lambda value: value)
    books, never_stored = _legacy_paged_library(sync_harness)
    # Premise: v4.1.43's classifier announced this book Changed, not New.
    assert never_stored.id not in _legacy_classification_replay(
        books, kobo.SYNC_ITEM_LIMIT,
    )
    token = _deliver_as_v4_1_43(sync_harness, books)

    upgrade = sync_harness.sync(token)
    reselected = sync_harness.sync(None)
    assert _entitlements(upgrade) + _entitlements(reselected) == [], (
        "kept v4.1.43 rows were announced again after the upgrade (Changed: "
        "the audit did not vouch for them; New: it cleared them). If the gap "
        "was closed on purpose, rewrite this test and the documented gap"
    )
    token = reselected.headers[sync_harness.token_header]

    if recovery == "full_sync":
        with sync_harness.app.test_request_context(
                f"/ajax/fullsync/{sync_harness.user.id}", method="POST"):
            assert admin.do_full_kobo_sync(
                sync_harness.user.id,
            ).status_code == 200
        expected = sorted(str(book.uuid) for book in books)
    else:
        with sync_harness.app.test_request_context(
                f"/ajax/kobo_resend/{sync_harness.user.id}/{never_stored.id}",
                method="POST"):
            assert admin.do_kobo_resend(
                sync_harness.user.id, never_stored.id,
            ).status_code == 200
        expected = [str(never_stored.uuid)]

    new_ids, changed_ids = [], []
    for _page in range(4):
        response = sync_harness.sync(token)
        new_ids += _entitlement_ids(response, kind="NewEntitlement")
        changed_ids += _entitlement_ids(response, kind="ChangedEntitlement")
        token = response.headers[sync_harness.token_header]
    assert changed_ids == []
    assert sorted(new_ids) == expected, (
        f"{recovery}: announced {len(new_ids)} books New; the book the reader "
        f"never stored was {'' if str(never_stored.uuid) in new_ids else 'not '}"
        "among them"
    )


@pytest.mark.parametrize("topology", (
    "single",
    "household",
    "household_lagging_kobo",
    "retired_household_kobo",
    "new_kobo_syncs_first",
))
def test_pre_ledger_upgrade_resends_no_held_book(
    sync_harness, monkeypatch, topology,
):
    """A Kobo from before v4.1.43 keeps what its own cursor says it walked.

    An install upgrading from v4.1.34 to v4.1.42 has no per-device ledger,
    only the account's flat ``KoboSyncedBooks`` history and each Kobo's own
    token (``_pre_ledger_account``).  After the upgrade every Kobo must stay
    silent about the books it holds through the gate's sequence
    (``_run_upgrade_sequence``), while what it lacks arrives once: a book
    added after its last sync, the books a lagging household Kobo never
    reached, and a hard delete, as one removal.  In the
    ``new_kobo_syncs_first`` shape a Kobo paired after the upgrade syncs
    first; it holds nothing and is sent the library New.  Fails if the
    upgrade ignores the flat history (the library comes back New, as on
    main) or credits a Kobo with books its own cursor never passed.
    """
    from cps import kobo

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    shape = "single" if topology == "new_kobo_syncs_first" else topology
    books, kobos = _pre_ledger_account(sync_harness, shape, count=120)
    added = _add_book_after_every_sync(sync_harness, books)
    removed = _add_tombstone_after_every_sync(sync_harness, books)
    expected_first = {
        device.id: {
            "new": sorted(str(book.uuid) for book in [*lacking, added]),
            "changed": [],
            "removed": [removed],
        }
        for device, _raw, _token, lacking in kobos
    }
    if topology == "new_kobo_syncs_first":
        new_kobo = _pair_kobo(sync_harness, "Kobo Paired After The Upgrade")
        token, initial = _sync_session(sync_harness, None, new_kobo, "c" * 64)
        assert sorted(initial["new"]) == sorted(
            str(book.uuid) for book in [*books, added]
        )
        assert initial["changed"] == []
        kobos.append((new_kobo, "c" * 64, token, []))
        expected_first[new_kobo.id] = _QUIET

    results, member = _run_upgrade_sequence(
        sync_harness, monkeypatch,
        [(device, raw, token) for device, raw, token, _lacking in kobos],
    )

    first = {
        device_id: {kind: sorted(ids) for kind, ids in steps["first"].items()}
        for device_id, steps in results.items()
    }
    assert first == expected_first, (
        f"{topology}: first sync after the upgrade: {_tally(results)}"
    )
    assert {
        device_id: {
            step: announced for step, announced in steps.items()
            if step != "first"
        }
        for device_id, steps in results.items()
    } == {
        device_id: {
            "second": _QUIET,
            "tokenless": _QUIET,
            "magic": {"new": [str(member.uuid)], "changed": [], "removed": []},
            "after": _QUIET,
        }
        for device_id in results
    }, f"{topology}: {_tally(results)}"


@pytest.mark.parametrize("first_token", ("none", "store"))
def test_pre_ledger_kobo_without_a_cursor_is_sent_its_library_new(
    sync_harness, monkeypatch, first_token,
):
    """A pre-ledger Kobo whose first sync carries no cursor is seeded with nothing.

    A factory-reset reader presents no token, and so can one syncing after a
    USB eject (#2131); an official-store token carries no library cursor
    either.  Nothing then separates a reader that holds nothing from one that
    holds everything, and crediting the account's history would starve the
    reset reader until Full Sync.  So the library goes out New once and the
    Kobo is sealed: the settled token it may present next seeds nothing
    more.  A deliberate limit of the upgrade, not a repair; fails if a
    request without a cursor is credited with the account's flat history.
    """
    from cps import kobo

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    books = _seed_legacy_install(sync_harness, count=30, downloaded=True)
    token = None if first_token == "none" else "store-token.store-signature"

    _token, first = _sync_session(sync_harness, token, sync_harness.device, "a" * 64)
    _token, settled = _sync_session(
        sync_harness, _settled_legacy_token(books), sync_harness.device, "a" * 64,
    )

    assert sorted(first["new"]) == sorted(str(book.uuid) for book in books), (
        _brief(first)
    )
    assert first["changed"] == []
    assert settled == _QUIET, _brief(settled)


def test_kobo_paired_before_a_new_accounts_first_sync_is_sent_its_library_new(
    sync_harness, monkeypatch,
):
    """A new account's second Kobo is new to the server whatever cursor it shows.

    Only an account crossing from before v4.1.43 has flat history older than
    its first seal, so only its Kobos wait for a seed.  A new account's first
    sync seals every Kobo it has.  Its second Kobo, paired before that sync
    and syncing later with a cursor from another server (#2131), must be sent
    the library New: the flat history and downloads the first Kobo's sync
    left are that Kobo's.  Fails if the second Kobo waits to be seeded from
    them.
    """
    from cps import kobo

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    books = _seed_legacy_install(sync_harness, count=30, record_flat=False)
    second = _pair_kobo(sync_harness, "Kobo Paired Before The First Sync")

    _token, first = _sync_session(sync_harness, None, sync_harness.device, "a" * 64)
    _token, paired_early = _sync_session(
        sync_harness, _settled_legacy_token(books), second, "b" * 64,
    )

    library = sorted(str(book.uuid) for book in books)
    assert sorted(first["new"]) == library, _brief(first)
    assert sorted(paired_early["new"]) == library, _brief(paired_early)
    assert paired_early["changed"] == []


def test_kobo_sealed_without_history_is_not_sent_new_a_book_it_reported_reading(
    sync_harness, monkeypatch,
):
    """The audit's position sentinel still covers a Kobo sealed without history.

    A Kobo first seen after its account's first seal gets no share of the
    flat history, as with the second Kobo of an install from v4.1.33 or
    older, whose first contact comes after the upgrade.  A reading position
    it reported before its first library sync is the one record that it
    holds that book, so the book goes out Changed rather than New.  The
    pre-ledger seed now covers the flat-history case through which
    ``test_real_download_and_sync_orderings_converge`` reached this
    sentinel.  Fails if the audit stops writing it.
    """
    from cps import kobo

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    books = _seed_legacy_install(sync_harness, count=5, downloaded=True)
    _token, first = _sync_session(
        sync_harness, _settled_legacy_token(books), sync_harness.device, "a" * 64,
    )
    assert first == _QUIET, _brief(first)
    later = _pair_kobo(sync_harness, "Kobo First Seen After The Upgrade")
    assert sync_harness.put_position(
        40.0, clock="2026-08-29T15:00:00Z", internal_device_id=later.id,
    ).status_code == 200

    _token, announced = _sync_session(sync_harness, None, later, "c" * 64)

    read = sync_harness.book
    assert announced == {
        "new": sorted(str(book.uuid) for book in books if book is not read),
        "changed": [str(read.uuid)],
        "removed": [],
    }, _brief(announced)


@pytest.mark.parametrize("change", ("edited", "unarchived", "archived"))
def test_pre_ledger_seed_leaves_out_a_book_that_changed_past_the_cursor(
    sync_harness, monkeypatch, change,
):
    """A flat-history book the Kobo's cursor does not vouch for is not seeded.

    The seed credits a book only as its reader last saw it: unchanged, and
    not archived, since the cursor passed it.  A book edited since is
    announced New, never Changed: the seed cannot tell whether the reader
    still holds the old version (the download may have been a browser's),
    and a reader without the book drops Changed (#1735).  The reader's
    reading position on the book does not change that.  A book restored
    from the archive since is sent New as well; one still archived is sent
    as a removal.  Every other held book stays silent.  Fails if the seed
    ignores the book's own clock, the archive clock or the archive flag, or
    if the one-time audit's reading-position sentinel reaches a seeded Kobo.
    """
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    books = _seed_legacy_install(sync_harness, count=12, downloaded=True)
    token = _settled_legacy_token(books)
    target = books[4]
    after_cursor = max(book.last_modified for book in books) + timedelta(hours=1)
    if change == "edited":
        target.title = f"{target.title} (revised)"
        target.last_modified = after_cursor
        sync_harness.session.add(ub.DeviceReadingPosition(
            device_id=sync_harness.device.id,
            book_id=target.id,
            client_modified_at=after_cursor - timedelta(days=3),
            progress_percent=42,
        ))
    else:
        sync_harness.session.add(ub.ArchivedBook(
            user_id=sync_harness.user.id,
            book_id=target.id,
            is_archived=change == "archived",
            last_modified=(
                after_cursor if change == "unarchived"
                else min(book.last_modified for book in books)
            ),
        ))
    sync_harness.session.commit()

    token, first = _sync_session(sync_harness, token, sync_harness.device, "a" * 64)
    _token, tokenless = _sync_session(sync_harness, None, sync_harness.device, "a" * 64)

    if change == "archived":
        expected = {"new": [], "changed": [], "removed": [str(target.uuid)]}
    else:
        expected = {"new": [str(target.uuid)], "changed": [], "removed": []}
    assert [first, tokenless] == [expected, _QUIET], (
        f"{change}: first {_brief(first)}, tokenless {_brief(tokenless)}, "
        f"target Changed: {str(target.uuid) in first['changed']}"
    )


@pytest.mark.parametrize("second_kobo", ("active", "retired"))
def test_pre_ledger_shelf_household_kobo_is_not_credited_with_a_later_shelving(
    sync_harness, monkeypatch, second_kobo,
):
    """A household Kobo is seeded with a shelf book only once it passed its shelving.

    In "only sync selected shelves" mode a book reaches a Kobo by joining a
    Kobo-synced shelf.  A household's flat history is the union of both
    readers', so a long-held library book shelved after the first reader's
    last sync, which only the second reader then fetched, is in that history
    with a download to vouch for it.  The first reader must still be sent it
    New, while the rest of the shelf stays silent on both.  A retired reader
    counts: its deliveries stay in the history.  Fails if the seed credits a
    household Kobo with a book whose shelving its own cursor has not passed.
    """
    from cps import db, kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    sync_harness.user.kobo_only_shelves_sync = True
    books = _seed_legacy_install(sync_harness, count=12, downloaded=True)
    first_cursor = max(book.last_modified for book in books)
    shelved_at = first_cursor + timedelta(hours=1)
    old = min(book.last_modified for book in books) - timedelta(days=10)
    later = db.Books(
        "Shelved Later", "Shelved Later", "Author", old,
        db.Books.DEFAULT_PUBDATE, "1.0", old, "shelved-later", 0, [], [],
    )
    sync_harness.session.add(later)
    sync_harness.session.flush()
    later.uuid = "00000000-0000-0000-0010-000000000001"
    sync_harness.session.add_all([
        db.Data(later.id, "EPUB", 10_000_001, "shelved-later"),
        ub.KoboSyncedBooks(
            user_id=sync_harness.user.id, book_id=later.id,
            book_uuid=str(later.uuid),
        ),
        ub.Downloads(user_id=sync_harness.user.id, book_id=later.id),
    ])
    shelf, _link = _add_kobo_shelf(
        sync_harness, date_added=old - timedelta(days=1),
    )
    sync_harness.session.add_all([
        ub.BookShelf(
            book_id=book.id, shelf=shelf.id, ub_shelf=shelf, order=number,
            date_added=shelved_at if book is later else old - timedelta(days=1),
        )
        for number, book in enumerate([*books[1:], later], start=2)
    ])
    sync_harness.session.commit()
    second = _pair_kobo(
        sync_harness, "Second Household Kobo", active=second_kobo == "active",
    )
    readers = [(sync_harness.device, "a" * 64, _settled_legacy_token(books))]
    if second_kobo == "active":
        readers.append((second, "b" * 64, kobo.SyncToken.SyncToken(
            books_last_modified=shelved_at,
            books_last_created=max(book.timestamp for book in books),
        ).build_sync_token()))

    announced = {}
    for device, raw_device_id, token in readers:
        _token, announced[device.id] = _sync_session(
            sync_harness, token, device, raw_device_id,
        )

    expected = {sync_harness.device.id: {
        "new": [str(later.uuid)], "changed": [], "removed": [],
    }}
    if second_kobo == "active":
        expected[second.id] = _QUIET
    assert announced == expected, {
        device_id: _brief(session) for device_id, session in announced.items()
    }


def test_pre_ledger_single_kobo_keeps_its_magic_shelf_books(
    sync_harness, monkeypatch,
):
    """A single Kobo's flat history is its own, magic-shelf books included.

    With one Kobo on the account -- a web reader is not a Kobo -- the flat
    history lists exactly what that Kobo was offered, so in "only sync
    selected shelves" mode a held book that only a magic shelf selects is
    seeded like the rest.  It stays silent at the first sync after the
    upgrade and when the shelf's cache rebuild selects it again.  Fails if
    the household shelf-date rule reaches a single Kobo, or if another kind
    of device counts as a second Kobo.
    """
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    sync_harness.user.kobo_only_shelves_sync = True
    books = _seed_legacy_install(sync_harness, count=8, downloaded=True)
    magic_only = books[-1]
    shelf, _link = _add_kobo_shelf(
        sync_harness, date_added=books[0].last_modified - timedelta(days=1),
    )
    sync_harness.session.add_all([
        ub.BookShelf(
            book_id=book.id, shelf=shelf.id, ub_shelf=shelf, order=number,
            date_added=books[0].last_modified - timedelta(days=1),
        )
        for number, book in enumerate(books[1:-1], start=2)
    ])
    sync_harness.session.add(ub.Device(
        user_id=sync_harness.user.id,
        kind="webreader",
        display_name="Web reader",
        model="CWNG web reader",
        active=True,
        created_by="auto",
    ))
    sync_harness.session.commit()
    cursor = max(book.last_modified for book in books)
    _rebuild_magic_shelf_cache(
        monkeypatch, [magic_only], cursor - timedelta(hours=1),
    )

    token, first = _sync_session(
        sync_harness, _settled_legacy_token(books), sync_harness.device, "a" * 64,
    )
    _rebuild_magic_shelf_cache(
        monkeypatch, [magic_only], cursor + timedelta(days=1),
    )
    _token, rebuilt = _sync_session(sync_harness, token, sync_harness.device, "a" * 64)

    assert [first, rebuilt] == [_QUIET, _QUIET], (
        f"first {_brief(first)}, after the rebuild {_brief(rebuilt)}"
    )


@pytest.mark.parametrize("action", ("resend", "full_sync"))
def test_reset_before_a_pre_ledger_kobos_first_sync_still_delivers_new(
    sync_harness, monkeypatch, action,
):
    """A resend or Full Sync made before the reader's first sync goes out New.

    The admin resends a book, or runs Full Sync, after the upgrade but
    before the reader syncs again.  The reader once read the book, so it
    reported a reading position for it.  The resent book must be announced
    New -- a reader that lost it drops Changed (#1735) -- and nothing else
    with it; Full Sync announces the library New.  Fails if the seed credits
    the resent book, or if the one-time audit's reading-position sentinel
    reaches a Kobo that was just seeded, which turns the resent book Changed.
    """
    from cps import admin, kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    monkeypatch.setattr(admin, "calibre_db", sync_harness.calibre_db)
    monkeypatch.setattr(admin, "_", lambda value: value)
    books = _seed_legacy_install(sync_harness, count=10, downloaded=True)
    token = _settled_legacy_token(books)
    target = books[3]
    sync_harness.session.add(ub.DeviceReadingPosition(
        device_id=sync_harness.device.id,
        book_id=target.id,
        client_modified_at=target.last_modified + timedelta(days=1),
        progress_percent=42,
    ))
    sync_harness.session.commit()
    if action == "resend":
        with sync_harness.app.test_request_context(
                f"/ajax/kobo_resend/{sync_harness.user.id}/{target.id}",
                method="POST"):
            assert admin.do_kobo_resend(
                sync_harness.user.id, target.id,
            ).status_code == 200
        expected = [str(target.uuid)]
    else:
        with sync_harness.app.test_request_context(
                f"/ajax/fullsync/{sync_harness.user.id}", method="POST"):
            assert admin.do_full_kobo_sync(
                sync_harness.user.id,
            ).status_code == 200
        expected = sorted(str(book.uuid) for book in books)

    token, first = _sync_session(sync_harness, token, sync_harness.device, "a" * 64)
    _token, following = _sync_session(sync_harness, token, sync_harness.device, "a" * 64)

    assert (sorted(first["new"]), first["changed"]) == (expected, []), (
        f"{action}: first {_brief(first)}, "
        f"resent/read book Changed: {str(target.uuid) in first['changed']}"
    )
    assert following == _QUIET, _brief(following)


@pytest.mark.parametrize("origin", ("v4_1_43", "pre_ledger"))
@pytest.mark.parametrize("record", (
    "download", "kobo_statistics", "device_position", "none",
))
def test_upgrade_counts_only_the_accounts_own_records_of_delivery(
    sync_harness, monkeypatch, origin, record,
):
    """Each record of delivery vouches for a book; no other record does.

    Three records show that the account took delivery of a book: a
    ``Downloads`` row, reading statistics one of its Kobos reported, and a
    reading position this Kobo reported.  Any one of them keeps the book
    silent across the upgrade, for a v4.1.43 row the audit judges and for
    the pre-v4.1.43 seed alike.  Without one the book goes out New once,
    whatever another account's download or statistics, statistics with no
    figures, another device's position, or a position the server wrote say.
    Fails if a record is ignored, or read across accounts or devices.
    """
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    if origin == "v4_1_43":
        books, token = _v4_1_43_install(sync_harness, count=5)
    else:
        books = _seed_legacy_install(sync_harness, count=5, downloaded=True)
        token = _settled_legacy_token(books)
    target = books[2]
    sync_harness.session.query(ub.Downloads).filter_by(
        user_id=sync_harness.user.id, book_id=target.id,
    ).delete()
    other_user = sync_harness.user.id + 1
    web_reader = ub.Device(
        user_id=sync_harness.user.id,
        kind="webreader",
        display_name="Web reader",
        model="CWNG web reader",
        active=True,
        created_by="auto",
    )
    reported = record == "kobo_statistics"
    states = []
    for user_id, figures in (
            (other_user, (12, 30)),
            (sync_harness.user.id, (12, 30) if reported else (None, None))):
        read = ub.ReadBook(
            user_id=user_id, book_id=target.id,
            read_status=ub.ReadBook.STATUS_IN_PROGRESS,
        )
        state = ub.KoboReadingState(user_id=user_id, book_id=target.id)
        state.current_bookmark = ub.KoboBookmark(progress_percent=10.0)
        state.statistics = ub.KoboStatistics(
            spent_reading_minutes=figures[0],
            remaining_time_minutes=figures[1],
        )
        read.kobo_reading_state = state
        states.append(read)
    sync_harness.session.add_all([
        web_reader, *states,
        ub.Downloads(user_id=other_user, book_id=target.id),
    ])
    sync_harness.session.flush()
    for device_id, reported_at in (
            (web_reader.id, target.last_modified),
            (sync_harness.device.id,
             target.last_modified if record == "device_position" else None)):
        # A delivery may already have journaled a server-side position.
        position = sync_harness.session.query(
            ub.DeviceReadingPosition,
        ).filter_by(device_id=device_id, book_id=target.id).one_or_none()
        if position is None:
            position = ub.DeviceReadingPosition(
                device_id=device_id, book_id=target.id, progress_percent=10,
            )
            sync_harness.session.add(position)
        position.client_modified_at = reported_at
    if record == "download":
        sync_harness.session.add(ub.Downloads(
            user_id=sync_harness.user.id, book_id=target.id,
        ))
    sync_harness.session.commit()

    token, first = _sync_session(sync_harness, token, sync_harness.device, "a" * 64)
    _token, tokenless = _sync_session(sync_harness, None, sync_harness.device, "a" * 64)

    expected_first = (
        {"new": [str(target.uuid)], "changed": [], "removed": []}
        if record == "none" else _QUIET
    )
    assert [first, tokenless] == [expected_first, _QUIET], (
        f"{origin}/{record}: first {_brief(first)}, "
        f"tokenless {_brief(tokenless)}"
    )
