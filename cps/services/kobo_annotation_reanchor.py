# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Re-anchor Kobo highlights whose chapter file no longer exists in the book.

When a book's EPUB is re-converted (a better PDF conversion, a split spine)
the chapter filenames and the ``kobo.N.M`` span ids change underneath every
existing highlight. OBSERVED (notes/KOBO-HIGHLIGHTS-STATE.md §6): Nickel keeps
such rows but rewrites their ContentID to a file that no longer exists, so
they are invisible and the reader cannot even delete them.

CWNG holds the highlighted text, so it can find the passage again in the
current KEPUB and rewrite the location before serving the set back to the
device. Rows whose chapter still exists are left alone; rows whose text is
not found (or found in several places) are served unchanged and logged, never
dropped.
"""

from __future__ import annotations

import html
import os
import posixpath
import re
import unicodedata
import zipfile
from xml.etree import ElementTree

from cps import config, ub

_SPAN = re.compile(
    r'<span[^>]*\bclass="koboSpan"[^>]*\bid="(kobo\.\d+\.\d+)"[^>]*>(.*?)</span>',
    re.S,
)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_QUOTES = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    " ": " ", "‐": "-", "‑": "-", "–": "-", "—": "-",
})


def normalize(text):
    """Whitespace-, quote- and case-insensitive comparison form."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES)
    return _WS.sub(" ", text).strip().casefold()


def _span_text(inner):
    return html.unescape(_TAG.sub("", inner))


class Chapter:
    """One content document: its kobo spans and a searchable flat text."""

    def __init__(self, filename, markup):
        self.filename = filename
        self.spans = []          # (span_id, start, end) in flat-text coordinates
        parts = []
        cursor = 0
        for match in _SPAN.finditer(markup):
            text = _span_text(match.group(2))
            if not text:
                continue
            parts.append(text)
            self.spans.append((match.group(1), cursor, cursor + len(text)))
            cursor += len(text)
        self.text = "".join(parts)
        # map of normalized text -> original index, built lazily
        self._norm = None
        self._index = None

    def _build(self):
        norm_chars, index = [], []
        pending_space = False
        for pos, ch in enumerate(self.text):
            folded = unicodedata.normalize("NFKC", ch).translate(_QUOTES)
            for out in folded.casefold():
                if out.isspace():
                    pending_space = True
                    continue
                if pending_space and norm_chars:
                    norm_chars.append(" ")
                    index.append(pos)
                pending_space = False
                norm_chars.append(out)
                index.append(pos)
        self._norm = "".join(norm_chars)
        self._index = index

    def find(self, needle):
        """Return [(start, end)] original-text ranges matching ``needle``."""
        if self._norm is None:
            self._build()
        if not needle:
            return []
        hits = []
        start = self._norm.find(needle)
        while start >= 0:
            end = start + len(needle) - 1
            hits.append((self._index[start], self._index[end] + 1))
            start = self._norm.find(needle, start + 1)
        return hits

    def locate(self, pos, *, end=False):
        """Map a flat-text position to ``(span_id, offset_in_span)``."""
        for span_id, s0, s1 in self.spans:
            if (s0 <= pos < s1) or (end and pos == s1):
                return span_id, pos - s0
        return None


def _escape_span_id(span_id):
    return "span#" + span_id.replace(".", "\\.")


