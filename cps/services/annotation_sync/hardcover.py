# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""HardcoverHandler — push/delete annotations to Hardcover.

Extracted from cps/readingservices.py:process_annotation_for_sync as part
of the source/sync-target decoupling.

The handler is stateless — all DB writes happen in the dispatcher.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from ..annotation_colors import to_known_display_name
from .base import AnnotationSyncTargetHandler, SyncResult

log = logging.getLogger(__name__)

# The tag that names the reader of an annotation, as the Annotations page
# names its source. A source with no known reader gets no tag.
_SOURCE_TAGS = {
    "kobo": "Kobo",
    "koreader": "KOReader",
    "webreader": "Web reader",
    "crosspoint": "CrossPoint",
}


def _default_client_factory(token):
    """Lazy import so unit tests don't need the full Hardcover service."""
    from cps.services import hardcover as _hardcover
    return _hardcover.HardcoverClient(token)


def _default_config_getter():
    from cps import config
    return bool(
        getattr(config, "config_hardcover_annotations_sync", False)
        and getattr(config, "config_kobo_sync", False)
    )


def _default_book_identifiers(book):
    """Mirror of readingservices.get_book_identifiers."""
    identifiers = {}
    if book and getattr(book, "identifiers", None):
        for identifier in book.identifiers:
            id_type = (identifier.type or "").lower()
            if id_type in ("hardcover-id", "hardcover-edition", "hardcover-slug", "isbn"):
                identifiers[id_type] = identifier.val
    return identifiers


def _span_book_percent(book, span):
    """Percent of the book's text before a Kobo span, or None.

    The span gives the chapter file and a progress from 0 to 1 in that
    chapter, not in the book.
    """
    chapter = span.get("chapterFilename")
    progress = span.get("chapterProgress")
    if not chapter or isinstance(progress, bool) or not isinstance(progress, (int, float)):
        return None
    from cps.readingservices import EpubProgressCalculator
    percent = EpubProgressCalculator(book).calculate(chapter, progress)
    return round(percent, 2) if percent is not None else None


def _default_book_percent(book, annotation, span=None):
    """Percent of the book's text before an annotation, or None.

    An XPointer annotation (KOReader, CrossPoint) uses its XPointer. A Kobo
    annotation uses the span of its payload.
    """
    xpointer = getattr(annotation, "start_xpointer", None)
    if not xpointer:
        return _span_book_percent(book, span or {})
    from cps.annotations import _book_format_path
    from cps.services import koreader_xpointer as kx
    # A crengine XPointer addresses the plain EPUB, not the KEPUB.
    epub_path = _book_format_path(book, "EPUB")
    if not epub_path:
        return None
    found = kx.solid_index_of_xpointer(epub_path, xpointer)
    spine = kx.spine_solid_texts(epub_path)
    if not found or not spine:
        return None
    member, index = found
    total = sum(len(text) for _member, text in spine)
    before = 0
    for name, text in spine:
        if name == member:
            return round(100 * (before + index) / total, 2) if total else None
        before += len(text)
    return None


def _blacklist_check_for(session, book_id):
    """True if this book is blacklisted for Hardcover annotation sync."""
    from cps import ub
    row = session.query(ub.HardcoverBookBlacklist).filter(
        ub.HardcoverBookBlacklist.book_id == book_id,
    ).first()
    return bool(row and row.blacklist_annotations)


def _default_blacklist_check(book_id):
    from cps import ub
    return _blacklist_check_for(ub.session, book_id)


