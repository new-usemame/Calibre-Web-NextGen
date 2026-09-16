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

#: A line this wide (share of the page's text span) bridges every column gutter:
#: it is a band of its own -- a full-width heading, a rule, a plate.
FULL_SPAN = 0.70
#: A single line at least this wide (share of the measure) that crosses a
#: channel is a band element and does not veto the gutter: section heads sit
#: inside columned pages.
BAND_MIN = 0.55
#: Column layout is only believed when at least two columns each hold this many
#: lines. Anything thinner is a caption pair or a stray, not a typeset column.
COLUMN_MIN_LINES = 3
#: Prose fills its measure: the median column line is at least this full. A grid
#: of short cells is a table wearing a column's shape, and is left row-major.
COLUMN_FILL_MIN = 0.45
#: A page carrying at least this many vector paths may be holding ruled tables;
#: column traversal of a ruled grid destroys its rows, so it is left alone.
RULED_MIN_PATHS = 4

#: A text-free band this tall (share of page height) inside a page scan is figure
#: territory -- on this kind of book the diagrams live inside the page raster.
SCAN_GAP = 0.14
#: An empty side channel at least this wide (share of the text span) beside a
#: prose column is sidebar figure territory (a natal wheel next to its reading).
SIDE_CHANNEL_MIN = 0.25
#: On a scan, lettering set at least this far above the body is chart lettering,
#: not prose -- the sparse giant glyphs an OCR layer reads off a diagram.
CHART_LABEL_RATIO = 1.5
#: A line at least this much inside a figure region is lettering on the figure.
FIG_LINE_OVERLAP = 0.80

#: A vector diagram needs at least this many paths clustered together ...
VEC_MIN_PATHS = 25
#: ... covering at least this much of the page, and at most this much.
VEC_MIN_AREA = 0.04
VEC_MAX_AREA = 0.75

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

    kind: str                       # heading | body | note | furniture | caption | figure | artwork
    lines: List[extract.Line] = field(default_factory=list)
    level: int = 0                  # headings only
    number: Optional[int] = None    # notes only
    reason: str = ""
    bbox: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    image: Optional[extract.Image] = None
    #: Reading-order coordinates: a full-width band breaks the page, and columns
    #: read top-to-bottom within a band, left-to-right across it. Both zero on an
    #: ordinary single-column page, which is what makes this backward compatible.
    band: int = 0
    column: int = 0
    #: Caption lines absorbed into a figure: printed inside its territory (a chart
    #: title under a wheel), carried here so the figure keeps its caption without
    #: the words reading twice.
    caption_lines: List[extract.Line] = field(default_factory=list)
    #: Figures found by geometry rather than by an embedded image: the crop must
    #: prove it holds ink before it is emitted, so blank paper is never artwork.
    needs_ink: bool = False

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

    letters = sum(1 for ch in cleaned if ch.isalpha())
    if len(tokens) == 1:
        return letters < 3

    words = numbers = debris = 0
    for token in tokens:
        kind = _token_kind(token)
        words += kind == "word"
        numbers += kind == "number"
        debris += kind == "debris"

    if words < 2:
        return True
    if debris > words:
        return True
    if numbers >= 2 and numbers >= words:
        return True
    return False


#: Brackets only. Stripping quotation marks and stops as well would turn the
#: wreckage below back into the words it is not.
_ENCLOSING = "()[]{}<>«»"
_AN_INITIAL = re.compile(r"[A-Za-z]\.$")


