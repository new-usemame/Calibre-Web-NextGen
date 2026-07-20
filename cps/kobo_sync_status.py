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


def kobo_eligible_book_ids(user_id):
    """Book ids the user's Kobo-sync shelves make eligible for delivery, plus
    whether that set can be trusted.

    Mirrors the sync handler's own membership arm (``HandleSyncRequest``):
    books on a manual shelf of this user with ``kobo_sync`` set, UNION the
    books of their kobo_sync magic shelves. ``reliable`` is False when a magic
    shelf's membership query failed — an incomplete set must never be treated
    as "not eligible", or a transient DB error archives books off the device
    (fork #468).
    """
    rows = (ub.session.query(ub.BookShelf.book_id)
            .join(ub.Shelf, ub.Shelf.id == ub.BookShelf.shelf)
            .filter(ub.Shelf.user_id == user_id,
                    ub.Shelf.kobo_sync == True)  # noqa: E712
            .all())
    book_ids = {row[0] for row in rows}
    # Lazy: cps.kobo imports this module, so a top-level import would cycle.
    from .kobo import get_magic_shelf_book_ids_for_kobo
    magic_ids, reliable = get_magic_shelf_book_ids_for_kobo(user_id)
    return book_ids | magic_ids, reliable


# Archive every book this user has synced to their device that their Kobo-sync
# shelves do NOT make eligible, and record their non-synced shelves as archived
# so the device drops those too. Runs when "sync only selected shelves" goes
# off -> on (classic /me form and POST /api/v1/account/profile).
def update_on_sync_shelfs(user_id):
    # Fork #866/#1008: the previous query joined Shelf on user_id ALONE, never
    # on Shelf.id == BookShelf.shelf. Any one non-kobo_sync shelf owned by the
    # user therefore matched every synced book, so books that WERE on the
    # Kobo-sync shelf got archived off the device — reproduced live: a book on
    # a kobo_sync shelf was archived purely because an unrelated plain shelf
    # existed. Decide from the eligible-id set the sync handler itself uses.
    eligible, reliable = kobo_eligible_book_ids(user_id)
    if not reliable:
        log.warning("Kobo Sync: skipping the shelf-only archive sweep for user %s — "
                    "magic-shelf membership was incomplete, archiving now could "
                    "remove books the user selected", user_id)
        return

    synced = (ub.session.query(ub.KoboSyncedBooks)
              .filter(ub.KoboSyncedBooks.user_id == user_id).all())
    for b in synced:
        if b.book_id in eligible:
            continue
        change_archived_books(b.book_id, True)
        ub.session.query(ub.KoboSyncedBooks) \
            .filter(ub.KoboSyncedBooks.book_id == b.book_id) \
            .filter(ub.KoboSyncedBooks.user_id == user_id).delete()
        ub.session_commit()

    # Search all shelf which are currently not synced
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
