# -*- coding: utf-8 -*-
# Copyright (C) 2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.
"""Reading positions named by the book's words, for clients that hold no DOM.

Some reading clients (speed readers, text-to-speech players, plain-text
renderers) flatten a book to its words and keep their place as a word count.
They can say *which words* they are at, but not produce a crengine XPointer or
an epub.js CFI. Such a client sends an **anchor**:

    {"text": "<the word at the position>",
     "before": "<up to 8 words before it>",
     "after": "<up to 8 words after it>"}

and the server finds those words in the library EPUB (``locate``). The place
found becomes an XPointer in the library EPUB, which every other reader
already consumes exactly: KOReader directly, the web reader and the reading
sources through ``koreader_position``. In the other direction ``anchor_at``
turns any XPointer in the library EPUB back into an anchor, so the client
lands on the same sentence another reader left off at.

Matching ignores whitespace, case, accents, soft hyphens and the dash and
quote variants (``_fold``), because two tokenizers never agree on those. It is
decided by the text alone, with the client's percentage only choosing between
repeats of the same words: the nearest repeat is used only when every other
one is further from that percentage by more than ``_WINDOW`` of the book.
Anything short of that is ``None``, and the caller keeps the percentage.
"""

from __future__ import annotations

import os
import unicodedata
from functools import lru_cache
from typing import Optional

from .. import logger
from . import koreader_xpointer as kx

log = logger.create()

ANCHOR_WORDS = 8
# Bounds on what a client may send, generous for 8 long words a side.
MAX_TEXT_CHARS = 200
MAX_CONTEXT_CHARS = 600
# A needle shorter than this (folded) is too common to place anything.
_MIN_NEEDLE = 12
# How much nearer (fraction of the book) the chosen repeat must be than the next.
_WINDOW = 0.05

_DROPPED = frozenset("­​‌‍⁠﻿")
_DASHES = frozenset("‐‑‒–—―−")
_QUOTES = {"‘": "'", "’": "'", "‚": "'", "‛": "'",
           "“": '"', "”": '"', "„": '"', "‟": '"'}


def _fold(char: str) -> str:
    """The comparison form of one code point ('' when it never counts).

    Per code point, so a client folds the same way without our normaliser:
    compatibility-decompose it, drop combining marks (precomposed and
    decomposed accents differ between EPUB sources), lowercase the rest.
    Clients implement exactly these steps; keep them in step with the
    published table (``notes/KOSYNC-TEXT-ANCHOR-DESIGN.md``).
    """
    if char.isspace() or char in _DROPPED:
        return ""
    if char in _DASHES:
        return "-"
    if char in _QUOTES:
        return _QUOTES[char]
    return "".join(part for part in unicodedata.normalize("NFKD", char)
                   if unicodedata.category(part) != "Mn").lower()


def fold(text: str) -> str:
    return "".join(_fold(char) for char in text)


def parse_anchor(raw) -> Optional[dict]:
    """A validated ``{"text", "before", "after"}``, or ``None``.

    ``text`` is required and must hold something that compares; the context
    fields are optional strings. Oversized fields are refused, not clipped:
    a clipped anchor names different words than the client meant.
    """
    if not isinstance(raw, dict):
        return None
    text = raw.get("text")
    before = raw.get("before", "")
    after = raw.get("after", "")
    if before is None:
        before = ""
    if after is None:
        after = ""
    if not all(isinstance(v, str) for v in (text, before, after)):
        return None
    if (len(text) > MAX_TEXT_CHARS or len(before) > MAX_CONTEXT_CHARS
            or len(after) > MAX_CONTEXT_CHARS):
        return None
    if not fold(text):
        return None
    return {"text": text, "before": before, "after": after}


