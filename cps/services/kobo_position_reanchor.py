# Copyright (C) 2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep reading positions attached to their prose when a book is re-converted.

A Kobo position is ``(chapter file, kobo span)``. Re-converting a book changes
both, and a chapter name is routinely reused for different prose, so presence
of the file proves nothing. The position's text is read from the OLD book while
it still exists and found again in the NEW one; a position whose old book is
gone is re-placed at the same fraction of the new book's text. Device latch
rows are re-armed so the device receives the new anchor after its download.
"""
from __future__ import annotations

import os
import posixpath
import zipfile

from cps import ub

from .kobo_annotation_reanchor import KepubIndex, _kepub_path, normalize

ANCHOR_CHARS = 200
_SHRINK = (200, 120, 60, 30)
LOOKAHEAD_SPANS = 40  # a v3 page-foot note block ran 14 spans before body prose resumed


def _chapter(index, name):
    if not name:
        return None
    name = name.split("#", 1)[0]
    chapter = index.chapters.get(name)
    if chapter is None:
        full = index.basenames.get(posixpath.basename(name))
        chapter = index.chapters.get(full) if full else None
    return chapter


def anchor_text(index, chapter_name, span_id, length=ANCHOR_CHARS):
    """The prose that starts at ``span_id`` of ``chapter_name`` in ``index``."""
    chapter = _chapter(index, chapter_name)
    if chapter is None:
        return ""
    for sid, start, _end in chapter.spans:
        if sid == span_id:
            return normalize(chapter.text[start:start + length])
    return ""


def _anchor_candidates(index, chapter_name, span_id, limit=LOOKAHEAD_SPANS):
    """Anchor snippets for ``span_id`` and the spans that follow it in reading order.

    A position saved on a paragraph the new conversion moved out of the body
    flow (a page-foot note that became an endnote) must follow the reading
    page, so the spans after it are candidates too."""
    chapter = _chapter(index, chapter_name)
    if chapter is None:
        return []
    ids = [sid for sid, _s, _e in chapter.spans]
    if span_id not in ids:
        return []
    out = []
    for sid in ids[ids.index(span_id):ids.index(span_id) + limit]:
        if sid in chapter.note_spans:
            continue
        snippet = anchor_text(index, chapter_name, sid)
        if snippet:
            out.append(snippet)
    return out


def locate_text(index, snippet):
    """``(chapter, span id)`` of the unique place ``snippet`` (or its head) occurs."""
    for length in _SHRINK:
        needle = snippet[:length].strip()
        if len(needle) < 12:
            return None
        hits = []
        for name in index.order:
            for start, _end in index.chapters[name].find(needle):
                hits.append((name, start))
                if len(hits) > 1:
                    break
            if len(hits) > 1:
                break
        if len(hits) == 1:
            name, start = hits[0]
            found = index.chapters[name].locate(start)
            if found is not None and found[0] in index.chapters[name].note_spans:
                return None  # the text lives in an endnote now; not a reading position
            if found is not None:
                return name, found[0]
        if not hits:
            continue
        # ambiguous at this length: a longer needle was already tried, so the
        # text repeats; give up rather than guess.
        return None
    return None


def locate_fraction(index, percent):
    """``(chapter, span id)`` at ``percent`` of the book's text."""
    lengths = [(name, len(index.chapters[name].text)) for name in index.order]
    total = sum(length for _n, length in lengths)
    if not total:
        return None
    try:
        fraction = min(max(float(percent or 0.0) / 100.0, 0.0), 1.0)
    except (TypeError, ValueError):
        fraction = 0.0
    target = fraction * total
    seen = 0
    for name, length in lengths:
        if seen + length > target or (name == lengths[-1][0]):
            chapter = index.chapters[name]
            offset = min(max(int(target - seen), 0), max(length - 1, 0))
            found = chapter.locate(offset)
            if found is None and chapter.spans:
                found = (chapter.spans[-1][0], 0)
            return (name, found[0]) if found else None
        seen += length
    return None


def _has_position(index, row):
    name = getattr(row, "location_source", None)
    chapter = _chapter(index, name)
    if chapter is None:
        return False
    span = getattr(row, "location_value", None)
    return any(sid == span for sid, _s, _e in chapter.spans)


