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

They therefore go through :mod:`cps.services.cover_preview_cache`, which already
owns this problem for cover tiles: sharded paths, a write-then-fsync-then-rename
that can never publish a half-written JPEG, an ``atime`` touch on every hit, and
a per-key stampede lock so twenty parallel misses fold into one render. Sharing
its root also puts these files under the existing hourly LRU sweeper and its
``CWA_PREVIEW_CACHE_MAX_MB`` budget, so the catalogue cannot grow without bound
and there is no second cleanup service to run.

The key is a hash of an explicitly namespaced payload, so a designer thumbnail
and a cover tile cannot be confused for one another; ``CACHE_VERSION`` is part of
it, which is how a change to how these images are drawn invalidates the old ones
instead of serving stale pictures of a superseded design.
"""
from __future__ import annotations

import hashlib
from typing import Callable, Optional, Tuple

from .. import logger
from . import cover_preview_cache

log = logger.create()

# Bumped whenever the catalogue imagery changes shape (new sample text, a
# different neutral scheme, a different size). Old entries then miss and are
# swept rather than being served as a picture of something that no longer exists.
CACHE_VERSION = "1"

# A 2:3 JPEG a few hundred pixels tall is tens of kilobytes. Anything past this
# is a renderer having a bad day, and it is served but not stored: the cache is
# for small images, and one runaway entry must not eat the whole budget.
MAX_IMAGE_BYTES = 512 * 1024


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


def cached(kind: str, identifier: str, width: int, height: int, renderer: str,
           render: Callable[[], bytes]) -> Tuple[bytes, bool]:
    """``(jpeg bytes, was_cached)`` for one catalogue image.

    The stampede lock is taken only on a miss, and the cache is re-checked once
    inside it: on a cold designer open, the first request through renders and the
    other nineteen wait for its bytes instead of starting nineteen subprocesses.
    """
    key = cache_key(kind, identifier, width, height, renderer)
    data = load(key)
    if data is not None:
        return data, True
    with cover_preview_cache.stampede_lock(key):
        data = load(key)
        if data is not None:
            return data, True
        data = render()
        store(key, data)
    return data, False
