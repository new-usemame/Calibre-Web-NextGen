# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-user membership over the one global Calibre library (#1939)."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, exists, false
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from . import calibre_db, constants, db, ub

SEED_CHUNK_SIZE = 500


class UserLibraryError(Exception):
    """A user-visible membership validation failure."""


class UserLibraryBookNotFound(UserLibraryError):
    """The requested book is absent from the target's visible global set."""


@dataclass(frozen=True)
class MembershipMutationResult:
    """Database-observed outcome of one membership mutation."""

    changed: bool
    affected_shelves: tuple = ()


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


def mark_response_user_specific():
    """Make the app-wide response hook emit private cache semantics."""
    try:
        from flask import g, has_request_context
        if has_request_context():
            g._common_filters_user_specific = True
    except RuntimeError:
        pass


def mode_for_user(user):
    """Return the named mode, including for legacy/minimal user objects."""
    mode_getter = getattr(user, "library_mode", None)
    if callable(mode_getter):
        return mode_getter()
    return (
        constants.LIBRARY_MODE_PERSONAL
        if bool(getattr(user, "has_own_library", False))
        else constants.LIBRARY_MODE_MONOLIBRARY
    )


def mode_payload(user):
    """Stable named-mode contract shared by self-service and admin APIs."""
    may_see_whole_archive = bool(
        getattr(user, "role_browse_global", lambda: False)()
    )
    return {
        "library_mode": mode_for_user(user),
        # ROLE_BROWSE_GLOBAL is deliberately the one capability behind both
        # whole-archive discovery and self-service mode switching. Without it,
        # an administrator manages this account's mode.
        "can_switch_library_mode": may_see_whole_archive,
        "library_mode_managed": not may_see_whole_archive,
        "my_library_seeded": bool(
            getattr(user, "user_library_seeded", False)
        ),
        "show_my_library_intro": (
            not bool(getattr(user, "is_anonymous", False))
            and not bool(getattr(user, "my_library_intro_dismissed", False))
        ),
    }


def membership_count(user_id, session=None):
    app_session = _session(session)
    return (app_session.query(ub.UserLibraryBook)
            .filter(ub.UserLibraryBook.user_id == int(user_id)).count())


