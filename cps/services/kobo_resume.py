# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional Kobo resume conversion: bounded admission, off-hub I/O, no writes."""
import logging
import sqlite3
import threading
import time
from pathlib import Path

from .parallel import cooperative_sleep

log = logging.getLogger(__name__)
# A stalled filesystem can occupy at most two daemon workers. There is no queue,
# and a timed-out worker retains its permit until it actually finishes.
_SLOTS = threading.BoundedSemaphore(2)
RESUME_TIMEOUT_SECONDS = 0.05


def exact_resume(book_id, source, kind, value):
    if kind != 'KoboSpan' or not source or not value:
        return None
    if not _SLOTS.acquire(blocking=False):
        return None
    done = threading.Event()
    result = []
    deadline = time.monotonic() + RESUME_TIMEOUT_SECONDS

    def worker():
        try:
            result.append(_resolve(book_id, source, kind, value))
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
