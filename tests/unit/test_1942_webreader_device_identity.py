# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later

"""Browser reading is one source per account, regardless of client identity."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import flask
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


INSTALLATION_A = "11111111-1111-4111-8111-111111111111"
INSTALLATION_B = "22222222-2222-4222-8222-222222222222"


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


def test_all_browsers_share_one_account_source(registry):
    """Different computers, old tabs, denied storage, and rotated keys agree."""
    from cps import ub
    from cps.services.device_registry import ensure_webreader_device_best_effort

    ids = [ensure_webreader_device_best_effort(
        user_id=7, installation_id=header, secret_key=key,
    ) for header, key in (
        (INSTALLATION_A, "first"), (INSTALLATION_B, "second"),
        (None, None), ("", ""), ("invalid\\nheader", None),
    )]
    assert ids[0] is not None
    assert set(ids) == {ids[0]}
    assert registry.query(ub.Device).count() == 1
    assert registry.query(ub.DeviceIdentity).count() == 0
    device = registry.query(ub.Device).one()
    assert device.display_name == "Browser"
    assert device.user_id == 7
    other = ensure_webreader_device_best_effort(user_id=8, installation_id=INSTALLATION_A)
    assert other not in ids and other is not None
    assert registry.query(ub.Device).filter_by(id=other).one().user_id == 8


def test_unidentified_browser_heartbeat_advances_without_regressing_or_writing_every_save(registry):
    """Headerless clients remain one source whose last-seen reflects later
    activity, with the same coarse heartbeat used for identified browsers.
    """
    from cps import ub
    from cps.services.device_registry import _ensure_legacy_webreader_device, LAST_SEEN_WRITE_INTERVAL
    first = datetime(2026, 9, 1, tzinfo=timezone.utc)
    device = _ensure_legacy_webreader_device(registry, ub, user_id=7, seen_at=first)
    registry.commit()
    for seen in (first - timedelta(days=1), first + timedelta(seconds=1)):
        same = _ensure_legacy_webreader_device(registry, ub, user_id=7, seen_at=seen)
        assert same.id == device.id
        assert same.last_seen_at.replace(tzinfo=timezone.utc) == first
    later = first + LAST_SEEN_WRITE_INTERVAL
    same = _ensure_legacy_webreader_device(registry, ub, user_id=7, seen_at=later)
    registry.commit()
    registry.expire_all()
    assert same.last_seen_at.replace(tzinfo=timezone.utc) == later
    assert registry.query(ub.Device).count() == 1


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
