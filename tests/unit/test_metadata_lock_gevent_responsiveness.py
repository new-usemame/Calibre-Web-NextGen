# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Waiting for the metadata.db write lock must not stall the gevent hub.

``cps/server.py`` runs gevent WITHOUT ``monkey.patch_all()``, so every
request is a greenlet and all of them share one OS thread. #954 covered
blocking *work* on that thread (provider fan-outs), and
``cps/services/parallel.py`` fixed it. This file covers the other door into
the same failure: blocking *waiting*.

``metadata_db_write_lock`` polls a POSIX flock and sleeps between attempts.
The poll loop has no other yield point, so a stdlib sleep there does not cost
one poll interval — it costs the whole wait, because the hub never runs again
until the lock is won. The other writer is the ingest processor holding the
lock across a ``calibredb add``, and the timeout is 120s, so on the pre-fix
code one metadata write during an ingest froze the entire app.

That is a user-visible outage on a common pairing (edit a book, rename or
merge a tag, upload — while a book is being ingested), and it also shows up in
CI as ``socket hang up`` when the e2e projects write concurrently.

The test measures the invariant the user cares about: while one greenlet waits
for the lock, other greenlets (= other people's requests) still get scheduled.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import sys
import threading
import time
import types
from pathlib import Path

import pytest

# Without this marker CI's `-m "smoke or unit"` selector deselects every test
# in this file, including the AST guard below. A guard nobody runs is not a
# guard: a PR reintroducing a hub-blocking sleep would merge green.
pytestmark = pytest.mark.unit

gevent = pytest.importorskip("gevent", reason="production WSGI server is gevent")
fcntl = pytest.importorskip("fcntl", reason="POSIX advisory locking")

REPO_ROOT = Path(__file__).resolve().parents[2]

# How long the competing writer (stand-in for ingest's calibredb add) holds
# the lock. Long enough to dwarf scheduler noise, short enough to keep the
# suite fast. The pre-fix stall equals this value in full.
_HOLD_SECONDS = 1.0
# The hub must keep serving other greenlets throughout. Pre-fix the worst gap
# is the entire hold; with a cooperative sleep it is a scheduler tick. 300ms
# sits far from both, so the test is decisive without being flaky on a loaded
# CI box. Same threshold as test_request_fanout_gevent_responsiveness.py.
_MAX_TOLERABLE_STALL = 0.3


def _load(mod_name: str, rel_path: str):
    """Load a cps module in isolation (no Flask app init)."""
    cps_pkg = sys.modules.get("cps")
    if cps_pkg is None:
        cps_pkg = types.ModuleType("cps")
        cps_pkg.__path__ = [str(REPO_ROOT / "cps")]
        sys.modules["cps"] = cps_pkg

    if "cps.services" not in sys.modules:
        services_pkg = types.ModuleType("cps.services")
        services_pkg.__path__ = [str(REPO_ROOT / "cps" / "services")]
        sys.modules["cps.services"] = services_pkg

    spec = importlib.util.spec_from_file_location(mod_name, REPO_ROOT / rel_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


lock_mod = _load("cps.services.calibre_db_lock", "cps/services/calibre_db_lock.py")


class _CompetingWriter:
    """Holds the flock from a real OS thread, the way the ingest processor
    (a separate process entirely) holds it in production.

    flock is owned by the open file description, so a second ``os.open`` of
    the same path genuinely contends even from within this process — the
    waiter really does have to poll.
    """

    def __init__(self, lock_path: str, hold_seconds: float):
        self._path = lock_path
        self._hold = hold_seconds
        self.acquired = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            self.acquired.set()
            time.sleep(self._hold)
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def __enter__(self):
        self._thread.start()
        assert self.acquired.wait(timeout=5), "competing writer never took the lock"
        return self

    def __exit__(self, *_exc):
        self._thread.join(timeout=self._hold + 5)


def _worst_stall_while(run) -> float:
    """Run ``run()`` in one greenlet while a heartbeat greenlet tries to stay
    on schedule. Return the worst delay the heartbeat suffered — i.e. the
    longest any other request would have been frozen."""
    gaps: list[float] = []
    done: list[bool] = []

    def heartbeat():
        # Record the gap BEFORE testing the exit condition: the gap spanning
        # the stall is only observable on the wake after it, when `done` is
        # already set. Checking `done` first silently drops the measurement
        # and the test passes on broken code.
        last = time.monotonic()
        while True:
            gevent.sleep(0.02)
            now = time.monotonic()
            gaps.append(now - last)
            last = now
            if done:
                return

    hb = gevent.spawn(heartbeat)
    gevent.sleep(0)  # let the heartbeat take its first sample

    # Whatever `run` raised has to reach the caller: gevent.joinall() does not
    # re-raise, so without this a `run` that blew up immediately would be
    # measured as "no stall" and the test would go GREEN on broken code.
    failed: list[BaseException] = []

    def runner():
        try:
            run()
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            failed.append(exc)
        finally:
            done.append(True)

    gevent.joinall([gevent.spawn(runner)])
    hb.join(timeout=1)
    hb.kill(block=True)
    if failed:
        raise failed[0]
    assert gaps, "heartbeat never sampled"
    return max(gaps)


def test_waiting_for_the_write_lock_keeps_serving_other_requests(tmp_path):
    """The regression: a metadata write that lands during an ingest must not
    freeze every other request for the duration of the ingest."""
    lock_path = str(tmp_path / lock_mod.DEFAULT_LOCK_BASENAME)
    entered = []

    with _CompetingWriter(lock_path, _HOLD_SECONDS):
        def wait_for_the_lock():
            with lock_mod.metadata_db_write_lock(
                lock_dir=str(tmp_path), timeout=30, poll_interval=0.05
            ):
                entered.append(True)

        worst = _worst_stall_while(wait_for_the_lock)

    # The wait has to have actually happened, or the measurement is vacuous.
    assert entered == [True], "the waiter never acquired the lock"
    assert worst < _MAX_TOLERABLE_STALL, (
        f"the gevent hub stalled {worst:.2f}s while waiting for the metadata "
        f"write lock — every other request was frozen for that long. The poll "
        f"loop must yield (cooperative_sleep), not park the shared OS thread."
    )


def test_uncontended_lock_still_works(tmp_path):
    """The fix must not change the ordinary path: no contention, no waiting."""
    with lock_mod.metadata_db_write_lock(lock_dir=str(tmp_path), timeout=5):
        pass
    assert (tmp_path / lock_mod.DEFAULT_LOCK_BASENAME).exists()


def test_timeout_still_raises_when_the_lock_never_frees(tmp_path):
    """Yielding must not cost us the timeout: a lock held longer than the
    caller's patience still has to raise rather than hang forever."""
    lock_path = str(tmp_path / lock_mod.DEFAULT_LOCK_BASENAME)

    with _CompetingWriter(lock_path, _HOLD_SECONDS):
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            with lock_mod.metadata_db_write_lock(
                lock_dir=str(tmp_path), timeout=0.2, poll_interval=0.05
            ):
                pass
        # It gave up on schedule rather than riding out the whole hold.
        assert time.monotonic() - started < _HOLD_SECONDS


def test_lock_poll_loop_does_not_use_a_blocking_sleep():
    """AST guard. The behavioural test above can only catch this on a machine
    with gevent installed; this pins the invariant unconditionally, and names
    the specific call that regresses it."""
    source = (REPO_ROOT / "cps/services/calibre_db_lock.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "sleep"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "time"
    ]

    assert not offenders, (
        f"time.sleep() at line(s) {offenders} in calibre_db_lock.py. This module "
        f"runs on request greenlets and gevent is not monkey-patched, so a stdlib "
        f"sleep freezes the whole app for the length of the wait. Use "
        f"cps.services.parallel.cooperative_sleep instead."
    )
