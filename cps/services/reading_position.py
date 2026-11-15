# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.
"""Bridge the web reader's position into the shared cross-device progress store (#324).

The web reader keeps its exact position as an epub.js CFI in ``ub.Bookmark``.
Nothing outside the readers reads that row — it is opaque, format-specific and
formerly carried no timestamp — so a browser reading session used to be invisible to the
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
``koreader/plugins/cwngsync.koplugin/main.lua``), so a CFI there is an
unresolvable position.  Instead the row is written with an explicit
percentage-only sentinel and served as ``position_kind: "percentage"``, which
the plugin acts on with ``GotoPercent`` — an event both of KOReader's engines
implement (``ReaderRolling:onGotoPercent``, ``ReaderPaging:onGotoPercent``).
The row itself always stays percentage-only; when the requesting device holds
the very EPUB the web reader renders, the kosync GET serves the browser's CFI
converted to that file's XPointer instead (``koreader_position``), and in the
other direction ``read_resume_position`` adds the CFI of a KOReader XPointer.
Clients that have not advertised percentage support never receive these rows,
so this cannot mis-seek an older plugin.

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
from datetime import datetime, timedelta, timezone
from typing import Optional

from .. import logger, ub
from .device_reading_position import stage_position

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


def record_web_reader_progress(user, book_id: int, percentage: float,
                               *, origin_device_id=None, cfi=None,
                               share_with_devices=True, fmt="epub") -> bool:
    """Record a Browser position and, by default, advance shared carriers.

    Returns ``True`` when the Browser journal was recorded without sharing or
    when the shared carrier advanced. The caller is responsible for committing
    the session — both bookmark routes already do. M1 carries
    ``origin_device_id`` through this write boundary; M3 adds the per-device
    position row that can persist it independently of the shared carrier.

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

    # Direct service callers inside a browser request get the same identity as
    # both routes. Non-request unit callers deliberately stay side-effect free.
    if origin_device_id is None:
        try:
            from flask import g, has_request_context, request
            if has_request_context():
                from .device_registry import (
                    WEBREADER_INSTALLATION_ID_HEADER,
                    ensure_webreader_device_best_effort,
                )
                # The route has already attempted registration when it set g,
                # including a None result. Do not retry the same failed save.
                if hasattr(g, "annotation_origin_device_id"):
                    origin_device_id = g.annotation_origin_device_id
                else:
                    origin_device_id = ensure_webreader_device_best_effort(
                        user_id=user_id,
                        installation_id=request.headers.get(
                            WEBREADER_INSTALLATION_ID_HEADER,
                        ),
                    )
                    g.annotation_origin_device_id = origin_device_id
        except Exception:
            log.debug("Best-effort web-reader device observation failed", exc_info=True)

    from .share_place import gates_enforced
    if origin_device_id is None and gates_enforced(user_id):
        _warn_unregistered_browser(user_id, book_id)
        # A missing identity cannot create a journal or shared report. The
        # account's actual reading still follows the unchecked-save fan-out.
        from ..helper import queue_hardcover_reading_progress
        queue_hardcover_reading_progress(user, book_id, percentage)
        return False

    mode = web_place_mode(user_id, origin_device_id)
    if mode is not None:
        return _record_gated_progress(
            user, book_id, percentage, origin_device_id, cfi, fmt,
            share_with_devices=share_with_devices, checked=mode,
        )

    # Status remains an independent manual-intent guard. Position acceptance is
    # deliberately absent from this read: the shared primitive decides that in
    # its UPDATE WHERE clause after the caller's required write is settled.
    already_finished = False
    with ub.session.no_autoflush:
        read_row = (ub.session.query(ub.ReadBook)
                    .populate_existing()
                    .filter(ub.ReadBook.user_id == user_id,
                            ub.ReadBook.book_id == book_id)
                    .first())
        already_finished = (read_row is not None
                            and read_row.read_status == ub.ReadBook.STATUS_FINISHED)

    # The device journal records what this browser actually reported even when
    # its percentage loses the resolved furthest-wins comparison below. Its
    # exact epub.js CFI is intentionally private to this browser row; Kobo and
    # KOReader continue to receive only the portable percentage.
    if origin_device_id:
        observed_at = datetime.now(timezone.utc)
        try:
            with ub.begin_contained_nested(ub.session):
                stage_position(
                    device_id=origin_device_id,
                    book_id=book_id,
                    progress_percent=percentage,
                    cfi=cfi,
                    client_modified_at=observed_at,
                )
        except Exception as e:
            log.warning(
                "Could not record web-reader device position for user %s "
                "book %s: %s", user_id, book_id, e,
            )
            return False

    # A reader deliberately continuing from a device/source preview establishes
    # a new Browser place without editing the source it inspected. Keep the
    # attributed Browser journal above, but leave every shared device carrier
    # alone. Normal web reading retains the existing cross-device behavior.
    if share_with_devices is False:
        return True

    # Imported lazily: the KOSync protocol module pulls in cps.kobo, and this
    # service is imported from cps.web / cps.api.reader at request time.
    from ..progress_syncing.protocols.kosync import (WEB_READER_DEVICE,
                                                     read_status_for_percentage,
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
    # Both resolved carriers go in the one savepoint: the Kobo bookmark that
    # ``update_book_read_status`` maintains, and the KOSync row KOReader pulls
    # from (#1366). They describe the same position, so a partial write would
    # leave the two devices disagreeing about where the user is — worse than
    # neither being updated, which is a state the next save corrects.
    try:
        with ub.begin_contained_nested(ub.session):
            bookmark_outcome = update_book_read_status(
                user, book_id, percentage,
            )
            if not bookmark_outcome.accepted:
                log.debug(
                    "Web reader position not advanced for user %s book %s: "
                    "incoming %.2f%% <= accepted %.2f%%",
                    user_id, book_id, percentage,
                    bookmark_outcome.percentage,
                )
                return False
            kosync_outcome = record_percentage_only_progress(
                user_id, book_id, percentage, device=WEB_READER_DEVICE,
                web_cfi=cfi if fmt == "epub" else None,
                _return_outcome=True,
            )
            if not kosync_outcome.accepted:
                # A device may have advanced the KOReader carrier between the
                # two SQL verdicts. Resolve the derived status/bookmark from
                # the value that actually survived that second verdict.
                update_book_read_status(
                    user, book_id, kosync_outcome.percentage,
                )
                return False
    except Exception as e:
        log.warning("Could not share web reader progress for user %s book %s: %s",
                    user_id, book_id, e)
        return False

    log.debug("Web reader advanced progress for user %s book %s to %.2f%%",
              user_id, book_id, percentage)
    # Hardcover is the third carrier Kobo and KOReader progress already
    # reaches (#2289). Only an advanced position goes, so a browser behind a
    # device never pulls Hardcover back.
    from ..helper import queue_hardcover_reading_progress
    queue_hardcover_reading_progress(user, book_id, percentage)
    return True


def web_span(book_id, cfi, fmt="epub"):
    """Convert a Browser CFI using a stable library EPUB/KEPUB pair.

    The returned identity is 3b's provenance for converted spans. A format
    mismatch, missing archive or failed alignment leaves a percentage only.
    """
    from .. import calibre_db
    from .kobo_place_gate import _book_paths, _file_identity
    from .koreader_xpointer import cfi_to_xpointer
    from .kepub_alignment import xpointer_to_span
    from .parallel import run_blocking
    if fmt != "epub" or not cfi:
        return None, None
    try:
        book = calibre_db.get_book(int(book_id))
        epub, kepub = _book_paths(book)
        if not epub or not kepub:
            return None, None
        def convert():
            before = (_file_identity(epub), _file_identity(kepub))
            xpointer = cfi_to_xpointer(epub, cfi)
            found = xpointer_to_span(epub, kepub, xpointer) if xpointer else None
            if not found or before != (_file_identity(epub), _file_identity(kepub)):
                return None, None
            source, span = found
            return {"Source": source, "Type": "KoboSpan", "Value": span}, before[1]
        return run_blocking(convert)
    except Exception:
        log.debug("Could not align Browser CFI for book %s", book_id, exc_info=True)
        return None, None


def web_place_mode(user_id, device_id=None):
    """Checked/unchecked Browser, or None for the historical path.

    A gated Browser that cannot be registered stays private rather than
    falling through to the legacy shared writer.
    """
    from .share_place import gates_enforced
    if not gates_enforced(int(user_id)):
        return None
    with ub.session.no_autoflush:
        query = ub.session.query(ub.Device.share_place).filter(
            ub.Device.user_id == int(user_id), ub.Device.kind == "webreader",
        )
        if device_id is not None:
            query = query.filter(ub.Device.id == int(device_id))
        else:
            # Inactive legacy aliases share the kind; only the account's
            # Browser carries its Share my place choice.
            from .browser_source import ACCOUNT_BROWSER
            query = query.filter(ub.Device.created_by == ACCOUNT_BROWSER)
        row = query.first()
    return bool(row and row.share_place)


def _warn_unregistered_browser(user_id, book_id):
    log.warning(
        "Browser registration unavailable; keeping reading private user_id=%s book_id=%s",
        user_id, book_id,
    )


def record_gated_reader_open(user, book_id):
    """Journal a Browser open's status, and share it only when checked.

    Keep the account's session count and last-open time, using Core writes
    so private counters cannot bump a shared Kobo state via the ORM hook.
    A status-only open never changes either journal or shared place clocks.
    Caller commits. The legacy reader-open path is separate and unchanged.
    """
    from sqlalchemy import func, select, update
    from sqlalchemy.dialects.sqlite import insert
    from .device_registry import ensure_webreader_device_best_effort
    from .place_journal import write_report, _same
    from .place_arbiter import Report, report_wins
    from .kobo_place_gate import advance_feed_clock, skew_ordered_clock

    user_id = int(user.id)
    try:
        device_id = ensure_webreader_device_best_effort(user_id=user_id)
    except Exception:
        device_id = None
    if device_id is None:
        _warn_unregistered_browser(user_id, book_id)
        checked = False
    else:
        checked = web_place_mode(user_id, device_id)
    now = datetime.now(timezone.utc)
    table = ub.ReadBook.__table__
    own_status = ub.session.execute(select(ub.DeviceReadingPosition.reading_status).where(
        ub.DeviceReadingPosition.device_id == device_id,
        ub.DeviceReadingPosition.book_id == book_id,
    )).scalar() if device_id else None
    own = write_report(
        device_id=device_id, book_id=book_id, client_at=now, received_at=now,
        status=ub.ReadBook.STATUS_IN_PROGRESS, status_supplied=True,
    ) if device_id else None
    # A new private session may have counters without an account status.
    ub.session.execute(insert(table).values(
        user_id=user_id, book_id=book_id, read_status=ub.ReadBook.STATUS_UNREAD,
        times_started_reading=0, last_modified=None,
    ).on_conflict_do_nothing(index_elements=["user_id", "book_id"]))
    previous_row = ub.session.execute(select(table.c.read_status, table.c.last_modified).where(
        table.c.user_id == user_id, table.c.book_id == book_id,
    )).first()
    previous = previous_row.read_status
    can_resume = True
    shared_receipt = ub.session.execute(select(ub.KoboBookmark.server_modified_at).join(
        ub.KoboReadingState,
        ub.KoboReadingState.id == ub.KoboBookmark.kobo_reading_state_id,
    ).where(ub.KoboReadingState.user_id == user_id,
            ub.KoboReadingState.book_id == book_id)).scalar()
    status_clock = skew_ordered_clock(previous_row.last_modified, shared_receipt)
    status_newer = report_wins(
        Report(now, now),
        Report(status_clock, status_clock) if status_clock is not None else None,
    )
    if (checked and own and own.status_accepted and status_newer
            and previous in (ub.ReadBook.STATUS_DID_NOT_FINISH, ub.ReadBook.STATUS_ON_HOLD)):
        from .. import config, helper
        if config.config_read_column:
            can_resume = helper.set_custom_read_column_value(
                book_id, False, source="web reader resume")
    starting_status = previous if checked else own_status
    starting = can_resume and starting_status not in (
        ub.ReadBook.STATUS_IN_PROGRESS, ub.ReadBook.STATUS_FINISHED)
    ub.session.execute(update(table).where(
        table.c.user_id == user_id, table.c.book_id == book_id,
    ).values(
        times_started_reading=func.coalesce(table.c.times_started_reading, 0) + (1 if starting else 0),
        last_time_started_reading=now, last_modified=table.c.last_modified,
    ))
    if (not checked or not own or not own.status_accepted or not can_resume or not status_newer
            or previous == ub.ReadBook.STATUS_FINISHED):
        return
    # Compare using 3b's legacy skew baseline, but CAS the actual stored clock.
    # The journal write already holds SQLite's writer lock for this transaction.
    accepted = ub.session.execute(update(table).where(
        table.c.user_id == user_id, table.c.book_id == book_id,
        table.c.read_status == previous,
        _same(table.c.last_modified, previous_row.last_modified),
    ).values(read_status=ub.ReadBook.STATUS_IN_PROGRESS,
             last_modified=now, read_status_choice_at=now)).rowcount == 1
    if accepted:
        ub.session.expire_all()
        state_id = ub.session.execute(select(ub.KoboReadingState.id).where(
            ub.KoboReadingState.user_id == user_id,
            ub.KoboReadingState.book_id == book_id,
        )).scalar()
        if state_id:
            advance_feed_clock(state_id, now, session=ub.session)


def _record_gated_progress(user, book_id, percentage, device_id, cfi, fmt,
                           *, share_with_devices, checked):
    from .place_journal import write_report
    from .place_arbiter import Report
    from .kosync_place import latest_write, shared_report
    from ..progress_syncing.models import KOSyncProgress
    from ..progress_syncing.protocols.kosync import (
        PERCENTAGE_ONLY_LOCATOR, WEB_READER_DEVICE, get_book_checksums,
        get_progress_record, read_status_for_percentage, update_book_read_status,
    )
    observed_at = datetime.now(timezone.utc)
    status = read_status_for_percentage(percentage)
    try:
        with ub.begin_contained_nested(ub.session):
            own = write_report(
                device_id=device_id, book_id=book_id, client_at=observed_at,
                received_at=observed_at,
                place=dict(progress_percent=percentage, cfi=cfi,
                           location_source=None, location_type=None, location_value=None),
                status=status, status_supplied=True,
            ) if device_id else None
        # Hardcover follows this account's actual reading, including private
        # reading. It is independent of the shared device carrier verdict.
        from ..helper import queue_hardcover_reading_progress
        if own and own.place_accepted:
            queue_hardcover_reading_progress(user, book_id, percentage)
        if not checked or not share_with_devices:
            return bool(own and own.accepted)
        if not own or not own.place_accepted:
            return False
        read = ub.session.query(ub.ReadBook).filter_by(
            user_id=int(user.id), book_id=book_id).populate_existing().first()
        if (read is not None and read.read_status == ub.ReadBook.STATUS_FINISHED
                and status != ub.ReadBook.STATUS_FINISHED):
            return False
        with ub.begin_contained_nested(ub.session):
            identity = dict(user_id=int(user.id), document=str(book_id))
            current = get_progress_record(user.id, str(book_id), book_id)
            if current is not None and current.document != str(book_id):
                latest_write(
                    KOSyncProgress, identity,
                    dict(progress=current.progress, percentage=current.percentage,
                         device=current.device, device_id=current.device_id,
                         timestamp=current.timestamp, client_modified_at=current.client_modified_at,
                         web_cfi=current.web_cfi),
                    client_column="client_modified_at", server_column="timestamp",
                    candidate=shared_report(current),
                )
            if not latest_write(
                KOSyncProgress, identity,
                dict(progress=PERCENTAGE_ONLY_LOCATOR, percentage=percentage,
                     device=WEB_READER_DEVICE, device_id=None,
                     web_cfi=cfi if fmt == "epub" else None,
                     timestamp=observed_at, client_modified_at=observed_at),
                client_column="client_modified_at", server_column="timestamp",
                candidate=Report(observed_at, observed_at),
            ):
                return False
            ub.session.query(KOSyncProgress).filter(
                KOSyncProgress.user_id == user.id,
                KOSyncProgress.document.in_(set(get_book_checksums(book_id)) - {str(book_id)}),
            ).delete(synchronize_session=False)
            outcome = update_book_read_status(
                user, book_id, percentage, shared_accepted=True,
                client_at=observed_at, web_report=True, source_cfi=cfi, source_format=fmt,
                report_received_at=observed_at,
                web_status_accepted=own.status_accepted,
            )
            return outcome.accepted
    except Exception:
        log.warning("Could not record gated Browser place for user %s book %s",
                    user.id, book_id, exc_info=True)
        return False


# A KOReader push stamps the shared row, then advances the bookmark a moment
# later in the same request; this bounds that gap, not a device's clock.
_SAME_REPORT_SLACK = timedelta(seconds=60)
_MAX_KOREADER_DIGESTS = 3


def _kobo_resume_proof(connection, user_id, book_id, location, percentage,
                       shared_clock):
    """The winning native reporter's completed-download identity, if known.

    Match the journal's place AND effective clock, not just a span that
    another Kobo happened to report. A download after the report cannot
    establish which file that report addressed. Old databases fail closed.
    """
    import sqlite3
    from .kobo_place_gate import effective_clock
    from .kobo_post_download_restore import RESTORE_ARMED
    try:
        shared = connection.execute(
            "SELECT b.shared_from_koreader, b.server_modified_at "
            "FROM kobo_bookmark b JOIN kobo_reading_state s "
            "ON s.id=b.kobo_reading_state_id WHERE s.user_id=? AND s.book_id=? LIMIT 1",
            (user_id, book_id),
        ).fetchone()
        if not shared or shared[0] or not shared[1]:
            return None
        rows = connection.execute(
            "SELECT p.client_modified_at, p.server_modified_at, "
            "x.kepub_file_identity, x.completed_at, x.restore_state "
            "FROM device_reading_position p JOIN device d ON d.id=p.device_id "
            "LEFT JOIN kobo_device_book_download x "
            "ON x.device_id=p.device_id AND x.book_id=p.book_id "
            "AND lower(x.book_format)='kepub' "
            "WHERE d.user_id=? AND d.kind='kobo' AND p.book_id=? "
            "AND p.location_source=? AND p.location_type=? AND p.location_value=? "
            "AND p.progress_percent=? AND p.server_modified_at<=? "
            "ORDER BY p.server_modified_at DESC",
            (user_id, book_id, *location, percentage, shared[1]),
        ).fetchall()
        def clock(raw):
            return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
        for client, receipt, identity, completed, state in rows:
            if not receipt:
                continue
            received = clock(receipt)
            if (effective_clock(clock(client) if client else None, received)
                    != shared_clock.replace(tzinfo=None)):
                continue
            # The most recent matching reporter is the sender; do not borrow
            # proof from an older device when this sender has none.
            if (identity and completed and state != RESTORE_ARMED
                    and clock(completed) <= received):
                return identity
            return None
    except (sqlite3.Error, ValueError, TypeError):
        log.debug("Could not identify proven Kobo resume reporter", exc_info=True)
    return None


def _koreader_report(connection, user_id, book_id, bookmark_percent, bookmark_clock, utc):
    """``(xpointer, digests)`` when the resume position IS a KOReader report.

    The resume offers the shared Kobo bookmark. A KOReader push writes it
    through ``update_book_read_status`` with the percentage it also stores,
    beside its XPointer, on the book-id-keyed ``kosync_progress`` row. So the
    bookmark is that push when that row holds an XPointer at exactly the
    bookmark's percentage and was stamped no earlier than the bookmark
    (``_SAME_REPORT_SLACK`` covers the gap between the two writes of one
    push). Every other writer of the bookmark -- the web reader, a Kobo --
    either replaces the row with the percentage-only sentinel or leaves it
    older than the bookmark. The bookmark's own location fields are then
    stale (a KOReader push does not write them), so they must not be used.

    ``digests`` are the files that XPointer was reported from, from the
    per-device journal (``koreader_position.journal_report``), newest first;
    none (a push from before the journal) means no exact place, not the
    bookmark's stale location.
    """
    from .koreader_position import KOREADER_LOCATION_TYPE, is_xpointer
    row = connection.execute(
        "SELECT progress, percentage, timestamp FROM kosync_progress "
        "WHERE user_id=? AND document=?", (user_id, str(book_id)),
    ).fetchone()
    if (not row or not is_xpointer(row[0]) or row[1] is None or row[2] is None
            or row[1] != bookmark_percent):
        return None
    if utc(row[2]) < bookmark_clock - _SAME_REPORT_SLACK:
        return None
    digests = [digest for (digest,) in connection.execute(
        "SELECT p.location_source FROM device_reading_position p "
        "JOIN device d ON d.id=p.device_id "
        "WHERE d.user_id=? AND d.kind='koreader' AND p.book_id=? "
        "AND p.location_type=? AND p.location_value=? AND p.location_source IS NOT NULL "
        "ORDER BY p.server_modified_at DESC LIMIT ?",
        (user_id, book_id, KOREADER_LOCATION_TYPE, row[0], _MAX_KOREADER_DIGESTS),
    )]
    return row[0], digests


def read_resume_position(engine, user_id, book_id, fmt="epub"):
    """Keep the app session's CFI availability; read remote progress best-effort.

    The mandatory local lookup retains the app connection's normal busy timeout.
    Only the optional carrier uses a separate read-only, zero-timeout connection.
    Neither read flushes pending changes. Unknown historical local clocks allow
    an offer, but never an automatic replacement of the saved CFI.
    """
    import sqlite3
    from pathlib import Path

    with ub.session.no_autoflush:
        local = ub.session.query(ub.Bookmark).filter(
            ub.Bookmark.user_id == user_id,
            ub.Bookmark.book_id == book_id,
            ub.Bookmark.format == fmt,
        ).first()
        bookmark = local.bookmark_key if local else None
        local_updated_at = local.updated_at if local else None
    result = {"bookmark": bookmark, "resume": None}
    mode = web_place_mode(user_id)
    if mode is False:
        return result
    connection = None
    try:
        database = engine.url.database
        if not database or database == ":memory:":
            return result
        connection = sqlite3.connect(
            Path(database).resolve().as_uri() + "?mode=ro", uri=True, timeout=0,
        )
        connection.execute("BEGIN")
        if fmt != "epub":
            return result
        remote = connection.execute(
            "SELECT b.progress_percent, b.last_modified FROM kobo_bookmark b "
            "JOIN kobo_reading_state s ON s.id=b.kobo_reading_state_id "
            "WHERE s.user_id=? AND s.book_id=? LIMIT 1", (user_id, book_id),
        ).fetchone()
        if not remote:
            return result
        percentage = coerce_percentage(remote[0])
        if percentage is None or not remote[1]:
            return result
        def utc(raw):
            value = raw if isinstance(raw, datetime) else datetime.fromisoformat(raw)
            return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        synced_at = utc(remote[1])
        if mode is True:
            from .place_arbiter import Report, report_wins
            receipt = connection.execute(
                "SELECT b.server_modified_at FROM kobo_bookmark b "
                "JOIN kobo_reading_state s ON s.id=b.kobo_reading_state_id "
                "WHERE s.user_id=? AND s.book_id=? LIMIT 1", (user_id, book_id),
            ).fetchone()
            from .browser_source import ACCOUNT_BROWSER
            own = connection.execute(
                "SELECT p.client_modified_at, p.server_modified_at FROM device_reading_position p "
                "JOIN device d ON d.id=p.device_id WHERE d.user_id=? AND d.kind='webreader' "
                "AND d.created_by=? AND p.book_id=? "
                "AND (p.progress_percent IS NOT NULL OR p.cfi IS NOT NULL) LIMIT 1",
                (user_id, ACCOUNT_BROWSER, book_id),
            ).fetchone()
            own_report = (Report(utc(own[0]) if own[0] else None, utc(own[1])) if own
                          else Report(utc(local_updated_at), utc(local_updated_at))
                          if bookmark and local_updated_at else None)
            from .kobo_place_gate import EPOCH
            shared = Report(synced_at, utc(receipt[0]) if receipt[0] else EPOCH)
            if not report_wins(shared, own_report):
                return result
        elif bookmark and local_updated_at is not None and synced_at <= utc(local_updated_at):
            return result
        result["resume"] = {"percentage": percentage,
                            "synced_at": synced_at.isoformat(),
                            "mode": "offer" if result["bookmark"] else "automatic"}
        # Keep the original percentage query/response independent of optional
        # location fields (including older or partially migrated databases).
        try:
            location = connection.execute(
                "SELECT b.location_source, b.location_type, b.location_value "
                "FROM kobo_bookmark b JOIN kobo_reading_state s "
                "ON s.id=b.kobo_reading_state_id WHERE s.user_id=? AND s.book_id=? LIMIT 1",
                (user_id, book_id),
            ).fetchone()
            try:
                koreader = _koreader_report(connection, user_id, book_id, remote[0],
                                            synced_at, utc)
            except sqlite3.Error:
                # An app database without these tables keeps the Kobo path.
                log.debug("Could not read KOReader position for book %s", book_id,
                          exc_info=True)
                koreader = None
            kepub_identity = None
            if koreader is None and location and location[1] == "KoboSpan":
                kepub_identity = _kobo_resume_proof(
                    connection, user_id, book_id, location, percentage, synced_at)
            # Release the read snapshot before any optional EPUB work.
            connection.close()
            connection = None
            from .kobo_resume import exact_resume
            exact = None
            if koreader is not None:
                from .koreader_position import KOREADER_LOCATION_TYPE
                xpointer, digests = koreader
                for digest in digests:
                    exact = exact_resume(book_id, digest, KOREADER_LOCATION_TYPE, xpointer)
                    if exact:
                        break
            elif location and location[1] == "KoboSpan" and location[0] and location[2]:
                exact = (exact_resume(book_id, *location, kepub_identity=kepub_identity)
                         if kepub_identity else exact_resume(book_id, *location))
            if exact:
                result["resume"].update(exact)
                if mode is True:
                    result["resume"]["mode"] = "automatic"
        except Exception:
            log.debug("Could not load exact reader resume for book %s", book_id, exc_info=True)
    except Exception:
        log.debug("Could not load reader resume position for book %s", book_id, exc_info=True)
    finally:
        if connection is not None:
            connection.close()
    return result
