# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Stage 2: what each region of a page *is*.

Headings, running furniture, footnote zone, figures and captions — decided from
geometry and typography, with a named reason for every uncertain call so stage 4 can
route that page to the model instead of guessing.

The heading rule is the one that changed most from the reference implementation.
The old ``plausible_heading`` demanded that 70% of a heading's tokens be clean
alphabetic words, which is a *junk filter* wearing a heading rule's clothes: it threw
away ``Serapio of Alexandria (First Century CE?)`` (four clean words out of six, the
other two being ``(First`` and ``CE?)``) and 27 more like it in one book. Here the
typography decides, and the junk filter is demoted to a veto that looks for what
chart labels actually are — stray single glyphs and columns of numbers.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from . import extract

#: Page bands treated as running-head / running-foot territory.
HEADER_BAND = 0.075
FOOTER_BAND = 0.925
#: A band string printed on this share of pages is furniture, not content.
BOILER_HITS = 0.20
#: A short in-band string reads as furniture; a long one is body text that strayed.
BAND_TEXT_MAX = 90

#: A footnote number / inline marker is at most this share of its block's type size.
MARGIN_SIZE = 0.7
#: A footnote block is set at most this share of the body size ...
FN_SIZE_RATIO = 0.9
#: ... and a numbered note may start this far down the page.
FN_ZONE_NUMBERED = 0.45
#: A separate-span inline marker is at most this share of its line's type size. The
#: measured OCR layer sets them at 0.72-0.81, often flat ON the baseline.
MARKER_SIZE_MAX = 0.85

#: A heading is set at least this much larger than the body, or is bold at body size.
HEAD_RATIO = 1.05
#: A distinct size this far above the body earns a level in the heading ladder.
LADDER_RATIO = 1.12
#: Levels below this are more taxonomy than navigation.
LADDER_MAX_LEVELS = 4

#: A gutter this wide (share of page width) with no line crossing it reads as columns.
GUTTER_MIN = 0.06

FOLIO = re.compile(r"^(?:page\s*)?[\divxlcdmIVXLCDM]{1,8}[.)]?$")
CAPTION_LINE = re.compile(r"^(?:fig(?:ure|\.)|table|chart|plate|map|diagram)\s*\d", re.I)
_DIGITS = re.compile(r"\d+")
_WORDISH = re.compile(r"[A-Za-z][A-Za-z'’\-.]*$")
_NUMERIC = re.compile(r"[-+]?\d+(?:[.,]\d+)?$")
_MARKER_NOTATION = re.compile(r"\[\d{1,3}\]")
_STRIP_EDGES = "()[]{}<>«»\"'‘’“”.,:;!?—–-"


@dataclass
class Region(object):
    """One classified run of lines on a page."""

    kind: str                       # heading | body | note | furniture | caption | figure
    lines: List[extract.Line] = field(default_factory=list)
    level: int = 0                  # headings only
    number: Optional[int] = None    # notes only
    reason: str = ""
    bbox: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    image: Optional[extract.Image] = None

    @property
    def text(self):
        return " ".join(ln.stripped for ln in self.lines).strip()


@dataclass
class PageSkeleton(object):
    pno: int
    width: float
    height: float
    regions: List[Region] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    is_scan: bool = False

    @property
    def note_numbers(self):
        return {r.number for r in self.regions if r.kind == "note" and r.number is not None}

    @property
    def body_regions(self):
        return [r for r in self.regions if r.kind in ("heading", "body", "caption")]

    @property
    def confidence(self):
        """1.0 when nothing on the page needed a judgement call.

        Each named reason costs 0.2 — the number itself is only a sort key for
        routing; the reasons are what a reviewer reads.
        """
        return max(0.0, 1.0 - 0.2 * len(set(self.reasons)))


@dataclass
class BookStyle(object):
    body_size: float
    ladder: List[float] = field(default_factory=list)
    band_hits: dict = field(default_factory=dict)
    page_count: int = 0
    outline: List[dict] = field(default_factory=list)

    @property
    def boiler_threshold(self):
        return max(2, int(round(BOILER_HITS * self.page_count)))

    @property
    def levels(self):
        return list(range(1, len(self.ladder) + 1)) or [1]

    def level_for(self, size):
        for index, ladder_size in enumerate(self.ladder):
            if abs(size - ladder_size) < 0.35:
                return index + 1
        return min(LADDER_MAX_LEVELS, len(self.ladder) + 1)


def band_key(text):
    """Running heads differ only in their folio: fold the digits out."""
    return _DIGITS.sub("#", " ".join(text.lower().split()))


