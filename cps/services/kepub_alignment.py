# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.
"""Kobo span <-> KOReader XPointer, through the text a KEPUB shares with its EPUB.

A Kobo reports its place as ``(chapter file, kobo span)`` in the KEPUB it
holds. That KEPUB was made from the library EPUB: kepubify wrapped each
sentence in a ``<span class="koboSpan" id="kobo.P.S">`` and the body in two
``div``s, and CWNG's normalizer may then have split a chapter file into
pieces with new names (``kepub_spine_splitter``). So the span ids, the trees
and even the file names exist only in the KEPUB. What survives is the text:
measured on four Project Gutenberg books, the non-whitespace characters of
every spine document's ``<body>``, concatenated in spine order, are identical
in the EPUB and in the normalized, split KEPUB.

A span therefore crosses to the EPUB as its offset in that concatenation, and
back the same way -- only when the two concatenations are identical, the
proof that each offset names the same character on both sides. Anything else
(a changed or re-edited book, a spine document that cannot be read, a span
the KEPUB does not hold, a position outside every span) gives ``None``.

This module proves nothing about WHICH files the devices hold; callers do
(``koreader_position.kobo_position_for_device``).
"""

from __future__ import annotations

import os
import posixpath
import re
import zipfile
from bisect import bisect_right
from dataclasses import dataclass
from functools import lru_cache
from itertools import accumulate
from typing import Optional
from urllib.parse import unquote

from .. import logger
from . import koreader_xpointer as kx

log = logger.create()

_SPAN_ID = re.compile(r"kobo\.[0-9]{1,9}\.[0-9]{1,9}")
_BODY = f"{{{kx._XHTML_NS}}}body"


@dataclass(frozen=True)
class _Document:
    member: str
    offset: int    # where its text starts in the book's concatenated text
    spans: dict    # span id -> (start, end) in the document's own text
    starts: tuple  # sorted starts of spans that hold text, for lookups
    ids: tuple     # the span id at each of ``starts``


@dataclass(frozen=True)
class _Kepub:
    text: str          # every document's non-whitespace text, in spine order
    documents: tuple   # _Document per spine item
    by_member: dict    # member -> _Document, for members read exactly once


def span_to_xpointer(epub_path, kepub_path, source: str, span_id: str) -> Optional[str]:
    """The XPointer, in ``epub_path``, of the first character of a Kobo span.

    ``source`` names the KEPUB document the span is in (a Kobo's
    ``Location.Source``); ``kepub_path`` is the KEPUB the Kobo holds.
    """
    if not isinstance(span_id, str) or not _SPAN_ID.fullmatch(span_id):
        return None
    kepub = _kepub(kepub_path)
    if kepub is None:
        return None
    document = _document(kepub, source)
    if document is None or span_id not in document.spans:
        return None
    start, end = document.spans[span_id]
    if start == end:
        return None  # a span without text of its own
    epub = _aligned_epub(epub_path, kepub)
    if epub is None:
        return None
    index = document.offset + start
    members, texts, offsets = epub
    k = bisect_right(offsets, index) - 1
    return kx.xpointer_at_solid_index(epub_path, members[k], index - offsets[k], texts[k])


def xpointer_to_span(epub_path, kepub_path, xpointer: str) -> Optional[tuple[str, str]]:
    """``(document, span id)``: the Kobo span of ``kepub_path`` holding an XPointer.

    The span is the one whose text contains the character the XPointer
    points at (or, on whitespace, the next one); a character outside every
    span gives ``None``.
    """
    found = kx.solid_index_of_xpointer(epub_path, xpointer)
    kepub = _kepub(kepub_path)
    if found is None or kepub is None:
        return None
    epub = _aligned_epub(epub_path, kepub)
    if epub is None:
        return None
    member, local = found
    members, _texts, offsets = epub
    if members.count(member) != 1:
        return None
    index = offsets[members.index(member)] + local
    starts = [d.offset for d in kepub.documents]
    document = kepub.documents[bisect_right(starts, index) - 1]
    # Documents without text share an offset with the next one; take the
    # last document starting at or before the character, which holds it.
    local = index - document.offset
    k = bisect_right(document.starts, local) - 1
    if k < 0:
        return None
    span_id = document.ids[k]
    start, end = document.spans[span_id]
    if not start <= local < end:
        return None
    return document.member, span_id


