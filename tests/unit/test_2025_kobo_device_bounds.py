# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later

"""Persistent-storage bounds for acknowledged Kobo sync pages (#2025)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from werkzeug.exceptions import Conflict


pytestmark = pytest.mark.unit


@pytest.fixture
def registry_session(monkeypatch):
    from cps import ub

    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(ub, "session", session)
    yield session
    session.close()
    engine.dispose()


def _pending_page(ub, device_id, *, created_at):
    return ub.KoboDevicePendingSyncPage(
        device_id=device_id,
        incoming_token_hash=f"{device_id:064x}",
        outgoing_token=f"outgoing-{device_id}",
        response_body='[{"NewEntitlement":{}}]',
        response_headers_json="{}",
        confirmation_json="{}",
        created_at=created_at,
    )


def test_pending_page_ttl_prune_is_user_scoped_bounded_and_non_promoting(
    registry_session,
):
    from cps import kobo_sync_status, ub

    now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
    expired_at = now - kobo_sync_status.PENDING_SYNC_PAGE_TTL - timedelta(seconds=1)
    target_devices = []
    for index in range(12):
        device = ub.Device(
            user_id=7,
            kind="kobo",
            display_name=f"Target Kobo {index}",
            active=True,
            created_by="auto",
        )
        registry_session.add(device)
        registry_session.flush()
        target_devices.append(device)
        registry_session.add(_pending_page(ub, device.id, created_at=expired_at))

    fresh_device = ub.Device(
        user_id=7,
        kind="kobo",
        display_name="Fresh Kobo",
        active=True,
        created_by="auto",
    )
    other_user_device = ub.Device(
        user_id=8,
        kind="kobo",
        display_name="Other User Kobo",
        active=True,
        created_by="auto",
    )
    registry_session.add_all([fresh_device, other_user_device])
    registry_session.flush()
    registry_session.add_all([
        _pending_page(ub, fresh_device.id, created_at=now),
        _pending_page(ub, other_user_device.id, created_at=expired_at),
        ub.KoboDeviceBookEntitlement(
            device_id=target_devices[0].id,
            book_id=2025,
            fingerprint="f" * 64,
        ),
        ub.DeviceReadingPosition(
            device_id=target_devices[0].id,
            book_id=2025,
            rehydrate_needed=True,
        ),
    ])
    registry_session.commit()

    removed = kobo_sync_status.prune_expired_pending_sync_pages(7, now=now)
    registry_session.commit()

    assert removed == kobo_sync_status.PENDING_SYNC_PAGE_PRUNE_LIMIT == 10
    remaining_ids = {
        row.device_id for row in
        registry_session.query(ub.KoboDevicePendingSyncPage).all()
    }
    assert fresh_device.id in remaining_ids
    assert other_user_device.id in remaining_ids
    assert len(remaining_ids & {device.id for device in target_devices}) == 2
    assert registry_session.query(ub.KoboDeviceBookEntitlement).count() == 1
    assert registry_session.query(ub.DeviceReadingPosition).one().rehydrate_needed is True

    assert kobo_sync_status.prune_expired_pending_sync_pages(7, now=now) == 2
    registry_session.commit()
    remaining_ids = {
        row.device_id for row in
        registry_session.query(ub.KoboDevicePendingSyncPage).all()
    }
    assert remaining_ids == {fresh_device.id, other_user_device.id}


def test_best_effort_registration_surfaces_the_intentional_cap(
    registry_session, monkeypatch,
):
    from cps.services import device_registry

    for index in range(device_registry.MAX_KOBO_DEVICES_PER_USER):
        device_registry.upsert_kobo_device(
            registry_session,
            user_id=7,
            headers={"x-kobo-deviceid": f"{index:064x}"},
            secret_key="test-secret",
        )
    registry_session.commit()
    monkeypatch.setattr(
        device_registry,
        "sessionmaker",
        lambda **_kwargs: lambda: registry_session,
    )

    with pytest.raises(device_registry.KoboDeviceLimitReached):
        device_registry.register_kobo_device_best_effort(
            user_id=7,
            headers={"x-kobo-deviceid": "f" * 64},
            secret_key="test-secret",
            return_internal=True,
        )


def test_kobo_auth_returns_clear_conflict_when_new_device_exceeds_cap(monkeypatch):
    from cps import kobo_auth, ub
    from cps.services import device_registry

    class AuthQuery:
        def join(self, *_args, **_kwargs):
            return self

        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return SimpleNamespace(id=7)

    monkeypatch.setattr(kobo_auth, "get_auth_token", lambda: "valid-token")
    monkeypatch.setattr(kobo_auth.limiter, "check", lambda: None)
    monkeypatch.setattr(kobo_auth, "login_user", lambda _user: None)
    monkeypatch.setattr(ub, "session", SimpleNamespace(query=lambda *_args: AuthQuery()))

    def at_cap(**_kwargs):
        raise device_registry.KoboDeviceLimitReached(
            device_registry.KOBO_DEVICE_LIMIT_MESSAGE,
        )

    monkeypatch.setattr(device_registry, "register_kobo_device_best_effort", at_cap)
    wrapped = kobo_auth.requires_kobo_auth(lambda: ("must-not-run", 200))
    app = Flask(__name__)
    with app.test_request_context(
        "/token/v1/library/sync",
        headers={"x-kobo-deviceid": "f" * 64},
    ), pytest.raises(Conflict) as raised:
        wrapped()

    assert raised.value.code == 409
    assert raised.value.description == device_registry.KOBO_DEVICE_LIMIT_MESSAGE