def _token_kind(token):
    """One token of a would-be heading: a word, a number, or debris.

    Debris is what an OCR pass leaves where a table printed a glyph, and telling it
    from a word is the whole of the veto. It is not simply "one character": MEASURED
    on page 297 of the acceptance book, Table 8.2's header row comes back as
    ``Day Night  I' J/ /  0``, where ``I'`` and ``J/`` are two characters each and a
    rule that only knew about single characters counted the ``I`` as the pronoun.
    What separates them from a real one-letter token is that nothing but a bracket
    was taken off: ``A`` and ``I`` stand alone, an initial keeps its stop.
    """
    core = token.strip(_STRIP_EDGES)
    inner = token.strip(_ENCLOSING)
    if core and _WORDISH.match(core) and len(core) >= 2:
        return "word"
    if core in ("A", "I") and inner == core:
        return "word"
    if _AN_INITIAL.match(inner):
        return "word"
    if core and _NUMERIC.match(core):
        return "number"
    return "debris"


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
        # A heading begins where a sentence begins. No line in the acceptance book
        # needs this rule -- the one that looked like it did, 'bonify Mercury.
        # Conversely, if' (p493), is refused by the last-word rule below -- so it is
        # a guard against a shape that book does not happen to print, not a measured
        # fix, and no test here can drive it on its own.
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

    Bold still needs a floor, because the same book sets its example-chart labels and
    its notes in bold at a genuinely smaller rung. The floor is the body size less
    the tolerance the ladder already uses for scanner wobble, and it is
    ``LADDER_TOL`` rather than a tighter number for the reason the ladder has a
    tolerance at all: the wobble runs both ways. MEASURED on book 567, the bold
    section heads come back between 10.8 and 11.5pt against an 11.3pt body -- one
    printed rung -- and a floor at the body size itself takes the ones the scanner
    rounded up and refuses the 38 it rounded down, among them 'Mystery Traditions',
    'The Moon - Selene' and 'Saturn / Kronos, the "Shining One" (Phainon)'.
    """
    if not style.body_size:
        return False
    size = line.size
    if line.bold:
        return size >= style.body_size * (1.0 - LADDER_TOL)
    return size >= style.body_size * HEAD_RATIO and style.on_the_ladder(size)


# ----------------------------------------------------------------------- the skeleton

def page_skeleton(raw, style, layer_trusted=True):
    """Classify one page's regions, recording a reason for every uncertain call."""
    skel = PageSkeleton(pno=raw.pno, width=raw.width, height=raw.height,
                        is_scan=raw.is_page_scan)

    if not raw.text_blocks:
        skel.reasons.append("no_text_layer" if raw.images else "empty_page")
        for img in raw.images:
            # A textless leaf may be a plate or may be blank paper; the build
            # proves ink before either is emitted as a figure.
            skel.regions.append(Region(kind="figure", bbox=img.bbox, image=img,
                                       needs_ink=True))
        return skel

    body_blocks, note_regions = _split_off_notes(raw, style, skel)

    top_y = min((ln.bbox[1] for blk in raw.text_blocks for ln in blk.lines),
                default=None)

    kept_blocks = []
    for blk in body_blocks:
        kept = []
        for ln in blk.lines:
            reason = _furniture_reason(ln, raw, style, top_y)
            if reason:
                skel.regions.append(Region(kind="furniture", lines=[ln], reason=reason,
                                           bbox=ln.bbox))
            else:
                kept.append(ln)
        if kept:
            kept_blocks.append((blk, kept))

    embedded = [img for img in raw.images if img.substantial and not img.full_page]

    # Artwork that no embedded image claims: on a scan it is ink inside the page
    # raster; on a born-digital page it is a cluster of vector paths. Either way
    # the region is cut out of the page render, and the lettering the text layer
    # read off it rides with it instead of reading as prose. The territory is
    # measured FROM the text layer's geometry, though -- a layer the census
    # called garbage does not describe where the ink is, and territory measured
    # from it crops whole prose regions as 'figures' (book 561's shape).
    if raw.is_page_scan and layer_trusted:
        candidates = _scan_figures(kept_blocks, raw, style)
    elif raw.drawings and not raw.is_page_scan:
        candidates = _vector_figures(raw)
    else:
        candidates = []
    artwork = []
    if candidates:
        kept_blocks, artwork = _absorb_figure_content(kept_blocks, candidates,
                                                      style)
        skel.regions.extend(artwork)

    layout = _column_layout(kept_blocks, embedded, candidates, raw)
    if layout is not None:
        skel.reasons.append("columns_reordered")
        for blk, kept in kept_blocks:
            for band, column, group in layout.groups(kept):
                _classify_body(group, blk, style, skel, band=band, column=column)
    else:
        if _looks_multi_column([blk for blk, _ in kept_blocks], raw):
            skel.reasons.append("multi_column")
        for blk, kept in kept_blocks:
            _classify_body(kept, blk, style, skel)

    for img in embedded:
        band, column = layout.place(img.bbox) if layout else (0, 0)
        skel.regions.append(Region(kind="figure", bbox=img.bbox, image=img,
                                   band=band, column=column))
    for candidate in candidates:
        if layout is not None:
            candidate.band, candidate.column = layout.place(candidate.bbox)
        skel.regions.append(candidate)

    skel.regions.extend(note_regions)
    skel.regions.sort(key=_region_order)
    return skel


def _region_order(region):
    return (0 if region.kind != "note" else 1, region.band, region.column,
            round(region.bbox[1], 1), round(region.bbox[0], 1))


