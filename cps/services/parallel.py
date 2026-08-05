# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Gevent-aware parallel fan-out for work that runs on a request greenlet.

Why this module exists (fork #954)
----------------------------------
``cps/server.py`` serves requests with ``gevent.pywsgi`` and we deliberately
do NOT call ``gevent.monkey.patch_all()`` — the s6 services fork subprocesses
and patching breaks them. Consequences that are easy to forget:

* Every HTTP request is a greenlet, and every greenlet shares ONE OS thread.
* Anything that blocks that thread blocks *the hub*, so no other request is
  served until it returns. It is not "one slow request", it is a frozen app.
* ``concurrent.futures`` is exactly such a blocker. ``as_completed()``,
  ``Future.result()`` and the ``Executor`` context-manager exit (which calls
  ``shutdown(wait=True)``) all park the thread on a ``threading.Condition``
  /``Event``, which gevent cannot preempt. Confirmed with py-spy: the main
  thread sits in ``concurrent.futures._base.wait`` while workers run.

That is what made "Change cover" take the whole app down: the picker fanned
~16 metadata providers out through a ``ThreadPoolExecutor`` and waited on
``as_completed``, so every other user hung for the slowest provider's
timeout. Measured on cwn-local pre-fix: a 30ms book page took 11.4s.

The jobs themselves still need REAL OS threads — providers do blocking
socket reads via ``requests``, and unpatched gevent cannot make those
cooperative. So the answer is not "use greenlets", it is "keep the worker
threads, but wait for them the gevent way":
``gevent.threadpool.ThreadPool`` hands back greenlets whose ``join``/``get``
yield through the hub, while the work still runs on real threads (urllib3's
socket reads and ImageMagick's C code both release the GIL, so throughput is
unchanged).

``cps/services/cover_preview.py`` learned this the hard way for its Wand
pool; this module is the shared, reusable form so the next fan-out does not
have to rediscover it. ``tests/unit/test_request_fanout_gevent_responsiveness.py``
guards the request-path callers against regressing to stdlib futures.

Outside gevent (pytest, the tornado fallback in ``server.py``) there is no
hub to protect, so we use the stdlib executor and behave identically.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import time
from typing import Any, Callable, Iterable, Iterator, Optional, Tuple

try:  # pragma: no cover - exercised implicitly by whichever branch runs
    from gevent.threadpool import ThreadPool as _GeventThreadPool
    _HAVE_GEVENT_POOL = True
except ImportError:  # pragma: no cover - tornado fallback / bare test runners
    _GeventThreadPool = None  # type: ignore[assignment]
    _HAVE_GEVENT_POOL = False

try:  # pragma: no cover - exercised implicitly by whichever branch runs
    from gevent import sleep as _gevent_sleep
    _HAVE_GEVENT_SLEEP = True
except ImportError:  # pragma: no cover - tornado fallback / bare test runners
    _gevent_sleep = None  # type: ignore[assignment]
    _HAVE_GEVENT_SLEEP = False


def cooperative_sleep(seconds: float) -> None:
    """Sleep without freezing the hub — the wait-and-retry counterpart to
    ``fan_out``/``run_blocking``.

    Those two cover blocking *work*. This covers blocking *waiting*, which
    reaches the app through a different door: a poll loop that retries some
    contended resource. ``time.sleep`` parks the one OS thread every greenlet
    shares, and a retry loop has no other yield point, so the hub does not run
    again until the whole wait is over — the stall is the full wait, not one
    poll interval.

    That is not theoretical. ``services/calibre_db_lock.py`` polls for the
    metadata.db write lock for up to 120s while the ingest processor holds it
    across a ``calibredb add``, so on the pre-fix code any metadata write that
    landed during an ingest froze every other request for the whole wait.

    Under gevent the same pause is a scheduling point, so other requests keep
    being served while this greenlet waits. Outside gevent (pytest, the tornado
    fallback in ``server.py``, the ingest subprocess, which imports this module
    lazily and is not a greenlet at all) there is no hub to protect and the
    stdlib sleep behaves identically.
    """
    if _HAVE_GEVENT_SLEEP:
        _gevent_sleep(seconds)
    else:  # pragma: no cover - only on installs without gevent
        time.sleep(seconds)


@dataclasses.dataclass
class FanOutResult:
    """One finished job. ``exception`` is set instead of ``value`` when the
    job raised; a failing job never cancels its siblings, because callers
    report per-source status and must still show everything that worked.

    ``elapsed_ms`` is milliseconds from the start of the whole fan-out to the
    moment THIS job returned, and it is stamped inside the worker rather than
    read when the caller consumes the result. That distinction is load-bearing:
    there are more providers (16) than workers (5), so jobs complete in waves
    and the hub hands several finished jobs back in a single tick. A clock read
    in the consumer loop gives every job in a wave the same reading — observed
    live as 10 of 16 providers all reporting exactly 5316ms — which would make
    the picker's per-provider timings useless for spotting a slow source.
    """

    key: Any
    value: Any = None
    exception: Optional[BaseException] = None
    elapsed_ms: int = 0


def fan_out(
    jobs: Iterable[Tuple[Any, Callable[[], Any]]],
    *,
    max_workers: int,
) -> Iterator[Tuple[Any, FanOutResult]]:
    """Run ``jobs`` on a thread pool, yielding ``(key, FanOutResult)`` as each
    completes — ``concurrent.futures.as_completed`` semantics, minus the hub
    stall.

    ``jobs`` is an iterable of ``(key, zero-arg callable)``. ``key`` is opaque
    and comes straight back on the result, so callers can attribute a result
    to its provider without a side table.

    Results are yielded in completion order (not submission order) so callers
    can time each job individually; the picker shows a per-provider duration
    and would otherwise report the whole fan-out's elapsed time for every one.

    Exceptions raised by a job are captured on its ``FanOutResult`` rather
    than propagated.
    """
    jobs = list(jobs)
    if not jobs:
        return

    workers = max(1, min(max_workers, len(jobs)))
    fanout_started = time.monotonic()

    if _HAVE_GEVENT_POOL:
        yield from _fan_out_gevent(jobs, workers, fanout_started)
    else:
        yield from _fan_out_stdlib(jobs, workers, fanout_started)


def run_blocking(fn: Callable[[], Any]) -> Any:
    """Run ONE blocking callable without stalling the gevent hub.

    ``fan_out`` covers the many-jobs case. This is the single-job one, and it
    exists because the #954 root cause reaches the app through plain
    sequential calls too, not only through fan-outs (fork #1111):

    * ``cps/editbooks.py`` downloads the cover inline on the edit-book POST,
      so a slow cover CDN froze every other user's page load for up to the
      ``requests`` read timeout of 30s.
    * ``cps/search_metadata.py`` runs a single provider inline for the
      per-provider preview search, which blocks on the provider's own socket
      reads for as long as that provider takes.

    Neither is a fan-out, so neither was reachable by ``fan_out`` — but both
    sit on the request greenlet, which is the only thing that matters here.

    The return value is passed through and exceptions are re-raised in the
    caller (with the worker frame kept in the traceback), so wrapping a call
    site does not change its control flow.

    Uses ONE bounded, shared pool rather than a pool per call. A pool per call
    is unbounded native-thread admission: N concurrent cover saves means N OS
    threads and N outbound sockets, so a burst trades the hub freeze for
    thread/FD exhaustion. Bounding it does not reintroduce the freeze, because
    ``spawn()`` on a saturated gevent pool waits COOPERATIVELY — measured at a
    296ms worst-case hub gap against a 211ms idle baseline while six 1s jobs
    queued through two slots. The submitting request queues; everyone else is
    still served. That is backpressure, which is what we want under load.
    """
    if not _HAVE_GEVENT_POOL:
        # gevent is not installed at all, so there is no hub to protect and
        # the thread hop would only add latency.
        return fn()

    pool = _offload_pool()
    if pool is None:
        # Already off the hub's thread — e.g. a provider reached through
        # fan_out, whose workers are real OS threads. Blocking here harms
        # nobody, and hopping again would only add a thread.
        return fn()

    return pool.spawn(fn).get()


# Bound on concurrent offloaded blocking calls. These are network waits, so
# the number is about capping OS threads and sockets under a burst, not about
# CPU. Past it, requests queue cooperatively instead of spawning more threads.
_MAX_OFFLOAD_WORKERS = 16

_OFFLOAD_POOL = None
_OFFLOAD_POOL_THREAD = None


def _offload_pool():
    """The shared pool, or ``None`` when the caller is not on the thread that
    owns it.

    A ``gevent.threadpool.ThreadPool`` belongs to the hub of the thread that
    created it, so it must not be driven from another OS thread. Returning
    ``None`` for that case lets ``run_blocking`` degrade to a direct call,
    which is correct there: off the hub's thread nothing is being frozen.
    """
    global _OFFLOAD_POOL, _OFFLOAD_POOL_THREAD
    import threading

    if _OFFLOAD_POOL is None:
        _OFFLOAD_POOL = _GeventThreadPool(_MAX_OFFLOAD_WORKERS)
        _OFFLOAD_POOL_THREAD = threading.get_ident()
    return _OFFLOAD_POOL if _OFFLOAD_POOL_THREAD == threading.get_ident() else None


def _timed(fn, fanout_started):
    """Wrap ``fn`` so the finish time is captured in the worker, the instant
    the job returns — see FanOutResult.elapsed_ms for why the consumer's
    clock is not good enough."""
    def _run():
        try:
            return fn(), _elapsed_ms(fanout_started)
        except BaseException as exc:
            # Carry the elapsed time out with the failure: callers report a
            # duration for failed providers too.
            exc._fan_out_elapsed_ms = _elapsed_ms(fanout_started)
            raise
    return _run


def _elapsed_ms(since: float) -> int:
    return int((time.monotonic() - since) * 1000)


def _result_from(key, value, exception, fanout_started) -> FanOutResult:
    if exception is not None:
        return FanOutResult(
            key=key,
            exception=exception,
            elapsed_ms=getattr(exception, "_fan_out_elapsed_ms", _elapsed_ms(fanout_started)),
        )
    payload, elapsed_ms = value
    return FanOutResult(key=key, value=payload, elapsed_ms=elapsed_ms)


def _fan_out_gevent(jobs, workers, fanout_started) -> Iterator[Tuple[Any, FanOutResult]]:
    """Production path. ``pool.spawn`` returns a greenlet; ``gevent.iwait``
    yields greenlets as they finish and yields to the hub while waiting, so
    other requests keep being served for the whole fan-out."""
    import gevent

    pool = _GeventThreadPool(workers)
    try:
        greenlets = {}
        for key, fn in jobs:
            greenlets[pool.spawn(_timed(fn, fanout_started))] = key

        for greenlet in gevent.iwait(list(greenlets)):
            key = greenlets[greenlet]
            yield key, _result_from(key, greenlet.value, greenlet.exception, fanout_started)
    finally:
        # Every greenlet has completed by the time iwait is exhausted, so this
        # only reaps the idle worker threads. On an early exit (a caller that
        # stops consuming the generator) it also stops the pool rather than
        # leaking its threads.
        pool.kill()


def _fan_out_stdlib(jobs, workers, fanout_started) -> Iterator[Tuple[Any, FanOutResult]]:
    """No gevent (tests, tornado fallback): no hub to protect, so the stdlib
    executor is safe and semantically identical."""
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {pool.submit(_timed(fn, fanout_started)): key for key, fn in jobs}
        for future in concurrent.futures.as_completed(futures):
            key = futures[future]
            yield key, _result_from(
                key,
                None if future.exception() else future.result(),
                future.exception(),
                fanout_started,
            )
    finally:
        pool.shutdown(wait=False)
