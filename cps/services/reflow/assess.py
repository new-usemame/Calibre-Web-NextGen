# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Stage 0: what kind of PDF is this, in one word and a page of evidence.

The verdict becomes a plain sentence on the Reflow page before the user spends
anything, so it has to be honest about the one case that fools every character
counter: a scanned book carrying a text layer that is pure noise. Only the words
tell the truth, which is why this counts common English words rather than bytes.
"""

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import List

from . import extract

#: Real English prose is 25-40% these words. OCR mojibake scores near zero, which is
#: how "there is a text layer" is told apart from "there is a usable text layer".
STOPWORDS = frozenset(
    "the and of to in is that it for as with was this by are be on not or from at which "
    "an but have has had they we you his her their he she its into than then when who all "
    "would there been more one so if what may can will such no other some these".split())

#: Share of a page's words that must be stopwords for the page to read as prose.
STOPWORD_MIN = 0.06
#: A page needs this many words before the share above means anything.
PROSE_MIN_WORDS = 30
#: Median characters per page below this means a sparse scan or a 2-up layout.
THIN_TEXT_CHARS = 900
#: Share of pages that must read as prose for the book's layer to be trusted.
TRUSTED_LAYER = 0.40
#: Share of pages that must be a full-page raster for the book to be a scan.
SCAN_SHARE = 0.50

VERDICTS = ("BORN_DIGITAL", "OCR_LAYER", "THIN_TEXT", "NO_TEXT_LAYER", "GARBAGE_TEXT")

_WORDS = re.compile(r"[a-z']+")


def looks_like_prose(text):
    """Distinguish real text from OCR garbage."""
    words = _WORDS.findall((text or "").lower())
    if len(words) < PROSE_MIN_WORDS:
        return False
    return sum(1 for w in words if w in STOPWORDS) / len(words) >= STOPWORD_MIN


@dataclass
class PageCensus(object):
    pno: int
    chars: int
    words: int
    prose: bool
    full_page_image: bool
    images: int
    drawings: int

    def to_dict(self):
        return {"pno": self.pno, "chars": self.chars, "words": self.words,
                "prose": self.prose, "full_page_image": self.full_page_image,
                "images": self.images, "drawings": self.drawings}


@dataclass
class Assessment(object):
    verdict: str
    pages: int
    median_chars: float
    prose_share: float
    scan_share: float
    empty_share: float
    drawings_total: int
    thin_text: bool
    fonts: dict = field(default_factory=dict)
    census: List[PageCensus] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)

    @property
    def layer_is_trusted(self):
        return self.prose_share >= TRUSTED_LAYER

    def to_dict(self):
        return {"verdict": self.verdict, "pages": self.pages,
                "median_chars": round(self.median_chars, 1),
                "prose_share": round(self.prose_share, 4),
                "scan_share": round(self.scan_share, 4),
                "empty_share": round(self.empty_share, 4),
                "drawings_total": self.drawings_total,
                "thin_text": self.thin_text,
                "layer_is_trusted": self.layer_is_trusted,
                "fonts": self.fonts, "reasons": self.reasons}

    def describe(self):
        """The sentence the Reflow page shows before anybody spends anything."""
        return {
            "BORN_DIGITAL": "This PDF has a real text layer. Most pages will convert "
                            "without asking a model anything.",
            "OCR_LAYER": "Every page is a picture of the page with OCR text behind it. "
                         "The words are readable, but the structure has to be rebuilt.",
            "THIN_TEXT": "There is very little text on each page. This is usually a "
                         "sparse scan or two printed pages photographed as one.",
            "NO_TEXT_LAYER": "There is no text in this PDF at all, only images. Every "
                             "page has to be read by the model, which costs the most.",
            "GARBAGE_TEXT": "This PDF has a text layer, but it is not readable words. "
                            "It has to be treated as if there were no text at all.",
        }[self.verdict]


def page_census(raw):
    text = raw.text
    return PageCensus(pno=raw.pno, chars=raw.chars,
                      words=len(_WORDS.findall(text.lower())),
                      prose=looks_like_prose(text),
                      full_page_image=raw.is_page_scan,
                      images=len(raw.images), drawings=raw.drawings)


def font_census(raw_pages):
    fonts = Counter()
    for raw in raw_pages:
        for blk in raw.text_blocks:
            for line in blk.lines:
                for span in line.spans:
                    if span.text.strip():
                        fonts[span.font] += len(span.text.strip())
    return dict(fonts.most_common(12))


def assess(source, page_numbers=None):
    """Census a document (a PyMuPDF document, or already-read raw pages)."""
    if isinstance(source, list):
        raw_pages = source
    else:
        raw_pages = extract.read_pages(source, page_numbers)
    return assess_pages(raw_pages)


def assess_pages(raw_pages):
    if not raw_pages:
        return Assessment(verdict="NO_TEXT_LAYER", pages=0, median_chars=0.0,
                          prose_share=0.0, scan_share=0.0, empty_share=1.0,
                          drawings_total=0, thin_text=True,
                          reasons=["the document has no pages"])

    census = [page_census(raw) for raw in raw_pages]
    pages = len(census)
    median_chars = statistics.median(c.chars for c in census)
    prose_share = sum(1 for c in census if c.prose) / pages
    scan_share = sum(1 for c in census if c.full_page_image) / pages
    empty_share = sum(1 for c in census if c.chars < 20) / pages
    drawings_total = sum(c.drawings for c in census)
    thin_text = median_chars < THIN_TEXT_CHARS

    reasons = []
    if empty_share >= 0.9:
        verdict = "NO_TEXT_LAYER"
        reasons.append("%d%% of pages carry almost no extractable text" % round(empty_share * 100))
    elif prose_share < 0.1 and median_chars >= 20:
        verdict = "GARBAGE_TEXT"
        reasons.append("only %d%% of pages read as English prose despite a text layer"
                       % round(prose_share * 100))
    elif scan_share >= SCAN_SHARE:
        verdict = "OCR_LAYER"
        reasons.append("%d%% of pages are a single full-page image with text behind it"
                       % round(scan_share * 100))
        if not drawings_total:
            reasons.append("no vector drawings anywhere: nothing was typeset digitally")
    elif thin_text:
        verdict = "THIN_TEXT"
        reasons.append("median %d characters per page" % median_chars)
    else:
        verdict = "BORN_DIGITAL"
        reasons.append("median %d characters per page, %d%% of pages read as prose"
                       % (median_chars, round(prose_share * 100)))

    return Assessment(verdict=verdict, pages=pages, median_chars=median_chars,
                      prose_share=prose_share, scan_share=scan_share,
                      empty_share=empty_share, drawings_total=drawings_total,
                      thin_text=thin_text, fonts=font_census(raw_pages),
                      census=census, reasons=reasons)
