# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""#2291: a library synced onto the server by a copy tool shows up without a
manual "Reconnect Calibre Database".

Sync tools write a new metadata.db and rename it over the old one. The
engine's single connection stays on the replaced file, so the per-request
snapshot reset still reads the old library. These tests drive the real
``CalibreDB.setup_db`` against a real SQLite library and swap the file the way
rsync/Syncthing do.
"""

import os
import shutil
import sqlite3
import time
from types import SimpleNamespace

import pytest

from cps import db

SCHEMA = """
CREATE TABLE books (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL DEFAULT 'Unknown',
    sort TEXT, author_sort TEXT, timestamp TIMESTAMP, pubdate TIMESTAMP,
    series_index REAL NOT NULL DEFAULT 1.0, last_modified TIMESTAMP, path TEXT NOT NULL DEFAULT '',
    has_cover INTEGER DEFAULT 0, uuid TEXT);
CREATE TABLE library_id (id INTEGER PRIMARY KEY, uuid TEXT NOT NULL);
INSERT INTO library_id VALUES (1, 'test-library');
CREATE TABLE custom_columns (id INTEGER PRIMARY KEY, label TEXT, name TEXT, datatype TEXT,
    mark_for_delete INTEGER, editable INTEGER, display TEXT, is_multiple INTEGER, normalized INTEGER);
"""

_CLASS_STATE = ("_init", "engine", "config", "session_factory", "_desktop_compat",
                "_metadata_db_path", "_metadata_db_identity", "_metadata_db_content",
                "_replacement_checked_at", "_replacement_candidate")


def _write_library(path, titles):
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO books(title) VALUES (?)", [(t,) for t in titles])
    con.commit()
    con.close()


def _sync_over(lib, titles, age_seconds=60):
    """Replace metadata.db the way a sync tool does: write beside, rename over."""
    staged = os.path.join(lib, ".metadata.db.partial")
    _write_library(staged, titles)
    if age_seconds:
        past = time.time() - age_seconds
        os.utime(staged, (past, past))
    os.replace(staged, os.path.join(lib, "metadata.db"))


def _titles(cdb):
    cdb.ensure_session()
    return sorted(r[0] for r in cdb.session.execute(db.text("SELECT title FROM calibre.books")))


def _next_request(cdb, app_db):
    """What each request does: the hook, then a fresh snapshot."""
    db.CalibreDB._replacement_checked_at = None  # the check interval has elapsed
    reconnected = db.CalibreDB.reconnect_if_metadata_db_replaced(app_db)
    cdb.ensure_session()
    cdb.session.rollback()
    return reconnected


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.delenv("DESKTOP_COMPAT_MODE", raising=False)
    monkeypatch.delenv("NETWORK_SHARE_MODE", raising=False)
    saved = {name: getattr(db.CalibreDB, name) for name in _CLASS_STATE}
    lib = tmp_path / "library"
    lib.mkdir()
    _write_library(str(lib / "metadata.db"), ["Existing Book"])
    app_db = str(tmp_path / "app.db")
    sqlite3.connect(app_db).close()
    db.CalibreDB.config = SimpleNamespace(config_calibre_dir=str(lib), db_configured=False,
                                          invalidate=lambda *a: None)
    db.CalibreDB.setup_db(str(lib), app_db)
    cdb = db.CalibreDB(expire_on_commit=False, init=True)
    yield str(lib), app_db, cdb
    db.CalibreDB.dispose()
    if db.CalibreDB.engine is not None:
        db.CalibreDB.engine.dispose()
    for name, value in saved.items():
        setattr(db.CalibreDB, name, value)


def test_a_synced_library_is_visible_on_the_next_request(library):
    lib, app_db, cdb = library
    assert _titles(cdb) == ["Existing Book"]

    _sync_over(lib, ["Existing Book", "Synced Book"])

    assert _next_request(cdb, app_db) is True
    assert _titles(cdb) == ["Existing Book", "Synced Book"]
    # The adopted file is now the baseline: the following request is quiet.
    assert _next_request(cdb, app_db) is False
    assert _titles(cdb) == ["Existing Book", "Synced Book"]


def test_a_file_still_changing_is_adopted_once_it_settles(library):
    lib, app_db, cdb = library
    _sync_over(lib, ["Existing Book", "Half Copied"], age_seconds=0)

    assert _next_request(cdb, app_db) is False
    assert _titles(cdb) == ["Existing Book"]

    # Seen unchanged on the next check: complete, so adopt it.
    assert _next_request(cdb, app_db) is True
    assert _titles(cdb) == ["Existing Book", "Half Copied"]


def test_edits_to_the_same_file_do_not_reconnect(library):
    lib, app_db, cdb = library
    engine = db.CalibreDB.engine
    con = sqlite3.connect(os.path.join(lib, "metadata.db"))
    con.execute("INSERT INTO books(title) VALUES ('Added In Place')")
    con.commit()
    con.close()

    assert _next_request(cdb, app_db) is False
    assert db.CalibreDB.engine is engine
    assert _titles(cdb) == ["Added In Place", "Existing Book"]


def test_a_library_missing_mid_swap_is_left_alone_until_it_returns(library):
    lib, app_db, cdb = library
    os.remove(os.path.join(lib, "metadata.db"))

    assert _next_request(cdb, app_db) is False

    _sync_over(lib, ["Existing Book", "Arrived Later"])
    assert _next_request(cdb, app_db) is True
    assert _titles(cdb) == ["Arrived Later", "Existing Book"]


def test_checks_are_rate_limited_between_requests(library):
    lib, app_db, cdb = library
    assert _next_request(cdb, app_db) is False  # stamps the check time

    _sync_over(lib, ["Existing Book", "Synced Book"])
    assert db.CalibreDB.reconnect_if_metadata_db_replaced(app_db) is False
    assert _titles(cdb) == ["Existing Book"]


def test_the_same_file_under_a_new_inode_number_does_not_reconnect(library):
    # SMB mounted with noserverino renumbers an untouched file.
    lib, app_db, cdb = library
    path = os.path.join(lib, "metadata.db")
    past = time.time() - 60
    os.utime(path, (past, past))  # long settled, so only the guard can stop a reconnect
    assert _next_request(cdb, app_db) is False
    engine = db.CalibreDB.engine
    staged = os.path.join(lib, ".metadata.db.copy")
    shutil.copy2(path, staged)
    os.replace(staged, path)

    assert _next_request(cdb, app_db) is False
    assert db.CalibreDB.engine is engine
