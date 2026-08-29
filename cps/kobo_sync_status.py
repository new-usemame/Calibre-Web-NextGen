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
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
# from sqlalchemy import exc

log = logger.create()

_LEDGER_UPSERT_BATCH_SIZE = 250


# Record the current user's delivered book identity.
def add_synced_books(book_id, book_uuid=None):
    synced_book = ub.session.query(ub.KoboSyncedBooks).filter(
        ub.KoboSyncedBooks.book_id == book_id,
        ub.KoboSyncedBooks.user_id == current_user.id,
    ).one_or_none()
    if synced_book is None:
        ub.session.add(ub.KoboSyncedBooks(
            user_id=current_user.id,
            book_id=book_id,
            book_uuid=str(book_uuid) if book_uuid else None,
        ))
    elif book_uuid and synced_book.book_uuid != str(book_uuid):
        synced_book.book_uuid = str(book_uuid)
    ub.session_commit()


def _book_identity(identity):
    if isinstance(identity, (tuple, list)) and len(identity) == 2:
        book_id, book_uuid = identity
        return int(book_id), str(book_uuid) if book_uuid else None
    return int(identity), None


def add_synced_books_batch(book_identities, *, commit=True):
    """Stage a delivered page, optionally committing the shared transaction."""
    page_books = dict(_book_identity(identity) for identity in book_identities)
    if not page_books:
        return True

    user_id = current_user.id
    present = {
        row.book_id: row for row in
        ub.session.query(ub.KoboSyncedBooks).filter(
            ub.KoboSyncedBooks.user_id == user_id,
            ub.KoboSyncedBooks.book_id.in_(page_books),
        ).all()
    }
    for book_id, row in present.items():
        book_uuid = page_books[book_id]
        if book_uuid and row.book_uuid != book_uuid:
            row.book_uuid = book_uuid
    missing_book_ids = set(page_books) - set(present)
    if missing_book_ids:
        ub.session.bulk_save_objects([
            ub.KoboSyncedBooks(
                user_id=user_id,
                book_id=book_id,
                book_uuid=page_books[book_id],
            )
            for book_id in missing_book_ids
        ])
    return ub.session_commit() if commit else True


def get_device_entitlement_fingerprints(device_id, book_ids):
    """Return the last delivered ledger record for each candidate book."""
    if not device_id or not book_ids:
        return {}
    rows = ub.session.query(
            ub.KoboDeviceBookEntitlement.book_id,
            ub.KoboDeviceBookEntitlement.fingerprint,
            ub.KoboDeviceBookEntitlement.payload_schema_version,
            ub.KoboDeviceBookEntitlement.change_basis,
            ub.KoboDeviceBookEntitlement.updated_at,
        ).filter(
            ub.KoboDeviceBookEntitlement.device_id == int(device_id),
            ub.KoboDeviceBookEntitlement.book_id.in_(set(book_ids)),
        ).all()
    return {row.book_id: row for row in rows}


def stage_device_entitlement_fingerprints(
    device_id, fingerprints, change_bases=None, payload_schema_version=1,
):
    """Upsert delivered entitlement hashes into the caller's transaction.

    The sync handler stages this alongside ``add_synced_books_batch``
    (``commit=False``) and makes both durable in one checked request-level
    commit before the response token is constructed.
    """
    if not device_id or not fingerprints:
        return
    change_bases = change_bases or {}
    now = datetime.now(timezone.utc)
    items = list(fingerprints.items())
    for offset in range(0, len(items), _LEDGER_UPSERT_BATCH_SIZE):
        rows = [
            {
                "device_id": int(device_id),
                "book_id": int(book_id),
                "fingerprint": fingerprint,
                "payload_schema_version": int(payload_schema_version),
                "change_basis": change_bases.get(book_id),
                "updated_at": now,
            }
            for book_id, fingerprint in items[
                offset:offset + _LEDGER_UPSERT_BATCH_SIZE
            ]
        ]
        statement = sqlite_insert(ub.KoboDeviceBookEntitlement).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=["device_id", "book_id"],
            set_={
                "fingerprint": statement.excluded.fingerprint,
                "payload_schema_version": statement.excluded.payload_schema_version,
                "change_basis": statement.excluded.change_basis,
                "updated_at": statement.excluded.updated_at,
            },
        )
        ub.session.execute(statement)


def get_device_deleted_entitlement_fingerprints(device_id, book_uuids):
    """Return delivered hard-delete ledger records for one device."""
    if not device_id or not book_uuids:
        return {}
    rows = ub.session.query(
            ub.KoboDeviceDeletedEntitlement.book_uuid,
            ub.KoboDeviceDeletedEntitlement.fingerprint,
            ub.KoboDeviceDeletedEntitlement.payload_schema_version,
            ub.KoboDeviceDeletedEntitlement.change_basis,
            ub.KoboDeviceDeletedEntitlement.updated_at,
        ).filter(
            ub.KoboDeviceDeletedEntitlement.device_id == int(device_id),
            ub.KoboDeviceDeletedEntitlement.book_uuid.in_(set(book_uuids)),
        ).all()
    return {row.book_uuid: row for row in rows}


