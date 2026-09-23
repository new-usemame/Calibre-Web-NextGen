# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Which books belong on a user's e-readers: one rule for Kobo and KOReader.

A user decides once, on the account page, whether their e-readers carry the
whole library they can see or only the shelves they marked for e-reader sync.
Kobo sync (``cps/kobo.py``) and the KOReader library manifest
(``cps/progress_syncing/protocols/kosync_library.py``) both ask this module,
so the two can never disagree about what a device should hold.

``membership`` is the set algebra over the user's own choices:

* shelf-only sync off, whole-library mode: unbounded (every visible book);
* shelf-only sync off, My Library on: the user's My Library;
* shelf-only sync on: books on the user's ``kobo_sync`` shelves, plus the
  books of ``kobo_sync`` magic shelves, intersected with My Library when that
  is on.

``held_book_ids`` then applies the visibility funnel every other surface uses
(``CalibreDB.common_filters``: content restrictions, language, hidden books,
My Library) and leaves out archived books, which is what a device holds.

Kobo's delivery query keeps its own SQL form because its sync cursor needs the
membership timestamps; ``tests/unit/test_ereader_scope_kobo_parity.py`` drives
the real Kobo sync in every mode and fails if the two ever diverge.
"""

from dataclasses import dataclass
from typing import FrozenSet, Optional

from flask_babel import gettext as _

from .. import constants, db, ub, user_library


SCOPE_LIBRARY = "library"
SCOPE_SHELVES = "shelves"


@dataclass(frozen=True)
class Membership:
    """The books a user's own e-reader choices admit, before visibility.

    ``book_ids`` is ``None`` when nothing bounds the scope (whole library,
    no My Library): every visible book belongs. ``reliable`` is ``False``
    when a magic shelf that informs a shelf-only scope could not be read,
    in which case the set may be short and must not drive a removal (#468).
    """

    mode: str
    book_ids: Optional[FrozenSet[int]]
    reliable: bool
    personal: bool

    @property
    def bounded(self):
        return self.book_ids is not None


def shelf_only(user):
    return bool(getattr(user, "kobo_only_shelves_sync", False))


def shelf_marks_enabled(config):
    """Whether marking a shelf for e-reader sync can reach a device here.

    Kobo sync and the KOReader library both deliver the shelf-only scope, so
    the per-shelf mark matters when either one is switched on. A server that
    syncs only KOReader must still let its readers choose their shelves.
    ``config`` is the app config the caller already holds.
    """
    if getattr(config, "config_kobo_sync", False):
        return True
    return koreader_library_on()


def koreader_library_on():
    """Whether KOReader sync, and with it the KOReader library, is switched on."""
    from ..progress_syncing.settings import is_koreader_sync_enabled
    return is_koreader_sync_enabled()


def magic_shelves_off_warning():
    """What a reader is told on marking a magic shelf while the admin's magic
    shelf sync setting is off. That one setting also governs the KOReader
    library, and is labelled for e-readers once KOReader sync is on."""
    if koreader_library_on():
        return _("E-reader sync for Magic Shelves is disabled globally — this shelf "
                 "won't reach your e-readers until 'Sync Magic Shelves to e-readers "
                 "(Kobo and KOReader)' is enabled in CWA Settings.")
    return _("Kobo sync for Magic Shelves is disabled globally — "
             "this shelf won't reach your Kobo until 'Sync Magic "
             "Shelves to Kobo' is enabled in CWA Settings.")


def personal_library(user):
    """My Library is on for this user (the #1939 personal mode)."""
    return (
        bool(getattr(user, "has_own_library", False))
        or user_library.mode_for_user(user) == constants.LIBRARY_MODE_PERSONAL
    )


def _kobo_sync_shelf_book_ids(session, user_id):
    rows = (session.query(ub.BookShelf.book_id)
            .join(ub.Shelf, ub.BookShelf.shelf == ub.Shelf.id)
            .filter(ub.Shelf.user_id == user_id,
                    ub.Shelf.kobo_sync.is_(True)))
    return {row.book_id for row in rows}


def _library_book_ids(session, user_id):
    rows = (session.query(ub.UserLibraryBook.book_id)
            .filter(ub.UserLibraryBook.user_id == user_id))
    return {row.book_id for row in rows}


def membership(user, *, session=None, magic_shelf_book_ids=None,
               magic_shelf_membership_reliable=True):
    """Return the :class:`Membership` the user's e-reader choices admit.

    Kobo sync passes the magic-shelf ids it already computed for the same
    request, so the magic-shelf half is evaluated once. Other callers leave
    them out and this asks the same Kobo helper; it evaluates each magic
    shelf's rule for ``current_user``, so the request must be acting as
    ``user``.
    """
    session = session or ub.session
    user_id = int(user.id)
    shelves = shelf_only(user)
    personal = personal_library(user)
    reliable = True
    book_ids = None
    if shelves:
        if magic_shelf_book_ids is None:
            # Imported here: cps.kobo imports this module.
            from ..kobo import get_magic_shelf_book_ids_for_kobo
            (magic_shelf_book_ids,
             magic_shelf_membership_reliable) = get_magic_shelf_book_ids_for_kobo(user_id)
        book_ids = _kobo_sync_shelf_book_ids(session, user_id)
        book_ids |= set(magic_shelf_book_ids or ())
        reliable = bool(magic_shelf_membership_reliable)
    if personal:
        library_ids = _library_book_ids(session, user_id)
        book_ids = library_ids if book_ids is None else book_ids & library_ids
    return Membership(
        mode=SCOPE_SHELVES if shelves else SCOPE_LIBRARY,
        book_ids=None if book_ids is None else frozenset(book_ids),
        reliable=reliable,
        personal=personal,
    )


def visible_book_ids(user, *, cdb=None):
    """Every book id the user can see, archived and hidden books excluded."""
    if cdb is None:
        from .. import calibre_db as cdb
    cdb.ensure_session()
    rows = cdb.session.query(db.Books.id).filter(cdb.common_filters(user=user))
    return {row[0] for row in rows}


def held_book_ids(user, *, cdb=None, session=None, scope=None):
    """The ids of the books an e-reader of ``user`` should hold right now."""
    if scope is None:
        scope = membership(user, session=session)
    visible = visible_book_ids(user, cdb=cdb)
    if scope.book_ids is None:
        return frozenset(visible)
    return frozenset(visible & scope.book_ids)