def _aligned_epub(epub_path, kepub: _Kepub):
    """``(members, texts, offsets)`` of the EPUB's spine, if its text is the KEPUB's."""
    spine = kx.spine_solid_texts(epub_path)
    if spine is None:
        return None
    members = [m for m, _t in spine]
    texts = [t for _m, t in spine]
    if "".join(texts) != kepub.text:
        return None
    offsets = [0, *accumulate(len(t) for t in texts)][:-1]
    return members, texts, offsets


def _document(kepub: _Kepub, source) -> Optional[_Document]:
    """The spine document a Kobo's ``Location.Source`` names, or None."""
    if not isinstance(source, str) or not source or len(source) > 2048:
        return None
    found = set()
    for path in (source, unquote(source)):
        if path.startswith("/") or "\\" in path or "\x00" in path:
            continue
        member = posixpath.normpath(path)
        if member in kepub.by_member:
            found.add(member)
    return kepub.by_member[found.pop()] if len(found) == 1 else None


def _kepub(kepub_path) -> Optional[_Kepub]:
    try:
        path = os.fspath(kepub_path)
        stat = os.stat(path)
    except (OSError, TypeError):
        return None
    if stat.st_size > kx.MAX_ARCHIVE_BYTES:
        return None
    try:
        return _load_kepub(path, stat.st_mtime_ns, stat.st_size)
    except Exception:
        log.debug("kepub_alignment: cannot read %s", path, exc_info=True)
        return None


@lru_cache(maxsize=8)
def _load_kepub(path: str, _mtime_ns: int, _size: int) -> Optional[_Kepub]:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        container = kx._xml(archive.read("META-INF/container.xml"))
        rootfile = container.find(f".//{{{kx._CONTAINER_NS}}}rootfile")
        if rootfile is None or not rootfile.get("full-path"):
            return None
        opf_path = rootfile.get("full-path")
        package = kx._xml(archive.read(opf_path))
        opf_dir = posixpath.dirname(opf_path)
        manifest = {}
        for item in package.iterfind(f"{{{kx._OPF_NS}}}manifest/{{{kx._OPF_NS}}}item"):
            if item.get("id") and item.get("href") and item.get("id") not in manifest:
                manifest[item.get("id")] = posixpath.normpath(
                    posixpath.join(opf_dir, unquote(item.get("href"))))
        spine = package.find(f"{{{kx._OPF_NS}}}spine")
        if spine is None:
            return None
        parts, documents, offset = [], [], 0
        for ref in spine.iterfind(f"{{{kx._OPF_NS}}}itemref"):
            member = manifest.get(ref.get("idref"))
            if member not in names or archive.getinfo(member).file_size > kx.MAX_CHAPTER_BYTES:
                return None
            text, spans = _read_document(archive.read(member))
            ordered = sorted((s, i) for i, (s, e) in spans.items() if e > s)
            documents.append(_Document(member, offset, spans,
                                       tuple(s for s, _i in ordered),
                                       tuple(i for _s, i in ordered)))
            parts.append(text)
            offset += len(text)
    counts = {}
    for document in documents:
        counts[document.member] = counts.get(document.member, 0) + 1
    return _Kepub("".join(parts), tuple(documents),
                  {d.member: d for d in documents if counts[d.member] == 1})


def _read_document(raw: bytes):
    """``(text, spans)`` of one KEPUB document's body.

    The same walk, and the same notion of a text node, as the EPUB side
    (``koreader_xpointer._solid_texts``): a comment's own text is not text.
    """
    text = raw.decode("utf-8-sig", errors="strict")
    root = kx._xml(kx._numeric_named_entities(text).encode("utf-8"))
    body = root.find(_BODY)
    if body is None:
        raise ValueError("a spine document without an XHTML body")
    parts, spans, count = [], {}, 0

    def walk(el):
        nonlocal count
        span_id = el.get("id")
        start = count
        for child in kx._raw_children(el):
            if isinstance(child, str):
                packed = child.translate(kx._NO_SPACE)
                parts.append(packed)
                count += len(packed)
            elif isinstance(child.tag, str):
                walk(child)
        if span_id is not None and _SPAN_ID.fullmatch(span_id):
            if span_id in spans:
                raise ValueError(f"duplicate span id {span_id}")
            spans[span_id] = (start, count)

    walk(body)
    return "".join(parts), spans
