# SPDX-License-Identifier: GPL-3.0-or-later
"""Upgrade preservation and a competing first-write race at the clock gate."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import ub
from cps.progress_syncing import models
from cps.services import kosync_place
from cps.services.place_arbiter import Report

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def enforce_share_place(monkeypatch):
    monkeypatch.setenv("CWNG_SHARE_PLACE_GATES", "1")


@pytest.mark.parametrize("cascade", [False, True])
def test_receipt_clock_upgrade_preserves_legacy_rows_and_is_repeatable(cascade):
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE user (id INTEGER PRIMARY KEY)")
    connection.execute("INSERT INTO user VALUES (1)")
    foreign_key = ", FOREIGN KEY(user_id) REFERENCES user(id) ON DELETE CASCADE" if cascade else ""
    connection.execute("CREATE TABLE kosync_progress (id INTEGER PRIMARY KEY, "
                       "user_id INTEGER NOT NULL, document TEXT NOT NULL, progress TEXT NOT NULL, "
                       "percentage REAL NOT NULL, device TEXT NOT NULL, device_id TEXT, timestamp TIMESTAMP"
                       + foreign_key + ")")
    connection.execute("INSERT INTO kosync_progress VALUES (7,1,'digest','page',40,'Kindle','raw','2026-09-01 12:00:00')")
    models.ensure_kosync_progress_table(connection)
    row = connection.execute("SELECT id, percentage, timestamp, received_at FROM kosync_progress").fetchone()
    assert row == (7, 40, "2026-09-01 12:00:00", None)
    connection.execute("UPDATE kosync_progress SET received_at='2026-09-01 12:00:01'")
    models.ensure_kosync_progress_table(connection)
    assert connection.execute("SELECT received_at FROM kosync_progress").fetchone()[0] == "2026-09-01 12:00:01"
    connection.close()


def test_losing_first_insert_rejudges_the_concurrent_newer_report(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "race.db"))
    ub.Base.metadata.create_all(engine, tables=[ub.User.__table__, models.KOSyncProgress.__table__])
    sessions = sessionmaker(bind=engine)
    old_read, newer_committed = Event(), Event()
    now = datetime.now(timezone.utc)

    def write(older):
        with sessions() as session:
            execute = session.execute
            paused = False

            def pause_after_first_read(statement, *args, **kwargs):
                nonlocal paused
                result = execute(statement, *args, **kwargs)
                if older and statement.is_select and not paused:
                    paused = True
                    old_read.set()
                    assert newer_committed.wait(10), "newer first writer failed to commit"
                return result

            session.execute = pause_after_first_read
            if not older:
                assert old_read.wait(10), "older first writer did not read"
            stamp = now - timedelta(seconds=60 if older else 30)
            won = kosync_place.latest_write(
                models.KOSyncProgress, dict(user_id=1, document="1"),
                dict(progress="older" if older else "newer", percentage=80 if older else 20,
                     device="Kindle", device_id="a" if older else "b",
                     timestamp=stamp, received_at=now),
                client_column="timestamp", server_column="received_at",
                candidate=Report(stamp, now), session=session)
            session.commit()
            if not older:
                newer_committed.set()
            return won

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            older = pool.submit(write, True)
            newer = pool.submit(write, False)
            assert newer.result(timeout=15) is True
            assert older.result(timeout=15) is False
        with sessions() as session:
            row = session.query(models.KOSyncProgress).one()
            assert (row.progress, row.percentage) == ("newer", 20)
    finally:
        engine.dispose()
