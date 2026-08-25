# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later

"""Safety classification for device-reported Kobo entitlements."""

from dataclasses import dataclass, replace

from .kobo_import import KoboContentScan, ParsedKoboBook


@dataclass(frozen=True)
class ReconciliationPreview:
    candidates: tuple[ParsedKoboBook, ...]
    volume_rows: int
    skipped_invalid: int
    skipped_preview: int
    skipped_unclassified: int
    skipped_present: int
    skipped_unresolved: int
    already_scheduled: int


def build_reconciliation_preview(scan, existing_tombstone_uuids,
                                 synced_book_uuids, book_lookup):
    """Remove server-known non-candidates; leave device ownership to a human."""
    existing_tombstone_uuids = set(existing_tombstone_uuids)
    synced_book_uuids = set(synced_book_uuids)
    candidates = []
    skipped_present = 0
    skipped_unresolved = 0
    already_scheduled = 0

    for book in scan.books:
        if book.uuid in existing_tombstone_uuids:
            already_scheduled += 1
            continue
        try:
            library_book = book_lookup(book.uuid)
        except Exception:
            skipped_unresolved += 1
            continue
        if library_book is not None:
            skipped_present += 1
            continue
        candidates.append(replace(book, synced=book.uuid in synced_book_uuids))

    return ReconciliationPreview(
        candidates=tuple(candidates),
        volume_rows=scan.volume_rows,
        skipped_invalid=scan.skipped_invalid,
        skipped_preview=scan.skipped_preview,
        skipped_unclassified=scan.skipped_unclassified,
        skipped_present=skipped_present,
        skipped_unresolved=skipped_unresolved,
        already_scheduled=already_scheduled,
    )


def scan_from_candidates(candidates):
    """Rebuild a scan for confirmation-time safety revalidation."""
    books = tuple(candidates)
    return KoboContentScan(
        books=books,
        volume_rows=len(books),
        skipped_invalid=0,
        skipped_preview=0,
        skipped_unclassified=0,
    )
