# SPDX-License-Identifier: GPL-3.0-or-later
"""Upgrade and downgrade preserve devices and their reading observations."""
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

pytestmark = pytest.mark.unit


def test_upgrade_repeat_and_downgrade_preserve_existing_rows():
    path = Path(__file__).parents[2] / "cps/migrations/versions/device_book_scope.py"
    spec = importlib.util.spec_from_file_location("scope_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE device (id INTEGER PRIMARY KEY, display_name TEXT)"))
        conn.execute(text("INSERT INTO device VALUES (1, 'Reader A')"))
        conn.execute(text("CREATE TABLE device_reading_position (device_id INTEGER, progress FLOAT)"))
        conn.execute(text("INSERT INTO device_reading_position VALUES (1, 37.5)"))
        migration.upgrade(conn)
        assert conn.execute(text("SELECT book_scope FROM device")).scalar() is None
        conn.execute(text("UPDATE device SET book_scope = '{\"mode\":\"library\"}'"))
        migration.upgrade(conn)
        assert conn.execute(text("SELECT book_scope FROM device")).scalar() == '{"mode":"library"}'
        migration.downgrade(conn)
        migration.downgrade(conn)
        assert {c["name"] for c in inspect(conn).get_columns("device")} == {"id", "display_name"}
        assert conn.execute(text("SELECT * FROM device")).all() == [(1, "Reader A")]
        assert conn.execute(text("SELECT * FROM device_reading_position")).all() == [(1, 37.5)]
        migration.upgrade(conn)
        assert conn.execute(text("SELECT book_scope FROM device")).scalar() is None
    engine.dispose()
