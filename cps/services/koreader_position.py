# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.
"""Exact reading positions between KOReader and the web reader (#324).

KOReader reports its place as a crengine XPointer into the file it holds; the
web reader reports an epub.js CFI into the EPUB it renders. Handing only the
percentage across lands each reader pages away from the other, so this module
converts the position itself (``koreader_xpointer``) -- but only when the
conversion is provably about the same file on both sides:

* the file is identified the way KOReader identifies it, by the partial MD5 of
  its bytes (``checksums/koreader.py``): a device's document digest names the
  file it holds, and the library EPUB's own digest (cached per path, size and
  modification time) names the file the web reader renders;
* a KOReader report is journalled per device with the digest it was sent under
  (``journal_report``), because the shared ``kosync_progress`` row is keyed by
  book id and no longer says which file its XPointer belongs to.

Anything short of that proof leaves the hand-off at the percentage, as before.
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections import OrderedDict
from typing import Iterable, Optional

from .. import logger

log = logger.create()

# ``DeviceReadingPosition.location_type`` of a KOReader report. Its
# ``location_value`` is the XPointer and its ``location_source`` the partial MD5
# of the file that XPointer addresses (the digest the device sent it under).
KOREADER_LOCATION_TYPE = "koreader_xpointer"

_MAX_XPOINTER_CHARS = 4096
_SHA256_MAX = 64
_SHA256 = OrderedDict()
_SHA256_LOCK = threading.Lock()


def is_xpointer(progress) -> bool:
    """True for a crengine XPointer (a reflowable-document position).

    KOReader sends a page number instead for fixed-layout documents (PDF,
    CBZ), and the web reader's sentinel is neither.
    """
    return (isinstance(progress, str) and progress.startswith("/body/")
            and len(progress) <= _MAX_XPOINTER_CHARS)


def file_digest(epub_path) -> Optional[str]:
    """KOReader's partial MD5 of a file (lower case), or ``None``."""
    from .koreader_library import file_facts
    try:
        _size, checksum = file_facts(os.fspath(epub_path))
    except (OSError, TypeError):
        return None
    return checksum.lower() if checksum else None


def _identity(path):
    stat = os.stat(path)
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _sha256(path, identity) -> str:
    """SHA-256 of the whole file, cached per file identity."""
    key = (path,) + identity
    with _SHA256_LOCK:
        cached = _SHA256.get(key)
        if cached is not None:
            _SHA256.move_to_end(key)
            return cached
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    value = digest.hexdigest()
    with _SHA256_LOCK:
        _SHA256[key] = value
        while len(_SHA256) > _SHA256_MAX:
            _SHA256.popitem(last=False)
    return value


def web_resume(epub_path, xpointer: str, device_digests: Iterable[str]) -> Optional[dict]:
    """``{"cfi", "epub_sha256"}`` for a KOReader XPointer, or ``None``.

    ``device_digests`` are the partial MD5s the XPointer was reported under;
    one of them must be the digest of ``epub_path`` itself, or the XPointer
    addresses some other file and is not converted. The SHA-256 lets the web
    reader confirm it renders these same bytes (``readerResume.ts``).
    """
    if not is_xpointer(xpointer):
        return None
    from .koreader_xpointer import xpointer_to_cfi
    try:
        path = os.fspath(epub_path)
        before = _identity(path)
        wanted = {d.lower() for d in device_digests if isinstance(d, str) and d}
        if not wanted or file_digest(path) not in wanted:
            return None
        cfi = xpointer_to_cfi(path, xpointer)
        if cfi is None:
            return None
        fingerprint = _sha256(path, before)
        if _identity(path) != before:
            return None  # replaced while we read it
    except (OSError, TypeError):
        return None
    return {"cfi": cfi, "epub_sha256": fingerprint}


def device_xpointer(epub_path, cfi: str, device_digest: str) -> Optional[str]:
    """The XPointer of a web-reader CFI in the file a device holds, or ``None``.

    Only when ``device_digest`` (the document digest the device asked with) is
    the digest of ``epub_path``, the EPUB the web reader renders: an XPointer
    computed from any other file is never handed to a device.
    """
    if not isinstance(device_digest, str) or not device_digest:
        return None
    from .koreader_xpointer import cfi_to_xpointer
    try:
        path = os.fspath(epub_path)
        before = _identity(path)
        if file_digest(path) != device_digest.lower():
            return None
        xpointer = cfi_to_xpointer(path, cfi)
        if xpointer is None or _identity(path) != before:
            return None
    except (OSError, TypeError):
        return None
    return xpointer


def web_position_for_device(*, user_id, book_id, record, document) -> Optional[str]:
    """The XPointer, in the requesting device's file, of the web reader's place.

    ``record`` is the ``kosync_progress`` row about to be served. It qualifies
    only when the web reader wrote it (a percentage-only row) and the CFI that
    produced it is known for certain: the browser journal holds an EPUB CFI at
    exactly that percentage which is also the user's saved EPUB bookmark (the
    bookmark row is per format, so this also rules out a KEPUB CFI from the
    classic reader). The XPointer is then derived per request against the
    library EPUB, and only if ``document`` -- the digest the device asked with
    -- is that EPUB's digest (``device_xpointer``).
    """
    from .. import calibre_db, ub
    from ..annotations import _book_format_path
    from ..progress_syncing.protocols.kosync import (PERCENTAGE_ONLY_LOCATOR,
                                                     WEB_READER_DEVICE)
    from .parallel import run_blocking

    if (record is None or record.progress != PERCENTAGE_ONLY_LOCATOR
            or record.device != WEB_READER_DEVICE or record.percentage is None):
        return None
    cfi = ub.session.query(ub.Bookmark.bookmark_key).filter(
        ub.Bookmark.user_id == int(user_id),
        ub.Bookmark.book_id == int(book_id),
        ub.Bookmark.format == "epub",
    ).scalar()
    if not cfi:
        return None
    reported = ub.session.query(ub.DeviceReadingPosition.id).join(
        ub.Device, ub.Device.id == ub.DeviceReadingPosition.device_id,
    ).filter(
        ub.Device.user_id == int(user_id),
        ub.Device.kind == "webreader",
        ub.DeviceReadingPosition.book_id == int(book_id),
        ub.DeviceReadingPosition.cfi == cfi,
        ub.DeviceReadingPosition.progress_percent == record.percentage,
    ).first()
    if reported is None:
        return None
    book = calibre_db.get_book(int(book_id))
    epub_path = _book_format_path(book, "EPUB") if book is not None else None
    if not epub_path:
        return None
    return run_blocking(lambda: device_xpointer(epub_path, cfi, document))


def journal_report(session, *, device_id, book_id, document, progress, percentage,
                   observed_at) -> None:
    """Record a KOReader progress report in the per-device journal.

    The caller commits. ``document`` is the digest the device sent, kept as
    the report's ``location_source`` so a later reader can tell which file the
    XPointer addresses.
    """
    from .device_reading_position import stage_position
    stage_position(
        device_id=device_id,
        book_id=book_id,
        progress_percent=percentage,
        location_source=document,
        location_type=KOREADER_LOCATION_TYPE if is_xpointer(progress) else "koreader_page",
        location_value=progress,
        client_modified_at=observed_at,
        session=session,
    )
