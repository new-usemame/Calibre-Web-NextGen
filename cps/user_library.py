# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-user membership over the one global Calibre library (#1939)."""

from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from . import calibre_db, db, ub, config

SEED_CHUNK_SIZE = 500


class UserLibraryError(Exception):
    """A user-visible membership validation failure."""


def _session(session=None):
    return session or ub.session


def invalidate_request_cache(user_id=None):
    """Drop common_filters' request cache after a membership mutation."""
    try:
        from flask import g, has_request_context
        if not has_request_context():
            return
        cache = getattr(g, "_user_library_filter_cache", None)
        if cache is None:
            return
        if user_id is None:
            cache.clear()
        else:
            cache.pop(int(user_id), None)
    except RuntimeError:
        pass


def membership_count(user_id, session=None):
    app_session = _session(session)
    return (app_session.query(ub.UserLibraryBook)
            .filter(ub.UserLibraryBook.user_id == int(user_id)).count())


def seed_user_library(user, *, added_by=None, chunk_size=SEED_CHUNK_SIZE,
                      app_session=None, cdb=None):
    """Idempotently seed ``user`` with every book they can currently see.

    Each chunk is its own app.db transaction. That bounds the BEGIN IMMEDIATE
    write-lock window introduced by the contained-savepoint policy (#1936).
    The enable flag is flipped only after every chunk succeeds, so a partial
    failure retries safely without exposing an incomplete set.
    """
    app_session = _session(app_session)
    cdb = cdb or calibre_db
    chunk_size = max(1, int(chunk_size))

    visible_query = (cdb.session.query(db.Books.id)
                     .filter(cdb.common_filters(
                         allow_show_global=True,
                         user=user,
                     ))
                     .order_by(db.Books.id))
    # common_filters reads hidden/archive state from app.db. Release that read
    # transaction before the first bounded write transaction begins.
    app_session.commit()

    inserted = 0
    chunk = []
    actor = int(added_by) if added_by is not None else None
    for (book_id,) in visible_query.yield_per(chunk_size):
        chunk.append({
            "user_id": int(user.id),
            "book_id": int(book_id),
            "added_by": actor,
        })
        if len(chunk) >= chunk_size:
            result = app_session.execute(
                sqlite_insert(ub.UserLibraryBook)
                .values(chunk)
                .on_conflict_do_nothing(
                    index_elements=["user_id", "book_id"]
                )
            )
            app_session.commit()
            inserted += max(0, result.rowcount or 0)
            chunk = []
    if chunk:
        result = app_session.execute(
            sqlite_insert(ub.UserLibraryBook)
            .values(chunk)
            .on_conflict_do_nothing(index_elements=["user_id", "book_id"])
        )
        app_session.commit()
        inserted += max(0, result.rowcount or 0)

    invalidate_request_cache(user.id)
    return inserted


def set_enabled(user, enabled, *, added_by=None, app_session=None, cdb=None,
                chunk_size=SEED_CHUNK_SIZE):
    """Set membership mode, seeding before the first off→on transition."""
    app_session = _session(app_session)
    desired = bool(enabled)
    if desired and not bool(getattr(user, "has_own_library", False)):
        seed_user_library(
            user,
            added_by=added_by,
            chunk_size=chunk_size,
            app_session=app_session,
            cdb=cdb,
        )
    if desired and membership_count(user.id, app_session) == 0 \
            and not user.role_browse_global():
        raise UserLibraryError(
            "My Library cannot be enabled with an empty set unless this "
            "user can browse the global library."
        )
    user.has_own_library = desired
    app_session.commit()
    invalidate_request_cache(user.id)
    return desired


def configure_new_user(user, *, app_session=None, cdb=None):
    """Apply the server default after a new user has received an id."""
    if not bool(getattr(config, "config_new_users_have_own_library", False)):
        return False
    set_enabled(
        user,
        True,
        added_by=user.id,
        app_session=app_session,
        cdb=cdb,
    )
    return True


def _require_enabled_library(user):
    if not bool(getattr(user, "has_own_library", False)):
        raise UserLibraryError("My Library is not enabled for this account.")


def _require_global_browse(user):
    _require_enabled_library(user)
    if not user.role_browse_global():
        raise UserLibraryError(
            "You need global-library browse permission to change My Library."
        )


def add_book(user, book_id, *, added_by=None, app_session=None, cdb=None):
    """Idempotently add a globally visible book to ``user``'s set."""
    _require_global_browse(user)
    app_session = _session(app_session)
    cdb = cdb or calibre_db
    book_id = int(book_id)
    exists = (cdb.session.query(db.Books.id)
              .filter(db.Books.id == book_id)
              .filter(cdb.common_filters(
                  allow_show_global=True,
                  user=user,
              )).first())
    if not exists:
        raise UserLibraryError("Book not found in the visible global library.")
    app_session.execute(
        sqlite_insert(ub.UserLibraryBook)
        .values(user_id=int(user.id), book_id=book_id,
                added_by=int(added_by) if added_by is not None else None)
        .on_conflict_do_nothing(index_elements=["user_id", "book_id"])
    )
    app_session.commit()
    invalidate_request_cache(user.id)
    return True


def remove_book(user, book_id, *, app_session=None):
    """Remove membership and this user's ordinary shelf links only.

    Kobo ownership, synced-book rows, annotations, bookmarks, and progress are
    deliberately untouched so they survive removal and a later re-add.
    """
    _require_enabled_library(user)
    app_session = _session(app_session)
    book_id = int(book_id)
    membership = (app_session.query(ub.UserLibraryBook)
                  .filter(ub.UserLibraryBook.user_id == int(user.id),
                          ub.UserLibraryBook.book_id == book_id).first())
    if membership is None:
        return []
    if membership_count(user.id, app_session) == 1 and not user.role_browse_global():
        raise UserLibraryError(
            "The last book cannot be removed unless this user can browse the "
            "global library."
        )
    shelves = (app_session.query(ub.Shelf)
               .join(ub.BookShelf, ub.BookShelf.shelf == ub.Shelf.id)
               .filter(ub.Shelf.user_id == int(user.id),
                       ub.BookShelf.book_id == book_id)
               .order_by(ub.Shelf.name).all())
    shelf_names = [shelf.name for shelf in shelves]
    if shelves:
        shelf_ids = [shelf.id for shelf in shelves]
        (app_session.query(ub.BookShelf)
         .filter(ub.BookShelf.shelf.in_(shelf_ids),
                 ub.BookShelf.book_id == book_id).delete(
                     synchronize_session=False))
    app_session.delete(membership)
    app_session.commit()
    invalidate_request_cache(user.id)
    return shelf_names


def removal_impact(user, book_id, *, app_session=None):
    """Return the confirmation contract without mutating membership."""
    _require_enabled_library(user)
    app_session = _session(app_session)
    book_id = int(book_id)
    membership = (app_session.query(ub.UserLibraryBook.id)
                  .filter(ub.UserLibraryBook.user_id == int(user.id),
                          ub.UserLibraryBook.book_id == book_id).first())
    if membership is None:
        raise UserLibraryError("Book is not in My Library.")
    shelves = (app_session.query(ub.Shelf.name)
               .join(ub.BookShelf, ub.BookShelf.shelf == ub.Shelf.id)
               .filter(ub.Shelf.user_id == int(user.id),
                       ub.BookShelf.book_id == book_id)
               .order_by(ub.Shelf.name).all())
    return {
        "affected_shelves": [row[0] for row in shelves],
        "kobo_removal_on_next_sync": True,
        "reading_data_preserved": True,
    }
