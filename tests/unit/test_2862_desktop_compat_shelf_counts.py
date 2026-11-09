# SPDX-License-Identifier: GPL-3.0-or-later
"""DESKTOP_COMPAT_MODE keeps Magic Shelf sidebar counts until the library changes (#2862).

Every authenticated request fills the sidebar counts for each Magic Shelf from a
per-(user, shelf) cache. The desktop-compat hook used to empty that cache on
every request, so each page, cover and API call re-ran every shelf's COUNT
query; on a 4,700-book NAS library that held requests for 20 to 120 seconds.
The hook exists so Calibre desktop's writes show up, so the counts are now
dropped only when metadata.db (or its WAL) has changed on disk.
"""

import os
import sqlite3

import pytest

import cps
from cps import magic_shelf
from tests.unit.test_application_factory import _stub_real_bootstrap


pytestmark = pytest.mark.unit


def _snapshot_hook(application):
    for func in application.before_request_funcs.get(None, []):
        if func.__name__ == "_desktop_compat_fresh_snapshot":
            return func
    raise AssertionError("desktop-compat before_request hook is not registered")


@pytest.fixture
def compat_app(monkeypatch, tmp_path):
    services, _start, _scheduled, _startup = _stub_real_bootstrap(monkeypatch)
    application = cps.create_app(cps.config, services)
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
    yield application, _snapshot_hook(application), db_path, calls
    magic_shelf.forget_book_counts()


def _request(application, hook, path="/"):
    with application.test_request_context(path):
        hook()
        return magic_shelf.cached_book_count_for_magic_shelf(1, 7)


def test_unchanged_library_reuses_counts_across_requests(compat_app):
    application, hook, _db_path, calls = compat_app
    first = _request(application, hook)
    for path in ("/", "/cover/1", "/api/v1/books", "/app/"):
        assert _request(application, hook, path) == first
    assert calls == [7], "an unchanged library must not re-count the shelf on every request"


def test_calibre_desktop_write_refreshes_counts_on_next_request(compat_app):
    application, hook, db_path, calls = compat_app
    first = _request(application, hook)
    assert _request(application, hook) == first

    # Calibre desktop adds a book: metadata.db changes on disk.
    con = sqlite3.connect(str(db_path))
    con.execute("INSERT INTO books (title) VALUES ('Added in Calibre')")
    con.commit()
    con.close()
    stat = os.stat(db_path)
    os.utime(db_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    second = _request(application, hook)
    assert second != first, "a desktop write must refresh the counts on the next request"
    assert calls == [7, 7]
    assert _request(application, hook) == second
    assert calls == [7, 7]


def test_unreadable_library_file_keeps_old_always_fresh_behaviour(compat_app, monkeypatch):
    application, hook, db_path, calls = compat_app
    monkeypatch.setattr(cps.calibre_db, "_metadata_db_path", str(db_path.parent / "missing.db"))
    _request(application, hook)
    _request(application, hook)
    assert calls == [7, 7], "with no way to tell whether the library changed, counts stay fresh"
