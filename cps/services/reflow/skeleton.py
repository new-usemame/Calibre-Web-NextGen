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
#: How far past the furniture, in points, the picture sent to the model starts. Small
#: enough that it can never reach a line of the book, and the clamp below makes sure.
CROP_GAP = 2.0

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
#: Two sizes this close are the same printed size. MEASURED on the acceptance book:
#: one 16pt chapter title reads between 15.5 and 16.4pt from page to page, a spread
#: of 5.8%, because the scan is photographed type and not a font instruction.
LADDER_TOL = 0.06
#: A heading level recurs through the book; display type on a title page does not,
#: and neither does the type on a chart. MEASURED on the acceptance book, the rungs
#: come out at 1, 1, 3, 37, 9 and 108 pages: the two heavy ones are its chapter and
#: section headings, and the four light ones are the cover and chart legends. Type
#: above the top rung is level 1 anyway, so a rare *real* heading loses nothing.
LADDER_MIN_PAGES = 0.02
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

    def body_box(self):
        """The part of the printed page whose words were kept.

        The model is sent this page's text with the running head, the running foot
        and the folio already removed, and a picture of the page. If the picture
        still prints all three, the two disagree and the model reconciles them the
        way a reader would -- by typing in what it can see. MEASURED on the
        acceptance book: "Chapter 4: The Hellenistic Astrologers" came back inside
        the chapter on every page of a 30-page range, five words that were not in
        the text layer, and every page failed the word gate for it. A written rule
        not to read what is in front of it did not hold on any provider. So the
        picture is cropped to the page the words came from.

        The crop only ever removes furniture. Each edge stops short of the
        outermost line that survived, so a page whose body starts high in the
        header band, or whose notes run down into the footer band, keeps the height
        it needs: an image missing a line of the book would be a worse defect than
        the one this fixes, because the model would then be placing words it cannot
        see.
        """
        top, bottom = 0.0, self.height
        kept, head, foot = [], [], []
        for region in self.regions:
            for line in region.lines:
                if region.kind != "furniture":
                    kept.append(line.bbox)
                elif line.bbox[3] <= self.height * HEADER_BAND:
                    head.append(line.bbox)
                elif line.bbox[1] >= self.height * FOOTER_BAND:
                    foot.append(line.bbox)
        if head:
            top = max(box[3] for box in head) + CROP_GAP
            if kept:
                top = min(top, min(box[1] for box in kept))
            top = max(0.0, top)
        if foot:
            bottom = min(box[1] for box in foot) - CROP_GAP
            if kept:
                bottom = max(bottom, max(box[3] for box in kept))
            bottom = min(self.height, bottom)
        return (0.0, top, self.width, bottom)

    @property
    def note_numbers(self):
        """In printed order: the sequence is the evidence a damaged number is read by."""
        return [r.number for r in self.regions
                if r.kind == "note" and r.number is not None]

    @property
    def note_regions(self):
        return [r for r in self.regions if r.kind == "note"]

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
        """The ladder runs largest first, so the first rung the size reaches is its
        level. Type above every rung is level 1: a heading larger than the book's
        largest cannot be set deeper than its smallest."""
        for index, centre in enumerate(self.ladder):
            if size >= centre * (1.0 - LADDER_TOL):
                return index + 1
        return min(LADDER_MAX_LEVELS, len(self.ladder) + 1)

    def on_the_ladder(self, size):
        """True when this size reaches a rung the book's own heading type defines.

        Below the bottom rung a size says nothing: on a scan the same paragraph
        measures differently line to line, so "a few percent above the body" is as
        likely to be the scanner as the typesetter.
        """
        return any(size >= centre * (1.0 - LADDER_TOL) for centre in self.ladder)


def heading_ladder(body_size, census, pages, page_count):
    """The heading sizes this book actually uses, largest first.

    Two things have to be true of a rung. It has to be *bigger than the body*, which
    is what makes it a heading at all; and it has to *recur*, which is what makes it
    a level rather than a one-off piece of display type. On a scan neither is a clean
    equality: the same printed heading measures differently on every page it appears
    on, so sizes within LADDER_TOL of a rung's running centre belong to that rung.
    """
    if not body_size:
        return []
    floor = max(2, int(round(LADDER_MIN_PAGES * page_count)))
    candidates = sorted(((size, chars) for size, chars in census.items()
                         if size >= body_size * LADDER_RATIO and chars >= 8),
                        key=lambda kv: -kv[0])

    clusters = []                      # [[weighted size total, chars, {pages}], ...]
    for size, chars in candidates:
        seen = pages.get(size, set())
        if clusters:
            centre = clusters[-1][0] / clusters[-1][1]
            if size >= centre * (1.0 - LADDER_TOL):
                clusters[-1][0] += size * chars
                clusters[-1][1] += chars
                clusters[-1][2] |= seen
                continue
        clusters.append([size * chars, chars, set(seen)])

    ladder = [total / chars for total, chars, seen in clusters if len(seen) >= floor]
    return [round(size, 2) for size in ladder[:LADDER_MAX_LEVELS]]


