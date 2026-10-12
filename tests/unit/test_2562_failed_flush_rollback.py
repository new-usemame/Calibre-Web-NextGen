# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#2562 — a failed flush must not leave the app.db session permanently broken.

A flush that fails (e.g. ``sqlite3.OperationalError: database is locked``)
leaves the shared ``ub.session`` in a PendingRollbackError state. Because the
session is never rolled back at request teardown, every later request that
touches it fails with PendingRollbackError (fork issue #2562).

``shutdown_session`` (``cps/__init__.py``) already rolls back and removes the
``calibre_db`` (metadata.db) session at teardown, but it did not touch
``ub.session`` (app.db). This test pins the fix: ``_rollback_failed_app_session``
rolls the app.db session back only when a failed flush has deactivated its
transaction, so the next query succeeds.
"""

import pytest
from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.exc import PendingRollbackError
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


class _Thing(Base):
    __tablename__ = "thing"
    id = Column(Integer, primary_key=True)
    name = Column(String)


@pytest.fixture
def session():
    """A throwaway app.db-shaped session, swapped in for ``ub.session``."""
    from cps import ub

    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    s = Session()
    s.add(_Thing(id=1, name="book"))
    s.commit()
    original = ub.session
    ub.session = s
    try:
        yield s
    finally:
        ub.session = original
        s.close()


def test_failed_flush_leaves_session_broken_without_rollback(session):
    """RED on main: a failed flush leaves the session in PendingRollbackError."""
    # Force a failed flush (duplicate primary key).
    session.add(_Thing(id=1, name="dup"))
    with pytest.raises(Exception):
        session.commit()

    # The session is now in a failed state; the next query raises
    # PendingRollbackError until the session is rolled back.
    with pytest.raises(PendingRollbackError):
        session.query(_Thing).count()


def test_rollback_failed_app_session_cleans_broken_session(session):
    """GREEN with the fix: the teardown helper rolls the broken session back."""
    from cps import _rollback_failed_app_session

    # Force a failed flush (duplicate primary key).
    session.add(_Thing(id=1, name="dup"))
    with pytest.raises(Exception):
        session.commit()

    # The teardown helper rolls the session back.
    _rollback_failed_app_session()

    # The next query succeeds.
    assert session.query(_Thing).count() == 1


def test_rollback_failed_app_session_is_noop_on_clean_session(session):
    """The helper must not disturb a healthy session."""
    from cps import _rollback_failed_app_session

    _rollback_failed_app_session()
    assert session.query(_Thing).count() == 1
