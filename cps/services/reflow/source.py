# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The chosen source layer for every page, with its provenance.

The printed page is authoritative. A page with a trustworthy native text layer is
used exactly as it printed; a page that is only a picture, or whose embedded layer
is demonstrably damaged, is recovered by local OCR *before* any structural work,
and the recovered words are treated as a NEW source layer with its own evidence —
never as the PDF's original words (DECISIONS R3/R4).

Everything downstream — skeleton, assemble, model input, builder, report — reads
the chosen layer from here, so there is exactly one answer to "what does this
page say". Recognition is bounded, cached by identity, cancellable, and free of
any paid call; an unavailable engine or language stops the job *before* spending,
named, rather than failing pages one by one after the money is gone.
"""

import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pymupdf

from . import assess, extract, ocr

#: Recovery modes: everything, only pages with no text at all, best effort, or
#: nothing. ``auto`` and ``textless`` are explicit choices: a missing engine or
#: language stops the run before any paid work. ``auto_if_available`` is the
#: pipeline's default: recover when the engine is there, and say so when it is
#: not, so an image-only book degrades to its disclosed facsimile rather than
#: becoming un-convertible on a host without Tesseract.
MODES = ("auto", "textless", "auto_if_available", "off")

#: Tesseract's word boxes bound the glyphs; an em is taller than cap height.
_SIZE_FROM_HEIGHT = 0.72

#: An engine-confidence flag under this mark makes the word an uncertain reading.
UNCERTAIN_SCORE = 85

#: A sparse page is thin, not damaged: below this many tokens the stopword test
#: says nothing, and only clearly non-word tokens may mark a layer as damaged.
_SPARSE_TOKENS = 30
#: A scan page whose words are nearly all fragments is a misread, not prose:
#: the stopword test can pass it on 'if' and 'a' while almost nothing on it is
#: a word of four letters or more (book 567's cover: 61 words, one long token,
#: unreadable as printed and worthless as the source the artwork is measured by).
_LONG_TOKENS_MIN = 8

_TOKEN = re.compile(r"[A-Za-z]{4,}")
_VOWELS = re.compile(r"[aeiouy]", re.I)
_REPEAT = re.compile(r"(.)\1\1")


def looks_like_noise(text):
    """True for a small token stream that is clearly not words (``xqzvv rrrq``).

    The stopword test needs a pageful of words before it means anything, so a
    handful of tokens is judged by shape: no vowels, or the same letter three
    times running. Real sparse text (``November 2016``) fails this on purpose —
    a thin page is an honest thin page, not a damaged one.
    """
    tokens = _TOKEN.findall(text or "")
    if not tokens:
        return False
    noise = sum(1 for token in tokens
                if not _VOWELS.search(token) or _REPEAT.search(token))
    return noise * 2 >= len(tokens)


@dataclass
class PageRecovery(object):
    """What happened to one page's source layer, in the terms the report uses."""

    pno: int
    layer: str = "native"        # native | ocr
    reason: str = ""             # image_only | damaged_layer | native_trusted | failed
    engine: str = ""
    language: str = ""
    language_identity: str = ""
    requested_dpi: int = 0
    effective_dpi: float = 0.0
    orientation: int = 0
    orientation_confidence: float = 0.0
    source_rotation: int = 0
    flags: tuple = ()
    words: int = 0
    uncertain_words: int = 0
    reused: bool = False
    failed: str = ""
    page_rect: tuple = ()        # displayed-page rect, for crop transforms
    derotation: tuple = ()       # displayed -> unrotated PDF space
    seconds: float = 0.0
    #: The engine's own low-confidence words as annotation records, kept for the
    #: adoption seam: a model answer is not allowed to strip them (see pipeline).
    uncertain: list = field(default_factory=list)

    def to_dict(self):
        return {"pno": self.pno, "layer": self.layer, "reason": self.reason,
                "engine": self.engine, "language": self.language,
                "language_identity": self.language_identity,
                "requested_dpi": self.requested_dpi,
                "effective_dpi": round(self.effective_dpi, 1),
                "orientation": self.orientation,
                "orientation_confidence": round(self.orientation_confidence, 2),
                "flags": list(self.flags), "words": self.words,
                "uncertain_words": self.uncertain_words,
                "reused": self.reused, "failed": self.failed,
                "seconds": round(self.seconds, 3)}


