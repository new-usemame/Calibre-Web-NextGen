# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Upgrade real browser histories without changing content or shared progress."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def db(tmp_path, monkeypatch):
    from cps import ub
    engine = create_engine(f"sqlite:///{tmp_path / 'upgrade.db'}")
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([ub.User(id=i, name=f"reader-{i}", email=f"{i}@example.invalid") for i in (7, 8, 9)])
    session.commit()
    monkeypatch.setattr(ub, "session", session)
    yield engine, session
    session.close()
    engine.dispose()


def _snapshot(conn, table):
    return [dict(r) for r in conn.execute(text(f"SELECT * FROM {table} ORDER BY id")).mappings()]


def test_populated_upgrade_preserves_content_positions_aliases_and_routing(db, monkeypatch):
    from cps import ub, annotations
    from cps.services.browser_source import migrate_account_browser_source
    from cps.services.device_registry import ensure_webreader_device_best_effort, resolve_owned_device_best_effort
    engine, session = db
    now = datetime(2026, 9, 1)
    browsers = [ub.Device(user_id=7, kind="webreader", display_name=f"Web reader {i}",
                          active=i != 2, created_by="auto") for i in range(3)]
    kobo = ub.Device(user_id=7, kind="kobo", display_name="Clara")
    other = ub.Device(user_id=8, kind="webreader", display_name="Custom source", active=False)
    session.add_all(browsers + [kobo, other]); session.flush()
    canonical, alias, retired = browsers
    rows = [ub.Annotation(
        user_id=7, book_id=10 + i, annotation_id=f"kept-{i}",
        source="webreader", highlighted_text=f"passage {i}", note_text=f"note {i}",
        cfi_range=f"epubcfi(/6/{i + 2}!/4/2:4)", hidden=i == 1,
        origin_device_id=device.id, last_editor_device_id=device.id,
        assigned_device_id=kobo.id if i == 2 else device.id,
        content_revision=4, routing_revision=7, client_modified_at=now,
        server_modified_at=now, last_synced=now,
    ) for i, device in enumerate(browsers)]
    session.add_all(rows); session.flush()
    for i, device in enumerate(browsers):
        session.add(ub.DeviceReadingPosition(
            device_id=device.id, book_id=10, progress_percent=80 - i * 30,
            cfi=f"position-{i}", location_value=f"location-{i}",
            client_modified_at=now + timedelta(hours=i),
            server_modified_at=now + timedelta(hours=i), rehydrate_needed=i == 0,
        ))
        session.add(ub.AnnotationDeviceState(
            device_id=device.id, annotation_id=rows[0].id,
            desired=True, updated_at=now + timedelta(hours=i),
        ))
        session.add(ub.DeviceRetiredAssignment(device_id=device.id, annotation_id=rows[2].id))
    session.add(ub.DeviceReadingPosition(device_id=alias.id, book_id=11, cfi="unique-book"))
    session.add(ub.Bookmark(user_id=7, book_id=10, format="epub", bookmark_key="shared-bookmark", updated_at=now))
    session.commit()
    old_public = alias.public_id
    with engine.connect() as conn:
        before = _snapshot(conn, "annotation")
        bookmark = _snapshot(conn, "bookmark")
        hardware = dict(conn.execute(text("SELECT * FROM device WHERE id=:id"), {"id": kobo.id}).mappings().one())
    migrate_account_browser_source(engine)
    session.expire_all()
    assert session.query(ub.Device).filter_by(kind="webreader", active=True).count() == 1
    assert canonical.display_name == "Browser"
    assert other.display_name == "Custom source" and not other.active
    assert session.query(ub.Device).filter_by(user_id=9).count() == 0
    assert session.query(ub.Device).count() == 5  # aliases retained, not deleted
    position = session.query(ub.DeviceReadingPosition).filter_by(device_id=canonical.id, book_id=10).one()
    assert (position.progress_percent, position.cfi, position.location_value) == (20, "position-2", "location-2")
    assert position.client_modified_at == now + timedelta(hours=2)
    assert position.rehydrate_needed is True
    assert session.query(ub.DeviceReadingPosition).filter_by(device_id=canonical.id, book_id=11).one().cfi == "unique-book"
    with engine.connect() as conn:
        after = _snapshot(conn, "annotation")
        assert _snapshot(conn, "bookmark") == bookmark
        assert dict(conn.execute(text("SELECT * FROM device WHERE id=:id"), {"id": kobo.id}).mappings().one()) == hardware
        assert conn.execute(text("PRAGMA foreign_key_check")).all() == []
    for original, current in zip(before, after):
        for col in ("origin_device_id", "assigned_device_id", "last_editor_device_id"):
            if original[col] in {alias.id, retired.id}:
                original[col] = canonical.id
        assert current == original  # includes every content field, clock, revision
    assert annotations._owned_device(old_public, 7, session).id == canonical.id
    assert annotations._owned_device(old_public, 8, session) is None
    assert resolve_owned_device_best_effort(user_id=7, public_id=old_public) == canonical.id
    assert resolve_owned_device_best_effort(user_id=8, public_id=old_public) is None
    public_ids, choices = annotations._annotation_device_payload(7, session, include_assignable=True)
    assert alias.id not in public_ids and old_public not in choices
    assert [v["type"] for v in choices.values()].count("webreader") == 1
    # An old restore request must not steal an assignment made to the Clara.
    monkeypatch.setattr(annotations, "_device_visibility_scopes", lambda *a: {7: frozenset((10, 11, 12))})
    _, restored_count, conflicts = annotations.restore_annotation_device(
        old_public, user_id=7, session=session, commit=session.commit,
    )
    assert restored_count == 0 and conflicts == 1
    assert rows[2].assigned_device_id == kobo.id
    assert ensure_webreader_device_best_effort(user_id=7, installation_id="old-tab") == canonical.id
    # Idempotence must not replay alias history over newer post-upgrade activity.
    position.cfi = "new-reading"; session.commit()
    with engine.connect() as conn:
        state = {t: _snapshot(conn, t) for t in ("device", "annotation", "device_reading_position", "annotation_device_state", "device_retired_assignment", "bookmark")}
    migrate_account_browser_source(engine)
    with engine.connect() as conn:
        assert {t: _snapshot(conn, t) for t in state} == state


