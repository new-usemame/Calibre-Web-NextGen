# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Storage, named deletion and shelf-collection state for registered devices."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone

from .. import ub


REQUESTED = "requested"
CLAIMED = "claimed"
COMPLETED = "completed"
FAILED = "failed"


class CapabilityValidationError(ValueError):
    pass


def _now(value=None):
    return value or datetime.now(timezone.utc)


def validate_storage(free_bytes, total_bytes):
    for value in (free_bytes, total_bytes):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise CapabilityValidationError("Storage values must be non-negative integers")
    if free_bytes > total_bytes:
        raise CapabilityValidationError("Free space cannot exceed total space")


def record_storage(*, session, user_id, device_id, free_bytes, total_bytes, now=None):
    validate_storage(free_bytes, total_bytes)
    device = session.query(ub.Device.id).filter_by(
        id=device_id, user_id=user_id, active=True,
    ).one_or_none()
    if device is None:
        raise CapabilityValidationError("Device was not found for this account")
    row = ub.DeviceStorageSnapshot(
        device_id=device_id, observed_at=_now(now),
        free_bytes=free_bytes, total_bytes=total_bytes,
    )
    session.add(row)
    session.flush()
    return row


def latest_storage(*, session, device_id):
    return session.query(ub.DeviceStorageSnapshot).filter_by(
        device_id=device_id,
    ).order_by(
        ub.DeviceStorageSnapshot.observed_at.desc(),
        ub.DeviceStorageSnapshot.id.desc(),
    ).first()


def queue_named_deletion(*, session, user_id, device_public_id,
                         inventory_item_id, now=None):
    item = (
        session.query(ub.DeviceInventoryItem)
        .join(ub.Device, ub.Device.id == ub.DeviceInventoryItem.device_id)
        .filter(
            ub.Device.public_id == device_public_id,
            ub.Device.user_id == user_id,
            ub.Device.active.is_(True),
            ub.DeviceInventoryItem.id == inventory_item_id,
        )
        .one_or_none()
    )
    if item is None:
        raise CapabilityValidationError("Named inventory item was not found for this device")
    existing = session.query(ub.DeviceBookDeletion).filter_by(
        device_id=item.device_id, lpath=item.lpath, checksum=item.checksum,
    ).one_or_none()
    if existing is not None:
        if existing.state == FAILED:
            existing.state = REQUESTED
            existing.requested_at = _now(now)
            existing.claimed_at = None
            existing.completed_at = None
            existing.failure_reason = None
            existing.claim_token = secrets.token_urlsafe(32)
            session.flush()
        return existing
    row = ub.DeviceBookDeletion(
        device_id=item.device_id,
        inventory_item_id=item.id,
        book_id=item.book_id,
        lpath=item.lpath,
        checksum=item.checksum,
        state=REQUESTED,
        requested_at=_now(now),
        claim_token=secrets.token_urlsafe(32),
    )
    session.add(row)
    session.flush()
    return row


def _owned_deletions(session, *, user_id, device_id):
    return (
        session.query(ub.DeviceBookDeletion)
        .join(ub.Device, ub.Device.id == ub.DeviceBookDeletion.device_id)
        .filter(
            ub.DeviceBookDeletion.device_id == device_id,
            ub.Device.user_id == user_id,
            ub.Device.active.is_(True),
        )
    )


def claim_next_deletion(*, session, user_id, device_id, now=None):
    active = _owned_deletions(
        session, user_id=user_id, device_id=device_id,
    ).filter(ub.DeviceBookDeletion.state == CLAIMED).order_by(
        ub.DeviceBookDeletion.id,
    ).first()
    if active is not None:
        return active
    row = _owned_deletions(
        session, user_id=user_id, device_id=device_id,
    ).filter(ub.DeviceBookDeletion.state == REQUESTED).order_by(
        ub.DeviceBookDeletion.id,
    ).first()
    if row is not None:
        row.state = CLAIMED
        row.claimed_at = _now(now)
        session.flush()
    return row


