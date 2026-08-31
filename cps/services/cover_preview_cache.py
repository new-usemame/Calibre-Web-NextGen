# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""On-disk cache for padded cover-preview JPEGs.

Cache layout::

    /config/.cwa-preview-cache/<aa>/<rest>.jpg

where ``<aa><rest>`` = ``sha256(book_id|cover_mtime|preset|fill|color)``
truncated to 16 hex chars. The 2-char prefix is the git-style sharding so
any single directory holds at most a few thousand files instead of
millions — keeps ``readdir()`` cheap and the LRU sweeper's stat-walk
predictable.

The cache key includes ``cover_mtime`` so an updated cover invalidates the
cache automatically without explicit purging. ``user_id`` is deliberately
NOT in the key: two users whose effective settings resolve to the same
``(preset, fill, color)`` triple should share the rendered tile — that's
the whole point of pulling resolution out of the rendering path.

Stampede protection: when N simultaneous requests miss the same cache
key, only one renders; the rest block on a per-key
:class:`threading.Lock` and serve the result. Critical for cold-cache
first-page-load bursts where 20+ tiles miss in parallel and we don't want
20 ImageMagick subprocesses fighting for the same render.

Design notes / non-obvious choices
----------------------------------

* **Atomic write-then-rename with ``fsync``.** A partial file produced by
  a crash mid-write is not just useless — it's actively harmful because
  ``cache_hit()`` would return its path and we'd serve truncated JPEG
  bytes to the browser. ``fsync(tmp.fileno())`` before ``rename()``
  forces the dirty pages to durable storage so a power-loss event can't
  resurrect an empty file under a real name. The temp file lives in the
  same directory as the target so ``rename()`` is a same-filesystem
  atomic op (cross-FS ``rename()`` falls back to copy and isn't atomic).

* **Stampede-lock dict has its own master lock.** A naive
  ``defaultdict(Lock)`` has a race window: thread A enters ``__missing__``
  and creates Lock1, thread B enters ``__missing__`` simultaneously and
  creates Lock2, both end up in the dict transiently. CPython's GIL
  *usually* serializes the dict insertion enough to mask this, but
  ``threading.Lock()`` construction can release the GIL and the
  defaultdict factory is not atomic across the get-or-set boundary. The
  master-lock dance is explicit and correct under contention.

* **``CACHE_ROOT`` is a module-level variable, not a computed constant.**
  Tests need to monkeypatch it to ``tmp_path``; production reads
  ``/config/.cwa-preview-cache`` directly. Keeping it as a simple
  assignment (not ``Path.from_env(...)`` or similar) makes
  ``monkeypatch.setattr(mod, "CACHE_ROOT", ...)`` the obvious
  isolation pattern.

* **``atime`` touch on hit + ``noatime`` resilience.** The LRU sweeper
  (Task 3) walks the cache and evicts oldest-``atime`` first. We
  explicitly ``utime(None)`` on every hit so recently-used tiles aren't
  evicted. On filesystems mounted with ``noatime`` (common on Linux
  servers for performance), ``atime`` won't update on read — but
  ``utime(None)`` forces both ``atime`` and ``mtime`` to "now" via an
  explicit syscall, so the LRU heuristic still works. We log a one-time
  warning if we detect ``noatime`` so the operator knows the eviction
  signal is coming from explicit ``utime()`` rather than passive reads.

* **All filesystem operations are ``OSError``-tolerant.** Permission
  denied, disk full, NFS hiccups — we degrade gracefully (return
  ``None``) rather than 500 the request. The endpoint can then serve
  the freshly-rendered bytes directly without caching them.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Dict, Optional

from cps import constants

log = logging.getLogger(__name__)

# Default production cache root. Module-level (not constant) so tests can
# `monkeypatch.setattr(mod, "CACHE_ROOT", tmp_path / ".cwa-preview-cache")`.
CACHE_ROOT: Path = Path(constants.CONFIG_DIR) / ".cwa-preview-cache"

# 16 hex chars = 64 bits of key space. Birthday-collision probability is
# ~1 in 2^32 at 4 billion entries; we're nowhere near that scale
# (rough upper bound: 100k books × 5 presets × 3 fills × 4 colors = 6M).
_KEY_HEX_LEN: int = 16

# Per-key Lock dict. Protected by `_stampede_master` for thread-safe
# get-or-insert. We never evict entries — the dict grows to one Lock per
# unique cache key ever requested, which at our scale is bounded by the
# entry count above and is fine in-process. If memory ever became an
# issue, a periodic GC by-popularity would be the fix; not needed yet.
_stampede_locks: Dict[str, threading.Lock] = {}
_stampede_master: threading.Lock = threading.Lock()

# One-shot flag so we only log the `noatime` warning the first time we
# notice we can't observe atime advancing. Guarded by `_stampede_master`
# (any lock would do; reusing avoids adding a second one).
_noatime_warned: bool = False


def cache_key(
    book_id: int,
    cover_mtime: int,
    preset: str,
    fill: str,
    color: Optional[str],
) -> str:
    """Compute the deterministic 16-hex-char cache key.

    Inputs are the natural identity of a rendered tile:
    ``(book × cover_mtime × preset × fill × color)``. ``cover_mtime``
    being part of the key means updating the cover file invalidates the
    cache automatically — no explicit purge needed.

    ``color`` of ``None`` and ``""`` are normalized to the same payload
    so they collide (both mean "no manual color"); see the
    matching test.

    ``user_id`` is intentionally absent — two users with the same
    effective settings share the tile.
    """
    payload = f"{book_id}|{cover_mtime}|{preset}|{fill}|{color or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:_KEY_HEX_LEN]


def cache_path(key: str) -> Path:
    """Return the on-disk path for ``key``.

    Does NOT create the parent directory — callers that write do that
    lazily; callers that read don't need it.
    """
    # Defensive: key must be at least the prefix length, else our path
    # layout breaks. Truncated/invalid keys are a programming error,
    # not a runtime input — fail loudly.
    if len(key) <= 2:
        raise ValueError(f"cache key too short for 2-char prefix layout: {key!r}")
    return CACHE_ROOT / key[:2] / f"{key[2:]}.jpg"


def cache_hit(key: str) -> Optional[Path]:
    """Return the path if the cache has this key, else ``None``.

    On a hit, updates the file's ``atime``/``mtime`` to "now" so the
    LRU sweeper sees it as recently-used. Failures during ``utime``
    (e.g. read-only filesystem, permission denied) are swallowed — a
    failed timestamp update is not worth turning a cache hit into a
    miss.
    """
    global _noatime_warned

    path = cache_path(key)
    try:
        if not path.is_file():
            return None
    except OSError:
        # Permission denied, NFS hiccup, etc. — treat as miss.
        return None

    # Touch atime/mtime. utime(None) sets both to "now" via the syscall,
    # which works even on `noatime`-mounted filesystems where a passive
    # read wouldn't bump atime.
    try:
        before = path.stat().st_atime
        os.utime(path, None)
        after = path.stat().st_atime
        # On noatime mounts the kernel may quietly ignore the atime
        # portion (mtime still moves though, which is what the sweeper
        # can also key off if needed). Warn once so the operator knows.
        if after <= before and not _noatime_warned:
            with _stampede_master:
                if not _noatime_warned:
                    log.warning(
                        "cover-preview cache: atime is not advancing on %s "
                        "(filesystem likely mounted noatime). LRU sweeper "
                        "will fall back to mtime.",
                        path.parent,
                    )
                    _noatime_warned = True
    except OSError:
        # Stat or utime failed — still return the hit; the sweeper can
        # cope with stale timestamps.
        pass

    return path


def write_to_cache(key: str, jpeg_bytes: bytes) -> Optional[Path]:
    """Atomically place ``jpeg_bytes`` at the key's cache path.

    Returns the resolved path on success, ``None`` on any filesystem
    error (permission denied, disk full, etc.). Callers should fall
    back to serving the bytes uncached when ``None`` is returned —
    a render that can't be persisted is still a valid response.

    Implementation: tempfile in the same directory, write + flush +
    ``fsync``, then ``os.rename`` to the final path. ``rename`` is
    atomic on POSIX when source and destination are on the same
    filesystem (which they are by construction here).
    """
    path = cache_path(key)
    tmp_path: Optional[str] = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # NamedTemporaryFile with delete=False gives us a path we can
        # rename. Prefix `.tmp-` makes orphans easy to spot if the
        # process dies between write and rename (the sweeper can
        # garbage-collect them on a future pass).
        with tempfile.NamedTemporaryFile(
            dir=str(path.parent),
            prefix=".tmp-",
            suffix=".jpg",
            delete=False,
        ) as tmp:
            tmp_path = tmp.name
            tmp.write(jpeg_bytes)
            tmp.flush()
            # fsync the file's pages to durable storage. Without this,
            # a power-loss event after rename could leave a zero-byte
            # file under a real cache name — which cache_hit would
            # then happily return.
            try:
                os.fsync(tmp.fileno())
            except OSError:
                # Some filesystems (tmpfs, certain network mounts)
                # don't support fsync. Not worth failing the write —
                # the durability cost is "cache may need re-render
                # after a crash", which is exactly what we recover
                # from anyway.
                pass
        os.rename(tmp_path, str(path))
        tmp_path = None  # transferred ownership; nothing to clean up
        return path
    except OSError as e:
        log.debug("write_to_cache failed for key=%s: %s", key, e)
        return None
    finally:
        # Belt and braces: if we created a tempfile but never renamed
        # it (exception between mkstemp and rename), clean up. The
        # sweeper would eventually get it via the .tmp- prefix, but
        # not leaking is better.
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def stampede_lock(key: str) -> threading.Lock:
    """Return the process-wide :class:`threading.Lock` for ``key``.

    Use as a context manager around the cache-miss render path so N
    simultaneous misses on the same tile fold into a single render::

        with stampede_lock(key):
            hit = cache_hit(key)         # re-check; some other thread
            if hit is not None:          # may have filled it while we
                return serve(hit)        # waited
            rendered = engine.render(...)
            write_to_cache(key, rendered)
            return serve_bytes(rendered)

    Same key always returns the same Lock instance. Different keys
    return different Lock instances so unrelated misses don't serialize.
    """
    # The master lock makes the get-or-create atomic across threads.
    # Without it, two threads can both observe `key not in dict` and
    # both create a Lock, racing to be the one stored. CPython's GIL
    # usually masks this but `threading.Lock()` construction can
    # release the GIL on contention, and the defaultdict factory
    # protocol is not atomic across get-or-set. Explicit master lock
    # is the only correct primitive.
    with _stampede_master:
        lock = _stampede_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _stampede_locks[key] = lock
        return lock