@dataclass
class Recovery(object):
    """The chosen layer for the whole book, plus its audit trail."""

    pages: List[extract.RawPage] = field(default_factory=list)
    provenance: Dict[int, PageRecovery] = field(default_factory=dict)
    attempted: int = 0
    reused: int = 0
    failed: int = 0
    ocr_words: int = 0
    uncertain_words: int = 0
    seconds: float = 0.0
    engine_unavailable: bool = False

    @property
    def recovered_pnos(self):
        return {pno for pno, prov in self.provenance.items()
                if prov.layer == "ocr" and prov.words}

    @property
    def ocr_pages(self):
        return sum(1 for prov in self.provenance.values() if prov.layer == "ocr")

    def figure_rect(self, pno, rect):
        """A figure box in the page's reading space, mapped to unrotated PDF space.

        On an upright page this is the identity. On a rotated spread the skeleton
        measured the territory in the upright reading space, and the crop must be
        cut where the PDF actually stores the ink.
        """
        prov = self.provenance.get(pno)
        if prov is None or prov.layer != "ocr" or not prov.derotation:
            return rect
        source = ocr._source_box(rect, prov.page_rect[2] - prov.page_rect[0],
                                 prov.page_rect[3] - prov.page_rect[1],
                                 prov.orientation)
        return tuple(pymupdf.Rect(source) * pymupdf.Matrix(prov.derotation))

    def summary(self):
        return {"mode_pages": self.ocr_pages,
                "attempted": self.attempted, "reused": self.reused,
                "failed": self.failed, "ocr_words": self.ocr_words,
                "uncertain_words": self.uncertain_words,
                "seconds": round(self.seconds, 2),
                "engine_unavailable": self.engine_unavailable,
                "pages": [prov.to_dict()
                          for _, prov in sorted(self.provenance.items())
                          if prov.layer == "ocr" or prov.failed]}


def needs_recovery(raw):
    """Why this page's native layer cannot be the source, or that it can.

    A page with no text is an OCR candidate when it is a picture of a page. A
    page whose embedded text is not words — a pageful the stopword test rejects,
    or a sparse stream of clear non-words — is a damaged-layer candidate only
    when the pixels underneath carry the real content (a scan); garbage set by
    a computer is not made meaningful by photographing it. A thin but real page
    keeps its layer, and trustworthy text is never re-read.
    """
    if not raw.text_blocks:
        return "image_only" if raw.images else ""
    if assess.looks_like_prose(raw.text):
        if raw.is_page_scan and assess.script_share(raw.text) < 0.5 \
                and len(raw.text.split()) >= _SPARSE_TOKENS \
                and len(_TOKEN.findall(raw.text)) < _LONG_TOKENS_MIN:
            return "damaged_layer"
        return ""
    if not raw.is_page_scan:
        return ""
    tokens = len(raw.text.split())
    if tokens >= _SPARSE_TOKENS or looks_like_noise(raw.text):
        return "damaged_layer"
    return ""


def candidates(raw_pages):
    """The (pno, reason) pairs a mode ``auto`` run would attempt."""
    out = []
    for raw in raw_pages:
        reason = needs_recovery(raw)
        if reason:
            out.append((raw.pno, reason))
    return out


def _page_from_ocr(result, original):
    """One OCR result as a RawPage the rest of the pipeline already understands.

    Tesseract's (block, paragraph, line) grouping is kept as the segmentation;
    reading order is NOT taken from it — the skeleton's own geometry pass orders
    bands and columns from the boxes, which is what a sideways two-page spread
    needs. Type size is estimated from glyph height, so the book's relative size
    machinery (ladder, markers, notes) still has something to measure.
    """
    groups = {}
    order = []
    for word in result.words:
        key = (word.block, word.paragraph, word.line)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(word)

    def size_of(word):
        height = max(1.0, word.bbox[3] - word.bbox[1])
        return round(height / _SIZE_FROM_HEIGHT, 2)

    blocks = {}
    for key in order:
        block_id, paragraph, _ = key
        blocks.setdefault((block_id, paragraph), []).append(key)

    out_blocks = []
    for number, (_, paragraph_keys) in enumerate(sorted(blocks.items())):
        lines = []
        for key in paragraph_keys:
            words = groups[key]
            spans = []
            for index, word in enumerate(words):
                text = word.text + (" " if index + 1 < len(words) else "")
                spans.append(extract.Span(
                    text=text, size=size_of(word), font="ocr", flags=0,
                    bbox=tuple(word.bbox), origin_y=float(word.bbox[3])))
            x0 = min(w.bbox[0] for w in words)
            y0 = min(w.bbox[1] for w in words)
            x1 = max(w.bbox[2] for w in words)
            y1 = max(w.bbox[3] for w in words)
            lines.append(extract.Line(spans=spans, bbox=(x0, y0, x1, y1)))
        box = (min(ln.bbox[0] for ln in lines), min(ln.bbox[1] for ln in lines),
               max(ln.bbox[2] for ln in lines), max(ln.bbox[3] for ln in lines))
        out_blocks.append(extract.Block(number=number, bbox=box, lines=lines))

    return extract.RawPage(
        pno=result.page_index, width=result.width, height=result.height,
        blocks=out_blocks, images=list(original.images),
        drawings=original.drawings,
        drawing_rects=list(original.drawing_rects))


