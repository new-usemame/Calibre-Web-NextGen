# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression coverage for progress-only KOReader device registration (#2075)."""

import logging
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from cps import ub
from cps.progress_syncing.models import AppBase, KOSyncProgress
from cps.services import device_registry


USER_ID = 2075
FROZEN_NOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)


def _kosync_module():
    import cps.progress_syncing.protocols.kosync  # noqa: F401
    return sys.modules["cps.progress_syncing.protocols.kosync"]


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return FROZEN_NOW.replace(tzinfo=None)
        return FROZEN_NOW.astimezone(tz)


class _RegistryDateTime(datetime):
    current = FROZEN_NOW

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls.current.replace(tzinfo=None)
        return cls.current.astimezone(tz)


@pytest.fixture
def protocol(monkeypatch):
    module = _kosync_module()
    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    AppBase.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    commit_events = []

    def record_commit(_connection):
        commit_events.append(True)

    event.listen(engine, "commit", record_commit)
    _RegistryDateTime.current = FROZEN_NOW

    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(module, "datetime", _FrozenDateTime)
    monkeypatch.setattr(device_registry, "datetime", _RegistryDateTime)
    monkeypatch.setattr(module, "is_koreader_sync_enabled", lambda: True)
    monkeypatch.setattr(
        module,
        "authenticate_user",
        lambda: SimpleNamespace(id=USER_ID, name="Progress-only reader"),
    )
    monkeypatch.setattr(
        module,
        "enrich_response_with_book_info",
        lambda response, _document: (response, None, None, None, None),
    )

    app = Flask(__name__)
    app.secret_key = "issue-2075-regression-secret"
    app.register_blueprint(module.kosync)
    client = app.test_client()

    try:
        yield SimpleNamespace(
            client=client,
            session=session,
            headers={"Accept": "application/vnd.koreader.v1+json"},
            clock=_RegistryDateTime,
            commit_events=commit_events,
        )
    finally:
        event.remove(engine, "commit", record_commit)
        session.close()
        engine.dispose()


def _put_progress(protocol, document, *, device_id=..., device="PocketBook Era"):
    payload = {
        "document": document,
        "progress": "cre://issue-2075-position",
        "percentage": 0.42,
        "device": device,
    }
    if device_id is not ...:
        payload["device_id"] = device_id
    return protocol.client.put(
        "/kosync/syncs/progress",
        headers=protocol.headers,
        json=payload,
    )


def _expected_body(document):
    return {"document": document, "timestamp": int(FROZEN_NOW.timestamp())}


def _utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@pytest.mark.unit
def test_progress_put_registers_payload_device_for_user(protocol):
    response = _put_progress(
        protocol,
        "issue-2075-with-device-id",
        device_id="stable-progress-client-id",
    )

    assert response.status_code == 200
    assert response.get_json() == _expected_body("issue-2075-with-device-id")
    progress = protocol.session.query(KOSyncProgress).one()
    assert progress.device == "PocketBook Era"
    assert progress.device_id == "stable-progress-client-id"

    device = protocol.session.query(ub.Device).filter_by(user_id=USER_ID).one()
    assert device.kind == "koreader"
    assert device.display_name == "PocketBook Era"
    assert device.active is True
    identity = protocol.session.query(ub.DeviceIdentity).filter_by(
        device_id=device.id,
        scheme=device_registry.KOREADER_SCHEME,
    ).one()
    assert identity.fingerprint != "stable-progress-client-id"


@pytest.mark.unit
def test_progress_put_without_device_id_persists_without_registration(protocol):
    response = _put_progress(protocol, "issue-2075-without-device-id")

    assert response.status_code == 200
    assert response.get_json() == _expected_body("issue-2075-without-device-id")
    progress = protocol.session.query(KOSyncProgress).one()
    assert progress.device == "PocketBook Era"
    assert progress.device_id is None
    assert protocol.session.query(ub.Device).filter_by(user_id=USER_ID).count() == 0


@pytest.mark.unit
def test_registration_raise_does_not_change_progress_response_or_persistence(
    protocol, monkeypatch,
):
    def registration_failure(**_kwargs):
        raise RuntimeError("simulated isolated registration failure")

    monkeypatch.setattr(
        device_registry,
        "register_koreader_device_best_effort",
        registration_failure,
    )

    response = _put_progress(
        protocol,
        "issue-2075-registration-failure",
        device_id="stable-but-registration-fails",
    )

    assert response.status_code == 200
    assert response.get_json() == _expected_body("issue-2075-registration-failure")
    progress = protocol.session.query(KOSyncProgress).one()
    assert progress.document == "issue-2075-registration-failure"
    assert progress.device_id == "stable-but-registration-fails"
    assert protocol.session.query(ub.Device).filter_by(user_id=USER_ID).count() == 0


