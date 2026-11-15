# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional Kobo resume conversion: bounded admission, off-hub I/O, no writes."""
import logging
import hashlib
import io
import math
import os
import posixpath
import sqlite3
import stat
import threading
import time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import unquote
import zipfile

from .koreader_position import KOREADER_LOCATION_TYPE
from .parallel import cooperative_sleep
from .kobo_position import (
    MAX_RESUME_ARCHIVE_BYTES,
    MAX_RESUME_DIRECTORY_ENTRIES,
    MAX_RESUME_HREF_CHARS,
    _resume_directory_start,
)

log = logging.getLogger(__name__)
# A stalled filesystem can occupy at most two daemon workers. There is no queue,
# and a timed-out worker retains its permit until it actually finishes.
_SLOTS = threading.BoundedSemaphore(2)
_WARM_SLOT = threading.BoundedSemaphore(1)


def _resume_timeout(default=0.05):
    try:
        value = float(os.environ.get('CWA_KOBO_RESUME_TIMEOUT_SECONDS', str(default)))
        if math.isfinite(value) and value > 0:
            return value
    except ValueError:
        pass
    log.warning('Invalid CWA_KOBO_RESUME_TIMEOUT_SECONDS; using %s seconds', default)
    return default


RESUME_TIMEOUT_SECONDS = _resume_timeout()
# A proven KEPUB span may need cold alignment after a restart or cache eviction.
# Measured cold resolution of a 353 KB EPUB took 119–154 ms, beyond the generic
# 50 ms budget. Keep a bounded quarter-second wait only for this bridge path;
# an explicit operator timeout still overrides both defaults.
KEPUB_RESUME_TIMEOUT_SECONDS = _resume_timeout(default=0.25)
_CACHE_MAX_ENTRIES = 256
_CACHE_TTL_SECONDS = 300
_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()


def chapter_resume(epub_path, source, kind, value, chapter_percent):
    """Return one fingerprinted chapter approximation without blocking the hub."""
    deadline = time.monotonic() + RESUME_TIMEOUT_SECONDS
    if (epub_path is None or kind != 'KoboSpan' or not source or not value
            or isinstance(chapter_percent, bool)
            or not isinstance(chapter_percent, (int, float))
            or not 0.0 <= float(chapter_percent) <= 100.0):
        return None
    if not _SLOTS.acquire(blocking=False):
        return None
    done = threading.Event()
    result = []

    def worker():
        try:
            result.append(_chapter_snapshot(epub_path, source, chapter_percent))
        except Exception:
            log.debug('Could not resolve chapter resume', exc_info=True)
        finally:
            done.set()
            _SLOTS.release()

    try:
        threading.Thread(target=worker, name='kobo-chapter-resume', daemon=True).start()
    except Exception:
        _SLOTS.release()
        return None
    while not done.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        cooperative_sleep(min(0.001, remaining))
    return result[0] if result else None


def _chapter_snapshot(epub_path, source, chapter_percent):
    """Admit a Kobo chapter-local approximation against one stable EPUB.

    KEPUB-only span ids normally do not exist in the source EPUB. The browser
    can still map the reported chapter progression after it rechecks this
    whole-archive fingerprint.
    """
    if not isinstance(source, str) or len(source) > MAX_RESUME_HREF_CHARS:
        return None
    href = unquote(source).lstrip('/')
    if (not href or '\\' in href or '#' in href
            or href != posixpath.normpath(href)
            or any(part in ('', '.', '..') for part in href.split('/'))):
        return None
    path = Path(epub_path)
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_RESUME_ARCHIVE_BYTES:
            return None
        raw = stream.read(MAX_RESUME_ARCHIVE_BYTES + 1)
        after = os.fstat(stream.fileno())
    identity = lambda item: (
        item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns,
    )
    if (len(raw) > MAX_RESUME_ARCHIVE_BYTES or identity(before) != identity(after)
            or identity(after) != identity(path.stat())):
        return None
    _resume_directory_start(raw)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = set()
        for info in archive.infolist():
            name = info.filename.removesuffix('/')
            if (not name or name.startswith('/') or '\\' in name
                    or info.filename != info.orig_filename
                    or any(part in ('', '.', '..') for part in name.split('/'))
                    or name in names or len(names) >= MAX_RESUME_DIRECTORY_ENTRIES):
                return None
            names.add(name)
        if href not in names:
            return None
    return {
        'chapter_href': href,
        'chapter_progression': float(chapter_percent) / 100.0,
        'epub_sha256': hashlib.sha256(raw).hexdigest(),
    }