def uncertain_spans(result):
    """Words the engine itself was not sure of, as annotation records.

    The score is the engine's own, not a probability, so nothing about a likely
    or alternative reading is asserted: the mark says the reading is uncertain
    and points at the printed source, which is the only honest thing to say.
    """
    return [{"token": word.text, "candidates": [], "score": word.confidence,
             "source_bbox": list(word.source_bbox)}
            for word in result.words if word.confidence < UNCERTAIN_SCORE]


def recover(doc, raw_pages, fingerprint, *, mode="auto",
            language="eng", dpi=300, cache_dir=None, scratch_dir=None,
            should_stop=None, progress=None):
    """Choose the source layer for every page, recovering what needs it.

    ``OCRUnavailable`` propagates: it means the job as asked cannot be done (the
    engine or the language data is missing), and it must stop the run before any
    paid work, named — never degrade page by page into silence. A recognition
    that fails on one page keeps that page's native layer and is recorded as
    failed, so a damaged original can still ship as a labelled facsimile.
    """
    mode = mode if mode in MODES else "auto"
    recovery = Recovery()
    chosen = []
    started = time.monotonic()
    wanted = []
    for raw in raw_pages:
        reason = needs_recovery(raw) if mode != "off" else ""
        if mode == "textless" and reason == "damaged_layer":
            reason = ""
        wanted.append(reason)
    total = sum(1 for reason in wanted if reason)
    done = 0

    # One engine check up front, before any paid or per-page work: an explicit
    # OCR choice with a missing engine stops here, named; the default path
    # records it once and keeps every page's native layer.
    if total:
        try:
            ocr._engine(language)
        except ocr.OCRUnavailable:
            if mode != "auto_if_available":
                raise
            recovery.engine_unavailable = True
            for raw, reason in zip(raw_pages, wanted):
                prov = PageRecovery(
                    pno=raw.pno, reason=reason or "native_trusted",
                    failed="engine or language data unavailable" if reason else "",
                    page_rect=(0.0, 0.0, raw.width, raw.height))
                if reason:
                    recovery.failed += 1
                recovery.provenance[raw.pno] = prov
                chosen.append(raw)
            recovery.pages = chosen
            recovery.seconds = time.monotonic() - started
            return recovery

    for raw, reason in zip(raw_pages, wanted):
        prov = PageRecovery(pno=raw.pno, reason=reason or "native_trusted",
                            page_rect=(0.0, 0.0, raw.width, raw.height))
        if not reason:
            prov.layer = "native"
            recovery.provenance[raw.pno] = prov
            chosen.append(raw)
            continue

        if should_stop is not None and should_stop():
            raise ocr.OCRCancelled("Text recognition was cancelled.")
        recovery.attempted += 1
        page_started = time.monotonic()
        try:
            result = ocr.recognize_page(
                doc[raw.pno], source_sha256=fingerprint, language=language,
                dpi=dpi, cache_dir=cache_dir, scratch_dir=scratch_dir,
                should_stop=should_stop)
        except ocr.OCRCancelled:
            raise
        except ocr.OCRUnavailable:
            if mode != "auto_if_available":
                raise
            # The default path: the user has not chosen OCR, so a missing engine
            # is a fact to disclose, not a reason to refuse the conversion.
            prov.layer = "native"
            prov.reason = reason
            prov.failed = "engine or language data unavailable"
            recovery.failed += 1
            recovery.engine_unavailable = True
            recovery.provenance[raw.pno] = prov
            chosen.append(raw)
            done += 1
            if progress is not None:
                progress(done, total)
            continue
        except ocr.OCRFailed as exc:
            prov.layer = "native"
            prov.reason = reason
            prov.failed = str(exc)[:200]
            recovery.failed += 1
            recovery.provenance[raw.pno] = prov
            chosen.append(raw)
            done += 1
            if progress is not None:
                progress(done, total)
            continue

        prov.layer = "ocr"
        prov.reason = reason
        prov.engine = result.engine_version
        prov.language = result.language
        prov.language_identity = result.language_identity
        prov.requested_dpi = result.requested_dpi
        prov.effective_dpi = result.effective_dpi
        prov.orientation = result.orientation_clockwise
        prov.orientation_confidence = result.orientation_confidence
        prov.source_rotation = result.source_rotation
        prov.flags = tuple(result.flags)
        prov.words = len(result.words)
        prov.uncertain_words = sum(
            1 for word in result.words if word.confidence < UNCERTAIN_SCORE)
        prov.uncertain = uncertain_spans(result)
        prov.reused = bool(getattr(result, "reused", False))
        prov.derotation = tuple(doc[raw.pno].derotation_matrix)
        prov.seconds = time.monotonic() - page_started
        recovery.ocr_words += prov.words
        recovery.uncertain_words += prov.uncertain_words
        recovery.reused += 1 if prov.reused else 0
        chosen.append(_page_from_ocr(result, raw))
        recovery.provenance[raw.pno] = prov
        done += 1
        if progress is not None:
            progress(done, total)

    recovery.pages = chosen
    recovery.seconds = time.monotonic() - started
    return recovery
