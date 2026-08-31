# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later

"""#1942 M1: a browser installation is a private, first-class Device."""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from pathlib import Path

import flask
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


INSTALLATION_A = "11111111-1111-4111-8111-111111111111"
INSTALLATION_B = "22222222-2222-4222-8222-222222222222"
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def registry(tmp_path):
    from cps import ub

    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}", future=True)
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    original = ub.session
    ub.session = session
    app = flask.Flask(__name__)
    app.secret_key = "issue-1942-test-secret"
    try:
        with app.app_context():
            yield session
    finally:
        session.close()
        ub.session = original
        engine.dispose()


def test_webreader_hmac_is_exact_and_deterministic():
    from cps.services.device_registry import _webreader_fingerprint

    secret = b"deterministic-secret"
    expected = hmac.new(
        secret,
        b"cwng-device:webreader:v1\0" + b"7\0" + INSTALLATION_A.encode(),
        hashlib.sha256,
    ).hexdigest()
    assert _webreader_fingerprint(7, INSTALLATION_A, secret) == expected
    assert _webreader_fingerprint(7, INSTALLATION_A, secret) == expected
    assert _webreader_fingerprint(8, INSTALLATION_A, secret) != expected
    assert _webreader_fingerprint(7, INSTALLATION_B, secret) != expected
    assert _webreader_fingerprint(7, INSTALLATION_A, b"another-secret") != expected


def test_installation_creates_device_identity_without_storing_raw_id(registry):
    from cps import ub
    from cps.services.device_registry import (
        WEBREADER_SCHEME,
        ensure_webreader_device_best_effort,
    )

    device_id = ensure_webreader_device_best_effort(
        user_id=7,
        installation_id=INSTALLATION_A,
    )
    registry.expire_all()
    device = registry.query(ub.Device).filter_by(id=device_id).one()
    identity = registry.query(ub.DeviceIdentity).filter_by(device_id=device_id).one()

    assert device.kind == "webreader"
    assert device.created_by == "auto"
    assert device.display_name == "Web reader"
    assert identity.scheme == WEBREADER_SCHEME
    assert identity.scheme == "webreader-cookie-hmac-sha256-v2"
    assert identity.key_version == 1
    assert len(identity.fingerprint) == 64
    stored_values = (
        device.public_id,
        device.display_name,
        device.model,
        device.platform,
        identity.scheme,
        identity.fingerprint,
    )
    assert all(INSTALLATION_A not in (value or "") for value in stored_values)


def test_same_installation_is_stable_and_two_browsers_are_separate(registry):
    from cps import ub
    from cps.services.device_registry import ensure_webreader_device_best_effort

    first = ensure_webreader_device_best_effort(user_id=7, installation_id=INSTALLATION_A)
    first_again = ensure_webreader_device_best_effort(user_id=7, installation_id=INSTALLATION_A)
    second = ensure_webreader_device_best_effort(user_id=7, installation_id=INSTALLATION_B)

    assert first_again == first
    assert second != first
    devices = registry.query(ub.Device).filter_by(user_id=7, kind="webreader").all()
    assert {device.id for device in devices} == {first, second}
    assert {device.display_name for device in devices} == {"Web reader", "Web reader 2"}
    assert registry.query(ub.DeviceIdentity).count() == 2


def test_same_browser_profile_is_domain_separated_between_users(registry):
    from cps import ub
    from cps.services.device_registry import ensure_webreader_device_best_effort

    user_7 = ensure_webreader_device_best_effort(
        user_id=7, installation_id=INSTALLATION_A,
    )
    user_8 = ensure_webreader_device_best_effort(
        user_id=8, installation_id=INSTALLATION_A,
    )

    assert user_7 != user_8
    assert registry.query(ub.Device).filter_by(id=user_7, user_id=7).one()
    assert registry.query(ub.Device).filter_by(id=user_8, user_id=8).one()
    identities = registry.query(ub.DeviceIdentity).order_by(ub.DeviceIdentity.id).all()
    assert len(identities) == 2
    assert identities[0].fingerprint != identities[1].fingerprint


def test_missing_installation_id_keeps_a_distinct_legacy_singleton(registry):
    from cps import ub
    from cps.services.device_registry import ensure_webreader_device_best_effort

    browser = ensure_webreader_device_best_effort(user_id=7, installation_id=INSTALLATION_A)
    legacy = ensure_webreader_device_best_effort(user_id=7)
    legacy_again = ensure_webreader_device_best_effort(user_id=7, installation_id=None)

    assert legacy_again == legacy
    assert legacy != browser
    assert registry.query(ub.DeviceIdentity).filter_by(device_id=legacy).count() == 0
    assert registry.query(ub.Device).filter_by(user_id=7, kind="webreader").count() == 2


