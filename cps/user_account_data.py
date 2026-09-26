# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Single source of truth for everything app.db holds for one user account.

:mod:`cps.user_book_data` enumerates the rows that tie a user to a *book*.
This module is its sibling for the rest: the rows that belong to the account
itself — registered devices and their ledgers, KOReader progress, shelves and
magic shelves, credentials, sessions, preferences — plus the user row.

Deleting a user used to call the per-user-book enumerator and then a
hand-written list for the remainder. That list had fallen behind the schema:
an admin who deleted an account was told it was gone while its e-readers
(names, identities, storage snapshots), KOReader reading positions and magic
shelves stayed in app.db.

:func:`purge_user_account` is now the only place that set is enumerated. It
does not commit; the caller owns the transaction. SQLite foreign-key
cascades are not enabled, so every child is deleted before its parent. When
adding a model that belongs to a user (a ``user_id`` column, or a parent that
is user-scoped such as ``Device``), add it here or to
``user_book_data.PER_USER_BOOK_MODELS``; the test suite walks the model
registry and fails on any user-scoped table left out.
"""

import os
import shutil

from . import logger, ub, user_book_data

log = logger.create()


# Models removed with an account that are not per-user-book rows. Children
# are listed with their parents; the order here is documentation, the purge
# below owns the delete order.
USER_ACCOUNT_MODELS = (
    # Per-user book preferences outside the book enumerator (never migrated
    # between books, so they only need the account-level purge).
    "FavoriteBook",
    "UserBookCover",              # + the user's cover JPEGs under CONFIG_DIR
    "KoboDeletedBook",            # tombstones for the user's Kobo sync
    # Registered devices and every ledger hanging off them.
    "Device",
    "DeviceIdentity",
    "DeviceInventoryReport",
    "DeviceInventoryItem",
    "DeviceBookDeletion",
    "DeviceStorageSnapshot",
    "DeviceCollectionSync",
    "AnnotationDeviceState",
    "DeviceRetiredAssignment",
    "KoboDeviceBookDownload",
    # KOReader (kosync protocol) positions and device names.
    "KOSyncProgress",
    # Shelves and magic shelves, including other users' rows that point at
    # them (hide records, OPDS exposure, per-viewer cache).
    "Shelf",
    "BookShelf",
    "ShelfArchive",
    "OpdsShelfExposure",
    "MagicShelf",
    "MagicShelfCache",
    "HiddenMagicShelfTemplate",
    "OpdsMagicShelfExposure",
    # Credentials, sessions and sign-in links.
    "UserAppPassword",
    "KOReaderPairing",
    "RemoteAuthToken",
    "User_Sessions",
    "OAuth",
    # Preferences and notices.
    "CoverDesignPreset",
    "HiddenCoverDesignPreset",
    "DismissedDuplicateGroup",
    "UserNoticeDelivery",
    # The account itself.
    "User",
)


def _delete(session, model, *criteria):
    session.query(model).filter(*criteria).delete(synchronize_session=False)


def purge_user_account(user_id, session=None):
    """Delete the user row and everything app.db holds for that account.

    Runs the per-user-book purge first (it scopes some ledgers through the
    user's devices, so it must see them), then the account-level rows, then
    the user. Also removes the account's personal cover images from disk.
    Does not commit. Guard rails (Guest, last admin) are the caller's.
    """
    session = session if session is not None else ub.session
    user_id = int(user_id)

    user_book_data.purge_user_book_data(user_id=user_id, session=session)

    _delete(session, ub.FavoriteBook, ub.FavoriteBook.user_id == user_id)
    _delete(session, ub.UserBookCover, ub.UserBookCover.user_id == user_id)
    _delete(session, ub.KoboDeletedBook, ub.KoboDeletedBook.user_id == user_id)

    # Devices: every child before the device rows themselves. The book purge
    # above already removed the device-scoped Kobo entitlement, pending-page,
    # position and delivery ledgers, and the Kobo annotation state reached
    # through the user's book states.
    device_ids = session.query(ub.Device.id).filter(
        ub.Device.user_id == user_id).scalar_subquery()
    for model in (ub.DeviceBookDeletion,        # -> device_inventory_item
                  ub.DeviceInventoryItem,       # -> device_inventory_report
                  ub.DeviceInventoryReport,
                  ub.DeviceIdentity,
                  ub.DeviceStorageSnapshot,
                  ub.AnnotationDeviceState,
                  ub.DeviceRetiredAssignment,
                  ub.KoboDeviceBookDownload):
        _delete(session, model, model.device_id.in_(device_ids))
    _delete(session, ub.DeviceCollectionSync,
            (ub.DeviceCollectionSync.user_id == user_id)
            | ub.DeviceCollectionSync.device_id.in_(device_ids))
    _delete(session, ub.Device, ub.Device.user_id == user_id)

    from .progress_syncing.models import KOSyncProgress
    _delete(session, KOSyncProgress, KOSyncProgress.user_id == user_id)

    # Magic shelves. A public one is deleted too, exactly as a regular public
    # shelf always has been: it is the owner's content, and other users only
    # held a view of it. Their rows that point at it (hide records, OPDS
    # exposure, cached results) go with it so a recycled id cannot inherit
    # them.
    magic_ids = session.query(ub.MagicShelf.id).filter(
        ub.MagicShelf.user_id == user_id).scalar_subquery()
    for model in (ub.MagicShelfCache, ub.HiddenMagicShelfTemplate,
                  ub.OpdsMagicShelfExposure):
        _delete(session, model,
                (model.user_id == user_id) | model.shelf_id.in_(magic_ids))
    _delete(session, ub.MagicShelf, ub.MagicShelf.user_id == user_id)

    # Regular shelves, their book links and OPDS exposure (the owner's and
    # other users' of the owner's public shelves). The shelf-archive rows are
    # Kobo tombstones for this user's own devices, which are gone.
    shelf_ids = session.query(ub.Shelf.id).filter(
        ub.Shelf.user_id == user_id).scalar_subquery()
    _delete(session, ub.BookShelf, ub.BookShelf.shelf.in_(shelf_ids))
    _delete(session, ub.OpdsShelfExposure,
            (ub.OpdsShelfExposure.user_id == user_id)
            | ub.OpdsShelfExposure.shelf_id.in_(shelf_ids))
    _delete(session, ub.Shelf, ub.Shelf.user_id == user_id)
    _delete(session, ub.ShelfArchive, ub.ShelfArchive.user_id == user_id)

    # Credentials: pairings reference app passwords, so they go first.
    from .services import koreader_pairing
    koreader_pairing.forget_user(user_id, session=session)
    _delete(session, ub.UserAppPassword, ub.UserAppPassword.user_id == user_id)
    _delete(session, ub.RemoteAuthToken, ub.RemoteAuthToken.user_id == user_id)
    _delete(session, ub.User_Sessions, ub.User_Sessions.user_id == user_id)
    oauth = getattr(ub, "OAuth", None)  # absent without flask-dance
    if oauth is not None:
        _delete(session, oauth, oauth.user_id == user_id)

    # Cover-design presets. A library-scoped preset an admin shared is the
    # owner's content like a public shelf; other users' "hide this preset"
    # rows for it go too, since preset ids can be reused.
    library_keys = ["library-%s" % row.id for row in session.query(
        ub.CoverDesignPreset.id).filter(
        ub.CoverDesignPreset.user_id == user_id,
        ub.CoverDesignPreset.scope == "library")]
    _delete(session, ub.HiddenCoverDesignPreset,
            (ub.HiddenCoverDesignPreset.user_id == user_id)
            | ub.HiddenCoverDesignPreset.preset_key.in_(library_keys))
    _delete(session, ub.CoverDesignPreset,
            ub.CoverDesignPreset.user_id == user_id)

    _delete(session, ub.DismissedDuplicateGroup,
            ub.DismissedDuplicateGroup.user_id == user_id)
    _delete(session, ub.UserNoticeDelivery,
            ub.UserNoticeDelivery.user_id == user_id)

    # Default synchronize_session: a caller still holding the User instance
    # sees it as deleted (its loaded attributes stay readable) instead of an
    # expired row that can no longer be refreshed.
    session.query(ub.User).filter(ub.User.id == user_id).delete()
    session.flush()

    from .services import user_cover
    cover_dir = user_cover.cover_directory(user_id)
    if os.path.isdir(cover_dir):
        try:
            shutil.rmtree(cover_dir)
        except OSError as e:
            log.warning("[user-account-data] could not remove personal "
                        "covers %s: %s", cover_dir, e)
    log.info("[user-account-data] purged account data for user_id=%s", user_id)
