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
from array import array
from bisect import bisect_left, bisect_right
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
    """The whole book's solid text, folded, with each character's origin.

    ``origin[i]`` is the offset, in the spine's solid texts laid end to end,
    of the character folded character ``i`` came from; ``starts`` holds where
    each spine item begins in that run. A flat integer array, because a
    long novel has millions of characters and this is cached.
    """

    def __init__(self, spine):
        self.members = [member for member, _text in spine]
        self.solids = [text for _member, text in spine]
        parts, origin, starts, at = [], array("l"), [], 0
        for solid in self.solids:
            starts.append(at)
            for j, char in enumerate(solid):
                folded = _fold(char)
                parts.append(folded)
                origin.extend([at + j] * len(folded))
            at += len(solid)
        self.text = "".join(parts)
        self.origin = origin
        self.starts = starts

    def source(self, i):
        """``(spine item index, solid index in it)`` of folded character ``i``."""
        at = self.origin[i]
        m = bisect_right(self.starts, at) - 1
        return m, at - self.starts[m]


@lru_cache(maxsize=8)
def _folded(path: str, _mtime_ns: int, _size: int) -> Optional[_Folded]:
    spine = kx.spine_solid_texts(path)
    return _Folded(spine) if spine else None


@lru_cache(maxsize=8)
def _reading(path: str, _mtime_ns: int, _size: int) -> Optional[tuple]:
    return kx.spine_reading_texts(path)


def _stat_key(epub_path):
    try:
        path = os.fspath(epub_path)
        stat = os.stat(path)
    except (OSError, TypeError):
        return None
    return path, stat.st_mtime_ns, stat.st_size


def _book(epub_path) -> Optional[_Folded]:
    key = _stat_key(epub_path)
    return _folded(*key) if key else None


def _reading_texts(epub_path) -> Optional[tuple]:
    """``kx.spine_reading_texts``, read once per file version: a pull names
    every highlight of a book by its words."""
    key = _stat_key(epub_path)
    return _reading(*key) if key else None


_HIT_LIMIT = 64


def _hits(haystack: str, needle: str, limit: int = _HIT_LIMIT) -> list:
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
    found = place(epub_path, anchor, percentage)
    return found[0] if found else None


def place(epub_path, anchor: dict, percentage: Optional[float] = None):
    """``(xpointer, percent)`` of the anchor's words in ``epub_path``, or None.

    ``percent`` (0-100) is how far into the book's text the words are. Every
    client counts its own percentage over its own text (with or without the
    front matter, by words or by pages), so the same sentence carries a
    different figure from each; this one is the same for every client that
    names that sentence, which is what lets positions be compared.
    """
    book = _book(epub_path)
    if book is None or not book.text:
        return None
    before, text, after = (fold(anchor.get(k) or "") for k in ("before", "text", "after"))
    at = _find(book, before, text, after, percentage)
    if at is None:
        return None
    m, j = book.source(at)
    xpointer = kx.xpointer_at_solid_index(epub_path, book.members[m], j, book.solids[m])
    if not xpointer:
        return None
    return xpointer, at / len(book.text) * 100.0


def _find(book: _Folded, before: str, text: str, after: str,
          percentage: Optional[float]) -> Optional[int]:
    """Folded index where ``text`` starts, framed by its context, or None."""
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
        if len(hits) >= _HIT_LIMIT:
            return None  # the nearest repeat may be among those not counted
        chosen = _choose(hits, None if target is None else target - offset, len(book.text))
        if chosen is None:
            return None  # the words repeat; a shorter needle repeats more
        return chosen + offset
    return None


# --- Text quotes: a passage named by its words ------------------------------
#
# A highlight from a client that holds no DOM arrives as the passage's own
# words with a few words either side (W3C TextQuoteSelector's exact, prefix
# and suffix). ``place_quote`` finds it as an XPointer range in the library
# EPUB, the form KOReader draws and the web reader converts; ``quote_at``
# turns any range there back into a quote for the client.

MAX_QUOTE_CHARS = 4000


