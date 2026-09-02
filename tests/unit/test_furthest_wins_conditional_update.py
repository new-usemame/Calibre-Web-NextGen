# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""F-9073e5/F-fe2b1c: the database, not a stale ORM read, picks furthest."""

import sqlite3
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import ub
from cps.progress_syncing.models import AppBase, KOSyncProgress


def _kosync_module():
    import cps.progress_syncing.protocols.kosync  # noqa: F401
    return sys.modules["cps.progress_syncing.protocols.kosync"]


@pytest.mark.unit
def test_two_real_connections_preserve_furthest_locator(
        tmp_path, monkeypatch):
    """A stale percentage-only writer cannot erase a later device locator.

    Connection A reads 40% and approves a browser write to 50%. Connection B
    then commits 60% plus a real KOReader xpointer. A's Session still holds the
    stale 40% object when its writer runs, exactly reproducing F-fe2b1c without
    a mocked connection or row. The final conditional UPDATE must see B's 60%
    in the database and reject A's sentinel write.
    """
    module = _kosync_module()
    database = tmp_path / "furthest-wins.db"
    engine = create_engine(
        "sqlite:///{}".format(database),
        connect_args={"timeout": 5},
    )
    AppBase.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    seed = sessions()
    connection_a = sessions()
    connection_b = sessions()
    try:
        seed.add(KOSyncProgress(
            user_id=7,
            document="42",
            progress="/body/DocFragment[4]/body/p[1].0",
            percentage=40.0,
            device="KOReader seed",
            device_id="seed-device",
        ))
        seed.commit()

        monkeypatch.setattr(ub, "session", connection_a)
        monkeypatch.setattr(module, "get_book_checksums", lambda _book_id: [])

        stale = module.get_progress_record(7, None, 42)
        assert stale.percentage == pytest.approx(40.0)

        device_row = connection_b.query(KOSyncProgress).one()
        device_row.progress = "/body/DocFragment[12]/body/p[9].0"
        device_row.percentage = 60.0
        device_row.device = "KOReader winner"
        device_row.device_id = "winner-device"
        connection_b.commit()

        module.record_percentage_only_progress(
            7, 42, 50.0, device="Web reader",
        )
        connection_a.commit()

        verify = sessions()
        try:
            winner = verify.query(KOSyncProgress).one()
            assert winner.percentage == pytest.approx(60.0)
            assert winner.progress == "/body/DocFragment[12]/body/p[9].0"
            assert winner.device == "KOReader winner"
        finally:
            verify.close()
    finally:
        connection_b.close()
        connection_a.close()
        seed.close()
        engine.dispose()


@pytest.mark.unit
def test_legacy_exact_duplicates_migrate_to_unique_furthest_row(tmp_path):
    """Ordinary legacy duplicates are reduced to the correct unique winner."""
    from cps.progress_syncing.models import ensure_kosync_progress_table

    database = tmp_path / "legacy-kosync.db"
    connection = sqlite3.connect(str(database))
    try:
        connection.execute("""
            CREATE TABLE kosync_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                document TEXT NOT NULL,
                progress TEXT NOT NULL,
                percentage REAL NOT NULL,
                device TEXT NOT NULL,
                device_id TEXT,
                timestamp TIMESTAMP
            )
        """)
        connection.executemany(
            "INSERT INTO kosync_progress "
            "(user_id, document, progress, percentage, device, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (7, "42", "lower-locator", 40.0, "lower", "2026-01-01"),
                (7, "42", "cwng:percentage", 60.0, "sentinel", "2026-01-03"),
                (7, "42", "real-winner", 60.0, "locator", "2026-01-02"),
            ],
        )
        connection.commit()

        ensure_kosync_progress_table(connection)

        rows = connection.execute(
            "SELECT progress, percentage, device FROM kosync_progress"
        ).fetchall()
        assert rows == [("real-winner", 60.0, "locator")]
        indexes = connection.execute(
            "PRAGMA index_list(kosync_progress)"
        ).fetchall()
        unique = {row[1] for row in indexes if row[2]}
        assert "uq_kosync_progress_user_document" in unique
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO kosync_progress "
                "(user_id, document, progress, percentage, device) "
                "VALUES (7, '42', 'duplicate', 70, 'duplicate')"
            )
    finally:
        connection.close()


@pytest.mark.unit
@pytest.mark.parametrize("unique_keyword", ["", "UNIQUE "])
def test_incompatible_named_index_is_replaced_before_startup_continues(
        tmp_path, unique_keyword):
    """Neither a non-unique nor wrongly keyed named index is sufficient."""
    from cps.progress_syncing.models import ensure_kosync_progress_table

    database = tmp_path / "wrong-kosync-index.db"
    connection = sqlite3.connect(str(database))
    try:
        connection.execute("""
            CREATE TABLE kosync_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                document TEXT NOT NULL,
                progress TEXT NOT NULL,
                percentage REAL NOT NULL,
                device TEXT NOT NULL,
                device_id TEXT,
                timestamp TIMESTAMP
            )
        """)
        connection.execute(
            "CREATE {}INDEX uq_kosync_progress_user_document "
            "ON kosync_progress(document)".format(unique_keyword)
        )
        connection.commit()

        ensure_kosync_progress_table(connection)

        indexes = {
            row[1]: bool(row[2])
            for row in connection.execute(
                "PRAGMA index_list(kosync_progress)"
            ).fetchall()
        }
        assert indexes["uq_kosync_progress_user_document"] is True
        columns = tuple(
            row[2]
            for row in connection.execute(
                "PRAGMA index_info('uq_kosync_progress_user_document')"
            ).fetchall()
        )
        assert columns == ("user_id", "document")
        with pytest.raises(sqlite3.IntegrityError):
            connection.executemany(
                "INSERT INTO kosync_progress "
                "(user_id, document, progress, percentage, device) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (7, "42", "first", 20.0, "reader"),
                    (7, "42", "second", 30.0, "reader"),
                ],
            )
    finally:
        connection.close()