def book_style(raw_pages, outline=None):
    """Measure the book once: body size, heading ladder, repeated band strings.

    The body-size census only looks at the central band of each page. Footnotes are
    set smaller and running heads smaller still; on a short document (or a sample)
    they can out-vote the body text and every downstream ratio then reads off a wrong
    baseline.
    """
    central = Counter()
    everything = Counter()
    bands = Counter()

    for raw in raw_pages:
        top, bottom = raw.height * HEADER_BAND, raw.height * FN_ZONE_NUMBERED
        for blk in raw.text_blocks:
            for ln in blk.lines:
                chars = len(ln.stripped)
                if not chars:
                    continue
                size = ln.size
                everything[size] += chars
                if ln.bbox[3] > top and ln.bbox[1] < bottom:
                    central[size] += chars
                if ln.bbox[3] <= top or ln.bbox[1] >= raw.height * FOOTER_BAND:
                    if len(ln.stripped) <= BAND_TEXT_MAX:
                        bands[band_key(ln.stripped)] += 1

    census = central or everything
    body_size = census.most_common(1)[0][0] if census else 0.0

    ladder = []
    if body_size:
        candidates = [(size, chars) for size, chars in census.items()
                      if size >= body_size * LADDER_RATIO and chars >= 8]
        ladder = [size for size, _ in sorted(candidates, key=lambda kv: -kv[0])]
        ladder = ladder[:LADDER_MAX_LEVELS]

    return BookStyle(body_size=body_size, ladder=ladder, band_hits=dict(bands),
                     page_count=len(raw_pages), outline=list(outline or []))


# ------------------------------------------------------------------ heading vetoes

def looks_like_chart_junk(text):
    """True for the things that outrank real headings on font size alone.

    Astrological charts, tables and figure keys carry large glyphs and stray
    characters — ``DAY CHART a 9 -5 e``, ``'Ts a 9``. A table of contents built from
    those is unusable. This asks what junk *is* (loose glyphs, columns of numbers)
    rather than demanding that a heading be made of tidy dictionary words, which is
    what cost the reference implementation 27 real headings.
    """
    cleaned = _MARKER_NOTATION.sub(" ", text or "").strip()
    tokens = cleaned.split()
    if not tokens:
        return True

    cores = [t.strip(_STRIP_EDGES) for t in tokens]
    cores = [c for c in cores if c]
    letters = sum(1 for ch in cleaned if ch.isalpha())

    if len(tokens) == 1:
        return letters < 3

    words = [c for c in cores if _WORDISH.match(c) and (len(c) >= 2 or c in ("A", "I"))]
    if len(words) < 2:
        return True
    glyphs = [c for c in cores if len(c) == 1 and not c.isdigit() and c not in ("A", "I")]
    if len(glyphs) > len(words):
        return True
    numbers = [c for c in cores if _NUMERIC.match(c)]
    if len(numbers) >= 2 and len(numbers) >= len(words):
        return True
    return False


def acceptable_heading(text):
    """The shape of a heading, independent of how it is set."""
    text = (text or "").strip()
    if not 4 <= len(text) <= 120:
        return False
    if text.endswith((".", ",", ":", ";")):
        # A line that ends in sentence punctuation is a sentence. One real heading per
        # book is lost this way; it comes back through the model route.
        return False
    return not looks_like_chart_junk(text)


def continues_lowercase(line):
    text = line.stripped
    return bool(text) and text[0].islower()


def heading_ish(line, style):
    if not style.body_size:
        return False
    size = line.size
    if size >= style.body_size * HEAD_RATIO:
        return True
    return line.bold and size >= style.body_size * 0.98


# ----------------------------------------------------------------------- the skeleton

def page_skeleton(raw, style):
    """Classify one page's regions, recording a reason for every uncertain call."""
    skel = PageSkeleton(pno=raw.pno, width=raw.width, height=raw.height,
                        is_scan=raw.is_page_scan)

    if not raw.text_blocks:
        skel.reasons.append("no_text_layer" if raw.images else "empty_page")
        for img in raw.images:
            skel.regions.append(Region(kind="figure", bbox=img.bbox, image=img))
        return skel

    body_blocks, note_regions = _split_off_notes(raw, style, skel)

    if _looks_multi_column(body_blocks, raw):
        skel.reasons.append("multi_column")

    for blk in body_blocks:
        kept = []
        for ln in blk.lines:
            reason = _furniture_reason(ln, raw, style)
            if reason:
                skel.regions.append(Region(kind="furniture", lines=[ln], reason=reason,
                                           bbox=ln.bbox))
            else:
                kept.append(ln)
        if kept:
            _classify_body(kept, blk, style, skel)

    for img in raw.images:
        if img.substantial and not img.full_page:
            skel.regions.append(Region(kind="figure", bbox=img.bbox, image=img))

    skel.regions.extend(note_regions)
    skel.regions.sort(key=_region_order)
    return skel


def _region_order(region):
    return (0 if region.kind != "note" else 1, round(region.bbox[1], 1),
            round(region.bbox[0], 1))