def stage_device_deleted_entitlement_fingerprints(
    device_id, fingerprints, change_bases=None, payload_schema_version=1,
):
    """Upsert hard-delete entitlement hashes into the sync transaction."""
    if not device_id or not fingerprints:
        return
    change_bases = change_bases or {}
    now = datetime.now(timezone.utc)
    items = list(fingerprints.items())
    for offset in range(0, len(items), _LEDGER_UPSERT_BATCH_SIZE):
        rows = [
            {
                "device_id": int(device_id),
                "book_uuid": str(book_uuid),
                "fingerprint": fingerprint,
                "payload_schema_version": int(payload_schema_version),
                "change_basis": change_bases.get(book_uuid),
                "updated_at": now,
            }
            for book_uuid, fingerprint in items[
                offset:offset + _LEDGER_UPSERT_BATCH_SIZE
            ]
        ]
        statement = sqlite_insert(ub.KoboDeviceDeletedEntitlement).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=["device_id", "book_uuid"],
            set_={
                "fingerprint": statement.excluded.fingerprint,
                "payload_schema_version": statement.excluded.payload_schema_version,
                "change_basis": statement.excluded.change_basis,
                "updated_at": statement.excluded.updated_at,
            },
        )
        ub.session.execute(statement)


def get_unseeded_kobo_device_ids(user_id):
    """Return this user's physical Kobo devices lacking an upgrade seed."""
    device_ids = {
        row.id for row in ub.session.query(ub.Device.id).filter(
            ub.Device.user_id == int(user_id),
            ub.Device.kind == "kobo",
        ).all()
    }
    if not device_ids:
        return []
    seeded = {
        row.device_id for row in
        ub.session.query(ub.KoboDeviceEntitlementSeed.device_id).filter(
            ub.KoboDeviceEntitlementSeed.device_id.in_(device_ids),
        ).all()
    }
    return sorted(device_ids - seeded)


def user_has_completed_entitlement_seed(user_id):
    """Whether this user's upgrade boundary has already been crossed."""
    return ub.session.query(ub.KoboDeviceEntitlementSeed.device_id).join(
        ub.Device,
        ub.Device.id == ub.KoboDeviceEntitlementSeed.device_id,
    ).filter(
        ub.Device.user_id == int(user_id),
        ub.Device.kind == "kobo",
    ).first() is not None


def mark_device_entitlement_ledgers_seeded(device_ids):
    """Idempotently mark complete upgrade seeding for physical devices."""
    device_ids = sorted({int(device_id) for device_id in device_ids if device_id})
    if not device_ids:
        return
    now = datetime.now(timezone.utc)
    statement = sqlite_insert(ub.KoboDeviceEntitlementSeed).values([
        {"device_id": device_id, "seeded_at": now}
        for device_id in device_ids
    ])
    ub.session.execute(statement.on_conflict_do_nothing(index_elements=["device_id"]))


def get_kobo_device_ids_requiring_classification(user_id, version):
    """Return seeded Kobo devices whose delivery rows predate ``version``."""
    return [
        row.device_id for row in ub.session.query(
            ub.KoboDeviceEntitlementSeed.device_id,
        ).join(
            ub.Device,
            ub.Device.id == ub.KoboDeviceEntitlementSeed.device_id,
        ).filter(
            ub.Device.user_id == int(user_id),
            ub.Device.kind == "kobo",
            ub.KoboDeviceEntitlementSeed.classification_version < int(version),
        ).order_by(ub.KoboDeviceEntitlementSeed.device_id).all()
    ]


def mark_device_entitlement_classification(device_ids, version):
    """Stage the completed New/Changed classification migration."""
    normalized = sorted({int(device_id) for device_id in device_ids if device_id})
    if not normalized:
        return
    ub.session.query(ub.KoboDeviceEntitlementSeed).filter(
        ub.KoboDeviceEntitlementSeed.device_id.in_(normalized),
    ).update(
        {ub.KoboDeviceEntitlementSeed.classification_version: int(version)},
        synchronize_session=False,
    )


def _record_user_book_deletions(session, user_id, book_deletions, deleted_at):
    added = 0
    for book_id, book_uuid in book_deletions:
        if not book_uuid:
            continue
        existing = (
            session.query(ub.KoboDeletedBook)
            .filter(ub.KoboDeletedBook.user_id == user_id,
                    ub.KoboDeletedBook.book_uuid == book_uuid)
            .one_or_none()
        )
        if existing is None:
            session.add(ub.KoboDeletedBook(
                user_id=user_id,
                book_uuid=book_uuid,
                deleted_at=deleted_at,
            ))
            added += 1
        if book_id is not None:
            session.query(ub.KoboSyncedBooks).filter(
                ub.KoboSyncedBooks.user_id == user_id,
                ub.KoboSyncedBooks.book_id == book_id,
            ).delete(synchronize_session=False)
    return added


