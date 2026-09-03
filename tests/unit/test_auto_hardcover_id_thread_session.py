# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression coverage for Hardcover auto-fetch's worker-local DB session.

The admin route constructs this task on the request thread, while WorkerThread
later calls ``run`` on a different OS thread.  CalibreDB sessions must therefore
be acquired by ``run``, not by the task constructor.
"""

from __future__ import annotations

import inspect
import threading
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import object_session, scoped_session, sessionmaker
from sqlalchemy.pool import StaticPool

from cps import db

pytestmark = pytest.mark.unit
from cps.tasks import auto_hardcover_id as module
from cps.tasks.auto_hardcover_id import TaskAutoHardcoverID


class _NoResultsHardcover:
    """Network-free provider: the real DB load is the behavior under test."""

    def search(self, _query):
        return []


def _worker_local_calibre_db(monkeypatch):
    """Install a real scoped SQLAlchemy session factory backed by SQLite.

    ``StaticPool`` keeps the in-memory database visible to both the request
    thread and the worker thread; ``scoped_session`` still produces a distinct
    Session per thread, matching CalibreDB's production lifecycle.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    event.listen(engine, "connect", lambda connection, _record:
                 connection.execute("ATTACH DATABASE ':memory:' AS calibre"))
    db.Base.metadata.create_all(engine)
    factory = scoped_session(sessionmaker(bind=engine, future=True))
    monkeypatch.setattr(db.CalibreDB, "session_factory", factory)
    monkeypatch.setattr(db.CalibreDB, "_init", True)

    session = factory()
    session.add(db.Books(
        "Thread-bound Hardcover book", "Thread-bound Hardcover book", "Author",
        datetime.now(timezone.utc), datetime.now(timezone.utc), "1.0",
        datetime.now(timezone.utc), "thread-bound-hardcover-book", False, [], [],
    ))
    session.commit()
    factory.remove()
    return engine, factory


def test_auto_hardcover_id_loads_books_with_a_worker_local_calibre_session(monkeypatch):
    """A task made on one thread must load persistent Books on another.

    This uses the real ``db.CalibreDB`` and its real thread-scoped SQLAlchemy
    factory.  The provider is mocked only to avoid external HTTP.
    """
    engine, factory = _worker_local_calibre_db(monkeypatch)
    from cps import ub

    app_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ub.HardcoverMatchQueue.__table__.create(app_engine)
    app_factory = scoped_session(sessionmaker(bind=app_engine, future=True))
    monkeypatch.setattr(module.ub, "init_db_thread", app_factory)
    monkeypatch.setattr(module.config, "hardcover_sync_enabled", lambda: True)
    monkeypatch.setattr(module.config, "resolved_hardcover_token", lambda: "token")
    monkeypatch.setattr(module, "Hardcover", _NoResultsHardcover)
    monkeypatch.setattr(TaskAutoHardcoverID, "_save_stats", lambda self: None)

    task = TaskAutoHardcoverID(batch_size=1, rate_limit_delay=0)
    assert not hasattr(task, "calibre_db"), (
        "CalibreDB must not be created while the request thread constructs "
        "the task; run() owns the worker-thread session lifecycle"
    )
    request_thread_id = threading.get_ident()
    observed = {}
    original_get_books_for_batch = task._get_books_for_batch

    def capture_loaded_books(book_ids):
        books = original_get_books_for_batch(book_ids)
        observed["worker_thread_id"] = threading.get_ident()
        observed["book_session"] = object_session(books[0]) if books else None
        observed["task_session"] = task.calibre_db.session
        return books

    monkeypatch.setattr(task, "_get_books_for_batch", capture_loaded_books)
    worker_errors = []

    def run_on_worker():
        try:
            task.run(None)
        except Exception as ex:  # pragma: no cover - asserted below
            worker_errors.append(ex)
        finally:
            factory.remove()

    worker = threading.Thread(target=run_on_worker, name="hardcover-worker")
    worker.start()
    worker.join(timeout=5)

    try:
        assert not worker.is_alive(), "Hardcover worker did not finish"
        assert not worker_errors
        assert observed["worker_thread_id"] != request_thread_id
        assert observed["book_session"] is observed["task_session"]
        assert task.books_processed == 1
    finally:
        app_factory.remove()
        app_engine.dispose()
        factory.remove()
        engine.dispose()


def test_auto_hardcover_id_keeps_calibredb_construction_inside_run():
    """Source pin: task construction must remain safe on a request thread."""
    init_source = inspect.getsource(TaskAutoHardcoverID.__init__)
    run_source = inspect.getsource(TaskAutoHardcoverID.run)

    assert "db.CalibreDB(" not in init_source
    assert "db.CalibreDB(" in run_source
