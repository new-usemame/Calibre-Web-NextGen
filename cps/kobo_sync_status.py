# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

from .cw_login import current_user
from . import logger, ub
from datetime import datetime, timezone
from sqlalchemy.sql.expression import or_, and_, true
# from sqlalchemy import exc

log = logger.create()


# Add the current book id to kobo_synced_books table for current user, if entry is already present,
# do nothing (safety precaution)
def add_synced_books(book_id):
    is_present = ub.session.query(ub.KoboSyncedBooks).filter(ub.KoboSyncedBooks.book_id == book_id)\
        .filter(ub.KoboSyncedBooks.user_id == current_user.id).count()
    if not is_present:
        synced_book = ub.KoboSyncedBooks()
        synced_book.user_id = current_user.id
        synced_book.book_id = book_id
        ub.session.add(synced_book)
        ub.session_commit()


def record_book_deletion(book_id, book_uuid, session=None):
    """Record a book hard-deletion as a tombstone for each user who had
    it synced to a Kobo device.

    Called by editbooks.delete_whole_book / delete_book_from_table BEFORE
    the metadata.db row is removed (so book.uuid is still accessible).

    For every (user_id, book_id) pair in kobo_synced_books with this
    book_id, inserts a kobo_deleted_book row capturing the UUID. The
    Kobo sync handler emits DeletedEntitlement for these rows on each
    affected user's next sync, then advances archive_last_modified past
    them so the device sees each tombstone exactly once. Without this,
    the device retains the book locally forever — calibre absence is
    not interpreted as deletion, only tombstones are.

    No-op when book_uuid is falsy (defensive — shouldn't happen, but
    saves us from corrupt rows if upstream changes).

    Idempotent per (user_id, book_uuid): the UNIQUE constraint coalesces
    re-runs to the existing row (deleted_at unchanged) via
    INSERT OR IGNORE semantics.
    """
    if not book_uuid:
        return
    s = session if session else ub.session
    affected_user_ids = [
        row.user_id for row in
        s.query(ub.KoboSyncedBooks.user_id)
         .filter(ub.KoboSyncedBooks.book_id == book_id)
         .all()
    ]
    if not affected_user_ids:
        return

    now = datetime.now(timezone.utc)
    for user_id in affected_user_ids:
        existing = (
            s.query(ub.KoboDeletedBook)
             .filter(ub.KoboDeletedBook.user_id == user_id,
                     ub.KoboDeletedBook.book_uuid == book_uuid)
             .one_or_none()
        )
        if existing is None:
            s.add(ub.KoboDeletedBook(
                user_id=user_id,
                book_uuid=book_uuid,
                deleted_at=now,
            ))

    # Clear the now-stale kobo_synced_books rows so the per-user
    # two-way-deletion logic doesn't trip over them on a later sync.
    s.query(ub.KoboSyncedBooks).filter(
        ub.KoboSyncedBooks.book_id == book_id
    ).delete(synchronize_session=False)

    if session is None:
        ub.session_commit()
    else:
        ub.session_commit(_session=s)


# Select all entries of current book in kobo_synced_books table, which are from current user and delete them
def remove_synced_book(book_id, all=False, session=None):
    if not all:
        user = ub.KoboSyncedBooks.user_id == current_user.id
    else:
        user = true()
    if not session:
        ub.session.query(ub.KoboSyncedBooks).filter(ub.KoboSyncedBooks.book_id == book_id).filter(user).delete()
        ub.session_commit()
    else:
        session.query(ub.KoboSyncedBooks).filter(ub.KoboSyncedBooks.book_id == book_id).filter(user).delete()
        ub.session_commit(_session=session)


def change_archived_books(book_id, state=None, message=None):
    archived_book = ub.session.query(ub.ArchivedBook).filter(and_(ub.ArchivedBook.user_id == int(current_user.id),
                                                                  ub.ArchivedBook.book_id == book_id)).first()
    if not archived_book:
        archived_book = ub.ArchivedBook(user_id=current_user.id, book_id=book_id)

    archived_book.is_archived = state if state else not archived_book.is_archived
    archived_book.last_modified = datetime.now(timezone.utc)        # toDo. Check utc timestamp

    ub.session.merge(archived_book)
    ub.session_commit(message)
    return archived_book.is_archived


def update_on_sync_shelfs(user_id):
    """Record the user's non-Kobo-sync shelves as archived, so their device
    drops those collections. Runs when "sync only selected shelves to Kobo"
    goes off -> on (classic ``/me`` form and ``POST /api/v1/account/profile``).

    Book-level reconciliation is deliberately NOT done here. ``HandleSyncRequest``
    (cps/kobo.py) already computes exactly this difference — synced books minus
    the books the user's kobo_sync manual and magic shelves make eligible, with
    the #468 fail-safe for unreliable magic membership — and it does the part
    that matters: it emits a ``ChangedEntitlement`` with ``archived=True`` so
    the DEVICE removes the book, and only then drops the tracking row.

    Fork #866/#1008: doing it here as well was worse than redundant.

    * The old query joined ``Shelf`` on ``user_id`` alone, never on
      ``Shelf.id == BookShelf.shelf``, so any one ordinary shelf in the account
      paired with every synced book and matched ``kobo_sync == 0``. Books that
      WERE on the Kobo-sync shelf got swept. Reproduced live.
    * It deleted each book's ``KoboSyncedBooks`` row before any sync had run.
      That row is the sync handler's only input for the removal command, and a
      swept book is by definition outside the eligible set the handler queries,
      so the device was never told to drop it — the books stayed on the reader
      forever, which is the symptom @auspex reported.
    * It also wrote ``ArchivedBook`` rows, hiding those books from the user's
      own library in the web UI. Turning on a Kobo sync preference should not
      archive most of someone's library.

    Leaving the tracking rows intact is what makes "the extras get archived off
    on the next sync" actually true.
    """
    shelves_to_archive = ub.session.query(ub.Shelf).filter(ub.Shelf.user_id == user_id).filter(
        ub.Shelf.kobo_sync == 0).all()
    # Toggling the setting off and on again used to append a duplicate archive
    # row per shelf every time (47 rows for 2 shelves on a test account).
    already = {row[0] for row in ub.session.query(ub.ShelfArchive.uuid)
               .filter(ub.ShelfArchive.user_id == user_id).all()}
    for a in shelves_to_archive:
        if a.uuid in already:
            continue
        ub.session.add(ub.ShelfArchive(uuid=a.uuid, user_id=user_id))
        ub.session_commit()
