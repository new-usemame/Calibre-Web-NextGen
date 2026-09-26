# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""A background task's app.db session gives its connection back when closed.

Thumbnail, cleanup, KEPUB repair, annotation and conversion tasks open their
own app.db session on a worker thread for every run. Each of those sessions
used to get a new engine, and the engine kept the connection it opened (the
database file and its WAL companions) until the process exited. On a server
that runs such tasks for weeks, that is a steady climb towards the process's
open-file limit. Closing a task session must leave nothing open.
"""

import gc
import os
import threading
from types import SimpleNamespace

import pytest

from cps import ub

pytestmark = pytest.mark.unit

RUNS = 20


@pytest.fixture
def app_db(tmp_path):
    previous_session, previous_path = ub.session, ub.app_DB_path
    ub.init_db(str(tmp_path / "app.db"))
    try:
        yield
    finally:
        engine = ub.session.get_bind()
        ub.session.close()
        engine.dispose()
        ub.session, ub.app_DB_path = previous_session, previous_path


def _open_files():
    gc.collect()
    return len(os.listdir("/dev/fd"))


def _on_a_worker_thread(run):
    failures = []

    def body():
        try:
            for _ in range(RUNS):
                run()
        except Exception as error:  # surfaced below, not swallowed
            failures.append(error)

    worker = threading.Thread(target=body)
    worker.start()
    worker.join(timeout=30)
    assert not worker.is_alive()
    assert not failures, failures


def _scoped_closed():
    session = ub.get_new_session_instance()
    session.query(ub.User).count()
    session.close()


def _scoped_removed():
    session = ub.get_new_session_instance()
    session.query(ub.User).count()
    session.remove()


def _thread_session_closed():
    session = ub.init_db_thread()
    session.query(ub.User).count()
    session.close()


@pytest.mark.skipif(not os.path.isdir("/dev/fd"), reason="needs /dev/fd")
@pytest.mark.parametrize("run", [_scoped_closed, _scoped_removed, _thread_session_closed],
                         ids=["new-session-close", "new-session-remove", "db-thread-close"])
def test_closed_task_sessions_leave_no_files_open(app_db, run):
    run()  # the shared pool may keep one idle connection; count from there
    before = _open_files()

    _on_a_worker_thread(run)

    # A small allowance for the worker thread's own pooled connection.
    assert _open_files() - before <= 3


def test_a_task_session_reads_what_a_request_committed(app_db):
    user = ub.User()
    user.name = "written-by-a-request"
    user.email = "request@example.invalid"
    user.password = "unused"
    ub.session.add(user)
    ub.session.commit()
    seen = SimpleNamespace(names=None)

    def read():
        session = ub.get_new_session_instance()
        try:
            seen.names = {row.name for row in session.query(ub.User).all()}
        finally:
            session.remove()

    worker = threading.Thread(target=read)
    worker.start()
    worker.join(timeout=30)

    assert "written-by-a-request" in seen.names
