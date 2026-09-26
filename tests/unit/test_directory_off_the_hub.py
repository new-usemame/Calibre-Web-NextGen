# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Directory (LDAP) calls do not hold up the rest of the server.

The server runs every request as a greenlet on one OS thread, and python-ldap
waits on the network inside C, where no other greenlet can run. A directory
that does not answer used to freeze every request for the whole connect
timeout, once per sign-in. The fake directory below waits the same way, with
a blocking ``time.sleep``.
"""

import time

import flask
import gevent
import pytest
from flask_simpleldap import LDAPException

from cps.services import simpleldap

pytestmark = pytest.mark.unit

WAIT = 0.3


class _Directory:
    def __init__(self):
        self.calls = 0
        self.down = False

    def get_object_details(self, user=None, **_):
        self.calls += 1
        time.sleep(WAIT)  # holds the OS thread, as python-ldap does
        if self.down:
            raise LDAPException("Can't contact LDAP server")
        return {"uid": [user]}

    def bind_user(self, username, password):
        return True if password == "right" else None


@pytest.fixture
def directory(monkeypatch):
    fake = _Directory()
    monkeypatch.setattr(simpleldap, "_ldap", fake)
    monkeypatch.setattr(simpleldap, "_reachability", simpleldap._Reachability())
    return fake


def _sign_ins(count, password="right"):
    """Start ``count`` sign-ins at once, each as its own request greenlet."""
    app = flask.Flask(__name__)

    def sign_in():
        with app.app_context():
            started = time.monotonic()
            return simpleldap.bind_user("alice", password), time.monotonic() - started

    return [gevent.spawn(sign_in) for _ in range(count)]


def _longest_stall(jobs):
    """How long other greenlets went without running while ``jobs`` ran."""
    stalls = []
    spawned = time.monotonic()  # the jobs have not run yet: nothing has yielded

    def ticker():
        last = spawned
        while True:
            gevent.sleep(0.01)
            now = time.monotonic()
            stalls.append(now - last)
            last = now
            if all(job.ready() for job in jobs):
                return

    gevent.spawn(ticker).join(timeout=10)
    gevent.joinall(jobs, timeout=10, raise_error=True)
    return max(stalls)


def test_a_slow_directory_does_not_hold_up_other_requests(directory):
    jobs = _sign_ins(1)
    assert _longest_stall(jobs) < WAIT / 2
    assert jobs[0].value[0] == (True, None)


def test_while_the_directory_is_unreachable_one_sign_in_at_a_time_waits_for_it(directory):
    directory.down = True
    (first,) = _sign_ins(1)
    first.join()
    assert first.value[0] == (None, "LDAP Server down: Can't contact LDAP server")

    # One sign-in tries the directory again; the others are answered at once.
    calls = directory.calls
    jobs = _sign_ins(3)
    gevent.joinall(jobs, raise_error=True)
    assert directory.calls == calls + 1
    answered_at_once = [elapsed for result, elapsed in (job.value for job in jobs)
                        if elapsed < WAIT / 2]
    assert len(answered_at_once) == 2

    # The first sign-in after it is back gets through, and so does everyone after.
    directory.down = False
    (back,) = _sign_ins(1)
    back.join()
    assert back.value[0] == (True, None)
    jobs = _sign_ins(3, password="wrong")
    gevent.joinall(jobs, raise_error=True)
    assert [job.value[0] for job in jobs] == [(False, None)] * 3
    assert directory.calls == calls + 1 + 1 + 3
