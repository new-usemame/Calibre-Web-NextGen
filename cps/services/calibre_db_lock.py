# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Process-shared advisory lock for metadata.db writes.

Two actors write to /calibre-library/metadata.db: the Flask app (Edit
Book / cover-picker commits via SQLAlchemy) and the ingest_processor
subprocess (calibredb add). When the library lives on a filesystem
with weak POSIX advisory-lock support — mergerfs, SMB, NFS, Docker
Desktop's virtiofs — SQLite's own fcntl-based locking can silently
fail, surfacing as ``apsw.BusyError: database is locked`` from
calibredb and stranding the import in /processed_books/failed.

This module provides a ``metadata_db_write_lock`` context manager that
both actors acquire before touching metadata.db. The lock lives in
/config (a local Docker volume) where fcntl flock always works, so
coordination is reliable even when the library volume is not.

The lock is intentionally exclusive — concurrency at this layer is
already serial because there's only one inotify watcher, one ingest
worker, and the web app's metadata.db writes are infrequent. The lock
costs only the duration of a single calibredb add or a single
SQLAlchemy commit.

Related: fork issue #192, upstream CWA #1082.
"""

from __future__ import annotations

import errno
import os
import time
from contextlib import contextmanager

try:
    from .parallel import cooperative_sleep
except ImportError:  # pragma: no cover — exercised by the standalone loader below
    # This file is also loaded as a STANDALONE module, with no package context:
    # tests/unit/test_metadata_db_write_coordination.py does it on purpose, via
    # spec_from_file_location, to get at the lock without paying for cps/'s
    # Flask init. Production never takes this branch — both real importers
    # (cps.editbooks / cps.api.browse, and ingest_processor's lazy
    # `from cps.services.calibre_db_lock import …`) have the package.
    #
    # Fall back to the exact primitive parallel.cooperative_sleep wraps, so the
    # standalone module behaves the same rather than silently reverting to the
    # blocking sleep this module exists to avoid.
    try:
        from gevent import sleep as cooperative_sleep
    except ImportError:
        cooperative_sleep = time.sleep

try:
    import fcntl

    HAS_FCNTL = True
except ImportError:  # pragma: no cover — Windows
    fcntl = None
    HAS_FCNTL = False


DEFAULT_LOCK_DIR = "/config"
DEFAULT_LOCK_BASENAME = ".cwa-metadata-write.lock"
DEFAULT_TIMEOUT_SECONDS = 120.0


def _resolve_lock_path(lock_dir: str | None) -> str:
    base = lock_dir or os.environ.get("CWA_METADATA_LOCK_DIR") or DEFAULT_LOCK_DIR
    return os.path.join(base, DEFAULT_LOCK_BASENAME)


@contextmanager
def metadata_db_write_lock(
    lock_dir: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    poll_interval: float = 0.1,
):
    """Acquire an exclusive process-shared lock for metadata.db writes.

    Parameters
    ----------
    lock_dir
        Directory to place the lock file in. Defaults to /config, the
        local Docker volume that always supports fcntl. Override via
        the ``CWA_METADATA_LOCK_DIR`` env var or for tests.
    timeout
        Seconds to wait for the lock before raising ``TimeoutError``.
        Default 120s — long enough to ride out the kindle-epub-fixer
        on a 40MB book on slow storage.
    poll_interval
        Seconds between non-blocking flock attempts. Lower is more
        responsive but burns more CPU. Default 0.1s is a fine balance.
    """
    if not HAS_FCNTL:
        # Windows / no-fcntl platforms — no-op fallback. The lock is
        # advisory anyway; on platforms without fcntl, the deployment
        # is not a Docker container where the contention matters.
        yield
        return

    lock_path = _resolve_lock_path(lock_dir)

    # The directory must exist. We deliberately do NOT create the lock
    # directory on demand — that would mask a misconfiguration (e.g.
    # /config not mounted). If the directory is missing, the
    # ENOENT-from-open below surfaces clearly.
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Timed out after {timeout:.1f}s waiting for "
                        f"metadata.db write lock at {lock_path}. Another "
                        f"writer (likely calibredb during ingest) is "
                        f"holding it. If this repeats, check that the "
                        f"ingest_processor isn't stuck."
                    ) from e
                # Cooperative: this poll loop runs on a request greenlet, and
                # it has no other yield point. A stdlib sleep here parks the
                # one OS thread every greenlet shares, so the whole app stops
                # answering for as long as the other writer holds the lock —
                # up to `timeout` (120s) while ingest runs a calibredb add.
                cooperative_sleep(poll_interval)

        try:
            # Record holder PID for diagnostics. Best-effort.
            try:
                os.ftruncate(fd, 0)
                os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
            except OSError:
                pass
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
