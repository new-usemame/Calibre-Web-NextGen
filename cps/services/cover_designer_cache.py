# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Disk cache for the cover designer's catalogue imagery.

Opening the designer asks for one thumbnail per arrangement and one sample per
lettering. Each of those is a real render — on a Calibre installation, a
``calibre-debug`` subprocess that takes a second or two — and they are identical
for every user and every book, forever. Rendering them once per process would
still mean a stall on the first open after each restart, and rendering them per
request would make the panel unusable on a machine with a hundred fonts.

The bytes therefore live in :mod:`cps.services.cover_preview_cache`, which
already owns this problem for cover tiles: sharded paths, a
write-then-fsync-then-rename that can never publish a half-written JPEG, and an
``atime`` touch on every hit. Sharing its root also puts these files under the
existing hourly LRU sweeper and its ``CWA_PREVIEW_CACHE_MAX_MB`` budget, so the
catalogue cannot grow without bound and there is no second cleanup service to
run.

The key is a hash of an explicitly namespaced payload, so a designer thumbnail
and a cover tile cannot be confused for one another; ``CACHE_VERSION`` is part of
it, which is how a change to how these images are drawn invalidates the old ones
instead of serving stale pictures of a superseded design.

Folding a burst of identical misses into one render
---------------------------------------------------

The panel asks for every thumbnail at once, so on a cold cache the same key is
missed several times within a few milliseconds and only one of those requests
should reach a renderer. The cover-tile path folds its burst with
``cover_preview_cache.stampede_lock``, a native :class:`threading.Lock`, and it
can afford to: it pads its image inline, on the request greenlet, so the greenlet
holding that lock never yields while it holds it.

This path cannot use it. A catalogue render is dispatched to the gevent
threadpool (``cover_preview._run_in_pool``), which *does* yield the calling
greenlet to the hub. CWNG serves every request from a greenlet and deliberately
does not call ``monkey.patch_all()``, so a second request's
``threading.Lock.acquire`` blocks the single OS thread the hub runs on: the first
request's render finishes on its worker thread, the hub is not running to deliver
the result, the first greenlet never resumes to release the lock, and the server
stops answering anything at all. That is not a theory — a cold container wedged
on the second concurrent thumbnail, and both a ``py-spy`` dump there and a
``faulthandler`` dump here show the one thread parked in ``Lock.acquire`` inside
this module with every pool worker idle, its render already done.

The single-flight lock here is therefore a ``gevent.lock.Semaphore`` when gevent
is importable — waiting on it parks the greenlet and leaves the hub free to run
everything else, including the delivery of the render that the waiter is waiting
for — and a plain :class:`threading.Semaphore` when it is not (unit runners and
other tools, where the waiter is a real thread and blocking it costs nothing).
It is also only ever an optimisation: a request that waits longer than
``SINGLE_FLIGHT_WAIT_SECONDS`` renders its own copy rather than waiting forever,
so a renderer that never returns costs one slow request instead of every future
request for that image.
"""
from __future__ import annotations

import hashlib
from typing import Callable, Dict, Optional, Tuple

try:  # pragma: no cover - gevent is always importable in the served runtime
    from gevent.lock import Semaphore as _Semaphore
except ImportError:  # pragma: no cover - unit runners and tools without gevent
    from threading import Semaphore as _Semaphore

from .. import logger
from . import cover_preview_cache

log = logger.create()

# Bumped whenever the catalogue imagery changes shape (new sample text, a
# different neutral scheme, a different size). Old entries then miss and are
# swept rather than being served as a picture of something that no longer exists.
CACHE_VERSION = "2"

# A 2:3 JPEG a few hundred pixels tall is tens of kilobytes. Anything past this
# is a renderer having a bad day, and it is served but not stored: the cache is
# for small images, and one runaway entry must not eat the whole budget.
MAX_IMAGE_BYTES = 512 * 1024

# Comfortably past the renderer's own 25-second ceiling, so in normal service the
# second request for an image gets the first one's bytes for free. Past it, the
# waiter stops waiting and renders the image itself: one duplicate subprocess is
# a much better outcome than a request that never answers.
SINGLE_FLIGHT_WAIT_SECONDS = 30.0

# One lock per cache key, created on first use and kept. Bounded by the
# catalogue — five arrangements plus at most ``MAX_FONT_CATALOGUE`` letterings,
# per renderer and size — so it settles at a few hundred entries per process.
_single_flight: Dict[str, object] = {}


def cache_key(kind: str, identifier: str, width: int, height: int, renderer: str) -> str:
    """The cache key for one catalogue image.

    *renderer* is part of the identity because Calibre and Pillow draw the same
    design differently: a thumbnail cached while Calibre was missing must not be
    served after it is installed.
    """
    payload = "cover-designer|%s|%s|%s|%sx%s|%s" % (
        CACHE_VERSION, kind, identifier, width, height, renderer or "none")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def load(key: str) -> Optional[bytes]:
    """The cached JPEG for *key*, or None. Never raises."""
    path = cover_preview_cache.cache_hit(key)
    if path is None:
        return None
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as error:  # pragma: no cover - unreadable cache entry
        log.debug("cover designer cache: could not read %s: %s", path, error)
        return None
    return data or None


def store(key: str, data: bytes) -> None:
    """Put *data* in the cache if it is small enough. Never raises."""
    if not data or len(data) > MAX_IMAGE_BYTES:
        return
    cover_preview_cache.write_to_cache(key, data)


def single_flight_lock(key: str):
    """The process-wide lock for *key* — the same object for every caller.

    Nothing guards the dictionary: ``setdefault`` is one C-level dictionary
    operation, so two threads racing to create the lock still come away holding
    the same one, and a greenlet cannot be suspended in the middle of it. That
    matters more than symmetry with the tile cache's master lock — a native lock
    taken on the way to the cooperative one would reintroduce exactly the
    hub-blocking acquire this module exists to avoid.
    """
    lock = _single_flight.get(key)
    if lock is None:
        lock = _single_flight.setdefault(key, _Semaphore(1))
    return lock


def cached(kind: str, identifier: str, width: int, height: int, renderer: str,
           render: Callable[[], bytes]) -> Tuple[bytes, bool]:
    """``(jpeg bytes, was_cached)`` for one catalogue image.

    The single-flight lock is taken only on a miss, and the cache is re-checked
    once inside it: on a cold designer open the first request through renders and
    the other nineteen wait for its bytes instead of starting nineteen
    subprocesses. Waiting is cooperative, so the request that is rendering can
    still be handed its result while the others wait — see the module docstring
    for what happens when it is not.
    """
    key = cache_key(kind, identifier, width, height, renderer)
    data = load(key)
    if data is not None:
        return data, True

    lock = single_flight_lock(key)
    holding = lock.acquire(timeout=SINGLE_FLIGHT_WAIT_SECONDS)
    try:
        if holding:
            data = load(key)
            if data is not None:
                return data, True
        else:
            # The key, not the identifier: this line must not carry a string the
            # request chose into the log.
            log.info("cover designer cache: %s was still rendering after %ss; "
                     "rendering it here too", key, SINGLE_FLIGHT_WAIT_SECONDS)
        data = render()
        store(key, data)
    finally:
        if holding:
            lock.release()
    return data, False
