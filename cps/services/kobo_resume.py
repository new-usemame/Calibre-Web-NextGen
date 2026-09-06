# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional Kobo resume conversion: bounded admission, off-hub I/O, no writes."""
import logging
import math
import os
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path

from .parallel import cooperative_sleep

log = logging.getLogger(__name__)
# A stalled filesystem can occupy at most two daemon workers. There is no queue,
# and a timed-out worker retains its permit until it actually finishes.
_SLOTS = threading.BoundedSemaphore(2)


def _resume_timeout():
    try:
        value = float(os.environ.get('CWA_KOBO_RESUME_TIMEOUT_SECONDS', '0.05'))
        if math.isfinite(value) and value > 0:
            return value
    except ValueError:
        pass
    log.warning('Invalid CWA_KOBO_RESUME_TIMEOUT_SECONDS; using 0.05 seconds')
    return 0.05


RESUME_TIMEOUT_SECONDS = _resume_timeout()
_CACHE_MAX_ENTRIES = 256
_CACHE_TTL_SECONDS = 300
_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()


def exact_resume(book_id, source, kind, value):
    deadline = time.monotonic() + RESUME_TIMEOUT_SECONDS
    if kind != 'KoboSpan' or not source or not value:
        return None
    from .. import config
    # Only in-memory settings here: no stat, path resolution, or DB lookup on
    # the hub. Scope by library/storage configuration as well as the full span.
    key = (getattr(config, 'config_calibre_dir', None),
           getattr(config, 'config_calibre_split', False),
           getattr(config, 'config_calibre_split_dir', None),
           getattr(config, 'config_use_google_drive', False),
           book_id, source, kind, value)
    if not _CACHE_LOCK.acquire(blocking=False):
        return None
    try:
        cached = _CACHE.get(key)
        if cached:
            expires, exact = cached
            if time.monotonic() < expires:
                return exact.copy()
            del _CACHE[key]
    finally:
        _CACHE_LOCK.release()
    if time.monotonic() >= deadline:
        return None
    if not _SLOTS.acquire(blocking=False):
        return None
    done = threading.Event()
    result = []

    def worker():
        try:
            exact = _resolve(book_id, source, kind, value)
            if exact and exact.get('cfi') and exact.get('epub_sha256'):
                # Publish even after the requesting greenlet has timed out.
                # Never hold this lock across I/O. Expiry is from completion,
                # not last access; hot entries cannot live indefinitely.
                with _CACHE_LOCK:
                    _CACHE[key] = (time.monotonic() + _CACHE_TTL_SECONDS, exact.copy())
                    _CACHE.move_to_end(key)
                    while len(_CACHE) > _CACHE_MAX_ENTRIES:
                        _CACHE.popitem(last=False)
            result.append(exact)
        except Exception:
            log.debug('Could not resolve exact reader resume for book %s', book_id, exc_info=True)
        finally:
            done.set()
            _SLOTS.release()

    try:
        threading.Thread(target=worker, name='kobo-resume', daemon=True).start()
    except Exception:
        _SLOTS.release()
        log.debug('Could not start reader resume conversion', exc_info=True)
        return None
    while not done.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            log.debug('Exact reader resume deadline exceeded for book %s', book_id)
            return None
        cooperative_sleep(min(0.001, remaining))
    return result[0] if result else None


def _resolve(book_id, source, kind, value):
    # No SQLAlchemy session is shared across threads. Both metadata lookup and
    # file access happen here, never on the request/hub thread.
    from .. import config
    from .kobo_position import _resume_snapshot
    if config.config_use_google_drive or not config.config_calibre_dir:
        return None
    metadata = Path(config.config_calibre_dir) / 'metadata.db'
    connection = sqlite3.connect(metadata.resolve().as_uri() + '?mode=ro', uri=True, timeout=0)
    try:
        row = connection.execute(
            "SELECT b.path, d.name FROM books b JOIN data d ON d.book=b.id "
            "WHERE b.id=? AND d.format='EPUB' LIMIT 1", (book_id,),
        ).fetchone()
    finally:
        connection.close()
    if not row:
        return None
    root = Path(config.get_book_path()).resolve()
    path = (root / row[0] / (row[1] + '.epub')).resolve()
    if not path.is_relative_to(root):
        return None
    snapshot = _resume_snapshot(path, source, kind, value)
    if snapshot:
        cfi, fingerprint = snapshot
        return {'cfi': cfi, 'epub_sha256': fingerprint}
    return None