#: A scanner writes one outline entry per page and titles it after the page. That is
#: a page index, not a table of contents. MEASURED: book 567's outline is 698 entries
#: reading "Page 1" to "Page 698"; a navigation built from it has no chapters in it.
_PAGE_LABEL = re.compile(r"^(page|p\.?|folio|sheet|image|scan)\s*[ivxlcdm\d]+$", re.I)


def outline_is_useful(outline):
    """True when the PDF's own outline is a table of contents worth keeping."""
    titles = [str(entry.get("title") or "").strip() for entry in outline or []]
    titles = [t for t in titles if t]
    if len(titles) < 2:
        return False
    labels = sum(1 for t in titles if _PAGE_LABEL.match(t))
    return labels <= len(titles) * 0.2


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
    pages = {}

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
                    pages.setdefault(size, set()).add(raw.pno)
                if ln.bbox[3] <= top or ln.bbox[1] >= raw.height * FOOTER_BAND:
                    if len(ln.stripped) <= BAND_TEXT_MAX:
                        bands[band_key(ln.stripped)] += 1

    census = central or everything
    body_size = census.most_common(1)[0][0] if census else 0.0

    ladder = heading_ladder(body_size, census, pages, len(raw_pages))

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


#: Words that stand inside a title but cannot end one. A line that stops on one of
#: these stopped because the page ran out of width, not because the phrase finished.
#: MEASURED on book 567: 'Capricorn, Mars and Saturn in' (p527), read as a heading
#: because the scan set it at 12.50pt against a 12.99pt heading rung.
_NOT_A_LAST_WORD = frozenset("""
a an the and or nor but if so then than that which who whom whose whether
of in on at to for from by with within without into onto over under above below
between among against about across after before during since until while because
although though unless upon toward towards through is are was were be been being am
it its his her their our your my no not
""".split())

_TRAILING_NON_WORD = re.compile(r"[^A-Za-z0-9']+$")


def acceptable_heading(text):
    """The shape of a heading, independent of how it is set.

    Two of the three rules here exist because a scan's measurements wobble: a line of
    a paragraph that comes back a little large reaches a heading rung and nothing
    about its type says otherwise, so the only evidence left is what the line says.
    A false heading costs more than a missed one -- the EPUB splits its chapters on
    the top of the ladder, so it breaks a chapter in the middle of a paragraph.
    """
    text = (text or "").strip()
    if not 4 <= len(text) <= 120:
        return False
    if text.endswith((".", ",", ":", ";")):
        # A line that ends in sentence punctuation is a sentence. One real heading per
        # book is lost this way; it comes back through the model route.
        return False
    if text[:1].islower():
        # A heading begins where a sentence begins. MEASURED on book 567:
        # 'bonify Mercury. Conversely, if' (p493).
        return False
    last = _TRAILING_NON_WORD.sub("", text).rsplit(" ", 1)[-1].lower()
    if last in _NOT_A_LAST_WORD:
        return False
    return not looks_like_chart_junk(text)


def continues_lowercase(line):
    text = line.stripped
    return bool(text) and text[0].islower()


def heading_ish(line, style):
    """Could this line be a heading, judged on how it is set rather than what it says.

    Weight is the reliable signal and size is not. MEASURED on book 567: the body of
    PDF page 112 comes back between 11.0 and 11.7pt and one line of a running
    paragraph at 12.00pt, in the same roman face -- read as "bigger than the body"
    that line becomes a heading, and half a sentence lands in the reader's table of
    contents. So roman type has to reach a rung the book actually uses; only bold
    type may be a heading on weight alone, which is what defect A needs (a run-in
    head is set on the body's own leading and may be barely larger than it).
    """
    if not style.body_size:
        return False
    size = line.size
    if line.bold:
        return size >= style.body_size * 0.98
    return size >= style.body_size * HEAD_RATIO and style.on_the_ladder(size)


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
        known = [r.number for r in notes if r.number is not None]
        opened = _note_number(blk.lines[0], blk.size, opening=True,
                              after=known[-1] if known else None)
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
    seen = [r.number for r in existing if r.number is not None]
    for position, ln in enumerate(blk.lines):
        number = _note_number(ln, blk.size, opening=(position == 0),
                              after=seen[-1] if seen else None)
        if number is not None:
            seen.append(number)
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


