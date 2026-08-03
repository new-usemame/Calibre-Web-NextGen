# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.
"""Bridge the web reader's position into the shared cross-device progress store (#324).

The web reader keeps its exact position as an epub.js CFI in ``ub.Bookmark``.
Nothing outside the readers reads that row — it is opaque, format-specific and
carries no timestamp — so a browser reading session used to be invisible to the
user's Kobo and to the book-detail progress display.

The portable part of a position is the *percentage*, which the client already
computes (``epub.locations.percentageFromCfi``).  Writing it into
``KoboBookmark.progress_percent`` is what makes it travel:

  * ``cps/kobo.py`` serves that field back to the device as ``ProgressPercent``
    whenever the parent ``KoboReadingState.last_modified`` has advanced past the
    device's sync token (``kobo.py:544`` and ``kobo.py:691``).  The parent bump
    is done for us by the ``before_flush`` listener in ``cps/ub.py``, which
    touches the parent whenever a ``KoboBookmark`` is new or dirty.
  * the classic and SPA book pages read the same field via
    ``helper.get_kosync_progress_display``.

We deliberately reuse KOSync's ``update_book_read_status`` rather than writing
the row here, so the web reader, KOReader and Kobo all converge on one
status-threshold implementation instead of three that can drift.

Not done here, on purpose: we do **not** write ``KOSyncProgress``.  KOReader
consumes that table's ``progress`` column as an engine-private crengine
xpointer (numeric values become a page number, anything else is applied as an
xpointer — ``koreader/plugins/cwasync.koplugin/main.lua``).  A CFI is neither,
so pushing one there would hand KOReader an unresolvable position.  Bridging
that direction needs a real CFI <-> xpointer canonicalization, which is tracked
separately on #324.

Conflict policy: **furthest wins**, matching the rule KOSync already applies
across devices (``kosync.py:1106``).  Opening a book in the browser can
therefore never regress a position pushed from a device.  Deliberately
restarting a book stays a "mark unread" action, which clears every carrier via
``helper.reset_reading_position``.
"""

import math
from typing import Optional

from .. import logger, ub

log = logger.create()

MIN_PERCENT = 0.0
MAX_PERCENT = 100.0


def coerce_percentage(raw) -> Optional[float]:
    """Parse a client-supplied reading percentage; return ``None`` if unusable.

    Rejects non-numeric input, booleans (``True`` would otherwise read as 1.0),
    NaN/inf, and anything outside 0-100 — this value reaches the database and
    the Kobo sync feed, so it is validated by allowlist rather than clamped.
    """
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    if value < MIN_PERCENT or value > MAX_PERCENT:
        return None
    return value


def record_web_reader_progress(user, book_id: int, percentage: float) -> bool:
    """Advance the shared progress carrier from a web-reader position.

    Returns ``True`` when the carrier was advanced.  The caller is responsible
    for committing the session — both bookmark routes already do.

    Skipped without a write when:
      * ``percentage`` is not a positive number.  A 0% sample is what the
        classic reader produces before ``epub.locations.generate()`` resolves
        (CWA #1364), and it carries no cross-device information either way.
      * a device has already reported an equal or further position.
    """
    if percentage is None or percentage <= MIN_PERCENT:
        return False

    try:
        user_id = int(user.id)
    except (AttributeError, TypeError, ValueError):
        return False

    stored = None
    state = (ub.session.query(ub.KoboReadingState)
             .filter(ub.KoboReadingState.user_id == user_id,
                     ub.KoboReadingState.book_id == book_id)
             .first())
    if state is not None and state.current_bookmark is not None:
        stored = state.current_bookmark.progress_percent

    if stored is not None and percentage <= stored:
        log.debug("Web reader position not advanced for user %s book %s: "
                  "incoming %.2f%% <= stored %.2f%%",
                  user_id, book_id, percentage, stored)
        return False

    # Imported lazily: the KOSync protocol module pulls in cps.kobo, and this
    # service is imported from cps.web / cps.api.reader at request time.
    from ..progress_syncing.protocols.kosync import update_book_read_status

    update_book_read_status(user, book_id, percentage)
    log.debug("Web reader advanced progress for user %s book %s to %.2f%%",
              user_id, book_id, percentage)
    return True