class HardcoverHandler(AnnotationSyncTargetHandler):
    """Push/delete annotations to Hardcover. Reads everything it needs
    from the Annotation ORM row + the optional payload."""

    target_name = "hardcover"

    def __init__(
        self,
        client_factory: Callable = _default_client_factory,
        config_getter: Callable[[], bool] = _default_config_getter,
        book_identifiers_getter: Callable = _default_book_identifiers,
        blacklist_check: Callable[[int], bool] = _default_blacklist_check,
        not_found_exception: Optional[type] = None,
        book_percent_getter: Callable = _default_book_percent,
    ):
        self._client_factory = client_factory
        self._config_getter = config_getter
        self._book_identifiers_getter = book_identifiers_getter
        self._blacklist_check = blacklist_check
        self._not_found_exception = not_found_exception
        self._book_percent_getter = book_percent_getter

    def for_session(self, session):
        """Rebind the blacklist lookup to ``session`` (see base docstring).

        A caller-supplied blacklist_check is left alone — it already knows
        where to read from, and silently replacing it would break injection.
        """
        from cps import ub
        if self._blacklist_check is not _default_blacklist_check:
            return self
        if session is getattr(ub, "session", None):
            return self
        return HardcoverHandler(
            client_factory=self._client_factory,
            config_getter=self._config_getter,
            book_identifiers_getter=self._book_identifiers_getter,
            blacklist_check=lambda book_id: _blacklist_check_for(session, book_id),
            not_found_exception=self._not_found_exception,
            book_percent_getter=self._book_percent_getter,
        )

    def is_enabled(self, user) -> bool:
        if not self._config_getter():
            return False
        if not getattr(user, "hardcover_token", None):
            return False
        return True

    def push(self, annotation, book, user, payload=None) -> SyncResult:
        # Skip annotations with no text content — same as legacy behaviour.
        if not annotation.highlighted_text and not annotation.note_text:
            return SyncResult(
                status="failed",
                error_message="annotation has no text content",
            )

        if self._blacklist_check(book.id):
            return SyncResult(
                status="failed",
                error_message=f"book {book.id} blacklisted for Hardcover annotations",
            )

        identifiers = self._book_identifiers_getter(book)
        if not identifiers:
            return SyncResult(
                status="failed",
                error_message="book has no Hardcover-compatible identifiers",
            )

        # Decide: add (first time) or update (existing target_record_id).
        existing = annotation.sync_target("hardcover")
        existing_record_id = existing.target_record_id if existing else None

        try:
            client = self._client_factory(user.hardcover_token)
            if existing_record_id:
                response = client.update_journal_entry(
                    journal_id=int(existing_record_id),
                    note_text=annotation.note_text,
                    highlighted_text=annotation.highlighted_text,
                )
            else:
                progress_percent = None
                span = ((payload or {}).get("location") or {}).get("span") or {}
                try:
                    progress_percent = self._book_percent_getter(book, annotation, span)
                except Exception as exc:  # the entry still goes without a page
                    log.warning("HardcoverHandler: no book percent for %s: %s",
                                annotation.annotation_id, exc)
                response = client.add_journal_entry(
                    identifiers=identifiers,
                    note_text=annotation.note_text,
                    progress_percent=progress_percent,
                    progress_page=None,
                    highlighted_text=annotation.highlighted_text,
                    # Hardcover turns this into a free-text tag, so it gets a
                    # colour we can NAME or nothing at all — neither "#A0A0A0"
                    # nor a foreign vocabulary's word is a tag anyone wants,
                    # and the caller skips the tag on a falsy value.
                    highlight_color=to_known_display_name(annotation.highlight_color),
                    source_tag=_SOURCE_TAGS.get(annotation.source),
                )
        except Exception as exc:
            log.warning("HardcoverHandler.push raised: %s", exc)
            return SyncResult(
                status="failed",
                error_message=str(exc),
                target_record_id=existing_record_id,
            )

        if not response or "id" not in response:
            return SyncResult(
                status="failed",
                error_message=f"empty Hardcover response: {response!r}",
                target_record_id=existing_record_id,
            )
        return SyncResult(
            status="synced",
            target_record_id=str(response["id"]),
        )

    def delete(self, sync_target, user) -> SyncResult:
        record_id = sync_target.target_record_id
        if not record_id:
            return SyncResult(
                status="tombstone",
                error_message="no remote record to delete",
            )
        try:
            client = self._client_factory(user.hardcover_token)
            returned = client.delete_journal_entry(journal_id=int(record_id))
        except Exception as exc:
            if self._not_found_exception and isinstance(exc, self._not_found_exception):
                return SyncResult(
                    status="tombstone",
                    target_record_id=record_id,
                    error_message="already deleted on remote",
                )
            log.warning("HardcoverHandler.delete raised: %s", exc)
            return SyncResult(
                status="failed",
                error_message=str(exc),
                target_record_id=record_id,
            )
        if returned is None:
            return SyncResult(
                status="tombstone",
                target_record_id=record_id,
                error_message="remote returned no id; treating as deleted",
            )
        if str(returned) != str(record_id):
            return SyncResult(
                status="failed",
                error_message=f"remote returned mismatched id: {returned!r}",
                target_record_id=record_id,
            )
        return SyncResult(
            status="tombstone",
            target_record_id=record_id,
        )
