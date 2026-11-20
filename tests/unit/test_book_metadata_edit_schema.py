# SPDX-License-Identifier: GPL-3.0-or-later
"""Metadata edit tracking must stay optional for shared Calibre libraries."""
import sqlite3

import pytest

from cps import db
from tests.unit.test_2291_replaced_metadata_db_reconnects import library  # noqa: F401

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("mode", ["normal", "desktop", "network"])
def test_reconnect_creates_tracking_only_in_writable_local_mode(library, monkeypatch, mode):
    """Cold setup is idempotent; shared-library modes must not add a table."""
    lib, app_db, cdb = library
    assert cdb.book_metadata_edits_available
    cdb.session.execute(db.text("DROP TABLE calibre.cwng_book_metadata_edit"))
    cdb.session.commit()
    if mode == "desktop":
        monkeypatch.setenv("DESKTOP_COMPAT_MODE", "true")
    elif mode == "network":
        monkeypatch.setenv("NETWORK_SHARE_MODE", "true")

    for _ in range(2):
        db.CalibreDB.setup_db(lib, app_db)
        assert cdb.book_metadata_edits_available == (mode == "normal")
        # A separate SQLite writer is how Calibre desktop accesses this file.
        with sqlite3.connect(lib + "/metadata.db", timeout=1) as connection:
            table = connection.execute(
                "SELECT name FROM sqlite_master WHERE name='cwng_book_metadata_edit'",
            ).fetchone()
            assert bool(table) == (mode == "normal")
            connection.execute("UPDATE books SET title='Desktop edit' WHERE id=1")
        cdb.session.rollback()
        assert cdb.session.execute(db.text("SELECT title FROM calibre.books WHERE id=1")).scalar() == "Desktop edit"
        cdb.session.rollback()