def _lines_bbox(lines, fallback):
    """The box the given lines actually occupy.

    A region's box has to describe the region, not the text block it was cut out
    of. When a run-in head is split off the front of a block, the paragraph left
    behind still starts where the block does if it keeps the block's box -- it then
    ties with its own heading on the page's reading order and the tie is broken by
    left edge, which on a scan is jitter. MEASURED on book 567 page 121 (index 120):
    the paragraph won the tie and the section head came out *after* the section's
    first paragraph.
    """
    boxes = [ln.bbox for ln in lines if ln.bbox]
    if not boxes:
        return fallback
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


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


def _furniture_reason(line, raw, style, top_y=None):
    y0, y1 = line.bbox[1], line.bbox[3]
    in_head = y1 <= raw.height * HEADER_BAND
    in_foot = y0 >= raw.height * FOOTER_BAND
    text = line.stripped
    if not (in_head or in_foot):
        # A scan's margins can push the running head below the strict band
        # (book 570's index: head at 11% of the page height). The page's
        # topmost line is still furniture when it reads as one -- caps,
        # carrying its folio, set smaller than the body, and title-sized.
        # Anything less specific stays content: a wrong yes here drops a
        # real line from the book while the counter stays green.
        if top_y is None or y0 > top_y + line.size:
            return None
        if not text or len(text) > BAND_TEXT_MAX:
            return None
        if not style.body_size or line.size >= style.body_size * 0.98:
            return None
        if sum(ch.isalpha() for ch in text) < 6:
            return None
        if not _is_caps(text) or not _carries_folio(text):
            return None
        return "running_caps"
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


def _carries_folio(text):
    """A running head's one stable part is the folio at either edge."""
    tokens = text.split()
    return bool(tokens) and (FOLIO.match(tokens[0]) is not None
                             or FOLIO.match(tokens[-1]) is not None)


def _is_caps(text):
    return any(ch.isalpha() for ch in text) and text == text.upper()


def _classify_body(lines, blk, style, skel, band=0, column=0):
    """Split a block into headings and prose, honouring run-in sub-headings."""
    if len(lines) >= 2 and heading_ish(lines[0], style) \
            and not any(heading_ish(ln, style) for ln in lines[1:]):
        head = lines[0].stripped
        if acceptable_heading(head) and not continues_lowercase(lines[1]):
            skel.regions.append(Region(kind="heading", lines=[lines[0]],
                                       level=style.level_for(lines[0].size),
                                       reason="run_in", bbox=lines[0].bbox,
                                       band=band, column=column))
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
                                       bbox=_lines_bbox(lines, blk.bbox),
                                       band=band, column=column))
            return
        skel.reasons.append("large_type_not_a_heading")

    if not lines:
        return
    kind = "caption" if CAPTION_LINE.match(lines[0].stripped) else "body"
    skel.regions.append(Region(kind=kind, lines=list(lines),
                               bbox=_lines_bbox(lines, blk.bbox),
                               band=band, column=column))


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


# ------------------------------------------------------------------ column order

def _gutters(boxes, left, span, full=FULL_SPAN, max_crossings=0):
    """x positions of vertical whitespace channels wide enough to be column gutters.

    Read from LINE boxes, not block boxes: on a page whose columns share baselines
    MuPDF hands both columns back as one block of interleaved lines, which is the
    exact shape the readiness probe failed on. A line spanning most of the text
    measure bridges every gutter and votes for none of them -- it is a band of its
    own (a full-width heading, a plate), not evidence against the columns.

    A bin is empty unless some line covers it -- EXCEPT a bin covered by exactly
    one line at least BAND_MIN of the measure wide: a full-width section head
    is a band, and one band line must not veto the gutter it crosses (book
    570's index, where 'OPPOSITION' bridges the channel). Ordinary ragged
    short lines are not that, and they still block, which is what keeps a
    single-column page from being shredded (book 565 p158's shape).
    """
    bins = 240
    covering = [[] for _ in range(bins)]
    for index, box in enumerate(boxes):
        width = box[2] - box[0]
        if width >= full * span:
            continue
        a = int((box[0] - left) / span * (bins - 1))
        z = int((box[2] - left) / span * (bins - 1))
        for i in range(max(0, a), min(bins - 1, z) + 1):
            covering[i].append(index)
    occupied = bytearray(bins)
    for i, lines in enumerate(covering):
        if not lines:
            continue
        if len(lines) == 1 and (boxes[lines[0]][2] - boxes[lines[0]][0]) \
                >= BAND_MIN * span:
            continue
        occupied[i] = 1
    gaps, i = [], 0
    while i < bins:
        if occupied[i]:
            i += 1
            continue
        j = i
        while j < bins and not occupied[j]:
            j += 1
        if i > bins * 0.12 and j < bins * 0.88 and (j - i) >= bins * 0.03:
            centre = left + (i + j) / 2.0 / bins * span
            crossings = sum(1 for box in boxes
                            if (box[2] - box[0]) < full * span
                            and box[0] < centre - span * GUTTER_MIN / 2.0
                            and box[2] > centre + span * GUTTER_MIN / 2.0)
            if crossings <= max_crossings:
                gaps.append(centre)
        i = j
    return gaps


