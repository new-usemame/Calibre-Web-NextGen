# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression coverage for progress-only KOReader device registration (#2075)."""

import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine
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


@pytest.fixture
def protocol(monkeypatch):
    module = _kosync_module()
    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    AppBase.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(module, "datetime", _FrozenDateTime)
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
        )
    finally:
        session.close()
        engine.dispose()


def _put_progress(protocol, document, *, device_id=...):
    payload = {
        "document": document,
        "progress": "cre://issue-2075-position",
        "percentage": 0.42,
        "device": "PocketBook Era",
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
