# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Acceptance tests for the Share my place upgrade migration (PR 1).

Spec: state/kindle-kids/specs/2026-10-11-share-my-place.md, section 3
criterion 1 ("every existing device starts with Share my place OFF (migration
sets existing rows off)") and section 8 (per-device status column).

The starting schema is the device / device_reading_position shape shipped at
origin/main b996bbe75, written out as raw SQL so it does not follow the models.
"""

from datetime import datetime

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import load_only, sessionmaker


pytestmark = pytest.mark.unit

OLD_SCHEMA = (
    """CREATE TABLE user (id INTEGER NOT NULL PRIMARY KEY, name VARCHAR(64))""",
    """CREATE TABLE device (
        id INTEGER NOT NULL,
        public_id VARCHAR(36) NOT NULL,
        user_id INTEGER NOT NULL,
        kind VARCHAR(32) NOT NULL,
        display_name VARCHAR(160) NOT NULL,
        model VARCHAR(160),
        platform VARCHAR(80),
        firmware_version VARCHAR(64),
        first_seen_at DATETIME NOT NULL,
        last_seen_at DATETIME NOT NULL,
        last_metadata_at DATETIME,
        active BOOLEAN NOT NULL,
        created_by VARCHAR(32) NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (public_id),
        FOREIGN KEY(user_id) REFERENCES user (id) ON DELETE CASCADE
    )""",
    "CREATE INDEX ix_device_user_active_last_seen ON device (user_id, active, last_seen_at)",
    "CREATE UNIQUE INDEX uq_device_account_browser ON device (user_id) "
    "WHERE kind = 'webreader' AND created_by = 'account-browser'",
    "CREATE INDEX ix_device_user_display_name ON device (user_id, display_name)",
    """CREATE TABLE device_reading_position (
        id INTEGER NOT NULL,
        device_id INTEGER NOT NULL,
        book_id INTEGER NOT NULL,
        location_source VARCHAR,
        location_type VARCHAR,
        location_value VARCHAR,
        progress_percent FLOAT,
        content_source_progress_percent FLOAT,
        cfi TEXT,
        client_modified_at DATETIME,
        server_modified_at DATETIME NOT NULL,
        rehydrate_needed BOOLEAN DEFAULT '0' NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_device_reading_position_device_book UNIQUE (device_id, book_id),
        FOREIGN KEY(device_id) REFERENCES device (id) ON DELETE CASCADE
    )""",
    "CREATE INDEX ix_device_reading_position_rehydrate "
    "ON device_reading_position (device_id, rehydrate_needed)",
    "CREATE INDEX ix_device_reading_position_book ON device_reading_position (book_id)",
)

SEEN = datetime(2026, 9, 1, 8, 0, 0)


def _seed_old_rows(conn):
    conn.execute(text("INSERT OR IGNORE INTO user (id, name) VALUES (7, 'reader')"))
    for device_id, public_id, kind, created_by, label in (
        (1, "11111111-1111-1111-1111-111111111111", "kobo", "auto", "Kobo Clara"),
        (2, "22222222-2222-2222-2222-222222222222", "koreader", "auto", "Kindle"),
        (3, "33333333-3333-3333-3333-333333333333", "webreader", "account-browser", "Browser"),
    ):
        conn.execute(text(
            "INSERT INTO device (id, public_id, user_id, kind, display_name, first_seen_at, "
            "last_seen_at, active, created_by) VALUES (:id, :pid, 7, :kind, :label, :seen, "
            ":seen, 1, :by)"),
            {"id": device_id, "pid": public_id, "kind": kind, "label": label,
             "seen": SEEN, "by": created_by})
    conn.execute(text(
        "INSERT INTO device_reading_position (device_id, book_id, progress_percent, "
        "client_modified_at, server_modified_at, rehydrate_needed) "
        "VALUES (1, 42, 37.5, :seen, :seen, 0)"), {"seen": SEEN})


def _column_names(engine, table):
    return {column["name"] for column in inspect(engine).get_columns(table)}


def _assert_upgraded(engine):
    """Sharing fields load after this slice, without requiring later migrations."""
    from cps import ub

    with engine.connect() as conn:
        shares = dict(conn.execute(text("SELECT id, share_place FROM device")).all())
        progress = conn.execute(text(
            "SELECT progress_percent FROM device_reading_position WHERE book_id = 42"
        )).scalar_one()
    assert shares == {1: 0, 2: 0, 3: 0}
    assert progress == 37.5

    db = sessionmaker(bind=engine)()
    try:
        # This test runs only the sharing migration against its pinned old
        # schema. Later independent Device columns are not part of that slice.
        devices = db.query(ub.Device).options(load_only(
            ub.Device.id, ub.Device.share_place,
            ub.Device.share_place_enabled_at, ub.Device.display_name,
        )).order_by(ub.Device.id).all()
        assert [d.share_place for d in devices] == [False, False, False]
        assert [d.share_place_enabled_at for d in devices] == [None, None, None]
        assert [d.display_name for d in devices] == ["Kobo Clara", "Kindle", "Browser"]
        position = db.query(ub.DeviceReadingPosition).one()
        assert position.reading_status is None
        assert position.status_modified_at is None
        assert position.progress_percent == 37.5
    finally:
        db.close()


def test_migration_turns_share_my_place_off_for_every_existing_device(tmp_path):
    from cps import ub

    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}")
    with engine.begin() as conn:
        for statement in OLD_SCHEMA:
            conn.execute(text(statement))
        _seed_old_rows(conn)
    assert "share_place" not in _column_names(engine, "device")

    session = sessionmaker(bind=engine)()
    try:
        ub.migrate_device_share_place(engine, session)
    finally:
        session.close()

    _assert_upgraded(engine)
    # A row added later by code that does not name the column (an older build's
    # insert, a raw backfill) also starts off.
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO device (id, public_id, user_id, kind, display_name, first_seen_at, "
            "last_seen_at, active, created_by) VALUES (4, "
            "'44444444-4444-4444-4444-444444444444', 7, 'kobo', 'Libra', :seen, :seen, 1, "
            "'auto')"), {"seen": SEEN})
        assert conn.execute(text(
            "SELECT share_place FROM device WHERE id = 4")).scalar_one() == 0
    engine.dispose()


def test_migration_is_idempotent_and_keeps_choices_made_after_the_first_run(tmp_path):
    from cps import ub

    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}")
    with engine.begin() as conn:
        for statement in OLD_SCHEMA:
            conn.execute(text(statement))
        _seed_old_rows(conn)

    session = sessionmaker(bind=engine)()
    try:
        ub.migrate_device_share_place(engine, session)
        with engine.begin() as conn:
            conn.execute(text("UPDATE device SET share_place = 1 WHERE id = 2"))
            conn.execute(text(
                "UPDATE device_reading_position SET reading_status = :finished",
            ), {"finished": ub.ReadBook.STATUS_FINISHED})
        ub.migrate_device_share_place(engine, session)
    finally:
        session.close()

    with engine.connect() as conn:
        shares = dict(conn.execute(text("SELECT id, share_place FROM device")).all())
        status = conn.execute(text(
            "SELECT reading_status FROM device_reading_position")).scalar_one()
    assert shares == {1: 0, 2: 1, 3: 0}
    assert status == ub.ReadBook.STATUS_FINISHED
    engine.dispose()


def test_the_startup_migrator_upgrades_an_existing_app_db(tmp_path, monkeypatch):
    """Boot the real migrator (twice) on an app.db whose device tables predate
    Share my place: proves the migration is registered, not just defined."""
    from cps import config_sql, constants, ub

    monkeypatch.setattr(constants, "CONFIG_DIR", str(tmp_path), raising=False)
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}", future=True)
    ub.Base.metadata.create_all(engine)
    config_sql._Settings.__table__.create(engine, checkfirst=True)
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        conn.execute(text("DROP TABLE device_reading_position"))
        conn.execute(text("DROP TABLE device"))
        for statement in OLD_SCHEMA[1:]:
            conn.execute(text(statement))
        _seed_old_rows(conn)
    assert "share_place" not in _column_names(engine, "device")
    assert "reading_status" not in _column_names(engine, "device_reading_position")

    session = sessionmaker(bind=engine, future=True)()
    try:
        ub.migrate_Database(session)
        ub.migrate_Database(session)
    finally:
        session.close()

    _assert_upgraded(engine)
    engine.dispose()