# Device locators this module can place exactly in the library EPUB: a Kobo
# span (``source`` = chapter href) and a KOReader XPointer (``source`` = the
# partial MD5 of the file it was reported from, see ``koreader_position``).
_EXACT_KINDS = ('KoboSpan', KOREADER_LOCATION_TYPE)


def exact_resume(book_id, source, kind, value, *, wait=True, kepub_identity=None):
    """``{'cfi', 'epub_sha256'}`` for a device locator, or ``None``.

    With ``wait=False`` the conversion only starts (when a slot is free) so a
    later call finds it cached; nothing is waited for or returned. Proven
    KEPUB locators revalidate the files on every call instead of caching the
    result, so a replaced book or revoked proof cannot reuse an exact place.
    """
    budget = (KEPUB_RESUME_TIMEOUT_SECONDS if kind == 'KoboSpan' and kepub_identity
              else RESUME_TIMEOUT_SECONDS)
    deadline = time.monotonic() + budget
    if kind not in _EXACT_KINDS or not source or not value:
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
        if not kepub_identity and cached:
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
            exact = (_resolve(book_id, source, kind, value, kepub_identity=kepub_identity)
                     if kepub_identity else _resolve(book_id, source, kind, value))
            if not kepub_identity and exact and exact.get('cfi') and exact.get('epub_sha256'):
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
    if not wait:
        return None
    while not done.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            log.debug('Exact reader resume deadline exceeded for book %s', book_id)
            return None
        cooperative_sleep(min(0.001, remaining))
    return result[0] if result else None


def _library_paths(book_id):
    # No SQLAlchemy session is shared across threads. Both metadata lookup and
    # file access happen here, never on the request/hub thread.
    from .. import config
    if config.config_use_google_drive or not config.config_calibre_dir:
        return None, None
    metadata = Path(config.config_calibre_dir) / 'metadata.db'
    connection = sqlite3.connect(metadata.resolve().as_uri() + '?mode=ro', uri=True, timeout=0)
    try:
        rows = connection.execute(
            "SELECT b.path, d.name, d.format FROM books b JOIN data d ON d.book=b.id "
            "WHERE b.id=? AND d.format IN ('EPUB', 'KEPUB')", (book_id,),
        ).fetchall()
    finally:
        connection.close()
    root = Path(config.get_book_path()).resolve()
    paths = {}
    for directory, name, fmt in rows:
        path = (root / directory / (name + '.' + fmt.lower())).resolve()
        if path.is_relative_to(root):
            paths.setdefault(fmt, path)
    return paths.get('EPUB'), paths.get('KEPUB')


def _resolve(book_id, source, kind, value, *, kepub_identity=None):
    from .kobo_position import _resume_snapshot
    path, kepub = _library_paths(book_id)
    if path is None:
        return None
    if kind == KOREADER_LOCATION_TYPE:
        from .koreader_position import web_resume
        return web_resume(path, value, (source,))
    snapshot = _resume_snapshot(path, source, kind, value)
    if snapshot:
        cfi, fingerprint = snapshot
        return {'cfi': cfi, 'epub_sha256': fingerprint}
    if kind != 'KoboSpan' or not kepub_identity or kepub is None:
        return None
    from .kobo_place_gate import _readable_kepub_identity
    from .kepub_alignment import span_to_xpointer
    from .koreader_xpointer import xpointer_to_cfi

    def bridge():
        if _readable_kepub_identity(kepub) != kepub_identity:
            return None
        xp = span_to_xpointer(path, kepub, source, value)
        cfi = xpointer_to_cfi(path, xp) if xp else None
        return cfi if _readable_kepub_identity(kepub) == kepub_identity else None

    snapshot = _resume_snapshot(path, source, kind, value, bridge=bridge)
    if snapshot:
        cfi, fingerprint = snapshot
        return {'cfi': cfi, 'epub_sha256': fingerprint}
    return None


def warm_alignment(book_id):
    """Best-effort preparse of existing files; no queue, conversion or proof.

    A cold novel can exceed the GET deadline. Downloads and journal reports
    prepare the identity-keyed parser caches on at most one daemon worker.
    GET still proves the reporting device's download and rechecks both files.
    """
    if not _WARM_SLOT.acquire(blocking=False):
        return

    def worker():
        try:
            from . import kepub_alignment
            epub, kepub = _library_paths(book_id)
            if epub is None or kepub is None:
                return
            parsed = kepub_alignment._kepub(kepub)
            if parsed is not None:
                kepub_alignment._aligned_epub(epub, parsed)
        except Exception:
            log.debug('Could not warm Kobo alignment for book %s', book_id, exc_info=True)
        finally:
            _WARM_SLOT.release()

    try:
        threading.Thread(target=worker, name='kobo-alignment-warm', daemon=True).start()
    except Exception:
        _WARM_SLOT.release()