class _ColumnLayout(object):
    """The page's columns and bands, measured once and shared by every region.

    A *band* is a horizontal slice delimited by anything spanning the text
    measure; within a band the columns read top-to-bottom, left-to-right, and the
    spanning item itself sits between the band above and the band below. Regions
    carry (band, column) so the final page sort is the reading order rather than
    the (y, x) interleave that destroyed the probe.

    When NO text line spans the measure the page is a set of independent
    columns (a two-up spread): each column is a page of its own and reads to
    its end before the next begins. Bands then belong to their own column: a
    section head or a photo on one side of the spread does not interleave the
    other side's paragraph with its own continuation.
    """

    def __init__(self, left, right, bounds, spanning_ys, band_major=True):
        self.left = left
        self.right = right
        self.bounds = bounds            # gutter bounds: ncols + 1 edges
        self.spanning_ys = spanning_ys  # centre-y of each spanning item, sorted
        self.span = right - left
        self.band_major = band_major

    @property
    def ncols(self):
        return len(self.bounds) - 1

    def column_of(self, bbox):
        centre = (bbox[0] + bbox[2]) / 2.0
        for k in range(self.ncols):
            if self.bounds[k] <= centre < self.bounds[k + 1]:
                return k
        return self.ncols - 1

    def column_width(self, column):
        return self.bounds[column + 1] - self.bounds[column]

    def band_of(self, bbox):
        centre = (bbox[1] + bbox[3]) / 2.0
        return sum(1 for y in self.spanning_ys if y < centre)

    def _local_band_of(self, bbox, column):
        centre = (bbox[1] + bbox[3]) / 2.0
        return sum(1 for y in self.spanning_ys
                   if y < centre and self.column_of(
                       (self.left, 0, self.right, 1)) == column)

    def place(self, bbox):
        """(band, column) for a region-sized box: spanning boxes close their band."""
        if self.band_major:
            if (bbox[2] - bbox[0]) >= FULL_SPAN * self.span:
                return self.band_of(bbox), self.ncols
            return self.band_of(bbox), self.column_of(bbox)
        return 0, self.column_of(bbox)

    def groups(self, lines):
        """A block's lines split into (band, column, lines) runs, in reading order.

        The block that arrives holding both columns' same-baseline lines leaves
        here as one run per column, each still in its own y order -- which is what
        makes the downstream classifier safe to join the lines of a run at all.
        """
        runs = {}
        for ln in lines:
            key = self.place(ln.bbox)
            runs.setdefault(key, []).append(ln)
        return [(band, column, group)
                for (band, column), group in sorted(runs.items())]