def test_upgrade_failure_rolls_back_all_regrouping(db):
    from cps import ub
    from cps.services.browser_source import migrate_account_browser_source
    engine, session = db
    session.add_all([ub.Device(user_id=7, kind="webreader", display_name=f"Web reader {i}") for i in (1, 2)])
    session.commit()
    with engine.begin() as conn:
        before = _snapshot(conn, "device")
        conn.execute(text("CREATE TRIGGER reject_browser_alias BEFORE UPDATE ON device "
                          "WHEN NEW.created_by='browser-alias' BEGIN SELECT RAISE(ABORT, 'injected failure'); END"))
    with pytest.raises(Exception, match="injected failure"):
        migrate_account_browser_source(engine)
    with engine.connect() as conn:
        assert _snapshot(conn, "device") == before


def test_pre_registry_reading_is_adopted_without_creating_sources_for_unused_accounts(db):
    from cps import ub
    from cps.services.browser_source import migrate_account_browser_source
    engine, session = db
    session.add(ub.Annotation(user_id=7, book_id=1, annotation_id="older-note", source="webreader",
                              note_text="existing note", position_type="unanchored", hidden=True))
    session.add(ub.Bookmark(user_id=8, book_id=1, format="epub", bookmark_key="old-position"))
    session.commit()
    with engine.connect() as conn:
        annotations = _snapshot(conn, "annotation")
        bookmarks = _snapshot(conn, "bookmark")
    migrate_account_browser_source(engine)
    migrate_account_browser_source(engine)
    with engine.connect() as conn:
        updated = _snapshot(conn, "annotation")
        assert updated[0]["origin_device_id"] is not None
        updated[0]["origin_device_id"] = None
        assert updated == annotations
        assert _snapshot(conn, "bookmark") == bookmarks
    session.expire_all()
    assert {d.user_id for d in session.query(ub.Device).all()} == {7, 8}


def test_concurrent_first_browser_writes_are_account_scoped_and_bounded(db):
    from cps import ub
    from cps.services.device_registry import upsert_webreader_device
    engine, session = db
    barrier = Barrier(8)
    def write(i):
        local = sessionmaker(bind=engine)()
        try:
            barrier.wait()
            device = upsert_webreader_device(local, user_id=7 + i % 2, installation_id=str(i), secret_key=str(i))
            result = (device.user_id, device.id)
            local.commit()
            return result
        finally:
            local.close()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(write, range(8)))
    assert len(set(results)) == 2
    assert session.query(ub.Device).count() == 2
    assert session.query(ub.DeviceIdentity).count() == 0


def test_deleted_account_annotation_history_cannot_block_upgrade(db):
    from cps.services.browser_source import migrate_account_browser_source
    engine, session = db
    # Old account deletion paths could leave annotations with no live owner.
    # Preserve that history, but do not invent a new orphan Browser device.
    with engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        conn.execute(text("INSERT INTO annotation (user_id, book_id, annotation_id, source, "
                          "routing_revision, content_revision) VALUES (999, 1, 'orphan', 'webreader', 1, 1)"))
        conn.commit()
        conn.execute(text("PRAGMA foreign_keys=ON"))
        before = _snapshot(conn, "annotation")
    migrate_account_browser_source(engine)
    with engine.connect() as conn:
        assert _snapshot(conn, "annotation") == before
        assert conn.execute(text("SELECT COUNT(*) FROM device")).scalar() == 0


def test_first_browser_source_is_not_committed_before_its_outer_transaction(db):
    from cps.services.device_registry import upsert_webreader_device
    engine, session = db
    upsert_webreader_device(session, user_id=7)
    session.rollback()
    with engine.connect() as observer:
        assert observer.execute(text("SELECT COUNT(*) FROM device")).scalar() == 0
