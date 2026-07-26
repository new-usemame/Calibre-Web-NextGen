# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""#1121 — the contracts `CalibreDB.session` had as a plain attribute.

Turning an attribute into a property is only safe if the property keeps every
contract the attribute had. Three of them were found by the test suite rather
than by reading the code, and all three are pinned here so a later refactor
cannot quietly drop one:

1. Assigning ``None`` drops the thread's session, and ``ensure_session()``
   then builds a working replacement. This is `cps/editbooks.py`'s recovery
   path for a poisoned transaction, and it is the one caller in ``cps/`` that
   assigns to ``session`` at runtime.
2. Instances built with ``CalibreDB.__new__(CalibreDB)`` — no ``__init__`` —
   can still read and write ``session``.
3. ``mock.patch.object(calibre_db, "session", ...)`` restores by *deleting*
   the attribute, so the property needs a deleter.

Plus the per-instance ``expire_on_commit`` preference, which background tasks
set to False and which must survive being resolved on a thread other than the
one that constructed the instance.
"""

from __future__ import annotations

import threading
from unittest import mock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.pool import StaticPool

pytestmark = pytest.mark.unit


def _wire():
    from cps.db import CalibreDB

    engine = create_engine("sqlite://",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    CalibreDB.engine = engine
    CalibreDB.session_factory = scoped_session(
        sessionmaker(bind=engine, autocommit=False, autoflush=True, future=True))
    return CalibreDB


def test_assigning_none_then_ensure_session_yields_a_working_replacement():
    """cps/editbooks.py:1063-1075 — drop a poisoned session and carry on."""
    cls = _wire()
    db = cls()

    first = db.session
    db.session.rollback()
    db.session.close()
    db.session = None

    assert db._peek_session() is None, (
        "assigning None must drop the thread's session, or editbooks would "
        "retry its commit on the same poisoned session")

    db.ensure_session()
    second = db.session

    assert second is not first, "ensure_session returned the dropped session"
    assert second.execute(text("select 1")).scalar() == 1
    assert db.session is second, "the replacement must be stable across reads"


def test_assigning_none_on_one_thread_does_not_disturb_another():
    """The point of #1121: recovery is scoped to the thread that runs it."""
    cls = _wire()
    db = cls()
    other_ready = threading.Event()
    may_finish = threading.Event()
    seen = {}

    def other_thread():
        seen["before"] = id(db.session)
        other_ready.set()
        may_finish.wait(10)
        seen["after"] = id(db.session)
        seen["usable"] = db.session.execute(text("select 1")).scalar()

    t = threading.Thread(target=other_thread)
    t.start()
    other_ready.wait(10)

    db.session = None            # this thread recovers
    db.ensure_session()

    may_finish.set()
    t.join(10)

    assert seen["after"] == seen["before"], (
        "another thread's session was dropped by a recovery it did not run")
    assert seen["usable"] == 1


def test_an_instance_built_without_init_can_still_use_session():
    """Several tests use CalibreDB.__new__ to avoid needing a Flask app."""
    cls = _wire()
    inst = cls.__new__(cls)

    sentinel = object()
    inst.session = sentinel
    assert inst.session is sentinel

    del inst.session
    assert inst.session is not sentinel, "deleting must drop the override"
    assert inst.session.execute(text("select 1")).scalar() == 1


def test_patch_object_round_trips_without_raising():
    """patch.object restores an absent attribute by deleting it."""
    cls = _wire()
    db = cls()
    real = db.session

    with mock.patch.object(db, "session", "stub"):
        assert db.session == "stub"

    assert db.session is real, (
        "after the patch exits, session must resolve through the registry again")


def test_expire_on_commit_preference_survives_on_another_thread():
    """Background tasks construct CalibreDB(expire_on_commit=False)."""
    cls = _wire()
    worker = cls(expire_on_commit=False)
    seen = {}

    def run():
        seen["value"] = worker.session.expire_on_commit

    t = threading.Thread(target=run)
    t.start()
    t.join(10)

    assert seen["value"] is False, (
        "a worker instance resolved a session on its own thread with "
        "expire_on_commit=True, so its detached objects would re-query")


def test_session_is_none_when_there_is_no_factory():
    """Callers check `if calibre_db.session is None` before the DB is set up."""
    from cps.db import CalibreDB

    original = CalibreDB.session_factory
    try:
        CalibreDB.session_factory = None
        inst = CalibreDB()
        assert inst.session is None
        assert inst._peek_session() is None
    finally:
        CalibreDB.session_factory = original
