# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression coverage for WAL-safe live SQLite copies."""

from __future__ import annotations

import sqlite3

import pytest

from cps.sqlite_utils import copy_sqlite_database


@pytest.mark.unit
def test_online_backup_contains_committed_write_still_in_wal(tmp_path):
    source = tmp_path / "app.db"
    snapshot = tmp_path / "app.db.bak"
    writer = sqlite3.connect(source)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE probe (value TEXT NOT NULL)")
        writer.execute("INSERT INTO probe VALUES ('checkpointed')")
        writer.commit()
        assert writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone() == (0, 0, 0)

        writer.execute("INSERT INTO probe VALUES ('committed-in-wal')")
        writer.commit()
        assert (tmp_path / "app.db-wal").stat().st_size > 0

        copy_sqlite_database(source, snapshot)

        # The copy itself must be a self-contained file. Opening a WAL-mode
        # database may create fresh sidecars, so assert this before observing it.
        assert not (tmp_path / "app.db.bak-wal").exists()
        assert not (tmp_path / "app.db.bak-shm").exists()
        with sqlite3.connect(snapshot) as observer:
            assert observer.execute("SELECT value FROM probe ORDER BY rowid").fetchall() == [
                ("checkpointed",),
                ("committed-in-wal",),
            ]
            assert observer.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    finally:
        writer.close()


@pytest.mark.unit
def test_online_restore_overwrites_stale_destination_wal(tmp_path):
    live_db = tmp_path / "metadata.db"
    backup = tmp_path / "metadata.db.bak"
    writer = sqlite3.connect(live_db)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE probe (value TEXT NOT NULL)")
        writer.execute("INSERT INTO probe VALUES ('restored')")
        writer.commit()
        assert writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone() == (0, 0, 0)
        copy_sqlite_database(live_db, backup)

        # This committed change exists only in the live database's WAL. A raw
        # replacement of metadata.db with the backup leaves the matching WAL
        # beside it, and SQLite reapplies this stale value on the next open.
        writer.execute("UPDATE probe SET value = 'stale-wal-value'")
        writer.commit()
        assert (tmp_path / "metadata.db-wal").stat().st_size > 0

        copy_sqlite_database(backup, live_db, restore=True)

        with sqlite3.connect(live_db) as observer:
            assert observer.execute("PRAGMA journal_mode").fetchone() == ("wal",)
            assert observer.execute("SELECT value FROM probe").fetchall() == [("restored",)]
            assert observer.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    finally:
        writer.close()
