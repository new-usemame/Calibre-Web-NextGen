# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-device wanted-book queue shared by the web UI and pull client."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from .. import ub
from . import device_capabilities


QUEUED = "queued"
CLAIMED = "claimed"
COMPLETED = "completed"
FAILED = "failed"
CLAIM_TTL = timedelta(minutes=30)

# Conservative native-format sets. A format absent here is never guessed to be
# readable. In particular, KOReader delivery deliberately excludes KFX/AZW3.
_FORMAT_PRIORITY = {
    "koreader": (
        "EPUB", "PDF", "MOBI", "FB2", "DJVU", "CBZ", "CBR", "TXT", "HTML", "RTF",
    ),
    "kobo": (
        "KEPUB", "EPUB", "PDF", "MOBI", "CBZ", "CBR", "TXT", "RTF", "HTML",
    ),
}
_CHECKSUM_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')


class DeliveryValidationError(ValueError):
    pass


@dataclass(frozen=True)
class QueueResult:
    delivery: Optional[ub.DeviceBookDelivery]
    created: bool
    reason: Optional[str] = None


def _utc_now(now=None):
    return now or datetime.now(timezone.utc)


def _aware(value):
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def select_device_format(device_kind: str, formats: Iterable):
    """Return the best readable Data-like row, or ``None``."""
    by_format = {
        str(row.format).upper(): row
        for row in formats
        if getattr(row, "format", None)
    }
    for candidate in _FORMAT_PRIORITY.get((device_kind or "").lower(), ()):
        if candidate in by_format:
            return by_format[candidate]
    return None


def _delivery_filename(book_id: int, data) -> str:
    stem = _UNSAFE_FILENAME.sub("_", str(getattr(data, "name", "") or "")).strip(" .")
    if not stem:
        stem = "Book"
    # Leave room for the stable id and extension while keeping the DB and FAT
    # filename safely below their common 255-byte/component ceilings.
    stem = stem[:180]
    return f"{stem} [{book_id}].{str(data.format).lower()}"


def _available_formats(book) -> list[str]:
    return sorted({
        str(row.format).upper()
        for row in getattr(book, "data", ())
        if getattr(row, "format", None)
    })


def queue_book_for_device(*, session, user_id: int, device_public_id: str, book,
                          now=None) -> QueueResult:
    """Idempotently express that ``book`` is wanted on an owned device."""
    now = _utc_now(now)
    device = session.query(ub.Device).filter_by(
        public_id=device_public_id, user_id=user_id, active=True,
    ).one_or_none()
    if device is None:
        raise DeliveryValidationError("Device was not found for this account")
    if device.kind not in _FORMAT_PRIORITY:
        raise DeliveryValidationError("This device cannot receive books")

    present = session.query(ub.DeviceInventoryItem.id).filter_by(
        device_id=device.id, book_id=int(book.id),
    ).first()
    existing = session.query(ub.DeviceBookDelivery).filter_by(
        device_id=device.id, book_id=int(book.id),
    ).one_or_none()
    if present is not None:
        if existing is not None and existing.state not in (COMPLETED, FAILED):
            existing.state = COMPLETED
            existing.completed_at = now
            existing.claim_expires_at = None
            existing.failure_reason = "Already present in device inventory"
            session.flush()
        return QueueResult(None, False, "already_on_device")

    if existing is not None and existing.state in (QUEUED, CLAIMED, COMPLETED):
        reason = "already_delivered" if existing.state == COMPLETED else "already_queued"
        return QueueResult(existing, False, reason)

    formats = _available_formats(book)
    selected = select_device_format(device.kind, getattr(book, "data", ()))
    if selected is None:
        available = ", ".join(formats) if formats else "none"
        reason = (
            f"{device.display_name} has no readable format available "
            f"({available})"
        )
        delivery = existing or ub.DeviceBookDelivery(
            device_id=device.id, book_id=int(book.id), queued_at=now,
        )
        if existing is None:
            session.add(delivery)
        delivery.state = FAILED
        delivery.format = None
        delivery.filename = None
        delivery.expected_size = None
        delivery.failure_reason = reason
        session.flush()
        return QueueResult(delivery, existing is None, reason)

    selected_size = int(selected.uncompressed_size)
    storage = device_capabilities.latest_storage(
        session=session, device_id=device.id,
    )
    if storage is not None and selected_size > storage.free_bytes:
        return QueueResult(None, False, "insufficient_storage")

    delivery = existing or ub.DeviceBookDelivery(
        device_id=device.id, book_id=int(book.id), queued_at=now,
    )
    if existing is None:
        session.add(delivery)
    delivery.state = QUEUED
    delivery.format = str(selected.format).upper()
    delivery.filename = _delivery_filename(book.id, selected)
    delivery.expected_size = selected_size
    delivery.expected_checksum = None
    delivery.claimed_at = None
    delivery.claim_expires_at = None
    # Minted at queue time, not at first claim, and preserved across re-claims.
    # A device that claims, loses power mid-download and comes back must be able
    # to resume with the token it already holds; regenerating on reclaim would
    # strand it holding a credential the server no longer recognises.
    delivery.claim_token = secrets.token_urlsafe(32)
    delivery.completed_at = None
    delivery.failure_reason = None
    session.flush()
    return QueueResult(delivery, existing is None)


def _owned_delivery_query(session, *, user_id, device_id):
    return (
        session.query(ub.DeviceBookDelivery)
        .join(ub.Device, ub.Device.id == ub.DeviceBookDelivery.device_id)
        .filter(
            ub.DeviceBookDelivery.device_id == device_id,
            ub.Device.user_id == user_id,
            ub.Device.active.is_(True),
        )
    )