def prepare_user_library_seed(user, *, chunk_size=SEED_CHUNK_SIZE,
                              app_session=None, cdb=None):
    """Idempotently insert every currently visible book in bounded chunks.

    Each chunk is its own app.db transaction. That bounds the BEGIN IMMEDIATE
    write-lock window introduced by the contained-savepoint policy (#1936). It
    deliberately does *not* set the durable seed-once fence: only an accepted
    mode switch may commit ``user_library_seeded`` and ``has_own_library``
    together. A partial or rejected attempt therefore retries safely through
    ON CONFLICT DO NOTHING without claiming the seed completed.
    """
    app_session = _session(app_session)
    cdb = cdb or calibre_db
    chunk_size = max(1, int(chunk_size))

    visible_query = (cdb.session.query(db.Books.id)
                     .filter(cdb.common_filters(
                         allow_show_global=True,
                         allow_show_archived=True,
                         allow_show_hidden=True,
                         user=user,
                     ))
                     .order_by(db.Books.id))
    # common_filters reads hidden/archive state from app.db. Release that read
    # transaction before the first bounded write transaction begins.
    app_session.commit()

    inserted = 0
    chunk = []
    for (book_id,) in visible_query.yield_per(chunk_size):
        chunk.append({
            "user_id": int(user.id),
            "book_id": int(book_id),
            # Initial enable is deliberately a wire no-op. These books were
            # already eligible before personal mode, so they are baseline
            # membership rather than new selection events for Kobo's
            # UserLibraryBook.added_at cursor arm.
            "added_at": datetime.min,
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


def set_library_mode(user, mode, *, app_session=None, cdb=None,
                     chunk_size=SEED_CHUNK_SIZE, seed_rows_prepared=False,
                     commit=True):
    """Switch modes; commit the seed fence and personal mode atomically."""
    app_session = _session(app_session)
    if mode not in constants.LIBRARY_MODES:
        raise UserLibraryError(
            "Library mode must be 'monolibrary' or 'personal_library'."
        )
    desired = mode == constants.LIBRARY_MODE_PERSONAL
    if desired and not bool(getattr(user, "user_library_seeded", False)):
        if not seed_rows_prepared:
            prepare_user_library_seed(
                user,
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
    if desired:
        # This fence and the mode are one transaction. Membership rows were
        # committed in bounded, idempotent chunks, but a rejected switch must
        # leave this false so the next attempt cannot skip the seed.
        user.user_library_seeded = True
    user.has_own_library = desired
    if commit:
        app_session.commit()
    invalidate_request_cache(user.id)
    return mode_for_user(user)


def set_enabled(user, enabled, **kwargs):
    """Compatibility adapter for callers not yet expressed in named modes."""
    mode = (constants.LIBRARY_MODE_PERSONAL if bool(enabled)
            else constants.LIBRARY_MODE_MONOLIBRARY)
    return set_library_mode(user, mode, **kwargs)


def migrate_users_to_personal_library(users, *, app_session=None, cdb=None,
                                      chunk_size=SEED_CHUNK_SIZE):
    """Explicitly seed-once and switch administrator-selected accounts.

    The durable ``user_library_seeded`` fence, rather than membership row
    count, decides whether seeding may run. That preserves a deliberately
    curated empty set and makes retries safe after partial failures.
    """
    app_session = _session(app_session)
    cdb = cdb or calibre_db
    report = []
    for user in users:
        inserted_books = 0
        was_seeded = bool(getattr(user, "user_library_seeded", False))
        previous_mode = mode_for_user(user)
        try:
            if not was_seeded:
                inserted_books = prepare_user_library_seed(
                    user,
                    chunk_size=chunk_size,
                    app_session=app_session,
                    cdb=cdb,
                )
            set_library_mode(
                user,
                constants.LIBRARY_MODE_PERSONAL,
                app_session=app_session,
                cdb=cdb,
                chunk_size=chunk_size,
                seed_rows_prepared=not was_seeded,
            )
            final_membership_count = membership_count(user.id, app_session)
            report.append({
                "user_id": int(user.id),
                "name": user.name,
                "status": (
                    "already_personal"
                    if previous_mode == constants.LIBRARY_MODE_PERSONAL
                    else "switched"
                ),
                # Number in the completed first seed, not merely rows inserted
                # by this attempt after a partial retry.
                "seeded_books": (
                    final_membership_count if not was_seeded else 0
                ),
                "inserted_books": inserted_books,
                "membership_count": final_membership_count,
                "library_mode": mode_for_user(user),
            })
        except Exception as ex:  # report one account without aborting the batch
            app_session.rollback()
            report.append({
                "user_id": int(user.id),
                "name": user.name,
                "status": "error",
                "seeded_books": 0,
                "inserted_books": inserted_books,
                "library_mode": mode_for_user(user),
                "error": str(ex),
            })
    return report


def global_missing_filter(user, *, app_session=None, cdb=None):
    """Metadata-db predicate for global books absent from a personal set."""
    if mode_for_user(user) != constants.LIBRARY_MODE_PERSONAL:
        return false()
    app_session = _session(app_session)
    cdb = cdb or calibre_db
    return db.user_library_membership_filter(
        app_session, cdb.session, user.id, include=False
    )


def dismiss_intro(user, *, app_session=None):
    """Durably and idempotently dismiss the account's introductory card."""
    app_session = _session(app_session)
    user.my_library_intro_dismissed = True
    app_session.commit()
    mark_response_user_specific()


def _require_enabled_library(user):
    if mode_for_user(user) != constants.LIBRARY_MODE_PERSONAL:
        raise UserLibraryError(
            "This action requires personal library mode."
        )


def _require_global_browse(user):
    _require_enabled_library(user)
    if not user.role_browse_global():
        raise UserLibraryError(
            "You need global-library browse permission to change My Library."
        )


def _add_visible_book(user, book_id, *, require_global_browse,
                      app_session=None, cdb=None):
    """Insert one membership after applying the target user's visibility."""
    if require_global_browse:
        _require_global_browse(user)
    else:
        _require_enabled_library(user)
    app_session = _session(app_session)
    cdb = cdb or calibre_db
    book_id = int(book_id)
    book = (cdb.session.query(db.Books)
              .filter(db.Books.id == book_id)
              .filter(cdb.common_filters(
                  allow_show_global=True,
                  user=user,
              )).first())
    if not book:
        raise UserLibraryBookNotFound(
            "Book not found in the visible global library."
        )
    mutation = app_session.execute(
        sqlite_insert(ub.UserLibraryBook)
        .values(user_id=int(user.id), book_id=book_id)
        .on_conflict_do_nothing(index_elements=["user_id", "book_id"])
    )
    app_session.commit()
    invalidate_request_cache(user.id)
    return book, max(0, mutation.rowcount or 0) > 0


def add_book(user, book_id, *, app_session=None, cdb=None,
             return_result=False):
    """Idempotently add a globally visible book to the caller's own set."""
    book, changed = _add_visible_book(
        user, book_id, require_global_browse=True,
        app_session=app_session, cdb=cdb,
    )
    if return_result:
        return MembershipMutationResult(changed=changed)
    return bool(book)


def admin_add_book(user, book_id, *, app_session=None, cdb=None):
    """Admin-managed addition for a target that cannot browse the archive.

    The administrator supplies the book, but the target user's ordinary
    language/tag/custom-column policy still decides whether it is a visible
    global book for that account. The browse-global role is intentionally not
    required: this is the recovery path promised to managed users.
    """
    book, _changed = _add_visible_book(
        user, book_id, require_global_browse=False,
        app_session=app_session, cdb=cdb,
    )
    return book


def remove_book(user, book_id, *, app_session=None, return_result=False):
    """Remove membership and this user's ordinary shelf links only.

    Kobo ownership, synced-book rows, annotations, bookmarks, and progress are
    deliberately untouched so they survive removal and a later re-add. For a
    managed account, one conditional DELETE makes the last-book policy and the
    mutation indivisible under both WAL and rollback-journal SQLite modes.
    """
    _require_enabled_library(user)
    app_session = _session(app_session)
    user_id = int(user.id)
    book_id = int(book_id)
    may_empty_library = bool(user.role_browse_global())
    statement = delete(ub.UserLibraryBook).where(
        ub.UserLibraryBook.user_id == user_id,
        ub.UserLibraryBook.book_id == book_id,
    )
    if not may_empty_library:
        other_membership = ub.UserLibraryBook.__table__.alias(
            "other_membership"
        )
        statement = statement.where(exists().where(
            other_membership.c.user_id == user_id,
            other_membership.c.book_id != book_id,
        ))
    mutation = app_session.execute(
        statement.execution_options(synchronize_session=False)
    )
    changed = max(0, mutation.rowcount or 0) > 0
    if not changed:
        membership_still_exists = (app_session.query(ub.UserLibraryBook.id)
                                   .filter(
                                       ub.UserLibraryBook.user_id == user_id,
                                       ub.UserLibraryBook.book_id == book_id,
                                   ).first()) is not None
        # Even a zero-row DELETE begins a sqlite3 write transaction. Close it
        # before reporting either an idempotent no-op or a policy rejection.
        app_session.commit()
        if membership_still_exists and not may_empty_library:
            raise UserLibraryError(
                "The last book cannot be removed unless this user can browse "
                "the global library."
            )
        result = MembershipMutationResult(changed=False)
        return result if return_result else []

    shelves = (app_session.query(ub.Shelf)
               .join(ub.BookShelf, ub.BookShelf.shelf == ub.Shelf.id)
               .filter(ub.Shelf.user_id == user_id,
                       ub.BookShelf.book_id == book_id)
               .order_by(ub.Shelf.name).all())
    shelf_names = [shelf.name for shelf in shelves]
    if shelves:
        shelf_ids = [shelf.id for shelf in shelves]
        (app_session.query(ub.BookShelf)
         .filter(ub.BookShelf.shelf.in_(shelf_ids),
                 ub.BookShelf.book_id == book_id).delete(
                     synchronize_session=False))
    app_session.commit()
    invalidate_request_cache(user.id)
    result = MembershipMutationResult(
        changed=True,
        affected_shelves=tuple(shelf_names),
    )
    return result if return_result else shelf_names


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
