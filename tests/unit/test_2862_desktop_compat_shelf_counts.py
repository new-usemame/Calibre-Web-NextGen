# SPDX-License-Identifier: GPL-3.0-or-later
"""DESKTOP_COMPAT_MODE keeps Magic Shelf sidebar counts until the library changes (#2862).

Every signed-in request fills the sidebar counts for each Magic Shelf from a
per-(user, shelf) cache. The desktop-compat hook used to empty that cache on
every request, so each page, cover and API call re-ran every shelf's COUNT
query; on a 4,700-book NAS library that held requests for 20 to 120 seconds.
The hook exists so Calibre desktop's writes show up, so the counts are now
dropped only when metadata.db (or its WAL) has changed on disk, and the very
request after such a write already shows the new count.

These drive the application's real before_request chain, in its registered
order, for a signed-in user with real Magic Shelf rows.
"""

import os
import sqlite3
from types import SimpleNamespace

import pytest
from flask import g
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import cps
from cps import magic_shelf, ub
from tests.unit.test_application_factory import _stub_real_bootstrap


pytestmark = pytest.mark.unit

USER_ID = 42
SHELF_ID = 7


@pytest.fixture
def compat_app(monkeypatch, tmp_path):
    services, _start, _scheduled, _startup = _stub_real_bootstrap(monkeypatch)
    application = cps.create_app(cps.config, services)

    engine = create_engine("sqlite://")
    ub.Base.metadata.create_all(engine, tables=[
        ub.MagicShelf.__table__, ub.HiddenMagicShelfTemplate.__table__])
    app_session = sessionmaker(bind=engine)()
    app_session.add(ub.MagicShelf(id=SHELF_ID, name="Unread", user_id=USER_ID))
    app_session.commit()
    monkeypatch.setattr(ub, "session", app_session)

    db_path = tmp_path / "metadata.db"
    con = sqlite3.connect(str(db_path))
    con.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT)")
    con.commit()
    con.close()
    monkeypatch.setattr(cps.calibre_db, "_desktop_compat", True)
    monkeypatch.setattr(cps.calibre_db, "session", None)
    monkeypatch.setattr(cps.calibre_db, "_metadata_db_path", str(db_path), raising=False)

    calls = []

    def fake_count(shelf_id):
        calls.append(shelf_id)
        return 10 + len(calls)

    monkeypatch.setattr(magic_shelf, "get_book_count_for_magic_shelf", fake_count)
    magic_shelf.forget_book_counts()
    user = SimpleNamespace(id=USER_ID, is_authenticated=True, is_anonymous=False,
                           view_settings={})
    yield application, user, db_path, calls
    magic_shelf.forget_book_counts()
    app_session.close()
    engine.dispose()


def _sidebar_count(application, user, path="/"):
    """Run one request's before_request chain; return the count it put in the sidebar."""
    with application.test_request_context(path):
        g._login_user = user
        application.preprocess_request()
        shelves = [s for s in g.magic_shelves_access if s.id == SHELF_ID]
        assert shelves, "the signed-in user's shelf must reach the sidebar"
        return shelves[0].book_count


def _desktop_write(db_path):
    con = sqlite3.connect(str(db_path))
    con.execute("INSERT INTO books (title) VALUES ('Added in Calibre')")
    con.commit()
    con.close()
    stat = os.stat(db_path)
    os.utime(db_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


def test_unchanged_library_reuses_counts_across_requests(compat_app):
    application, user, _db_path, calls = compat_app
    first = _sidebar_count(application, user)
    for path in ("/", "/cover/1", "/api/v1/books", "/app/"):
        assert _sidebar_count(application, user, path) == first
    assert calls == [SHELF_ID], "an unchanged library must not re-count the shelf on every request"


def test_calibre_desktop_write_shows_on_the_very_next_request(compat_app):
    application, user, db_path, calls = compat_app
    first = _sidebar_count(application, user)
    assert _sidebar_count(application, user) == first

    _desktop_write(db_path)

    second = _sidebar_count(application, user)
    assert second != first, "the first request after a desktop write must show the new count"
    assert _sidebar_count(application, user) == second
    assert calls == [SHELF_ID, SHELF_ID]


def test_unreadable_library_file_keeps_counts_always_fresh(compat_app, monkeypatch):
    application, user, db_path, calls = compat_app
    monkeypatch.setattr(cps.calibre_db, "_metadata_db_path", str(db_path.parent / "missing.db"))
    first = _sidebar_count(application, user)
    assert _sidebar_count(application, user) != first
    assert calls == [SHELF_ID, SHELF_ID]


def test_write_that_only_reaches_the_wal_still_refreshes_counts(compat_app):
    """A library in WAL mode takes a write into metadata.db-wal and leaves
    metadata.db itself untouched until a checkpoint."""
    application, user, db_path, calls = compat_app
    holder = sqlite3.connect(str(db_path))  # keeps the WAL from being checkpointed away
    try:
        holder.execute("PRAGMA journal_mode=WAL")
        holder.execute("PRAGMA wal_autocheckpoint=0")
        holder.execute("SELECT count(*) FROM books").fetchall()
        first = _sidebar_count(application, user)
        main_file = os.stat(db_path)

        writer = sqlite3.connect(str(db_path))
        writer.execute("INSERT INTO books (title) VALUES ('Added in Calibre')")
        writer.commit()
        writer.close()
        after = os.stat(db_path)
        assert (after.st_size, after.st_mtime_ns) == (main_file.st_size, main_file.st_mtime_ns), \
            "precondition: the write must reach only the WAL"

        assert _sidebar_count(application, user) != first
        assert calls == [SHELF_ID, SHELF_ID]
    finally:
        holder.close()