def test_device_cap_uses_one_legacy_fallback_and_logs_once(registry, caplog):
    from cps import ub
    from cps.services import device_registry

    device_registry._webreader_cap_logged_users.clear()
    caplog.set_level("INFO", logger=device_registry.__name__)
    browser_ids = []
    for index in range(device_registry.MAX_WEBREADER_DEVICES_PER_USER):
        installation_id = f"{index:08x}-0000-4000-8000-000000000000"
        browser_ids.append(device_registry.ensure_webreader_device_best_effort(
            user_id=7, installation_id=installation_id,
        ))

    over_cap = device_registry.ensure_webreader_device_best_effort(
        user_id=7,
        installation_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )
    over_cap_again = device_registry.ensure_webreader_device_best_effort(
        user_id=7,
        installation_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    )

    assert len(set(browser_ids)) == device_registry.MAX_WEBREADER_DEVICES_PER_USER
    assert over_cap_again == over_cap
    assert over_cap not in browser_ids
    assert registry.query(ub.DeviceIdentity).count() == device_registry.MAX_WEBREADER_DEVICES_PER_USER
    assert registry.query(ub.Device).filter_by(user_id=7, kind="webreader").count() == (
        device_registry.MAX_WEBREADER_DEVICES_PER_USER + 1
    )
    messages = [record.getMessage() for record in caplog.records]
    assert messages.count(
        "Web-reader device limit reached; using the legacy device bucket"
    ) == 1
    assert all("aaaaaaaa" not in message and "bbbbbbbb" not in message for message in messages)

    # Retiring a browser does not free a persistent-row slot. Otherwise a
    # delete/new-id loop would evade an active-only cap and restore the flood.
    registry.query(ub.Device).filter_by(id=browser_ids[0]).one().active = False
    registry.commit()
    after_retirement = device_registry.ensure_webreader_device_best_effort(
        user_id=7,
        installation_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    )
    assert after_retirement == over_cap
    assert registry.query(ub.DeviceIdentity).count() == device_registry.MAX_WEBREADER_DEVICES_PER_USER


def test_retired_browser_falls_back_until_explicitly_restored(registry):
    from cps import ub
    from cps.annotations import restore_annotation_device
    from cps.services.device_registry import ensure_webreader_device_best_effort

    registry.add(ub.User(
        id=7, name="Reader", email="reader@example.invalid",
        default_language="all",
    ))
    registry.commit()
    browser_id = ensure_webreader_device_best_effort(
        user_id=7, installation_id=INSTALLATION_A,
    )
    browser = registry.query(ub.Device).filter_by(id=browser_id).one()
    browser.active = False
    registry.commit()

    fallback_id = ensure_webreader_device_best_effort(
        user_id=7, installation_id=INSTALLATION_A,
    )
    registry.expire_all()
    assert fallback_id != browser_id
    assert registry.query(ub.Device).filter_by(id=browser_id).one().active is False
    assert registry.query(ub.DeviceIdentity).filter_by(device_id=fallback_id).count() == 0

    restored, restored_count, conflicts = restore_annotation_device(
        browser.public_id,
        user_id=7,
        session=registry,
        commit=registry.commit,
    )
    assert restored.id == browser_id
    assert (restored_count, conflicts) == (0, 0)
    assert ensure_webreader_device_best_effort(
        user_id=7, installation_id=INSTALLATION_A,
    ) == browser_id


def test_retired_legacy_singleton_is_reactivated_before_reuse(registry):
    from cps import ub
    from cps.services.device_registry import ensure_webreader_device_best_effort

    legacy_id = ensure_webreader_device_best_effort(user_id=7)
    legacy = registry.query(ub.Device).filter_by(id=legacy_id).one()
    prior_seen = datetime(2026, 8, 1, tzinfo=timezone.utc)
    legacy.active = False
    legacy.last_seen_at = prior_seen
    registry.commit()

    assert ensure_webreader_device_best_effort(user_id=7) == legacy_id
    registry.expire_all()
    reactivated = registry.query(ub.Device).filter_by(id=legacy_id).one()
    assert reactivated.active is True
    assert reactivated.last_seen_at.replace(tzinfo=timezone.utc) > prior_seen
    assert registry.query(ub.Device).filter_by(user_id=7, kind="webreader").count() == 1


def test_repeated_position_observations_throttle_last_seen_writes(registry):
    from cps.services.device_registry import upsert_webreader_device

    first_seen = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    device = upsert_webreader_device(
        registry,
        user_id=7,
        installation_id=INSTALLATION_A,
        secret_key="issue-1942-test-secret",
        seen_at=first_seen,
    )
    registry.commit()
    upsert_webreader_device(
        registry,
        user_id=7,
        installation_id=INSTALLATION_A,
        secret_key="issue-1942-test-secret",
        seen_at=first_seen + timedelta(seconds=1),
    )
    assert device.last_seen_at.replace(tzinfo=timezone.utc) == first_seen
    assert not registry.dirty

    upsert_webreader_device(
        registry,
        user_id=7,
        installation_id=INSTALLATION_A,
        secret_key="issue-1942-test-secret",
        seen_at=first_seen + timedelta(minutes=5),
    )
    assert device.last_seen_at.replace(tzinfo=timezone.utc) == first_seen + timedelta(minutes=5)


def test_origin_index_migration_is_additive_and_idempotent():
    from cps import ub

    engine = create_engine("sqlite:///:memory:", future=True)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE annotation ("
            "id INTEGER PRIMARY KEY, user_id INTEGER, book_id INTEGER, "
            "origin_device_id INTEGER)"
        ))
    ub.migrate_webreader_device_identity_slice(engine, None)
    ub.migrate_webreader_device_identity_slice(engine, None)
    with engine.connect() as conn:
        indexes = {row[1] for row in conn.execute(text("PRAGMA index_list(annotation)"))}
    assert "ix_annotation_user_book_origin" in indexes


def test_reader_storage_access_is_guarded_for_identity_theme_and_font():
    reader = (REPO / "frontend/src/pages/Reader.tsx").read_text(encoding="utf-8")
    identity = (REPO / "frontend/src/lib/deviceIdentity.ts").read_text(encoding="utf-8")
    helper = (REPO / "frontend/src/lib/safeStorage.ts").read_text(encoding="utf-8")

    assert "localStorage." not in reader
    assert "localStorage." not in identity
    assert "safeLocalStorageGet" in reader and "safeLocalStorageSet" in reader
    assert "safeLocalStorageGet" in identity and "safeLocalStorageSet" in identity
    assert "try {" in helper and "catch {" in helper
