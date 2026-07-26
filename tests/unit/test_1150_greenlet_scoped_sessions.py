# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Concurrent requests must not tear down each other's Session (fork #1150).

CWNG serves every request from a gevent greenlet and does not
``monkey.patch_all()``, so all concurrent requests run on one OS thread. Under
SQLAlchemy's default thread scope that means they share a single Session, while
``shutdown_session`` (``cps/__init__.py``) calls ``session_factory.remove()`` on
*every* request teardown. The first request to finish therefore closes the
Session the others are still reading from, and they fail wherever they happen to
be -- which is why this family has surfaced as a different-looking traceback
every time (#1048, #1121, CWA #1228).

#1121 fixed the *thread* half of this (a shared attribute discarding the
registry's thread-locality). It could not fix this half: ``threading.local()``
cannot separate greenlets that share an OS thread.

These tests drive real greenlets against the real factory builder. The first one
is the red: on ``main`` it fails with ``InvalidRequestError``.
"""

import gc
import threading

import gevent
import greenlet
import pytest
from gevent.event import Event
from sqlalchemy import Column, Integer, String, create_engine, text
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker
from sqlalchemy.pool import StaticPool

from cps.db import _make_session_factory

Base = declarative_base()


class _Thing(Base):
    __tablename__ = "thing"
    id = Column(Integer, primary_key=True)
    name = Column(String)


def _engine():
    """A throwaway SQLite engine shaped like the production one (cps/db.py)."""
    engine = create_engine(
        "sqlite://",
        isolation_level="SERIALIZABLE",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("insert into thing (id, name) values (1, 'book')"))
    return engine


def _pre_fix_factory(engine):
    """``session_factory`` exactly as ``setup_db`` built it before this fix.

    Default (thread) scope, which every request greenlet shares.
    """
    return scoped_session(sessionmaker(autocommit=False, autoflush=True,
                                       bind=engine, future=True))


@pytest.fixture
def factory():
    """A session factory over a throwaway SQLite DB, built the production way."""
    engine = _engine()
    sf = _make_session_factory(engine)
    yield sf
    sf.remove()
    engine.dispose()


def _overlapping_requests(sf):
    """Run the #1150 interleave and return the slow request's greenlet.

    Two overlapping requests. The slow one loads an ORM object and is still
    mid-work when the fast one finishes and Flask runs its teardown
    (``session_factory.remove()``, ``cps/__init__.py``). The slow request then
    touches its own object.
    """
    slow_has_loaded, fast_has_torn_down = Event(), Event()
    outcome = {}

    def slow_request():
        obj = sf().query(_Thing).first()
        outcome["read_before"] = obj.name
        slow_has_loaded.set()
        fast_has_torn_down.wait(timeout=10)
        sf().expire(obj)
        outcome["read_after"] = sf().query(_Thing).first().name

    def fast_request():
        slow_has_loaded.wait(timeout=10)
        sf().query(_Thing).first()
        sf.remove()
        fast_has_torn_down.set()

    slow = gevent.spawn(slow_request)
    fast = gevent.spawn(fast_request)
    gevent.joinall([slow, fast], timeout=30)
    return slow, outcome


def test_one_requests_teardown_does_not_detach_another_requests_objects(factory):
    """The #1150 red: the slow request must survive the fast one's teardown."""
    slow, outcome = _overlapping_requests(factory)

    if slow.exception is not None:
        raise AssertionError(
            "a request greenlet died because a concurrent request's teardown "
            "closed the Session underneath it (#1150): %r" % (slow.exception,)
        )
    assert outcome["read_before"] == "book"
    assert outcome["read_after"] == "book"


def test_control_the_pre_fix_factory_still_reproduces_the_crash():
    """Falsifiability control (notes/verify/FAILURE-MODES.md class 9b).

    The test above cannot be run against ``main`` directly -- the function it
    imports does not exist there. So the same scenario is run here against the
    factory ``main`` builds. If this stops failing, the scenario has stopped
    discriminating and the guard above is worthless.
    """
    engine = _engine()
    try:
        slow, _ = _overlapping_requests(_pre_fix_factory(engine))
        assert slow.exception is not None, (
            "the pre-fix, thread-scoped factory no longer reproduces #1150 -- "
            "the test above is no longer proving anything"
        )
        assert "not persistent within this Session" in str(slow.exception)
    finally:
        engine.dispose()


def test_each_greenlet_gets_its_own_session(factory):
    """Sessions are per-greenlet, not per-OS-thread.

    References are held for the whole check on purpose: releasing a Session lets
    CPython reuse its address, which makes an ``id()``-based comparison silently
    report sharing that isn't there.
    """
    sessions = {}
    ready = Event()

    def hold(tag):
        sessions[tag] = factory()
        ready.wait(timeout=10)  # keep every Session alive simultaneously

    greenlets = [gevent.spawn(hold, i) for i in range(4)]
    gevent.sleep(0)
    ready.set()
    gevent.joinall(greenlets, timeout=30)

    assert len(sessions) == 4
    assert len({id(s) for s in sessions.values()}) == 4, (
        "concurrent requests shared a Session: %r" % (sessions,)
    )


def test_os_threads_still_get_their_own_session(factory):
    """Greenlet scoping must subsume thread scoping, not replace it (#1121).

    Background work runs on real ``WorkerThread``s. They were isolated before
    this change and must stay isolated after it.
    """
    sessions = {}
    release = threading.Event()

    def hold(tag):
        sessions[tag] = factory()
        release.wait(timeout=10)

    threads = [threading.Thread(target=hold, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    while len(sessions) < 3:
        gevent.sleep(0.01)
    main_session = factory()
    release.set()
    for t in threads:
        t.join(timeout=10)

    all_sessions = list(sessions.values()) + [main_session]
    assert len({id(s) for s in all_sessions}) == 4, (
        "a worker thread shared a Session with another thread: %r" % (all_sessions,)
    )


def test_registry_does_not_retain_sessions_from_dead_greenlets(factory):
    """The leak a custom scopefunc would otherwise introduce.

    Passing ``scopefunc`` swaps ``ThreadLocalRegistry`` (a ``threading.local``,
    freed with its thread) for a plain dict, which keeps every dead greenlet's
    Session alive forever. Without the weak-keyed map this asserts 50.
    """
    def touch():
        factory().query(_Thing).first()  # no remove(): the leaky path

    gevent.joinall([gevent.spawn(touch) for _ in range(50)], timeout=30)
    gc.collect()

    live = len(factory.registry.registry)
    assert live <= 2, (
        "registry retained %d Sessions from greenlets that have exited" % live
    )


def test_session_is_stable_within_one_greenlet_across_another_teardown(factory):
    """``calibre_db.session`` must not change identity mid-request.

    The session property resolves through the registry on every read (#1149), so
    a foreign ``remove()`` used to swap the object out from under a request
    between two reads. Per-greenlet scoping is what makes the property stable.
    """
    first_read_done, foreign_teardown_done = Event(), Event()
    seen = {}

    def request():
        seen["first"] = factory()
        first_read_done.set()
        foreign_teardown_done.wait(timeout=10)
        seen["second"] = factory()

    def unrelated_request():
        first_read_done.wait(timeout=10)
        factory()
        factory.remove()
        foreign_teardown_done.set()

    gevent.joinall(
        [gevent.spawn(request), gevent.spawn(unrelated_request)], timeout=30
    )
    assert seen["first"] is seen["second"], (
        "the Session changed identity mid-request because another request tore down"
    )


def test_factory_is_greenlet_scoped_not_thread_scoped(factory):
    """Pin the scope key itself, so a refactor cannot quietly restore thread scope."""
    assert factory.registry.scopefunc is greenlet.getcurrent
