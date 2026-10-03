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
