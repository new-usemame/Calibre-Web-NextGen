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

Reaching KOReader as well (#1366) needs a second carrier, because KOReader
pulls from ``KOSyncProgress`` and never looks at the Kobo bookmark.  What we
must **not** do is write a CFI into that table's ``progress`` column: KOReader
consumes it as an engine-private crengine xpointer (numeric values become a
page number, anything else is applied as an xpointer —
``koreader/plugins/cwasync.koplugin/main.lua``), so a CFI there is an
unresolvable position.  Instead the row is written with an explicit
percentage-only sentinel and served as ``position_kind: "percentage"``, which
the plugin acts on with ``GotoPercent`` — an event both of KOReader's engines
implement (``ReaderRolling:onGotoPercent``, ``ReaderPaging:onGotoPercent``).
That lands the reader near where the browser stopped rather than exactly there;
an exact hand-off still needs CFI <-> xpointer canonicalization, tracked on
#324.  Clients that have not advertised percentage support never receive these
rows, so this cannot mis-seek an older plugin.

Conflict policy: **furthest wins**, matching the rule KOSync already applies
across devices (``kosync.py:1106``).  Opening a book in the browser therefore
does not regress a device position this request can observe — scoped
deliberately, because acceptance is decided in Python rather than in the
UPDATE, so it is not proof against a device committing inside the read-write
window.  See ``record_web_reader_progress`` for that limit in full.
Deliberately restarting a book stays a "mark unread" action, which clears every
carrier — the position rows via ``helper.reset_reading_position`` and the
read-status tri-state via ``helper.mirror_read_status_to_readbook``, so the
finished-book guard below stays clearable on a custom-read-column install too
(#1343).
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
    already_finished = False
    with ub.session.no_autoflush:
        read_row = (ub.session.query(ub.ReadBook)
                    .populate_existing()
                    .filter(ub.ReadBook.user_id == user_id,
                            ub.ReadBook.book_id == book_id)
                    .first())
        already_finished = (read_row is not None
                            and read_row.read_status == ub.ReadBook.STATUS_FINISHED)

        state = (ub.session.query(ub.KoboReadingState)
                 .populate_existing()
                 .filter(ub.KoboReadingState.user_id == user_id,
                         ub.KoboReadingState.book_id == book_id)
                 .first())
        if state is not None and state.current_bookmark is not None:
            ub.session.refresh(state.current_bookmark)
            stored = state.current_bookmark.progress_percent

    # Imported lazily: the KOSync protocol module pulls in cps.kobo, and this
    # service is imported from cps.web / cps.api.reader at request time.
    from ..progress_syncing.protocols.kosync import (read_status_for_percentage,
                                                     record_percentage_only_progress,
                                                     update_book_read_status)

    # A sample from the browser must not un-finish a book the user marked Read.
    #
    # Checking the status is not belt-and-braces on the percentage check below,
    # it is the only thing that covers this case: ``edit_book_read_status``
    # creates a *bare* ``ub.KoboBookmark()`` when it marks a book read
    # (``cps/helper.py``), so ``progress_percent`` is NULL and the comparison
    # below is skipped entirely. ``update_book_read_status`` would then recompute
    # the status from the incoming percentage and write it unconditionally, so
    # simply reopening a finished book downgraded it to IN_PROGRESS, counted a
    # new reading session, and — via the ``before_flush`` parent bump — pushed
    # ``StatusInfo: "Reading"`` to the user's Kobo. Destroying state the user set
    # deliberately is the worst thing this best-effort helper could do.
    #
    # The reader-open path already refuses to touch a FINISHED status for the
    # same reason (``cps/web.py``); this keeps the two consistent. Restarting a
    # book stays "mark as unread", which clears every carrier — including
    # ``ReadBook.read_status`` on a custom-read-column install (#1343) — and is
    # the documented way back to 0.
    #
    # Scoped to writes that would actually DOWNGRADE the status, not every write
    # to a finished book (#1343). ``update_book_read_status`` finishes a book at
    # ``FINISHED_PERCENT_THRESHOLD``, so the save that crossed the line marked it
    # FINISHED and a blanket refusal then dropped the *next* one: the stored
    # percentage stuck just under 100 and the device was told "99%"
    # indefinitely. A sample that still means "finished" costs the user nothing
    # and is not what this guard is for; furthest-wins below keeps it honest.
    if already_finished and read_status_for_percentage(percentage) != ub.ReadBook.STATUS_FINISHED:
        log.debug("Web reader position not shared for user %s book %s: "
                  "%.2f%% would un-finish a book already marked finished",
                  user_id, book_id, percentage)
        return False

    if stored is not None and percentage <= stored:
        log.debug("Web reader position not advanced for user %s book %s: "
                  "incoming %.2f%% <= stored %.2f%%",
                  user_id, book_id, percentage, stored)
        return False

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
    # Both carriers go in the one savepoint: the Kobo bookmark that
    # ``update_book_read_status`` maintains, and the KOSync row KOReader pulls
    # from (#1366). They describe the same position, so a partial write would
    # leave the two devices disagreeing about where the user is — worse than
    # neither being updated, which is a state the next save corrects.
    try:
        with ub.session.begin_nested():
            update_book_read_status(user, book_id, percentage)
            record_percentage_only_progress(
                user_id, book_id, percentage, device="Web reader",
            )
    except Exception as e:
        log.warning("Could not share web reader progress for user %s book %s: %s",
                    user_id, book_id, e)
        return False

    log.debug("Web reader advanced progress for user %s book %s to %.2f%%",
              user_id, book_id, percentage)
    return True