def complete_deletion(*, session, user_id, device_id, deletion_id,
                      claim_token, deleted, failure_reason=None, now=None):
    row = _owned_deletions(
        session, user_id=user_id, device_id=device_id,
    ).filter(ub.DeviceBookDeletion.id == deletion_id).one_or_none()
    if row is None or not secrets.compare_digest(row.claim_token or "", claim_token or ""):
        raise CapabilityValidationError("Deletion claim is not valid for this device")
    if row.state == COMPLETED:
        return row
    if row.state != CLAIMED:
        raise CapabilityValidationError("Deletion is not currently claimed")
    if not isinstance(deleted, bool):
        raise CapabilityValidationError("Deletion result must be a boolean")

    row.completed_at = _now(now)
    if deleted:
        # This exact positive acknowledgement is the only path that removes an
        # inventory observation. A later report omitting a path never calls it.
        item = session.query(ub.DeviceInventoryItem).filter_by(
            id=row.inventory_item_id,
            device_id=device_id,
            lpath=row.lpath,
            checksum=row.checksum,
        ).one_or_none()
        if item is not None:
            session.delete(item)
        row.state = COMPLETED
        row.failure_reason = None
    else:
        row.state = FAILED
        row.failure_reason = str(failure_reason or "Device refused deletion")[:512]
    session.flush()
    return row


def _collection_payload(session, *, user_id, device_id):
    report = session.query(ub.DeviceInventoryReport).filter_by(
        device_id=device_id,
    ).order_by(ub.DeviceInventoryReport.id.desc()).first()
    inventory = [] if report is None else session.query(ub.DeviceInventoryItem).filter_by(
        device_id=device_id, last_report_id=report.id,
    ).all()
    paths_by_book = {}
    for item in inventory:
        if item.book_id is not None:
            paths_by_book.setdefault(item.book_id, []).append(item.lpath)

    collections = []
    shelves = session.query(ub.Shelf).filter_by(user_id=user_id).order_by(
        ub.Shelf.name, ub.Shelf.id,
    ).all()
    for shelf in shelves:
        book_ids = [link.book_id for link in shelf.books.order_by(
            ub.BookShelf.order, ub.BookShelf.id,
        ).all()]
        books = sorted({path for book_id in book_ids for path in paths_by_book.get(book_id, ())})
        collections.append({
            "id": shelf.uuid or str(shelf.id),
            "name": shelf.name or "",
            "books": books,
        })
    return collections


def collection_snapshot(*, session, user_id, device_id, now=None):
    device = session.query(ub.Device.id).filter_by(
        id=device_id, user_id=user_id, active=True,
    ).one_or_none()
    if device is None:
        raise CapabilityValidationError("Device was not found for this account")
    collections = _collection_payload(
        session, user_id=user_id, device_id=device_id,
    )
    encoded = json.dumps(collections, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    row = session.query(ub.DeviceCollectionSync).filter_by(
        user_id=user_id, device_id=device_id,
    ).one_or_none()
    now = _now(now)
    if row is None:
        row = ub.DeviceCollectionSync(
            user_id=user_id, device_id=device_id, snapshot_hash=digest,
            revision=1, delivered_at=now,
        )
        session.add(row)
        session.flush()
    elif row.snapshot_hash != digest:
        row.snapshot_hash = digest
        row.revision += 1
        row.delivered_at = now
        row.applied_at = None
    else:
        row.delivered_at = now
    session.flush()
    return {
        "scope": row.scope_id,
        "revision": row.revision,
        "collections": collections,
    }


def acknowledge_collections(*, session, user_id, device_id, revision, now=None):
    row = session.query(ub.DeviceCollectionSync).filter_by(
        user_id=user_id, device_id=device_id,
    ).one_or_none()
    if row is None or isinstance(revision, bool) or revision != row.revision:
        raise CapabilityValidationError("Collection revision is not current for this device")
    row.applied_at = _now(now)
    session.flush()
    return row