class KepubIndex:
    """Spine documents of one KEPUB, keyed by zip path."""

    def __init__(self, path):
        self.chapters = {}
        self.order = []
        with zipfile.ZipFile(path) as archive:
            opf_path = self._opf_path(archive)
            base = posixpath.dirname(opf_path)
            for href in self._spine_hrefs(archive.read(opf_path)):
                name = posixpath.normpath(posixpath.join(base, href)) if base else href
                try:
                    markup = archive.read(name).decode("utf-8", "replace")
                except KeyError:
                    continue
                chapter = Chapter(name, markup)
                if chapter.spans:
                    self.chapters[name] = chapter
                    self.order.append(name)
        self.basenames = {}
        for name in self.order:
            self.basenames.setdefault(posixpath.basename(name), name)

    @staticmethod
    def _opf_path(archive):
        root = ElementTree.fromstring(archive.read("META-INF/container.xml"))
        for el in root.iter():
            if el.tag.endswith("rootfile"):
                return el.get("full-path")
        raise KeyError("container.xml has no rootfile")

    @staticmethod
    def _spine_hrefs(opf_bytes):
        root = ElementTree.fromstring(opf_bytes)
        items = {}
        for el in root.iter():
            if el.tag.endswith("}item") or el.tag == "item":
                items[el.get("id")] = el.get("href")
        hrefs = []
        for el in root.iter():
            if el.tag.endswith("}itemref") or el.tag == "itemref":
                href = items.get(el.get("idref"))
                if href:
                    hrefs.append(href)
        return hrefs

    def has_chapter(self, chapter_filename):
        if not chapter_filename:
            return False
        name = chapter_filename.split("#", 1)[0]
        return name in self.chapters or posixpath.basename(name) in self.basenames


def _kepub_path(book):
    rows = list(getattr(book, "data", None) or ())
    for wanted in ("KEPUB", "EPUB"):
        for row in rows:
            if (getattr(row, "format", "") or "").upper() == wanted:
                return os.path.join(
                    config.get_book_path(), book.path,
                    row.name + "." + wanted.lower(),
                )
    return None


def reanchor_rows(index, rows, entitlement_id, *, log):
    """Rewrite the location of rows whose chapter is gone; return the changed rows."""
    entitlement = (entitlement_id or "").strip().strip("{}")
    changed = []
    for row in rows:
        content_id = row.content_id or ""
        chapter = content_id.split("!!", 1)[1] if "!!" in content_id else ""
        if index.has_chapter(chapter):
            continue
        needle = normalize(row.highlighted_text)
        if len(needle) < 4:
            log.info("Kobo reanchor: row %s has no usable text; served as-is",
                     row.annotation_id)
            continue
        hits = []
        for name in index.order:
            for start, end in index.chapters[name].find(needle):
                hits.append((name, start, end))
                if len(hits) > 1:
                    break
            if len(hits) > 1:
                break
        if len(hits) != 1:
            log.info(
                "Kobo reanchor: row %s text %s in the current book; served as-is",
                row.annotation_id, "not found" if not hits else "is ambiguous",
            )
            continue
        name, start, end = hits[0]
        chapter_obj = index.chapters[name]
        first = chapter_obj.locate(start)
        last = chapter_obj.locate(end, end=True)
        if first is None or last is None:
            continue
        row.content_id = "{}!!{}".format(entitlement, name)
        row.start_container_path = _escape_span_id(first[0])
        row.start_offset = first[1]
        row.end_container_path = _escape_span_id(last[0])
        row.end_offset = last[1]
        row.chapter_progress = (start / len(chapter_obj.text)) if chapter_obj.text else 0.0
        row.context_string = chapter_obj.text[max(0, start - 60):end + 60]
        changed.append(row)
    return changed


def reanchor_for_book(book, entitlement_id, rows, *, log):
    """Re-anchor ``rows`` against the book's current KEPUB and persist."""
    path = _kepub_path(book)
    if not path or not os.path.isfile(path):
        return []
    def _chapter(row):
        content_id = row.content_id or ""
        return content_id.split("!!", 1)[1] if "!!" in content_id else ""

    index = KepubIndex(path)
    if all(index.has_chapter(_chapter(row)) for row in rows):
        return []
    changed = reanchor_rows(index, rows, entitlement_id, log=log)
    if changed:
        ub.session.commit()
        log.info(
            "Kobo reanchor: rewrote %d of %d annotation location(s) for book %s",
            len(changed), len(rows), getattr(book, "id", None),
        )
    return changed
