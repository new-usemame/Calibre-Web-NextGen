# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Small, HTTP-free transitions for the per-user reader status."""


from datetime import datetime, timezone


def stop_reading(session, user_id, book_id, read_book_model):
    """Move only an existing in-progress row to unread.

    Unlike the manual "mark unread" action, this preserves the row's saved
    device/web positions, session counters, timestamps, and attached history.
    A finished or absent row is a no-op. The status timestamp may advance as
    part of the normal sync signaling; the caller owns visibility checks and
    the transaction.
    """
    row = session.query(read_book_model).filter(
        read_book_model.user_id == int(user_id),
        read_book_model.book_id == int(book_id),
    ).one_or_none()
    if row is None or row.read_status != read_book_model.STATUS_IN_PROGRESS:
        return False

    row.read_status = read_book_model.STATUS_UNREAD
    row.read_status_choice_at = datetime.now(timezone.utc)
    return True


def record_reader_open(user, book_id):
    """Record that ``user`` opened ``book_id`` in a web reader.

    Shared by the classic ``/read`` route and the new UI reader's
    ``reader-open`` call (#2750), so both readers move a book onto Currently
    Reading the same way. Caller commits.

    CWA #1364: bump ``times_started_reading`` only on the transition into
    IN_PROGRESS from an unread, paused or abandoned state, so a refresh or
    Back/Forward is not a new session; always touch
    ``last_time_started_reading`` so "recently read" reflects every open. A
    finished book stays finished. With share-my-place gates on, the gated
    browser journal owns the write.
    """
    from .. import config, helper, ub
    from .reading_position import record_gated_reader_open
    from .share_place import gates_enforced

    if gates_enforced(int(user.id)):
        record_gated_reader_open(user, book_id)
        return
    read_row = ub.session.query(ub.ReadBook).filter(
        ub.ReadBook.user_id == int(user.id),
        ub.ReadBook.book_id == book_id,
    ).first()
    now = datetime.now(timezone.utc)
    if read_row is None:
        ub.session.add(ub.ReadBook(
            user_id=int(user.id),
            book_id=book_id,
            read_status=ub.ReadBook.STATUS_IN_PROGRESS,
            times_started_reading=1,
            last_time_started_reading=now,
            read_status_choice_at=now,
        ))
        return
    prev_status = read_row.read_status or 0
    can_resume = True
    if (prev_status in (ub.ReadBook.STATUS_DID_NOT_FINISH,
                        ub.ReadBook.STATUS_ON_HOLD)
            and config.config_read_column):
        # Opening the ordinary reader is an explicit resume. A
        # prior shared finished marker must not mask that resume.
        can_resume = helper.set_custom_read_column_value(
            book_id, False, source="web reader resume")
    if prev_status != ub.ReadBook.STATUS_IN_PROGRESS \
            and prev_status != ub.ReadBook.STATUS_FINISHED and can_resume:
        read_row.times_started_reading = (read_row.times_started_reading or 0) + 1
        read_row.read_status = ub.ReadBook.STATUS_IN_PROGRESS
    if can_resume and prev_status != ub.ReadBook.STATUS_FINISHED:
        read_row.read_status_choice_at = now
    read_row.last_time_started_reading = now