@pytest.mark.unit
def test_repeated_progress_inside_interval_does_not_commit_registry_heartbeat(protocol):
    first = _put_progress(
        protocol,
        "issue-2075-heartbeat-first",
        device_id="coalesced-progress-client",
    )
    device = protocol.session.query(ub.Device).filter_by(user_id=USER_ID).one()
    identity = protocol.session.query(ub.DeviceIdentity).filter_by(
        device_id=device.id,
        scheme=device_registry.KOREADER_SCHEME,
    ).one()
    first_device_seen = _utc(device.last_seen_at)
    first_identity_seen = _utc(identity.last_seen_at)

    protocol.commit_events.clear()
    protocol.clock.current = FROZEN_NOW + timedelta(minutes=1)
    repeated = _put_progress(
        protocol,
        "issue-2075-heartbeat-repeated",
        device_id="coalesced-progress-client",
    )

    assert first.status_code == repeated.status_code == 200
    # The progress row commits once. A second commit means the isolated
    # registry session still opened a write transaction for this heartbeat.
    assert len(protocol.commit_events) == 1
    protocol.session.expire_all()
    device = protocol.session.get(ub.Device, device.id)
    identity = protocol.session.get(ub.DeviceIdentity, identity.id)
    assert _utc(device.last_seen_at) == first_device_seen == FROZEN_NOW
    assert _utc(identity.last_seen_at) == first_identity_seen == FROZEN_NOW


@pytest.mark.unit
def test_progress_heartbeat_advances_after_interval_but_never_moves_backwards(protocol):
    _put_progress(
        protocol,
        "issue-2075-heartbeat-baseline",
        device_id="ordered-progress-client",
    )
    device = protocol.session.query(ub.Device).filter_by(user_id=USER_ID).one()
    identity = protocol.session.query(ub.DeviceIdentity).filter_by(
        device_id=device.id,
        scheme=device_registry.KOREADER_SCHEME,
    ).one()

    advanced_at = FROZEN_NOW + device_registry.LAST_SEEN_WRITE_INTERVAL
    protocol.clock.current = advanced_at
    protocol.commit_events.clear()
    advanced = _put_progress(
        protocol,
        "issue-2075-heartbeat-advanced",
        device_id="ordered-progress-client",
    )

    assert advanced.status_code == 200
    assert len(protocol.commit_events) == 2
    protocol.session.expire_all()
    assert _utc(protocol.session.get(ub.Device, device.id).last_seen_at) == advanced_at
    assert _utc(protocol.session.get(ub.DeviceIdentity, identity.id).last_seen_at) == advanced_at

    protocol.clock.current = FROZEN_NOW + timedelta(minutes=1)
    protocol.commit_events.clear()
    older = _put_progress(
        protocol,
        "issue-2075-heartbeat-older",
        device_id="ordered-progress-client",
    )

    assert older.status_code == 200
    assert len(protocol.commit_events) == 1
    protocol.session.expire_all()
    assert _utc(protocol.session.get(ub.Device, device.id).last_seen_at) == advanced_at
    assert _utc(protocol.session.get(ub.DeviceIdentity, identity.id).last_seen_at) == advanced_at


@pytest.mark.unit
def test_changed_client_device_string_updates_model_inside_interval(protocol):
    _put_progress(
        protocol,
        "issue-2075-metadata-baseline",
        device_id="renamed-progress-client",
    )
    device = protocol.session.query(ub.Device).filter_by(user_id=USER_ID).one()

    changed_at = FROZEN_NOW + timedelta(minutes=1)
    protocol.clock.current = changed_at
    protocol.commit_events.clear()
    changed = _put_progress(
        protocol,
        "issue-2075-metadata-changed",
        device_id="renamed-progress-client",
        device="PocketBook Color",
    )

    assert changed.status_code == 200
    assert len(protocol.commit_events) == 2
    protocol.session.expire_all()
    device = protocol.session.get(ub.Device, device.id)
    assert device.display_name == "PocketBook Era"
    assert device.model == "PocketBook Color"
    assert _utc(device.last_seen_at) == changed_at
    assert _utc(device.last_metadata_at) == changed_at


