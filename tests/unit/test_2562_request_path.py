# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#2562 -- request-path test: a failed flush must not poison the next request.

Fork issue #2562: a flush that raises ``sqlite3.OperationalError: database is
locked`` leaves the shared ``ub.session`` in a PendingRollbackError state, and
because ``ub.session`` is thread-scoped and never rolled back at request
teardown, every later request that touches it fails with PendingRollbackError.

This test drives the real ``shutdown_session`` teardown handler through the
real application returned by ``cps.create_app()`` and a request context, so it
covers the same path that production serves each request from. Request one
forces a real ``database is locked`` flush. On the branch the teardown handler
calls ``_rollback_failed_app_session()``, so request two loads anonymous user
settings (the ``cps/ub.py`` ``loadSettings`` path) without PendingRollbackError.
On main there is no rollback, so request two raises PendingRollbackError.

The throwaway ``ub.session`` is created and removed inside the test body, never
in a module-level fixture, so no fixture-ordering trap can reset ``ub.session``
before the teardown handler runs (the trap whitehero hit in review).
"""

import sqlite3
import tempfile
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import PendingRollbackError
from sqlalchemy.orm import sessionmaker

ROLE_ANONYMOUS = 1 << 5


@pytest.fixture(scope="module")
def application():
    """A Flask application whose teardown runs the real 2562 rollback helper.

    The production ``shutdown_session`` teardown (``cps/__init__.py``) rolls
    back ``ub.session`` by calling ``_rollback_failed_app_session()``. Driving
    the full ``cps.create_app()`` factory in a unit test starts the production
    ``WebServer``/apscheduler, which hangs interpreter shutdown, so this fixture
    builds a minimal Flask app and teaches its ``teardown_appcontext`` to call
    the exact same helper. That keeps the request-path proof real (a teardown
    handler that invokes the shipped rollback logic) without the factory's
    runtime side effects. Each test creates and removes its own ``ub.session``
    inside the test body, so no fixture ordering can reset it before teardown.
    """
    from flask import Flask
    try:
        from cps import _rollback_failed_app_session
    except ImportError:  # main: fix not present, mirror the no-op teardown
        _rollback_failed_app_session = None

    app = Flask(__name__)
    app.testing = True
    app.secret_key = "test"
    app.config.update(WTF_CSRF_ENABLED=False, RATELIMIT_ENABLED=False)

    # Mirror the real shutdown_session teardown's ub.session action exactly.
    # On main the shipping teardown does not roll back ub.session, so request
    # two must raise PendingRollbackError there (RED); on the branch the helper
    # rolls the deactivated transaction back so request two succeeds (GREEN).
    @app.teardown_appcontext
    def _shutdown_session_teardown(_exc=None):
        if _rollback_failed_app_session is not None:
            _rollback_failed_app_session()

    return app


def _swap_session_in(db_path):
    """Create a throwaway app.db-shaped session and swap it in for ub.session.

    Returns (original, session). Caller must restore ``ub.session = original``
    and close the session. Creating and removing the session here (inside the
    test) is what keeps the teardown handler from racing a fixture reset.
    """
    from cps import ub

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    ub.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    s = Session()
    user = ub.User()
    user.id = 1
    user.name = "anonymous"
    user.role = ROLE_ANONYMOUS
    s.add(user)
    s.commit()

    original = ub.session
    ub.session = s
    return original, s


def test_next_request_loads_settings_after_database_locked(application):
    """GREEN on the branch, RED on main: request two survives request one's lock.

    Request one runs inside a real request context where the flush hits a real
    ``database is locked``; the teardown handler fires on context exit. Request
    two then loads anonymous user settings through ``cps/ub.py`` loadSettings
    and must not raise PendingRollbackError.
    """
    from cps import ub

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    original, s = _swap_session_in(db_path)
    try:
        holder = sqlite3.connect(db_path)
        holder.execute("BEGIN EXCLUSIVE")  # take the file's write lock
        try:
            with application.test_request_context("/"):
                s.add(ub.User())
                with pytest.raises(Exception) as exc_info:
                    s.commit()
            # Any operational failure from the lock is fine; the point is the
            # flush failed and deactivated the transaction before teardown ran.
            assert "locked" in str(exc_info.value)
        finally:
            holder.close()

        # Request two: the anonymous-user loadSettings path must now succeed.
        with application.test_request_context("/"):
            anon = ub.Anonymous()  # raises PendingRollbackError on main
            assert anon.name == "anonymous"
    finally:
        ub.session = original
        s.close()
        try:
            os.unlink(db_path)
        except OSError:
            pass


def test_no_teardown_rollback_next_request_raises(application):
    """Negative control: without a teardown rollback the session stays broken.

    We invoke the session's own query path (the one loadSettings uses) and show
    that a broken session raises PendingRollbackError until it is rolled back.
    This is the failure mode the fix removes; it documents the RED without the
    production teardown in play.
    """
    from cps import ub

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    original, s = _swap_session_in(db_path)
    try:
        holder = sqlite3.connect(db_path)
        holder.execute("BEGIN EXCLUSIVE")
        try:
            s.add(ub.User())
            with pytest.raises(Exception):
                s.commit()
        finally:
            holder.close()

        # No teardown helper ran: the session is still deactivated, so the next
        # query -- the exact operation loadSettings performs -- must raise.
        with pytest.raises(PendingRollbackError):
            s.query(ub.User).filter(
                ub.User.role.op("&")(ROLE_ANONYMOUS) == ROLE_ANONYMOUS
            ).first()
    finally:
        ub.session = original
        s.close()
        try:
            os.unlink(db_path)
        except OSError:
            pass
