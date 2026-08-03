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
across devices (``kosync.py:1106``).  Opening a book in the browser therefore
does not regress a device position this request can observe — scoped
deliberately, because acceptance is decided in Python rather than in the
UPDATE, so it is not proof against a device committing inside the read-write
window.  See ``record_web_reader_progress`` for that limit in full.
Deliberately restarting a book stays a "mark unread" action, which clears every
carrier via ``helper.reset_reading_position``.
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

    # ``ub.session`` is a single long-lived Session shared across requests
    # (``init_db`` builds it once), so a plain query can answer from the identity
    # map rather than the row. ``populate_existing`` + ``refresh`` make this read
    # authoritative *within this transaction* — cheap, and strictly better than
    # comparing against whatever happens to be in memory.
    #
    # ``no_autoflush`` is load-bearing, not tidiness. Should a caller still have
    # a pending write on this session, a bare query would autoflush it here, so
    # a failure belonging to that REQUIRED write would surface inside this
    # best-effort helper and be logged (and swallowed) by the routes as an
    # optional progress-sharing failure. Reading without flushing keeps a read
    # from being the thing that trips someone else's write.
    #
    # Honest limit: this is not cross-connection atomicity. The read-then-write
    # below can still interleave with a writer on another connection, because
    # acceptance is decided in Python rather than by the UPDATE itself. So the
    # guarantee this gives is "never regresses a position it can observe", NOT
    # an absolute no-regression guarantee: a device that commits a further
    # position inside this window can still be rolled back to ours, and it only
    # recovers if that device pushes again. That is a pre-existing property of
    # this subsystem, not something introduced here — KOSync's own furthest-wins
    # check (kosync.py:1106) has exactly the same shape. Closing it means one
    # shared conditional-UPDATE primitive (accept in the WHERE clause, then
    # check the affected-row count) used by BOTH writers, so the two cannot
    # drift; that is tracked separately rather than half-done here.
    stored = None
    with ub.session.no_autoflush:
        state = (ub.session.query(ub.KoboReadingState)
                 .populate_existing()
                 .filter(ub.KoboReadingState.user_id == user_id,
                         ub.KoboReadingState.book_id == book_id)
                 .first())
        if state is not None and state.current_bookmark is not None:
            ub.session.refresh(state.current_bookmark)
            stored = state.current_bookmark.progress_percent

    if stored is not None and percentage <= stored:
        log.debug("Web reader position not advanced for user %s book %s: "
                  "incoming %.2f%% <= stored %.2f%%",
                  user_id, book_id, percentage, stored)
        return False

    # Imported lazily: the KOSync protocol module pulls in cps.kobo, and this
    # service is imported from cps.web / cps.api.reader at request time.
    from ..progress_syncing.protocols.kosync import update_book_read_status

    # Sharing a position must never cost the user their bookmark, so this write
    # goes in a SAVEPOINT: ``update_book_read_status`` creates ReadBook and
    # KoboReadingState rows carrying UNIQUE(user_id, book_id), and a first-ever
    # write racing a Kobo state PUT can raise IntegrityError at flush time —
    # which ``ub.session_commit`` does not catch. The savepoint confines that
    # failure to the progress write and leaves the caller's commit intact.
    #
    # PRECONDITION (#1318): the caller must have settled its own pending writes
    # first — ``ub.session_flush()`` — for two reasons. A savepoint only contains
    # what is flushed after it, so an unsettled caller write would roll back with
    # us; and settling it here would mean a failure of the caller's REQUIRED
    # write raising inside this best-effort helper, where the routes' broad
    # handler logs it as an optional progress-sharing failure and answers success
    # anyway. Both bookmark routes settle before calling in.
    try:
        with ub.session.begin_nested():
            update_book_read_status(user, book_id, percentage)
    except Exception as e:
        log.warning("Could not share web reader progress for user %s book %s: %s",
                    user_id, book_id, e)
        return False

    log.debug("Web reader advanced progress for user %s book %s to %.2f%%",
              user_id, book_id, percentage)
    return True