def parse_quote(raw) -> Optional[dict]:
    """A validated ``{"exact", "prefix", "suffix"}``, or ``None``.

    The same rules as ``parse_anchor``: ``exact`` must hold something that
    compares, and oversized fields are refused rather than clipped.
    """
    if not isinstance(raw, dict):
        return None
    exact = raw.get("exact")
    prefix = raw.get("prefix") or ""
    suffix = raw.get("suffix") or ""
    if not all(isinstance(v, str) for v in (exact, prefix, suffix)):
        return None
    if (len(exact) > MAX_QUOTE_CHARS or len(prefix) > MAX_CONTEXT_CHARS
            or len(suffix) > MAX_CONTEXT_CHARS):
        return None
    if not fold(exact):
        return None
    return {"exact": exact, "prefix": prefix, "suffix": suffix}


def place_quote(epub_path, quote: dict, percentage: Optional[float] = None):
    """``(start, end, passage, percent)`` of a quote in ``epub_path``, or None.

    ``start``/``end`` are XPointers around the quoted words and ``passage``
    is the book's own text between them, which compares equal to ``exact``.
    A passage running across two spine items has no single range and is
    None, as is anything ``place`` would not place.
    """
    book = _book(epub_path)
    if book is None or not book.text:
        return None
    prefix, exact, suffix = (fold(quote.get(k) or "") for k in ("prefix", "exact", "suffix"))
    at = _find(book, prefix, exact, suffix, percentage)
    if at is None:
        return None
    found = _range(epub_path, book, at, exact)
    return (*found, at / len(book.text) * 100.0) if found else None


def place_in_text_range(epub_path, text_range, text: str):
    """``(start, end, passage)`` of ``text`` starting inside ``text_range``, or None.

    ``text_range`` is ``(start, end)`` in the book's non-whitespace characters
    laid end to end (``kepub_alignment.span_text_range``): a device that named
    the span a highlight starts in, and its words, but no position this
    server can read inside the span. ``text`` must start there exactly once.
    """
    book = _book(epub_path)
    needle = fold(text or "")
    if book is None or not needle or not text_range:
        return None
    low, high = (bisect_left(book.origin, i) for i in text_range)
    at = book.text.find(needle, low)
    if not low <= at < high or low <= book.text.find(needle, at + 1) < high:
        return None
    return _range(epub_path, book, at, needle)


def _range(epub_path, book: _Folded, at: int, exact: str):
    """``(start, end, passage)`` around folded ``exact`` found at ``at``, or None."""
    (m, i), (m_last, j) = book.source(at), book.source(at + len(exact) - 1)
    if m != m_last:
        return None
    member, solid = book.members[m], book.solids[m]
    start = kx.xpointer_at_solid_index(epub_path, member, i, solid)
    end = kx.xpointer_at_solid_index(epub_path, member, j, solid, after=True)
    if not start or not end:
        return None
    passage = kx.passage_between(epub_path, start, end)
    # A quote that ends inside a character the book folds to several (a
    # ligature) would frame more than its words: refuse rather than widen.
    if passage is None or fold(passage) != exact:
        return None
    return start, end, passage


def quote_at(epub_path, start_xpointer: str, end_xpointer: str,
             words: int = ANCHOR_WORDS) -> Optional[dict]:
    """The quote for the passage between two XPointers into ``epub_path``."""
    span = kx.solid_span_of_xpointers(epub_path, start_xpointer, end_xpointer)
    if span is None:
        return None
    member, i, j = span
    texts = _reading_texts(epub_path)
    if not texts:
        return None
    found = [m for m, (name, _t, _s) in enumerate(texts) if name == member]
    if len(found) != 1:
        return None
    m = found[0]
    _name, text, solid_at = texts[m]
    if not 0 <= i < j <= len(solid_at):
        return None
    start, end = solid_at[i], solid_at[j - 1] + 1
    return parse_quote({
        "exact": " ".join(text[start:end].split()),
        "prefix": _bounded(_words_before(texts, m, start, words), from_end=True),
        "suffix": _bounded(_words_after(texts, m, end, words), from_end=False),
    })


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
    texts = _reading_texts(epub_path)
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
    # What is served must be what a client may send back (``parse_anchor``):
    # a script without spaces between words has no "word" to name here.
    return parse_anchor({
        "text": text[start:end],
        "before": _bounded(_words_before(texts, m, start, words), from_end=True),
        "after": _bounded(_words_after(texts, m, end, words), from_end=False),
    })


def _bounded(words, *, from_end):
    """``words`` joined, dropping the words furthest from the place to fit."""
    words = list(words)
    while words and len(" ".join(words)) > MAX_CONTEXT_CHARS:
        words.pop(0 if from_end else -1)
    return " ".join(words)