def claim_next_delivery(*, session, user_id: int, device_id: int, now=None,
                        claim_ttl=CLAIM_TTL, available_bytes=None):
    """Return the stable active claim, or lease the oldest reclaimable row."""
    now = _utc_now(now)
    rows = _owned_delivery_query(
        session, user_id=user_id, device_id=device_id,
    ).order_by(ub.DeviceBookDelivery.id).all()

    # Repeating "what do you have for me?" during one lease returns the same
    # token. The client can safely lose a response without multiplying work.
    for row in rows:
        if (row.state == CLAIMED and row.claim_expires_at is not None
                and _aware(row.claim_expires_at) > now):
            return row

    for row in rows:
        expired = (
            row.state == CLAIMED
            and (row.claim_expires_at is None or _aware(row.claim_expires_at) <= now)
        )
        if row.state != QUEUED and not expired:
            continue

        if (available_bytes is not None and row.expected_size is not None
                and row.expected_size > available_bytes):
            row.failure_reason = (
                f"insufficient_storage ({available_bytes} bytes available)"
            )
            continue

        # An inventory may have arrived after the browser queued this row. Its
        # positive observation wins; omissions still delete nothing.
        present = session.query(ub.DeviceInventoryItem.id).filter_by(
            device_id=device_id, book_id=row.book_id,
        ).first()
        if present is not None:
            row.state = COMPLETED
            row.completed_at = now
            row.claim_expires_at = None
            row.failure_reason = "Already present in device inventory"
            continue

        row.state = CLAIMED
        row.claimed_at = now
        row.claim_expires_at = now + claim_ttl
        row.claim_token = row.claim_token or secrets.token_urlsafe(32)
        row.attempt_count = int(row.attempt_count or 0) + 1
        session.flush()
        return row

    session.flush()
    return None


def refuse_delivery(*, session, user_id: int, device_id: int, delivery_id: int,
                    claim_token: str, reason: str, available_bytes=None):
    """Release a claim after a clean device-side preflight refusal."""
    row = _owned_delivery_query(
        session, user_id=user_id, device_id=device_id,
    ).filter(ub.DeviceBookDelivery.id == delivery_id).one_or_none()
    if row is None or not secrets.compare_digest(row.claim_token or "", claim_token or ""):
        raise DeliveryValidationError("Delivery claim is not valid for this device")
    if row.state != CLAIMED:
        raise DeliveryValidationError("Delivery is not currently claimed")
    if reason != "insufficient_storage":
        raise DeliveryValidationError("Invalid delivery refusal reason")
    if available_bytes is not None and (
            isinstance(available_bytes, bool) or not isinstance(available_bytes, int)
            or available_bytes < 0):
        raise DeliveryValidationError("Invalid available storage")
    row.state = QUEUED
    row.claimed_at = None
    row.claim_expires_at = None
    suffix = "" if available_bytes is None else f" ({available_bytes} bytes available)"
    row.failure_reason = reason + suffix
    session.flush()
    return row


def get_delivery_for_download(*, session, user_id: int, device_id: int,
                              delivery_id: int, claim_token: str):
    if not isinstance(claim_token, str) or not claim_token:
        return None
    return _owned_delivery_query(
        session, user_id=user_id, device_id=device_id,
    ).filter(
        ub.DeviceBookDelivery.id == delivery_id,
        ub.DeviceBookDelivery.state == CLAIMED,
        ub.DeviceBookDelivery.claim_token == claim_token,
    ).one_or_none()


def _validate_completion(*, lpath, checksum, size, mtime):
    if (not isinstance(lpath, str) or not lpath or len(lpath) > 1024
            or lpath.startswith(("/", "\\")) or "\x00" in lpath
            or any(part in ("", ".", "..")
                   for part in lpath.replace("\\", "/").split("/"))):
        raise DeliveryValidationError("Invalid installed path")
    if not isinstance(checksum, str) or not _CHECKSUM_RE.fullmatch(checksum):
        raise DeliveryValidationError("Invalid installed checksum")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise DeliveryValidationError("Invalid installed size")
    if isinstance(mtime, bool) or not isinstance(mtime, int) or mtime < 0:
        raise DeliveryValidationError("Invalid installed mtime")


def complete_delivery(*, session, user_id: int, device_id: int, delivery_id: int,
                      claim_token: str, lpath: str, checksum: str, size: int,
                      mtime: int, now=None):
    """Acknowledge an atomic install; repeating the same ack is a no-op."""
    _validate_completion(lpath=lpath, checksum=checksum, size=size, mtime=mtime)
    row = _owned_delivery_query(
        session, user_id=user_id, device_id=device_id,
    ).filter(ub.DeviceBookDelivery.id == delivery_id).one_or_none()
    if row is None or not secrets.compare_digest(row.claim_token or "", claim_token or ""):
        raise DeliveryValidationError("Delivery claim is not valid for this device")
    if row.state not in (CLAIMED, COMPLETED):
        raise DeliveryValidationError("Delivery is not currently claimed")
    if row.state == COMPLETED:
        return row

    row.state = COMPLETED
    row.completed_at = _utc_now(now)
    row.claim_expires_at = None
    row.installed_lpath = lpath
    row.installed_checksum = checksum.lower()
    row.installed_size = size
    row.installed_mtime = mtime
    row.failure_reason = None
    session.flush()
    return row