class _Folded:
    """The whole book's solid text, folded, with each character's origin."""

    def __init__(self, spine):
        self.members = [member for member, _text in spine]
        self.solids = [text for _member, text in spine]
        parts, origin = [], []
        for m, solid in enumerate(self.solids):
            for j, char in enumerate(solid):
                folded = _fold(char)
                parts.append(folded)
                origin.extend([(m, j)] * len(folded))
        self.text = "".join(parts)
        self.origin = origin


@lru_cache(maxsize=8)
def _folded(path: str, _mtime_ns: int, _size: int) -> Optional[_Folded]:
    spine = kx.spine_solid_texts(path)
    return _Folded(spine) if spine else None


def _book(epub_path) -> Optional[_Folded]:
    try:
        path = os.fspath(epub_path)
        stat = os.stat(path)
    except (OSError, TypeError):
        return None
    return _folded(path, stat.st_mtime_ns, stat.st_size)


def _hits(haystack: str, needle: str, limit: int = 64) -> list:
    out, start = [], 0
    while len(out) < limit:
        found = haystack.find(needle, start)
        if found < 0:
            break
        out.append(found)
        start = found + 1
    return out


def _choose(hits: list, target: Optional[float], total: int) -> Optional[int]:
    if len(hits) == 1:
        return hits[0]
    if target is None or not total:
        return None
    ranked = sorted(hits, key=lambda hit: abs(hit - target))
    if abs(ranked[1] - target) - abs(ranked[0] - target) <= _WINDOW * total:
        return None  # the percentage does not tell the repeats apart
    return ranked[0]


def locate(epub_path, anchor: dict, percentage: Optional[float] = None) -> Optional[str]:
    """XPointer, in ``epub_path``, of the first character of ``anchor["text"]``.

    ``percentage`` (0-100) is where the client believes it is; it only
    chooses between repeats of the anchor's words (see the module notes).
    """
    book = _book(epub_path)
    if book is None or not book.text:
        return None
    before, text, after = (fold(anchor.get(k) or "") for k in ("before", "text", "after"))
    if not text:
        return None
    target = None
    if percentage is not None:
        target = min(max(float(percentage), 0.0), 100.0) / 100.0 * len(book.text)
    for needle, offset in ((before + text + after, len(before)),
                           (text + after, 0),
                           (before + text, len(before))):
        if len(needle) < _MIN_NEEDLE:
            continue
        hits = _hits(book.text, needle)
        if not hits:
            continue
        chosen = _choose(hits, None if target is None else target - offset, len(book.text))
        if chosen is None:
            return None  # the words repeat; a shorter needle repeats more
        m, j = book.origin[chosen + offset]
        return kx.xpointer_at_solid_index(epub_path, book.members[m], j, book.solids[m])
    return None


def _words_before(texts, m, start, count):
    words = []
    while m >= 0 and len(words) < count:
        words = texts[m][1][:start].split()[-(count - len(words)):] + words
        m -= 1
        start = None
    return words[-count:] if count else []


def _words_after(texts, m, end, count):
    words = []
    while m < len(texts) and len(words) < count:
        words += texts[m][1][end:].split()[:count - len(words)]
        m += 1
        end = 0
    return words


def anchor_at(epub_path, xpointer: str, words: int = ANCHOR_WORDS) -> Optional[dict]:
    """The anchor for the word an XPointer into ``epub_path`` points at."""
    point = kx.solid_index_of_xpointer(epub_path, xpointer)
    if point is None:
        return None
    member, index = point
    texts = kx.spine_reading_texts(epub_path)
    if not texts:
        return None
    found = [m for m, (name, _t, _s) in enumerate(texts) if name == member]
    if len(found) != 1:
        return None
    m = found[0]
    _name, text, solid_at = texts[m]
    if not 0 <= index < len(solid_at):
        return None
    at = solid_at[index]
    start = at
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    end = at
    while end < len(text) and not text[end].isspace():
        end += 1
    return {
        "text": text[start:end],
        "before": " ".join(_words_before(texts, m, start, words)),
        "after": " ".join(_words_after(texts, m, end, words)),
    }