def record_user_book_deletions(user_id, book_uuids, session=None):
    """Record explicitly confirmed UUID tombstones for one user."""
    s = session if session else ub.session
    normalized = list(dict.fromkeys(
        str(book_uuid) for book_uuid in book_uuids if book_uuid
    ))
    if not normalized:
        return 0
    added = _record_user_book_deletions(
        s,
        int(user_id),
        [(None, book_uuid) for book_uuid in normalized],
        datetime.now(timezone.utc),
    )
    if session is None:
        ub.session_commit()
    else:
        ub.session_commit(_session=s)
    return added


def record_book_deletion(book_id, book_uuid, session=None):
    """Record a book hard-deletion as a tombstone for each user who had
    it synced to a Kobo device.

    Called by editbooks.delete_whole_book / delete_book_from_table BEFORE
    the metadata.db row is removed (so book.uuid is still accessible).

    For every (user_id, book_id) pair in kobo_synced_books with this
    book_id, inserts a kobo_deleted_book row capturing the UUID. The
    Kobo sync handler emits an archived ChangedEntitlement for these rows on
    each affected user's next sync, then advances archive_last_modified past
    them so each device cursor moves beyond the tombstone. Without this,
    the device retains the book locally forever — calibre absence is
    not interpreted as deletion, only tombstones are.

    The UUID retained at delivery wins whenever it is present, even if the
    caller also supplies one: it is what that user's device was actually told
    the book was, and the caller's value is only a fallback for rows written
    before UUID retention existed. If both are empty the row is a no-op.

    Idempotent per (user_id, book_uuid): the existing-row check preserves the
    first tombstone timestamp, backed by the table's unique constraint.
    """
    s = session if session else ub.session
    affected_rows = s.query(ub.KoboSyncedBooks).filter(
        ub.KoboSyncedBooks.book_id == book_id).all()
    if not affected_rows:
        return

    now = datetime.now(timezone.utc)
    recorded = False
    for synced_book in affected_rows:
        retained_uuid = synced_book.book_uuid or book_uuid
        if not retained_uuid:
            continue
        _record_user_book_deletions(
            s,
            synced_book.user_id,
            [(book_id, str(retained_uuid))],
            now,
        )
        recorded = True

    if not recorded:
        return

    if session is None:
        ub.session_commit()
    else:
        ub.session_commit(_session=s)


# Select all entries of current book in kobo_synced_books table, which are from current user and delete them
def remove_synced_book(book_id, all=False, session=None):
    s = session if session is not None else ub.session
    if not all:
        user = ub.KoboSyncedBooks.user_id == current_user.id
        device_ids = s.query(ub.Device.id).filter(
            ub.Device.user_id == current_user.id).scalar_subquery()
        device_filter = ub.KoboDeviceBookEntitlement.device_id.in_(device_ids)
    else:
        user = true()
        device_filter = true()
    s.query(ub.KoboDeviceBookEntitlement).filter(
        ub.KoboDeviceBookEntitlement.book_id == book_id,
    ).filter(device_filter).delete(synchronize_session=False)
    s.query(ub.KoboSyncedBooks).filter(
        ub.KoboSyncedBooks.book_id == book_id).filter(user).delete()
    if session is None:
        ub.session_commit()
    else:
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
    added = False
    for a in shelves_to_archive:
        if a.uuid in already:
            continue
        ub.session.add(ub.ShelfArchive(uuid=a.uuid, user_id=user_id))
        added = True
    # One commit for the user, not one per shelf. A bulk admin edit reaches this
    # once per selected account, and on SQLite every commit is an fsync — 100
    # users x 20 shelves used to be 2000 serial fsyncs inside one request.
    if added:
        ub.session_commit()


def needs_shelf_reconciliation(old_value, new_value):
    """Is this the "sync only selected shelves to Kobo" transition that has to
    record shelf tombstones?

    Only off -> on. Turning the setting back off needs nothing: the shelves the
    device dropped are re-sent by the next sync. Every call site that writes
    ``User.kobo_only_shelves_sync`` asks this question, so it is answered in one
    place — the four copies of this test are what let the SPA endpoint drift out
    of sync in the first place (#866/#1008).
    """
    return not old_value and bool(new_value)


def reconcile_shelves_safely(user_id):
    """``update_on_sync_shelfs`` with the shared failure policy. Returns True if
    the reconciliation completed.

    The setting itself is the user's choice and has to stick even if this trips,
    so failures are logged rather than raised. Repeating a failed run is safe —
    ``update_on_sync_shelfs`` skips shelves it has already tombstoned.
    """
    try:
        update_on_sync_shelfs(user_id)
        return True
    except Exception:
        # Leave the session usable for the rest of the request: this commits, so
        # a failure can leave the session in a state that breaks serialization.
        ub.session.rollback()
        log.error("Could not archive unsynced shelves for user %s", user_id, exc_info=True)
        return False