@pytest.mark.unit
def test_user_renamed_device_keeps_label_across_later_progress_push(protocol):
    from cps.annotations import rename_annotation_device

    _put_progress(
        protocol,
        "issue-2075-user-label-baseline",
        device_id="user-renamed-progress-client",
    )
    device = protocol.session.query(ub.Device).filter_by(user_id=USER_ID).one()
    renamed = rename_annotation_device(
        device.public_id,
        user_id=USER_ID,
        label="My reading buddy",
        session=protocol.session,
        commit=protocol.session.commit,
    )
    assert renamed.display_name == "My reading buddy"

    protocol.commit_events.clear()
    protocol.clock.current = FROZEN_NOW + timedelta(minutes=1)
    response = _put_progress(
        protocol,
        "issue-2075-user-label-repeated",
        device_id="user-renamed-progress-client",
        device="PocketBook Era",
    )

    assert response.status_code == 200
    protocol.session.expire_all()
    assert protocol.session.get(ub.Device, device.id).display_name == "My reading buddy"
    assert len(protocol.commit_events) == 1


@pytest.mark.unit
def test_deduplicated_label_device_still_coalesces_inside_interval(protocol):
    _put_progress(
        protocol,
        "issue-2075-dedup-first",
        device_id="dedup-progress-client-first",
    )
    _put_progress(
        protocol,
        "issue-2075-dedup-second",
        device_id="dedup-progress-client-second",
    )
    second = protocol.session.query(ub.Device).filter_by(
        user_id=USER_ID,
        display_name="PocketBook Era 2",
    ).one()

    protocol.commit_events.clear()
    protocol.clock.current = FROZEN_NOW + timedelta(minutes=1)
    repeated = _put_progress(
        protocol,
        "issue-2075-dedup-second-repeated",
        device_id="dedup-progress-client-second",
    )

    assert repeated.status_code == 200
    assert len(protocol.commit_events) == 1
    protocol.session.expire_all()
    assert protocol.session.get(ub.Device, second.id).display_name == "PocketBook Era 2"


@pytest.mark.unit
def test_progress_registration_cap_retains_progress_and_logs_once(
    protocol, monkeypatch, caplog,
):
    cap = 2
    raw_ids = [f"rotated-private-device-{index}" for index in range(cap + 2)]
    monkeypatch.setattr(
        device_registry, "MAX_KOREADER_DEVICES_PER_USER", cap, raising=False,
    )
    monkeypatch.setattr(
        device_registry, "_koreader_cap_logged_users", set(), raising=False,
    )

    responses = []
    with caplog.at_level(logging.WARNING, logger=device_registry.__name__):
        for index, raw_id in enumerate(raw_ids):
            responses.append(_put_progress(
                protocol,
                f"issue-2075-cap-{index}",
                device_id=raw_id,
                device=f"Capped reader {index}",
            ))
            if index == cap - 1:
                retained = protocol.session.query(ub.Device).filter_by(
                    user_id=USER_ID,
                    display_name="Capped reader 0",
                ).one()
                retained.active = False
                protocol.session.commit()

    assert all(response.status_code == 200 for response in responses)
    assert protocol.session.query(KOSyncProgress).count() == len(raw_ids)
    assert protocol.session.query(ub.Device).filter_by(user_id=USER_ID).count() == cap
    assert protocol.session.query(ub.DeviceIdentity).join(ub.Device).filter(
        ub.Device.user_id == USER_ID,
        ub.DeviceIdentity.scheme == device_registry.KOREADER_SCHEME,
    ).count() == cap
    cap_records = [
        record for record in caplog.records
        if "KOReader device limit reached" in record.getMessage()
    ]
    assert len(cap_records) == 1
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert all(raw_id not in logged for raw_id in raw_ids)


@pytest.mark.unit
def test_known_identity_still_updates_when_user_is_at_cap(protocol, monkeypatch):
    cap = 2
    monkeypatch.setattr(
        device_registry, "MAX_KOREADER_DEVICES_PER_USER", cap, raising=False,
    )
    for index in range(cap):
        response = _put_progress(
            protocol,
            f"issue-2075-known-at-cap-{index}",
            device_id=f"known-at-cap-{index}",
            device=f"Known reader {index}",
        )
        assert response.status_code == 200

    known = protocol.session.query(ub.Device).filter_by(
        user_id=USER_ID,
        display_name="Known reader 0",
    ).one()
    protocol.clock.current = FROZEN_NOW + device_registry.LAST_SEEN_WRITE_INTERVAL
    overflow = _put_progress(
        protocol,
        "issue-2075-known-at-cap-overflow",
        device_id="must-not-register-at-cap",
    )
    repeated = _put_progress(
        protocol,
        "issue-2075-known-at-cap-repeat",
        device_id="known-at-cap-0",
        device="Known reader 0",
    )

    assert overflow.status_code == repeated.status_code == 200
    protocol.session.expire_all()
    assert _utc(protocol.session.get(ub.Device, known.id).last_seen_at) == protocol.clock.current
    assert protocol.session.query(ub.Device).filter_by(user_id=USER_ID).count() == cap
    assert protocol.session.query(KOSyncProgress).count() == cap + 2
