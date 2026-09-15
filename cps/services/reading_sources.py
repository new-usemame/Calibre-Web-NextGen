# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Normalize stored reading positions without collapsing their provenance.

The per-device journal is evidence of what each source last reported.  The
Kobo bookmark is a separate, account-level sync carrier.  It may have reached
its value through a browser, Kobo, KOReader, or rehydration response, so this
module deliberately never labels that carrier as a particular device.
"""

from datetime import timezone

from .kobo_resume import chapter_resume, exact_resume


def _iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _device_resume(position, book_id, epub_path=None):
    resume = {"percentage": position.progress_percent, "exact": False}
    if position.cfi:
        resume.update({"cfi": position.cfi, "exact": True})
        return resume
    exact = exact_resume(
        book_id, position.location_source, position.location_type,
        position.location_value,
    )
    if exact:
        resume.update(exact)
        resume["exact"] = True
    elif position.location_type == "KoboSpan":
        chapter = chapter_resume(
            epub_path, position.location_source, position.location_type,
            position.location_value,
            position.content_source_progress_percent,
        )
        if chapter:
            resume.update(chapter)
    return resume


def device_source_rows(devices, positions, *, book_id, epub_path=None):
    """Return one honest last-report row per source, including stale reports."""
    by_device = {position.device_id: position for position in positions}
    rows = []
    for device in devices:
        position = by_device.get(device.id)
        if position is None:
            continue
        observed = position.client_modified_at or position.server_modified_at
        rows.append({
            "id": "device:{}".format(device.public_id),
            "device_public_id": device.public_id,
            "label": device.display_name or device.kind,
            "kind": device.kind,
            "active": bool(device.active),
            "observation": "last_reported",
            "progress_percent": position.progress_percent,
            "chapter_progress_percent": position.content_source_progress_percent,
            "observed_at": _iso(observed),
            "received_at": _iso(position.server_modified_at),
            "locator_type": position.location_type or ("epub_cfi" if position.cfi else None),
            "resume": _device_resume(position, book_id, epub_path),
            "writeback": "read_only",
            "rehydrate_needed": bool(position.rehydrate_needed),
        })
    return rows


def resolved_source_row(bookmark, *, book_id, exact=None):
    """Serialize the shared carrier while keeping unknown provenance unknown."""
    if bookmark is None or bookmark.progress_percent is None:
        return None
    if exact is None:
        exact = exact_resume(
            book_id, bookmark.location_source, bookmark.location_type,
            bookmark.location_value,
        )
    resume = {"percentage": bookmark.progress_percent, "exact": bool(exact)}
    if exact:
        resume.update(exact)
    return {
        "id": "synced",
        "label": "Other saved position",
        "kind": "resolved",
        "observation": "resolved",
        "provenance": "unknown",
        "progress_percent": bookmark.progress_percent,
        "observed_at": _iso(bookmark.last_modified),
        "locator_type": bookmark.location_type,
        "resume": resume,
        "writeback": "read_only",
    }
