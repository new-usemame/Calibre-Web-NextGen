# SPDX-License-Identifier: GPL-3.0-or-later
"""The gevent catalog worker must paginate the same visible books as Flask."""
from datetime import datetime
from io import BytesIO
from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import gevent
import pytest
from gevent.pywsgi import Input
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from cps import app, db, ub
from cps.services import catalog_search_worker as worker

pytestmark = pytest.mark.unit


@pytest.fixture
def search(tmp_path, monkeypatch):
    from cps.api import books

    app_path = tmp_path / "app.db"
    app_engine = create_engine(f"sqlite:///{app_path}")
    ub.Base.metadata.create_all(app_engine, tables=[
        ub.ArchivedBook.__table__, ub.ReadBook.__table__, ub.UserHiddenBook.__table__,
    ])
    app_engine.dispose()

    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql("ATTACH DATABASE ? AS calibre", (str(tmp_path / "metadata.db"),))
        conn.exec_driver_sql("ATTACH DATABASE ? AS app_settings", (str(app_path),))
        db._register_sqlite_udfs(conn.connection, None)
        db.Base.metadata.create_all(conn.execution_options(schema_translate_map={None: "calibre"}))

    config = SimpleNamespace(config_restricted_column=0, config_read_column=0,
                             config_books_per_page=2)
    viewer = SimpleNamespace(id=1, is_anonymous=False, filter_language=lambda: "all",
                             list_allowed_tags=lambda: [""], list_denied_tags=lambda: [""],
                             allowed_column_value="", denied_column_value="", has_own_library=False)
    monkeypatch.setattr(books, "config", config)
    monkeypatch.setattr(db, "current_user", viewer)
    monkeypatch.setattr(books, "current_user", viewer)
    monkeypatch.setattr(books, "_rows_to_items", lambda rows, _: [row.Books.id for row in rows])
    monkeypatch.setattr(worker, "_pool", None)
    monkeypatch.setattr(worker, "_admission", None)
    monkeypatch.delenv("CWNG_EXPERIMENTAL_SEARCH_THREADS", raising=False)
    monkeypatch.delenv("CWNG_EXPERIMENTAL_SEARCH_ADMISSION", raising=False)

    with Session(engine) as session:
        cdb = object.__new__(db.CalibreDB)
        cdb.session = session
        cdb.config = config
        cdb.get_cc_columns = lambda *args, **kwargs: []
        monkeypatch.setattr(books, "calibre_db", cdb)
        monkeypatch.setattr(ub, "session", session)

        for book_id, day in [(1, 3), (2, 2), (3, 2), (4, 1), (5, 4)]:
            stamp = datetime(2026, 1, day)
            book = db.Books("Café", "Café", "", stamp, stamp, "1.0", stamp,
                            f"book-{book_id}", 0, [], [])
            book.id = book_id
            session.add(book)
        session.add(ub.UserHiddenBook(user_id=viewer.id, book_id=5))
        session.commit()

        def request_books(*, gevent_input=True, **params):
            environ = {"wsgi.input": Input(BytesIO(), 0)} if gevent_input else {}
            with app.test_request_context("/api/v1/books", query_string={"search": "cafe", **params},
                                          environ_overrides=environ):
                return app.make_response(books.list_books.__wrapped__())

        try:
            yield request_books
        finally:
            if worker._pool is not None:
                worker._pool.kill()
    engine.dispose()


@pytest.mark.parametrize(("params", "single_scan", "expected"), [
    ({"page": 1}, False, [1, 3]),
    ({"page": 1}, True, [1, 3]),
    ({"page": 2}, True, [2, 4]),
    ({"page": 3}, True, []),
    ({"select_all": 1}, True, [1, 3, 2, 4]),
])
def test_worker_matches_inline_page(search, monkeypatch, params, single_scan, expected):
    monkeypatch.delenv("CWNG_EXPERIMENTAL_SEARCH_WORKER", raising=False)
    inline = search(**params).get_json()
    key = "ids" if "select_all" in params else "items"
    assert (inline["total"], inline[key]) == (4, expected)

    monkeypatch.setenv("CWNG_EXPERIMENTAL_SEARCH_WORKER", "1")
    monkeypatch.setenv("CWNG_EXPERIMENTAL_SEARCH_SINGLE_SCAN", str(int(single_scan)))
    with patch.object(worker, "_read", wraps=worker._read) as read:
        assert search(**params).get_json() == inline
    read.assert_called_once()
    assert (read.call_args.args[3] is not None) == (single_scan and key == "items")


def test_non_gevent_search_falls_back_inline(search, monkeypatch):
    monkeypatch.setenv("CWNG_EXPERIMENTAL_SEARCH_WORKER", "1")
    assert worker.select_page(None, None, None, {"wsgi.input": BytesIO()}) is None
    body = search(gevent_input=False).get_json()
    assert (body["total"], body["items"]) == (4, [1, 3])
    assert worker._pool is None


def test_full_admission_waits_for_slot_then_times_out(search, monkeypatch):
    monkeypatch.setenv("CWNG_EXPERIMENTAL_SEARCH_WORKER", "1")
    assert search().status_code == 200
    admission = worker._admission
    for _ in range(8):
        assert admission.acquire(blocking=False)
    assert not admission.acquire(blocking=False)

    busy = search()
    assert busy.status_code == 503
    assert busy.get_json()["error"]["code"] == "search_busy"
    assert busy.headers["Retry-After"] == "1"

    # Only a cooperative wait can run this greenlet and admit the next search.
    release = gevent.spawn(admission.release)
    assert search().status_code == 200
    release.get()


@pytest.mark.parametrize("params", [{"select_all": 1}, {"page": 1}])
def test_search_uses_current_visibility_after_worker_wait(search, monkeypatch, params):
    monkeypatch.setenv("CWNG_EXPERIMENTAL_SEARCH_WORKER", "1")
    select_page = worker.select_page

    def hide_after_query_is_built(*args, **kwargs):
        ub.session.add(ub.UserHiddenBook(user_id=1, book_id=1))
        ub.session.commit()
        return select_page(*args, **kwargs)

    monkeypatch.setattr(worker, "select_page", hide_after_query_is_built)
    queued = search(**params)
    monkeypatch.delenv("CWNG_EXPERIMENTAL_SEARCH_WORKER")
    inline = search(**params)
    assert queued.status_code == 200
    assert queued.get_json() == inline.get_json()
    assert queued.get_json()["total"] == 3
    assert 1 not in queued.get_json().get("ids", queued.get_json().get("items"))


def test_search_rejects_results_across_metadata_replacement(search, monkeypatch, tmp_path):
    monkeypatch.setenv("CWNG_EXPERIMENTAL_SEARCH_WORKER", "1")
    read = worker._read

    def replace_before_worker_opens(paths, *args):
        live = Path(paths["calibre"])
        replacement = tmp_path / "replacement.db"
        shutil.copyfile(live, replacement)
        with sqlite3.connect(replacement) as conn:
            conn.execute("DELETE FROM books WHERE id <> 4")
        replacement.replace(live)
        return read(paths, *args)

    monkeypatch.setattr(worker, "_read", replace_before_worker_opens)
    response = search(select_all=1)
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert "ids" not in response.get_json()