def _column_layout(kept_blocks, embedded, candidates, raw):
    """The page's column layout, or None when the page does not prove one.

    Fail closed on purpose: a page that only *might* be columns keeps the
    status-quo (y, x) order and its ``multi_column`` routing reason, because the
    cost of reading a table or a ragged page column-major is higher than the cost
    of asking. What is proved gets fixed; what is not gets looked at.
    """
    line_items = [(ln.bbox, ln.stripped) for _, kept in kept_blocks for ln in kept]
    items = [box for box, _ in line_items]
    items.extend(img.bbox for img in embedded)
    items.extend(candidate.bbox for candidate in candidates)
    if len(items) < 6:
        return None
    left = min(box[0] for box in items)
    right = max(box[2] for box in items)
    span = right - left
    if span < raw.width * 0.4:
        return None

    # Gutters are read from line boxes alone. The page's own photograph (a
    # facsimile behind the text of a two-up spread) spans the gutter and would
    # veto columns that are plainly there, leaving the two logical pages
    # interleaved (book 566 p20's shape). A real floating figure still votes:
    # it blocks the gutter it crosses, because a column boundary drawn through
    # a plate is no boundary at all.
    line_boxes = [box for box, _ in line_items]
    gutter_items = list(line_boxes)
    for img in embedded:
        if any(img.bbox[0] < box[2] and img.bbox[2] > box[0]
               and img.bbox[1] < box[3] and img.bbox[3] > box[1]
               for box in line_boxes):
            continue
        gutter_items.append(img.bbox)
    gutters = _gutters(gutter_items, left, span,
                       max_crossings=max(1, len(line_items) // 10))
    if not gutters:
        return None
    if raw.drawings >= RULED_MIN_PATHS and not candidates:
        # Ruled lines across the gutter: a grid, not prose columns. (A candidate
        # has already claimed the drawing territory as artwork, so prose left
        # beside it may still be ordered.)
        return None

    spanning = [box for box in items if (box[2] - box[0]) >= FULL_SPAN * span]
    columnar = [(box, text) for box, text in line_items
                if (box[2] - box[0]) < FULL_SPAN * span]
    if len(spanning) >= len(columnar):
        # Most lines fill the measure: a single-column page whose ragged short
        # lines opened fake gutters between their right edges. MEASURED on book
        # 565 page 158: an ordinary page shredded into 33 'bands' and three
        # 'columns' of one-line paragraphs. Real columns almost never span the
        # measure -- that is what makes them columns.
        return None

    # A full-width TEXT line makes bands meaningful (headings between column
    # bands). When nothing textual spans the measure the page is a set of
    # independent columns -- a two-up spread -- and each one reads to its end
    # before the next begins. A photo between the pages is paper, not a band.
    band_major = any((box[2] - box[0]) >= FULL_SPAN * span for box, _ in line_items)
    bounds = [left - 1.0] + gutters + [right + 1.0]
    layout = _ColumnLayout(
        left, right, bounds,
        sorted((box[1] + box[3]) / 2.0 for box in spanning),
        band_major=band_major)

    counts = [0] * layout.ncols
    fills = []
    short = [0] * layout.ncols
    for box, text in columnar:
        column = layout.column_of(box)
        counts[column] += 1
        width = bounds[column + 1] - bounds[column]
        if width > 0:
            fills.append((box[2] - box[0]) / width)
        if len(text.split()) <= 2 or len(text.replace(" ", "")) <= 12:
            # One- and two-word lines, and letter-spaced display words that only
            # pretend to be more ('q u a l it ie s'): both are cell content, not
            # flowing prose.
            short[column] += 1
    if sum(1 for count in counts if count >= COLUMN_MIN_LINES) < 2:
        return None
    for column, count in enumerate(counts):
        if count >= COLUMN_MIN_LINES and short[column] * 2 >= count:
            # A column of one- and two-word lines is a table's label column (or a
            # grid of cells), not a column of prose: reading it column-major
            # severs every row it prints. MEASURED on book 569's zodiacal tables
            # ('Characteristics' beside 'Northern - Commanding - ...').
            return None
    fills.sort()
    if fills and fills[len(fills) // 2] < COLUMN_FILL_MIN:
        return None
    if not _column_sequence_evidence(columnar, layout):
        # A mirror table of paired rows ('GEMINI looks at LEO' beside 'l e o
        # perceives g e m in i', book 569 p547) proves two clean columns of
        # fragments with no sequence inside either. Column-major prints every
        # left cell away from its right-hand pair, and no heuristic gets to
        # guess the table into unrelated lists: without sequence evidence the
        # rows stay as they print, pairs together.
        return None
    return layout


_SENTENCE_END = re.compile(r"[.!?;:\"”’)\]]\s*$")
_LIST_OPENER = re.compile(r"^\s*(?:\d|[•\-*–—])")
_YEAR = re.compile(r"\d{4}")


def _column_sequence_evidence(columnar, layout):
    """True when the columns read as independent sequences, not mirror rows.

    In order: a labelled sequence (numbers, bullets, sentence punctuation, or
    years in most lines, or an ascending numbered run down a column) is proof
    on its own. Without one, a page whose rows are paired fragments in both
    columns at shared baselines is a mirror table -- reading it column-major
    severs every pair, which is exactly the failure to avoid. Real prose is
    neither fragmented nor paired: it flows, and flow is the third proof.
    """
    labeled = sum(1 for _, text in columnar
                  if _LIST_OPENER.match(text) or _SENTENCE_END.search(text)
                  or _YEAR.search(text))
    if labeled * 5 >= len(columnar) * 3:
        return True
    if _has_number_sequence(columnar, layout):
        return True
    if _mirror_fragment_rows(columnar, layout):
        return False
    by_column = {}
    for box, text in columnar:
        by_column.setdefault(layout.column_of(box), []).append((box, text))
    for lines in by_column.values():
        lines.sort(key=lambda item: (item[0][1], item[0][0]))
        for (_, prev), (__, nxt) in zip(lines, lines[1:]):
            last = prev.rstrip(".,;:!?\"”’").rsplit(" ", 1)[-1] if prev else ""
            if prev and nxt and not _SENTENCE_END.search(prev) \
                    and nxt[:1].islower() \
                    and len(last) >= 2 and last.islower():
                return True
    return False


def _has_number_sequence(columnar, layout):
    """A column of numbered items ascending down the page: a sequence of its
    own (a GOOD/BAD list's 1..13, a timeline's ascending years)."""
    by_column = {}
    for box, text in columnar:
        by_column.setdefault(layout.column_of(box), []).append((box, text))
    for lines in by_column.values():
        lines.sort(key=lambda item: (item[0][1], item[0][0]))
        numbers = []
        for _, text in lines:
            match = re.match(r"\s*(\d{1,4})\b", text)
            if match:
                numbers.append(int(match.group(1)))
        ascending = sum(1 for a, b in zip(numbers, numbers[1:]) if b > a)
        if len(numbers) >= 3 and ascending * 2 >= len(numbers) - 1:
            return True
    return False


def _mirror_fragment_rows(columnar, layout):
    """Rows of short fragments printed in both columns at shared baselines.

    The mirror table ('GEMINI looks at LEO' beside 'l e o perceives g e m in
    i') is nothing but these; prose columns at the same density are not
    fragments (they are full sentences wrapping, well past 30 characters), and
    a numbered list has already been proved a sequence before this is asked.
    """
    rows = {}
    for box, text in columnar:
        key = round((box[1] + box[3]) / 2.0)
        rows.setdefault(key, []).append((box, text))
    paired = fragments = 0
    for key in sorted(rows):
        cols = {layout.column_of(box) for box, _ in rows[key]}
        if len(cols) < 2:
            continue
        paired += 1
        if all(len(text.replace(" ", "")) <= 30 for _, text in rows[key]):
            fragments += 1
    return paired >= 4 and fragments * 2 >= paired


# ----------------------------------------------------------- figures by geometry

def _chart_lettering(line, style):
    """True for the lettering an OCR layer reads off a diagram on a scanned page.

    Set far larger than the body: on a scan the chart's captions and glyphs come
    back two to four times the prose size, which no line of this book's prose
    ever reaches (its top heading rung measures below the cut). Size alone may
    speak, because the one thing a scan measures reliably is how big the ink is
    -- a body-sized date like ``November 2016`` failing the junk veto is a line
    of the book, not lettering, and must never be absorbed out of it.
    """
    return bool(style.body_size) and line.size >= style.body_size * CHART_LABEL_RATIO


def _inside(bbox, rect, share=FIG_LINE_OVERLAP):
    """True when ``bbox`` sits at least ``share`` inside ``rect``."""
    x0 = max(bbox[0], rect[0])
    y0 = max(bbox[1], rect[1])
    x1 = min(bbox[2], rect[2])
    y1 = min(bbox[3], rect[3])
    if x1 <= x0 or y1 <= y0:
        return False
    area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
    return area > 0 and (x1 - x0) * (y1 - y0) / area >= share


def _prose_rows(kept_blocks, raw, style):
    """The page's prose, merged into horizontal bands of (top, bottom, [line boxes]).

    Chart lettering and caption lines are not prose: the first lives inside the
    figure and must not slice its territory into strips, the second rides with
    the figure it names. What remains is the text a figure cannot overlap, which
    is what the figure territory is measured from.
    """
    top, bottom = raw.height * HEADER_BAND, raw.height * FOOTER_BAND
    spans = []
    for _, kept in kept_blocks:
        for ln in kept:
            if _chart_lettering(ln, style) or CAPTION_LINE.match(ln.stripped):
                continue
            y0, y1 = max(ln.bbox[1], top), min(ln.bbox[3], bottom)
            if y1 > y0:
                spans.append([y0, y1, ln.bbox])
    spans.sort()
    rows = []
    for y0, y1, bbox in spans:
        if rows and y0 <= rows[-1][1] + 2:
            rows[-1][1] = max(rows[-1][1], y1)
            rows[-1][2].append(bbox)
        else:
            rows.append([y0, y1, [bbox]])
    return rows, top, bottom


def _scan_figures(kept_blocks, raw, style):
    """Figure territory inside a full-page scan, from where the prose is not.

    A full-page scan carries all of its art inside the page raster, and the old
    branch threw the raster away whenever an OCR layer existed -- which is how
    book 567 lost every chart printed between its paragraphs. The text layer
    still says where the text is not: a tall full-width band with no prose in it,
    or a wide empty channel beside a prose column, is figure territory. What is
    found is cropped from the source render at build time, never redrawn.
    """
    rows, top, bottom = _prose_rows(kept_blocks, raw, style)
    if not rows:
        return []
    lines = [ln for _, kept in kept_blocks for ln in kept]
    left = min(ln.bbox[0] for ln in lines)
    right = max(ln.bbox[2] for ln in lines)
    span = right - left
    if span <= 1:
        return []

    candidates = []

    def add(x0, y0, x1, y1, why, pad_x0=4.0, pad_x1=4.0):
        if y1 - y0 < raw.height * SCAN_GAP or x1 - x0 < span * 0.2:
            return
        # The pad breathes into empty territory only: padding across the prose's
        # own edge pulls a sliver of every line into the crop.
        candidates.append(Region(
            kind="figure", reason=why, needs_ink=True,
            bbox=(max(0.0, x0 - pad_x0), max(0.0, y0 - 2.0),
                  min(raw.width, x1 + pad_x1), min(raw.height, y1 + 2.0))))

    # Full-width bands. A gap BETWEEN two prose rows is territory proved on both
    # sides. A gap at the edge of the page is weaker evidence -- the top and
    # bottom margins of a scan are blank paper with enough texture to fake ink --
    # so an edge gap is territory only when the OCR layer read lettering off the
    # figure inside it, and a LONE big line does not count: one piece of display
    # type over white space is a chapter opening (book 567 p93's 'CHAPTER 4'),
    # not a chart. The sect chart's sparse glyphs fill the gap with a dozen.
    first, last = rows[0], rows[-1]
    edge_lettering = [ln for ln in lines if _chart_lettering(ln, style)]
    if first[0] - top > raw.height * SCAN_GAP and sum(
            1 for ln in edge_lettering
            if ln.bbox[1] >= top - 2 and ln.bbox[3] <= first[0] + 2) >= 2:
        add(left, top, right, first[0], "scan_figure_band")
    for row, following in zip(rows, rows[1:]):
        gap = following[0] - row[1]
        if gap > raw.height * SCAN_GAP:
            add(left, row[1], right, following[0], "scan_figure_band")
    if bottom - last[1] > raw.height * SCAN_GAP and sum(
            1 for ln in edge_lettering
            if ln.bbox[1] >= last[1] - 2 and ln.bbox[3] <= bottom + 2) >= 2:
        add(left, last[1], right, bottom, "scan_figure_band")

    # Side channels: a tall run of prose lines that all leave the same wide
    # channel empty on one side (a natal wheel beside its reading). One line
    # running full width does not disprove the channel -- the prose resumes under
    # the figure, which is exactly how a floated plate is set -- so the channel is
    # measured over the longest contiguous run of lines that leave it open, and
    # the territory is then grown against the prose around it.
    for row_top, row_bottom, boxes in rows:
        if row_bottom - row_top < raw.height * SCAN_GAP:
            continue
        ordered = sorted(boxes, key=lambda box: (box[1], box[0]))
        for side in ("left", "right"):
            run = []
            for box in ordered + [None]:
                open_side = box is not None and (
                    (box[0] - left >= span * SIDE_CHANNEL_MIN) if side == "left"
                    else (right - box[2] >= span * SIDE_CHANNEL_MIN))
                if open_side:
                    run.append(box)
                    continue
                if run:
                    _side_territory(run, side, rows, left, right, raw, span, add)
                    run = []
    return candidates


def _side_territory(run, side, rows, left, right, raw, span, add):
    """One side-channel seed, grown against the prose, emitted if it is tall.

    The seed is the run's own rectangle; its top and bottom then grow past
    anything that does not reach into the channel (a section head set beside the
    wheel is not the wheel), stopping where prose actually crosses the channel.
    """
    if run[-1][3] - run[0][1] < raw.height * SCAN_GAP:
        return
    if side == "left":
        x0, x1 = left, min(box[0] for box in run)
    else:
        x0, x1 = max(box[2] for box in run), right
    if x1 - x0 < span * SIDE_CHANNEL_MIN * 0.8:
        return

    def crosses(box):
        return box[2] > x0 and box[0] < x1

    top = raw.height * HEADER_BAND
    bottom = raw.height * FOOTER_BAND
    for row_top, row_bottom, boxes in rows:
        for box in boxes:
            if not crosses(box):
                continue
            if box[1] < run[0][1]:
                # Above the run -- including a line straddling the run's first
                # line, which would otherwise have its middle cropped through.
                top = max(top, box[3])
            if box[3] > run[-1][3]:
                bottom = min(bottom, box[1])
    if side == "left":
        add(x0, top + 2.0, x1, bottom - 2.0, "scan_figure_side",
            pad_x0=4.0, pad_x1=0.0)
    else:
        add(x0, top + 2.0, x1, bottom - 2.0, "scan_figure_side",
            pad_x0=0.0, pad_x1=4.0)


def _vector_figures(raw):
    """Cluster a born-digital page's vector paths into diagram regions.

    Charts and geometric figures are drawn, not embedded: extracting only embedded
    images loses them whole. The guards are the v4 tool's, proven on the corpus:
    enough paths to be a drawing rather than a rule, not so many the page is a
    dense table or a traced scan, and a cluster of believable area.
    """
    rects = [r for r in raw.drawing_rects
             if (r[2] - r[0]) > 4 and (r[3] - r[1]) > 4]
    if len(rects) < VEC_MIN_PATHS:
        return []
    clusters = []
    for rect in rects:
        placed = False
        for cluster in clusters:
            box = cluster[0]
            near = abs(box[0] - rect[0]) < 40 and abs(box[1] - rect[1]) < 40
            overlap = not (rect[2] < box[0] or rect[0] > box[2]
                           or rect[3] < box[1] or rect[1] > box[3])
            if overlap or near:
                cluster[0] = (min(box[0], rect[0]), min(box[1], rect[1]),
                              max(box[2], rect[2]), max(box[3], rect[3]))
                cluster[1] += 1
                placed = True
                break
        if not placed:
            clusters.append([tuple(rect), 1])
    parea = raw.width * raw.height or 1.0
    out = []
    for box, count in clusters:
        area = (box[2] - box[0]) * (box[3] - box[1]) / parea
        if count >= VEC_MIN_PATHS and VEC_MIN_AREA <= area <= VEC_MAX_AREA:
            out.append(Region(kind="figure", reason="vector_figure",
                              bbox=box))
    return out


def _absorb_figure_content(kept_blocks, candidates, style):
    """Move lettering and captions inside figure territory out of the prose.

    The text layer reads a chart's labels as words; left in place they print as
    prose next to the crop that already shows them -- duplicate chart junk in the
    reading flow. Captions printed inside the territory (a chart title under a
    wheel) ride with their figure instead, so the words are kept once, as text.
    Only lettering is absorbed: a prose-sized line that strays into the territory
    stays prose, because a line of the book that is visible twice beats one that
    is visible nowhere -- an absorbed line whose blank territory is dropped at
    build time would be gone from the reader's book.
    """
    for candidate in candidates:
        captions = []
        kept = []
        for blk, lines in kept_blocks:
            inside, outside = [], []
            for ln in lines:
                (inside if _inside(ln.bbox, candidate.bbox) else outside).append(ln)
            for ln in inside:
                if CAPTION_LINE.match(ln.stripped):
                    captions.append(ln)
                elif _chart_lettering(ln, style):
                    candidate.lines.append(ln)
                else:
                    outside.append(ln)
            if outside:
                kept.append((blk, outside))
        kept_blocks = kept
        candidate.caption_lines = captions
        if captions:
            candidate.bbox = _crop_around_caption(candidate.bbox, captions)

    artwork = []
    for candidate in candidates:
        if candidate.lines:
            artwork.append(Region(kind="artwork", lines=candidate.lines,
                                  bbox=_lines_bbox(candidate.lines, candidate.bbox),
                                  reason="figure_lettering",
                                  band=candidate.band, column=candidate.column))
        candidate.lines = []
    return kept_blocks, artwork


def _crop_around_caption(bbox, captions):
    """Shrink a figure's crop so its absorbed caption prints as text, not twice.

    A caption at the territory's edge (under the wheel, over the chart) leaves a
    single clean piece. One printed in the diagram's middle does not, and there
    the whole territory is kept: a caption visible in the crop AND set as text is
    a small cosmetic price, and losing it would be a content one.
    """
    top = min(ln.bbox[1] for ln in captions)
    bottom = max(ln.bbox[3] for ln in captions)
    height = bbox[3] - bbox[1]
    above = (bbox[0], bbox[1], bbox[2], top - 1.0)
    below = (bbox[0], bottom + 1.0, bbox[2], bbox[3])
    pieces = sorted((above, below), key=lambda piece: piece[3] - piece[1],
                    reverse=True)
    if pieces[0][3] - pieces[0][1] >= height * 0.8:
        return pieces[0]
    return bbox


def is_marker_span(span, line_size):
    """A digit run small enough to be an inline note marker."""
    text = span.text.strip()
    if not re.fullmatch(r"\d{1,3}", text):
        return False
    return bool(line_size) and span.size <= MARKER_SIZE_MAX * line_size