def reanchor_position_rows(old_index, new_index, rows, *, log):
    """Move each KoboSpan row onto ``new_index``; return the rows that moved."""
    moved = []
    for row in rows:
        if (getattr(row, "location_type", None) or "").lower() != "kobospan":
            continue
        if not getattr(row, "location_value", None):
            continue
        if old_index is None and _has_position(new_index, row):
            continue
        # With the old file at hand every row is re-placed: a re-conversion can
        # reuse a chapter name and a span id for different text, so presence in
        # the new file proves nothing.
        target = None
        how = "fraction"
        if old_index is not None:
            for snippet in _anchor_candidates(old_index, row.location_source, row.location_value):
                target = locate_text(new_index, snippet)
                if target is not None:
                    how = "text"
                    break
        if target is None:
            target = locate_fraction(new_index, getattr(row, "progress_percent", None))
            how = "fraction"
        if target is None:
            log.info("Kobo position reanchor: no target for %s %s; left as-is",
                     row.location_source, row.location_value)
            continue
        log.info("Kobo position reanchor: %s %s -> %s %s by %s",
                 row.location_source, row.location_value, target[0], target[1], how)
        row.location_source, row.location_value = target
        row.location_type = "KoboSpan"
        moved.append(row)
    return moved


def _position_rows(book_id):
    states = ub.session.query(ub.KoboReadingState).filter(
        ub.KoboReadingState.book_id == int(book_id),
    ).all()
    bookmarks = [s.current_bookmark for s in states if s.current_bookmark is not None]
    latches = ub.session.query(ub.DeviceReadingPosition).filter(
        ub.DeviceReadingPosition.book_id == int(book_id),
    ).all()
    return bookmarks, latches


def reanchor_book_positions(book, *, old_kepub_path=None, log):
    """Re-anchor every reader's position for ``book`` onto its current KEPUB.

    Call after the book's files were replaced, with the path of a copy of the
    previous KEPUB when one is still available. Device latch rows that moved
    are re-armed so the next sync re-sends the position. Returns the number of
    rows moved.
    """
    new_path = _kepub_path(book)
    if not new_path or not os.path.isfile(new_path):
        return 0
    bookmarks, latches = _position_rows(book.id)
    if not bookmarks and not latches:
        return 0
    new_index = KepubIndex(new_path)
    old_index = None
    if old_kepub_path and os.path.isfile(old_kepub_path):
        old_index = KepubIndex(old_kepub_path)
    moved = reanchor_position_rows(old_index, new_index, bookmarks + latches, log=log)
    for row in moved:
        if isinstance(row, ub.DeviceReadingPosition):
            row.rehydrate_needed = True
    if moved:
        ub.session.commit()
        log.info("Kobo position reanchor: moved %d of %d position row(s) for book %s",
                 len(moved), len(bookmarks) + len(latches), getattr(book, "id", None))
    return len(moved)


_names_cache = {}


def _zip_names(path):
    try:
        stat = os.stat(path)
    except OSError:
        return None
    key = (path, stat.st_mtime_ns, stat.st_size)
    names = _names_cache.get(key)
    if names is None:
        try:
            with zipfile.ZipFile(path) as archive:
                names = frozenset(archive.namelist())
        except (OSError, zipfile.BadZipFile):
            return None
        _names_cache.clear()
        _names_cache[key] = names
    return names


def reanchor_missing_position(book, bookmark, *, log):
    """Emit-time guard: a bookmark whose chapter is not in the current KEPUB is
    re-placed at its fraction of the book (its text is no longer available).
    Returns True when the row was rewritten (not committed)."""
    if bookmark is None or not getattr(bookmark, "location_value", None):
        return False
    if (getattr(bookmark, "location_type", None) or "").lower() != "kobospan":
        return False
    source = (getattr(bookmark, "location_source", None) or "").split("#", 1)[0]
    if not source:
        return False
    path = _kepub_path(book)
    if not path:
        return False
    names = _zip_names(path)
    if names is None:
        return False
    base = posixpath.basename(source)
    if source in names or any(posixpath.basename(n) == base for n in names):
        return False
    moved = reanchor_position_rows(None, KepubIndex(path), [bookmark], log=log)
    return bool(moved)