#: What a scanner returns instead of a digit when it reads a note marker set two
#: points under the body. MEASURED on the acceptance book: ``Hephaestio.s°`` for 50,
#: ``Petosiris.si`` for 51, ``Republic.s6`` for 56, ``related.9°`` for 90,
#: ``Antiochus."1`` for 111, ``century.loo`` for 100, ``CE.20'`` for 201. The damage
#: is per glyph and it is the usual one: a 5 is read as an s, a 0 as a degree sign or
#: an o, a 1 as an l or an apostrophe, a pair of 1s as one double quote.
MARKER_GLYPHS = {"0": "0Oo°º", "1": "1lI|!i\'’‘", "2": "2Zz", "3": "3",
                 "4": "4", "5": "5sS", "6": "6b", "7": "7", "8": "8B", "9": "9gq"}
GLYPH_DIGITS = {char: digit for digit, chars in MARKER_GLYPHS.items() for char in chars}
GLYPH_DIGITS.update({'"': "11", "”": "11", "“": "11"})



def glyph_number(text):
    """The number a scanner could have been reading when it returned *text*.

    ``None`` when any glyph in the run is not one a digit is mistaken for, which
    is most of them: this is a lookup, not a guess.
    """
    digits = []
    for char in text:
        digit = GLYPH_DIGITS.get(char)
        if digit is None:
            return None
        digits.append(digit)
    return "".join(digits)


#: A note number the scanner read as letters, at the head of the note's own text:
#: the run, the space, and then the note's first word. MEASURED, page 109 of the
#: acceptance book opens its footnote zone with ``9° Tarrant, Thrasyllan Platonism``
#: and page 113 with ``"1 Edited in CCAG 5, 4``. A run that is nothing but letters
#: is a word and not a number, however well ``so`` reads as 50 -- a note that ran
#: over from the page before opens the zone with one.
_GLYPH_NOTE_NUMBER = re.compile("^(\\S{2,4})[ \\t]+(?=[A-Z\u201c\u2018\"\'])")


#: A footnote opening the OCR could not keep apart from its own number: the digits,
#: a space, and then the start of a sentence. The trailing context is what separates
#: it from an endnote entry ("15. Brennan..."), a table row and page-bottom debris.
_MERGED_NOTE_NUMBER = re.compile(r"^(\d{1,3})[ \t]+(?=[A-Z\u201c\u2018\"\'])")


def _note_number(line, block_size, opening=False, after=None):
    """The note's own number when the line opens one.

    Two printed shapes reach us. The raised number survives as its own small span —
    the strong signal, and the only one accepted mid-block. Or the scanner merged it
    into the first text span at full size, which MEASURED costs 138 footnotes on 70
    of the acceptance book's 698 pages; that shape is only read at the first line of
    a block already inside the footnote zone, where a number can only be a number.
    """
    spans = [sp for sp in line.spans if sp.text.strip()]
    if not spans:
        return None
    first = spans[0]
    text = first.text.strip()
    if re.fullmatch(r"\d{1,3}", text):
        if block_size and first.size > MARGIN_SIZE * block_size:
            return None
        return int(text)
    merged = _MERGED_NOTE_NUMBER.match(line.stripped)
    if merged:
        value = int(merged.group(1))
        # Footnotes ascend down the page. Where that is checkable it is the guard
        # that keeps a citation's own numbers ("112, trans. Oldfather") from opening
        # a note; at the first note in the zone there is nothing yet to check it
        # against, and the block's position in the zone carries the claim instead.
        if after is None:
            if opening:
                return value
        elif value > after:
            return value

    glyphed = _GLYPH_NOTE_NUMBER.match(line.stripped)
    if glyphed and not glyphed.group(1).isalpha() and not glyphed.group(1).isdigit():
        digits = glyph_number(glyphed.group(1))
        if digits and 0 < len(digits) <= 3 and int(digits) > 0:
            value = int(digits)
            if after is None:
                if opening:
                    return value
            elif value > after:
                return value
    return None


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
