# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#2562: a failed app.db flush must not break every later request.

A flush that fails (``database is locked`` while another writer holds app.db)
leaves ``ub.session`` needing a rollback. ``ub.session`` is one Session shared by
every request on the process, so until something rolls it back each later
request that reads it, starting with the anonymous user's settings on the login
page, raises ``PendingRollbackError`` until the server restarts.

The request-teardown handler that ``create_app()`` registers now rolls that
session back when, and only when, a failed flush has deactivated its
transaction. These tests run the teardown the real factory registers, against a
real SQLite file whose write lock is held by a second connection.
"""

import sqlite3

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import cps
from tests.unit.test_application_factory import _stub_real_bootstrap

ROLE_ANONYMOUS = 1 << 5


@pytest.fixture
def app_and_session(monkeypatch, tmp_path):
    real_anonymous = cps.ub.Anonymous
    services, *_ = _stub_real_bootstrap(monkeypatch)
    application = cps.create_app(cps.config, services)

    db_path = tmp_path / "app.db"
    # A short busy timeout keeps the held-lock flush failing fast.
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"timeout": 0.2})
    cps.ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    anonymous = cps.ub.User()
    anonymous.name = "Guest"
    anonymous.email = "guest@example.invalid"
    anonymous.role = ROLE_ANONYMOUS
    session.add(anonymous)
    session.commit()

    monkeypatch.setattr(cps.ub, "session", session)
    monkeypatch.setattr(cps.ub, "Anonymous", real_anonymous)
    try:
        yield application, session, db_path
    finally:
        session.close()
        engine.dispose()


def _fail_a_flush_inside_a_request(application, session, db_path):
    holder = sqlite3.connect(db_path)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        with application.test_request_context("/"):
            extra = cps.ub.User()
            extra.name = "written while locked"
            extra.email = "locked@example.invalid"
            session.add(extra)
            with pytest.raises(Exception, match="locked"):
                session.flush()
    finally:
        holder.close()


def test_next_request_loads_anonymous_settings_after_a_locked_flush(app_and_session):
    application, session, db_path = app_and_session
    _fail_a_flush_inside_a_request(application, session, db_path)

    with application.test_request_context("/login"):
        guest = cps.ub.Anonymous()

    assert guest.name == "Guest"


def test_teardown_leaves_a_healthy_transaction_alone(app_and_session):
    application, session, _db_path = app_and_session

    with application.test_request_context("/"):
        pending = cps.ub.User()
        pending.name = "pending"
        pending.email = "pending@example.invalid"
        session.add(pending)
        session.flush()

    assert session.in_transaction()
    session.commit()
    assert session.query(cps.ub.User).filter_by(name="pending").count() == 1


def test_teardown_during_another_threads_commit_does_not_abort_it(app_and_session, monkeypatch):
    """Task worker threads share ub.session; a request ending mid-commit must not roll it back.

    In one thread SQLAlchemy refuses that rollback, so the commit would still land;
    from a request thread racing a worker it would not. Assert it is never attempted.
    """
    application, session, _db_path = app_and_session
    engine = session.get_bind()
    attempts = []
    real_rollback = session.rollback

    def counting_rollback():
        attempts.append(1)
        return real_rollback()

    monkeypatch.setattr(session, "rollback", counting_rollback)

    def request_ends_while_committing(_connection):
        with application.test_request_context("/"):
            pass

    worker_row = cps.ub.User()
    worker_row.name = "committed by a worker"
    worker_row.email = "worker@example.invalid"
    session.add(worker_row)
    event.listen(engine, "commit", request_ends_while_committing)
    try:
        session.commit()
    finally:
        event.remove(engine, "commit", request_ends_while_committing)

    assert attempts == []
    assert session.query(cps.ub.User).filter_by(name="committed by a worker").count() == 1