def _split_off_notes(raw, style, skel):
    """Separate the footnote zone from the body, keeping multi-block notes together."""
    body, notes = [], []
    seen_note = False
    zone_top = raw.height * FN_ZONE_NUMBERED

    for blk in raw.text_blocks:
        if blk.bbox[1] < zone_top or not style.body_size:
            body.append(blk)
            continue
        if blk.size > style.body_size * FN_SIZE_RATIO:
            body.append(blk)
            continue
        opened = _note_number(blk.lines[0], blk.size)
        if opened is None and not seen_note:
            body.append(blk)
            continue
        seen_note = True
        notes.extend(_notes_in_block(blk, notes))

    if notes:
        numbers = [n.number for n in notes if n.number is not None]
        if len(numbers) != len(set(numbers)):
            skel.reasons.append("duplicate_note_numbers")
    return body, notes


def _notes_in_block(blk, existing):
    """One block can hold several notes; a new one opens with its own small number.

    Lines before the first number continue the note that ran over from the block
    above — a long note printed across two blocks, not a new unnumbered one.
    """
    out = []
    current = None
    for ln in blk.lines:
        number = _note_number(ln, blk.size)
        if number is not None:
            current = Region(kind="note", lines=[ln], number=number, bbox=ln.bbox)
            out.append(current)
            continue
        if current is None:
            if existing:
                _append_line(existing[-1], ln)
                continue
            current = Region(kind="note", lines=[ln], number=None, bbox=ln.bbox)
            out.append(current)
            continue
        _append_line(current, ln)
    return out


def _append_line(region, line):
    region.lines.append(line)
    region.bbox = (min(region.bbox[0], line.bbox[0]), region.bbox[1],
                   max(region.bbox[2], line.bbox[2]), line.bbox[3])


def _note_number(line, block_size):
    """The note's own number when the line opens one."""
    spans = [sp for sp in line.spans if sp.text.strip()]
    if not spans:
        return None
    first = spans[0]
    text = first.text.strip()
    if not re.fullmatch(r"\d{1,3}", text):
        return None
    if block_size and first.size > MARGIN_SIZE * block_size:
        return None
    return int(text)


def _furniture_reason(line, raw, style):
    y0, y1 = line.bbox[1], line.bbox[3]
    in_head = y1 <= raw.height * HEADER_BAND
    in_foot = y0 >= raw.height * FOOTER_BAND
    if not (in_head or in_foot):
        return None
    text = line.stripped
    if not text or len(text) > BAND_TEXT_MAX:
        return None
    if style.body_size and line.size > style.body_size * 1.02:
        return None
    if FOLIO.match(text):
        return "folio"
    if style.band_hits.get(band_key(text), 0) >= style.boiler_threshold:
        return "running"
    if _is_caps(text) and style.body_size and line.size < style.body_size * 0.98:
        return "running_caps"
    return None


def _is_caps(text):
    return any(ch.isalpha() for ch in text) and text == text.upper()


def _classify_body(lines, blk, style, skel):
    """Split a block into headings and prose, honouring run-in sub-headings."""
    if len(lines) >= 2 and heading_ish(lines[0], style) \
            and not any(heading_ish(ln, style) for ln in lines[1:]):
        head = lines[0].stripped
        if acceptable_heading(head) and not continues_lowercase(lines[1]):
            skel.regions.append(Region(kind="heading", lines=[lines[0]],
                                       level=style.level_for(lines[0].size),
                                       reason="run_in", bbox=lines[0].bbox))
            lines = lines[1:]
        elif acceptable_heading(head):
            # Bold, short, well-formed — but its sentence carries on underneath. The
            # model gets to look at this page; the deterministic answer is "prose".
            skel.reasons.append("run_in_candidate_rejected")

    if lines and all(heading_ish(ln, style) for ln in lines):
        joined = " ".join(ln.stripped for ln in lines)
        if acceptable_heading(joined):
            skel.regions.append(Region(kind="heading", lines=list(lines),
                                       level=style.level_for(lines[0].size),
                                       bbox=blk.bbox))
            return
        skel.reasons.append("large_type_not_a_heading")

    if not lines:
        return
    kind = "caption" if CAPTION_LINE.match(lines[0].stripped) else "body"
    skel.regions.append(Region(kind=kind, lines=list(lines), bbox=blk.bbox))


def _looks_multi_column(blocks, raw):
    """A wide vertical gutter no line crosses means the page is set in columns.

    Reflow does not reorder columns; it says so and lets stage 4 route the page.
    """
    spans = [(ln.bbox[0], ln.bbox[2]) for blk in blocks for ln in blk.lines
             if len(ln.stripped) > 12]
    if len(spans) < 6:
        return False
    left = min(x0 for x0, _ in spans)
    right = max(x1 for _, x1 in spans)
    if right - left < raw.width * 0.4:
        return False
    for fraction in (0.40, 0.45, 0.50, 0.55, 0.60):
        cut = left + (right - left) * fraction
        gutter = raw.width * GUTTER_MIN / 2.0
        if not any(x0 < cut + gutter and x1 > cut - gutter for x0, x1 in spans):
            return True
    return False


def is_marker_span(span, line_size):
    """A digit run small enough to be an inline note marker."""
    text = span.text.strip()
    if not re.fullmatch(r"\d{1,3}", text):
        return False
    return bool(line_size) and span.size <= MARKER_SIZE_MAX * line_size
