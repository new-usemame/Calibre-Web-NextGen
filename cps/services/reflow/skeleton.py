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
from statistics import median
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
#: The raster counterpart of the ruled veto: a scan's ruling lives in the page
#: raster, invisible to the path count. At least this many measure-spanning
#: rule strokes say the page is a ruled grid all the same -- one deck rule
#: under a heading pairs nothing, and a ruled register rules its rows.
RULE_SCAN_MIN = 3

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
    #: A note identity or caption transcription depends on damaged extraction.
    #: Keep its reading, but visibly qualify it instead of asserting certainty.
    uncertain: bool = False

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

def page_skeleton(raw, style, layer_trusted=True, pixel_probe=None):
    """Classify one page's regions, recording a reason for every uncertain call.

    ``pixel_probe`` answers the questions geometry cannot (an
    ``extract.ScanPixelProbe``): whether an edge gap's pixels hold marks at
    all, where a band's truly empty columns split two side-by-side charts, and
    which horizontal rules a scan's raster still carries. Without one, edge
    gaps stay proposals for the build's ink proof, bands are never split, and
    a raster's ruling cannot veto a column reading."""
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

    embedded = [img for img in raw.images if img.substantial and not img.full_page
                and not getattr(img,'page_background',False)]

    # A full-bleed cover or plate is the page itself with a little text over
    # it: one figure, nothing left to slice. Evidence is the pixels and the
    # prose together, so a text page with a scan behind it can never qualify.
    cover = _full_bleed_plate(raw, kept_blocks, pixel_probe)
    if cover is not None:
        skel.regions.append(Region(kind="figure", bbox=cover.bbox, image=cover,
                                   needs_ink=True))
        # Uncertain OCR over artwork is retained in the intact plate, not
        # promoted to reading prose. Credible titles and real notes still read.
        retained = []
        for blk, lines in kept_blocks:
            credible = [ln for ln in lines if _credible_line(ln)]
            uncertain = [ln for ln in lines if not _credible_line(ln)]
            if credible:
                retained.append((blk, credible))
            if uncertain:
                skel.regions.append(Region(kind="artwork", lines=uncertain,
                    bbox=_lines_bbox(uncertain, blk.bbox), reason="plate_lettering"))
        kept_blocks = retained

    # Artwork that no embedded image claims: on a scan it is ink inside the page
    # raster; on a born-digital page it is a cluster of vector paths. Either way
    # the region is cut out of the page render, and the lettering the text layer
    # read off it rides with it instead of reading as prose. The territory is
    # measured FROM the text layer's geometry, though -- a layer the census
    # called garbage does not describe where the ink is, and territory measured
    # from it crops whole prose regions as 'figures' (book 561's shape).
    if raw.is_page_scan and layer_trusted and cover is None:
        candidates = _scan_figures(kept_blocks, raw, style, pixel_probe)
    elif raw.drawings and not raw.is_page_scan:
        candidates = _vector_figures(raw)
    else:
        candidates = []
    artwork = []
    if candidates:
        kept_blocks, artwork = _absorb_figure_content(kept_blocks, candidates,
                                                      style)
        skel.regions.extend(artwork)

    layout = _column_layout(kept_blocks, embedded, candidates, raw,
                            pixel_probe=pixel_probe)
    if isinstance(layout, _RowTable):
        # The rows are measured, and the page keeps the printed (y, x) order --
        # but each row's cells are emitted as one region, because the paragraph
        # join across a row boundary fuses whole rows into each other.
        skel.reasons.append("mirror_table")
        kept_blocks = _emit_table_rows(layout, kept_blocks, skel)
        layout = None
    if not raw.is_page_scan:
        kept_blocks = _regroup_native_titles(kept_blocks, style)
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


def _emit_table_rows(table, kept_blocks, skel):
    """Each measured fragment row as one body region, in x order within the row.

    The blocks the cells arrived in are irrelevant here -- MuPDF and Tesseract
    both scatter a row's cells across blocks. What is left of the kept blocks
    once the table's lines are lifted out classifies exactly as before.
    """
    wanted = {box for group in table.rows for box, _ in group}
    by_box = {}
    rest = []
    for blk, kept in kept_blocks:
        remainder = []
        for ln in kept:
            if ln.bbox in wanted:
                by_box[ln.bbox] = ln
            else:
                remainder.append(ln)
        if remainder:
            rest.append((blk, remainder))
    for group in table.rows:
        lines = sorted((by_box[box] for box, _ in group if box in by_box),
                       key=lambda ln: (ln.bbox[0], ln.bbox[1]))
        if lines:
            skel.regions.append(Region(kind="body", lines=lines,
                                       reason="table_row",
                                       bbox=_lines_bbox(lines, lines[0].bbox)))
    return rest


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


#: A full-bleed cover or plate holds at most this many prose lines; more and
#: the page is text with a picture behind it, which is the background's job.
PLATE_PROSE_MAX = 8
#: ... and at least this share of its blocks inked with the prose masked: a
#: cover is art across the whole page (1.0 measured on book 567 index 0), an
#: end-of-chapter page or a title page with two art bands is mostly paper
#: (0.0 and 0.22 measured).
PLATE_INK_COVER = 0.4


def _credible_line(line):
    return (sum(len(sp.text.split()) for sp in line.spans if not sp.uncertain)
            > sum(len(sp.text.split()) for sp in line.spans) / 2)


def _full_bleed_plate(raw, kept_blocks, pixel_probe):
    """The page's own full-page image when the page IS the picture: a cover or
    plate with a little text over it, inked across the whole page.

    A text page with a scan behind it is not this: the full-page raster is
    excluded from embedded figures on purpose, and it only becomes the figure
    when the pixels and the prose both say the page is its artwork. Measured
    on book 567's cover: full-bleed art under a few title lines -- without
    the rule the cover prints as fragments and ships as a strip.
    """
    if pixel_probe is None or not raw.is_page_scan:
        return None
    images = [img for img in raw.images if img.full_page]
    if not images:
        return None
    # Low-confidence OCR fragments over a design are not evidence of prose.
    # Native lines count normally; the pixel gate still proves full-page art.
    credible = sum(_credible_line(ln) for _, kept in kept_blocks for ln in kept)
    if credible > PLATE_PROSE_MAX:
        return None
    rect = (0.0, 0.0, raw.width, raw.height)
    try:
        if pixel_probe.coverage(rect) < PLATE_INK_COVER:
            return None
    except Exception:
        return None
    return images[0]


def _split_off_notes(raw, style, skel):
    """Separate the footnote zone from the body, keeping multi-block notes together.

    The zone's start is chosen, not just found: a number read out of glyphs is
    the weakest evidence there is (``24° Sagittarius.`` decodes to note 240),
    so a glyph-read opening stands only when every later opening ascends from
    it. A later, smaller note proves the "opening" was prose -- book 569 page
    355, where two degree readings opened a zone that swallowed 24 lines of
    body and the real note 10 under them. Raised digit spans and merged digits
    carry their own evidence and do not ask for the corroboration.
    """
    body, notes = [], []

    def keep_adjacent_captions(block, anchor):
        # A page-bottom note does not own a printed caption in another column.
        captions = [ln for ln in block.lines if _squashed_caption(ln.stripped)
                    and (ln.bbox[0] >= anchor[2] or ln.bbox[2] <= anchor[0])]
        if not captions:
            return block
        body.append(extract.Block(number=block.number, lines=captions,
                                  bbox=_lines_bbox(captions, block.bbox)))
        kept = [ln for ln in block.lines if ln not in captions]
        return extract.Block(number=block.number, lines=kept,
                             bbox=_lines_bbox(kept, block.bbox)) if kept else None

    zone_top = raw.height * FN_ZONE_NUMBERED
    eligible = []
    for blk in raw.text_blocks:
        if blk.bbox[1] < zone_top or not style.body_size:
            body.append(blk)
        elif blk.size > style.body_size * FN_SIZE_RATIO:
            body.append(blk)
        else:
            eligible.append(blk)

    openings = []
    for blk in eligible:
        per_block = []
        for pos, ln in enumerate(blk.lines):
            number = _note_number(
                ln, blk.size, opening=(pos == 0), after=None,
                following=blk.lines[pos + 1] if pos + 1 < len(blk.lines) else None)
            if number is not None:
                per_block.append((pos, number, _opening_strength(ln, blk.size)))
        openings.append(per_block)
    flat = [(bi, pos, number, strength)
            for bi, per_block in enumerate(openings)
            for pos, number, strength in per_block]
    start = None
    for index, (bi, pos, number, strength) in enumerate(flat):
        if strength == "glyph" and any(n <= number for _, _, n, _ in flat[index + 1:]):
            continue
        start = (bi, pos)
        break

    if start is None:
        body.extend(eligible)
    else:
        bi, pos = start
        body.extend(eligible[:bi])
        first = eligible[bi]
        if pos:
            # The zone opens mid-block: the lines above the first number are
            # body the false opening would have swallowed with it.
            body.append(extract.Block(
                number=first.number,
                bbox=_lines_bbox(first.lines[:pos], first.bbox),
                lines=first.lines[:pos]))
            first = extract.Block(
                number=first.number,
                bbox=_lines_bbox(first.lines[pos:], first.bbox),
                lines=first.lines[pos:])
        first = keep_adjacent_captions(first, first.lines[0].bbox)
        notes.extend(_notes_in_block(first, notes))
        for blk in eligible[bi + 1:]:
            blk = keep_adjacent_captions(blk, notes[-1].bbox)
            if blk is not None:
                notes.extend(_notes_in_block(blk, notes))

    if notes:
        numbers = [n.number for n in notes if n.number is not None]
        if len(numbers) != len(set(numbers)):
            skel.reasons.append("duplicate_note_numbers")
    return body, notes


def _opening_strength(line, block_size):
    """How a zone-opening number was read: a raised digit span of its own, digits
    merged into the note's text or set on a line of their own, or glyphs that
    only spell digits -- the reading that asks the rest of the zone to
    corroborate it. Mirrors ``_note_number``: a full-size digit span is not a
    raised number, whatever it spells."""
    spans = [sp for sp in line.spans if sp.text.strip()]
    if spans and re.fullmatch(r"\d{1,3}", spans[0].text.strip()):
        if not (block_size and spans[0].size > MARGIN_SIZE * block_size):
            return "raised"
    if _MERGED_NOTE_NUMBER.match(line.stripped) \
            or re.fullmatch(r"\d{1,3}", line.stripped):
        return "merged"
    return "glyph"


def _notes_in_block(blk, existing):
    """One block can hold several notes; a new one opens with its own small number.

    Lines before the first number continue the note that ran over from the block
    above — a long note printed across two blocks, not a new unnumbered one.
    """
    out = []
    current = None
    seen = [r.number for r in existing if r.number is not None]
    for position, ln in enumerate(blk.lines):
        number = _note_number(
            ln, blk.size, opening=(position == 0),
            after=seen[-1] if seen else None,
            following=blk.lines[position + 1] if position + 1 < len(blk.lines)
            else None)
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


def _note_number(line, block_size, opening=False, after=None, following=None):
    """The note's own number when the line opens one.

    Three printed shapes reach us. The raised number survives as its own small
    span — the strong signal, and the only one accepted mid-block. Or the scanner
    merged it into the first text span at full size, which MEASURED costs 138
    footnotes on 70 of the acceptance book's 698 pages; that shape is only read
    at the first line of a block already inside the footnote zone, where a
    number can only be a number. Or the number sits on a line of its own at the
    note's own size, the note's sentence on the line under it -- book 569's
    '10' above 'I b N Sa h l , The Fifty Judgments 6.'; that shape asks for the
    sentence, so a bare folio cannot open a note.
    """
    spans = [sp for sp in line.spans if sp.text.strip()]
    if not spans:
        return None
    first = spans[0]
    text = first.text.strip()
    if re.fullmatch(r"\d{1,3}", text):
        if block_size and first.size > MARGIN_SIZE * block_size:
            pass
        else:
            return int(text)
    if following is not None and re.fullmatch(r"\d{1,3}", line.stripped):
        head = following.stripped
        if (head[:1].isupper() or head[:1] in "“‘\"'") and len(head.split()) >= 2:
            value = int(line.stripped)
            if value > 0 and (after is None or value > after):
                return value
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
            # The weakest reading asks for one more witness: a citation opens
            # with a name and a comma ("9° Tarrant, ..."), not with a finished
            # sentence. "24° Sagittarius. Once ..." is a degree in running
            # prose -- glyph-decoded to 240 it would open a zone that swallows
            # the body under it (book 569 page 355).
            rest = line.stripped[glyphed.end(1):].lstrip()
            if _PROSE_SENTENCE.match(rest):
                return None
            if after is None:
                if opening:
                    return value
            elif value > after:
                return value
    return None


#: What follows a glyph-read number when the line is prose, not a citation: the
#: first word after it ends with sentence punctuation ("Sagittarius. Once ...").
_PROSE_SENTENCE = re.compile(r"[A-Z\u201c\u2018\"']\S*[.!?](?:\s|$)")


def _furniture_reason(line, raw, style, top_y=None):
    y0, y1 = line.bbox[1], line.bbox[3]
    in_head = y1 <= raw.height * HEADER_BAND
    in_foot = y0 >= raw.height * FOOTER_BAND
    text = line.stripped
    if not (in_head or in_foot):
        # A scan's margins can push the running head below the strict band
        # (book 570's index: head at 11% of the page height). The page's
        # topmost line is still furniture when it reads as one -- caps,
        # carrying its folio, set smaller than the body, and title-sized --
        # or when it is nothing but the folio (book 569's '516' and '324').
        # Anything less specific stays content: a wrong yes here drops a
        # real line from the book while the counter stays green.
        if top_y is None or y0 > top_y + line.size:
            return None
        if not text or len(text) > BAND_TEXT_MAX:
            return None
        if style.body_size and line.size > style.body_size * 1.02:
            return None
        if FOLIO.match(text):
            return "folio"
        if line.size >= style.body_size * 0.98:
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


def _regroup_native_titles(kept_blocks, style):
    """Lift complete display runs from heterogeneous native extraction blocks."""
    from .heading_units import native_units
    flat=[ln for _,lines in kept_blocks for ln in lines]
    groups=native_units([ln.to_dict() for ln in flat])
    titles=[[flat[i] for i in group] for group in groups if len(group)>1
            and all(heading_ish(flat[i],style) for i in group)]
    owned={id(ln) for group in titles for ln in group}
    out=[]
    for blk,lines in kept_blocks:
        rest=[ln for ln in lines if id(ln) not in owned]
        if rest:out.append((blk,rest))
    # A display initial can share the removed title's extraction block.
    # Bind it to the first adjacent body line before raised markers can move
    # that paragraph ahead of the initial in source-coordinate sorting.
    for blk,lines in list(out):
        if len(lines)!=1:continue
        initial=lines[0]
        if not (len(initial.stripped)==1 and initial.stripped.isalpha()
                and initial.size>=2*style.body_size):continue
        neighbors=[]
        for other,body in out:
            if other is blk:continue
            regular=[ln for ln in body if abs(ln.size/style.body_size-1)<=LADDER_TOL]
            if not regular:continue
            first=min(regular,key=lambda ln:ln.bbox[1])
            if (0<=first.bbox[0]-initial.bbox[2]<=style.body_size and
                    abs(first.bbox[1]-initial.bbox[1])<=style.body_size):
                neighbors.append((other,body))
        if len(neighbors)!=1:continue
        other,body=neighbors[0]
        out.remove((blk,lines));out.remove((other,body))
        combined=lines+body;box=_lines_bbox(combined,other.bbox)
        out.append((extract.Block(other.number,box,combined),combined))
    for lines in titles:
        box=_lines_bbox(lines,lines[0].bbox)
        out.append((extract.Block(-1,box,lines),lines))
    return out


def _classify_body(lines, blk, style, skel, band=0, column=0):
    """Split a block into headings and prose, honouring run-in sub-headings."""
    if lines and CAPTION_LINE.match(lines[0].stripped):
        skel.regions.append(Region(kind="caption",lines=list(lines),
            bbox=_lines_bbox(lines,blk.bbox),band=band,column=column))
        return
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


def _column_layout(kept_blocks, embedded, candidates, raw, pixel_probe=None):
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
    if raw.is_page_scan and pixel_probe is not None \
            and _scan_ruled(pixel_probe, line_boxes, left, right):
        # The same veto measured from pixels: a scan's ruling lives in the page
        # raster, invisible to the path count, and column traversal of a ruled
        # register destroys its rows exactly the same way. (A figure candidate
        # does not lift this veto: on a scan the candidate's territory is raster
        # too, and ruling between the rows is not artwork.)
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
    mirror = _paired_rows(columnar, layout)
    row_groups = mirror
    if sum(1 for count in counts if count >= COLUMN_MIN_LINES) < 2:
        return None
    for column, count in enumerate(counts):
        if count >= COLUMN_MIN_LINES and short[column] * 2 >= count:
            # A column of one- and two-word lines is a table's label column (or a
            # grid of cells), not a column of prose: reading it column-major
            # severs every row it prints. MEASURED on book 569's zodiacal tables
            # ('Characteristics' beside 'Northern - Commanding - ...'). A paired
            # table is the same veto with the row structure already measured.
            return _RowTable(row_groups) if row_groups else None
    fills.sort()
    if fills and fills[len(fills) // 2] < COLUMN_FILL_MIN:
        return None
    if row_groups:
        # Paired rows proved by shared entities or mirror fragments: the row
        # is the object, never two unrelated lists -- held-out fixture A's
        # Opened/Closed projects, book 569's sign pairs. This outranks a
        # labelled-sequence proof: both columns of a pair table can carry
        # ascending years, and the rows are still one project each.
        return _RowTable(row_groups)
    if not _column_sequence_evidence(columnar, layout):
        return None
    return layout


def _scan_ruled(pixel_probe, line_boxes, left, right):
    """True when the page raster carries repeated rules across the measure.

    The territory is the text's own box, breathed by two line heights so the
    grid's outermost rules -- which bound the first and last cells rather than
    sitting between lines -- are still seen. A probe failure fails closed: no
    veto, and the status-quo column reading keeps.
    """
    heights = sorted(box[3] - box[1] for box in line_boxes if box[3] > box[1])
    pad = 2.0 * (heights[len(heights) // 2] if heights else 12.0)
    top = min(box[1] for box in line_boxes) - pad
    bottom = max(box[3] for box in line_boxes) + pad
    try:
        strokes = pixel_probe.rules((left, top, right, bottom))
    except Exception:
        return False
    return len(strokes) >= RULE_SCAN_MIN


def _baseline_clusters(columnar):
    """Line items grouped by shared baseline, y ordered; a row's cells can sit
    a half point apart (an aspect cell set taller than its pair cells)."""
    clusters = []
    for box, text in sorted(columnar,
                            key=lambda item: (item[0][1] + item[0][3]) / 2.0):
        centre = (box[1] + box[3]) / 2.0
        if clusters and centre - clusters[-1][0] <= 3.0:
            clusters[-1][0] = centre
            clusters[-1][1].append((box, text))
        else:
            clusters.append([centre, [(box, text)]])
    return clusters


def _despaced(text):
    return "".join(ch.lower() for ch in text if ch.isalpha())


def _paired_rows(columnar, layout):
    """Row groups of a paired table, in print order: the row is the object.

    A row proves its cells belong together when they share a word this row
    alone prints (held-out fixture A: 'cedar' in exactly its Opened and its
    Closed cell; book 569's sign pairs, each sign named only across its own
    row). Letter-spaced cells answer as their letters, not their chunks:
    'g e m in i' contains 'gemini'. Fixture B's 'reached' spans two rows, so
    nothing there is row-unique and nothing pairs. Prose in columns may share
    the ruler's shape, but it ends its sentences: terminal punctuation
    anywhere vetoes the table.
    """
    clustered = _baseline_clusters(columnar)
    despaced_rows = [[_despaced(text) for _, text in group]
                     for _, group in clustered]
    aligned = 0
    paired_rows = []
    for index, (_, group) in enumerate(clustered):
        cols = {layout.column_of(box) for box, _ in group}
        if len(cols) < 2:
            continue
        aligned += 1
        if any(_SENTENCE_END.search(text) for _, text in group):
            return []
        despaced = despaced_rows[index]
        shared = set()
        for _, text in group:
            for word in text.split():
                clean = word.strip(".,;:!?\"”’()[]·-").lower()
                if len(clean) >= 4 and any(ch.isalpha() for ch in clean) \
                        and sum(clean in other for other in despaced) >= 2:
                    shared.add(clean)
        elsewhere = "".join(
            cell for other, row in enumerate(despaced_rows)
            if other != index for cell in row)
        if {t for t in shared if t not in elsewhere}:
            paired_rows.append(group)
    if aligned >= 4 and len(paired_rows) >= max(4, aligned - 1):
        return paired_rows
    return []


class _RowTable(object):
    """A mirror table measured and refused as prose columns: the row is the unit.

    Carries the paired fragment rows as clustered line groups so the page pass
    can emit each row as one region. Without that, the printed (y, x) order is
    kept but the paragraph join glues every cell to the lowercase letter-spaced
    cell after it -- book 569 p547 read 'Sextile t a u r u s looks at v ir g o':
    row one's aspect fused with row two's signs.
    """

    def __init__(self, rows):
        self.rows = [list(group) for group in rows]


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
    if _paired_rows(columnar, layout):
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
            if _chart_lettering(ln, style) or _squashed_caption(ln.stripped):
                continue
            y0, y1 = max(ln.bbox[1], top), min(ln.bbox[3], bottom)
            if y1 > y0:
                spans.append([y0, y1, ln.bbox])
    spans.sort()
    # Two lines belong to one band when the gap between them is a leading, not a
    # space. The OCR layer's own leading runs wider than the native layer's --
    # book 569's panels measure two and a half points on a seven-point line -- so
    # the tolerance follows the page's line height, bounded both ways.
    heights = sorted(y1 - y0 for y0, y1, _ in spans)
    tolerance = 2.0 if not heights else max(
        2.0, min(4.0, heights[len(heights) // 2] * 0.5))
    rows = []
    for y0, y1, bbox in spans:
        if rows and y0 <= rows[-1][1] + tolerance:
            rows[-1][1] = max(rows[-1][1], y1)
            rows[-1][2].append(bbox)
        else:
            rows.append([y0, y1, [bbox]])
    return rows, top, bottom


def _scan_figures(kept_blocks, raw, style, pixel_probe=None):
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
    side_seeds = []

    def has_caption(bx0, bx1, y1):
        return any(_squashed_caption(ln.stripped)
                   and ln.bbox[1] >= y1 - 12 and ln.bbox[1] <= y1 + 30
                   and (ln.bbox[0] + ln.bbox[2]) / 2.0 >= bx0
                   and (ln.bbox[0] + ln.bbox[2]) / 2.0 <= bx1
                   for ln in lines)

    def add(x0, y0, x1, y1, why, pad_x0=4.0, pad_x1=4.0, side=None):
        if y1 - y0 < raw.height * SCAN_GAP or x1 - x0 < span * 0.2:
            return
        boxes = [(x0, x1)]
        if pixel_probe is not None and why == "scan_figure_band":
            # Two charts side by side are two figures, one two-panel chart is
            # not: a truly empty column from the band's top to its bottom,
            # measured from pixels, AND each side answering with its own
            # caption. A probe failure fails closed: no split, never no figure.
            try:
                channels = pixel_probe.channels((x0, y0, x1, y1)) or []
            except Exception:
                channels = []
            halves = list(zip([x0] + channels, channels + [x1])) \
                if channels else []
            if len(halves) >= 2 \
                    and sum(1 for bx0, bx1 in halves
                            if has_caption(bx0, bx1, y1)) >= 2:
                boxes = halves
        for bx0, bx1 in boxes:
            # The pad breathes into empty territory only: padding across the
            # prose's own edge pulls a sliver of every line into the crop.
            candidates.append(Region(
                kind="figure", reason=why, needs_ink=True,
                bbox=(max(0.0, bx0 - pad_x0), max(0.0, y0 - 2.0),
                      min(raw.width, bx1 + pad_x1), min(raw.height, y1 + 2.0))))
            if side:
                side_seeds.append((side, candidates[-1]))

    # Full-width bands. A gap BETWEEN two prose rows is territory proved on both
    # sides. An edge gap is the weakest evidence there is -- the margins of a
    # scan are blank paper -- so two kinds are still refused at the door: ONE
    # piece of display type over white space is a chapter opening (book 567
    # p93's 'CHAPTER 4'), not a chart, and a margin whose pixels hold no marks
    # is not proposed at all. A chart with no readable lettering (book 569's
    # circular pair) passes on the same pixel proof.
    first, last = rows[0], rows[-1]

    def lone_display(y0, y1):
        display = [ln for ln in lines
                   if _chart_lettering(ln, style)
                   and ln.bbox[1] >= y0 - 2 and ln.bbox[3] <= y1 + 2]
        return len(display) == 1

    def edge_has_ink(x0, y0, x1, y1):
        if pixel_probe is None:
            return True
        try:
            return bool(pixel_probe.has_ink((x0, y0, x1, y1)))
        except Exception:
            return True

    if first[0] - top > raw.height * SCAN_GAP \
            and not lone_display(top, first[0]) \
            and edge_has_ink(left, top, right, first[0]):
        add(left, top, right, first[0], "scan_figure_band")
    for row, following in zip(rows, rows[1:]):
        gap = following[0] - row[1]
        if gap > raw.height * SCAN_GAP:
            add(left, row[1], right, following[0], "scan_figure_band")
    if bottom - last[1] > raw.height * SCAN_GAP \
            and not lone_display(last[1], bottom) \
            and edge_has_ink(left, last[1], right, bottom):
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
    return _partition_captioned_sides(candidates, side_seeds, lines, rows, raw, pixel_probe)


def _partition_captioned_sides(candidates, seeds, lines, rows, raw, pixel_probe):
    """Resolve overlapping side seeds into complete, independently captioned art.

    Prose paragraphs locate an empty side column, not individual chart bounds.
    Printed captions partition that column; source pixels establish each unit's
    extent, including artwork wider than its extracted caption text.
    """
    if pixel_probe is None or not hasattr(pixel_probe, "ink_bounds"):
        return candidates
    groups = []
    for side, candidate in seeds:
        matched = []
        for index, (group_side, members) in enumerate(groups):
            if side != group_side:
                continue
            for other in members:
                a, b = candidate.bbox, other.bbox
                overlap = min(a[2], b[2]) - max(a[0], b[0])
                if overlap >= min(a[2]-a[0], b[2]-b[0]) * 0.8 \
                        and min(a[3], b[3]) > max(a[1], b[1]):
                    matched.append(index)
                    break
        members = [candidate]
        for index in reversed(matched):
            members.extend(groups.pop(index)[1])
        groups.append((side, members))
    replacements = {}
    removed = set()
    for side, members in groups:
        left = min(c.bbox[0] for c in members)
        right = max(c.bbox[2] for c in members)
        top = min(c.bbox[1] for c in members)
        bottom = max(c.bbox[3] for c in members)
        anchors = sorted([ln for ln in lines if _squashed_caption(ln.stripped)
                          and left <= (ln.bbox[0]+ln.bbox[2])/2 <= right
                          and top <= ln.bbox[1] <= bottom + 30], key=_caption_order)
        if not anchors or any(a.bbox[3] >= b.bbox[1]
                                  for a, b in zip(anchors, anchors[1:])):
            continue
        x0 = max(c.bbox[0] for c in members) if side == "right" else 0.0
        x1 = min(c.bbox[2] for c in members) if side == "left" else raw.width
        units = []
        ink_extents = []
        cursor = top
        base_x0, base_x1 = x0, x1
        try:
            for caption in anchors:
                x0, x1 = base_x0, base_x1
                if caption.bbox[1] <= cursor:
                    break
                centre = (caption.bbox[0]+caption.bbox[2])/2
                prose = [box for _, _, boxes in rows for box in boxes
                         if box[3] > cursor and box[1] < caption.bbox[1]
                         and ((box[0]+box[2])/2 < centre if side == "right"
                              else (box[0]+box[2])/2 > centre)]
                # A ragged line above the art may reach farther than the actual
                # neighboring column. Broaden the query to its typical edge;
                # pixels locate the art, and prose intersection vetoes adoption.
                if prose:
                    if side == "right":
                        x0 = min(x0, median(box[2] for box in prose))
                    else:
                        x1 = max(x1, median(box[0] for box in prose))
                if caption.bbox[0] < x0-4 or caption.bbox[2] > x1+4:
                    break
                ink = pixel_probe.ink_bounds((x0, cursor, x1, caption.bbox[1]))
                if not ink or ink[3]-ink[1] < raw.height * SCAN_GAP:
                    break
                if any(min(ink[2], box[2]) > max(ink[0], box[0])
                       and min(ink[3], box[3]) > max(ink[1], box[1]) for box in prose):
                    break
                ink_extents.append(ink)
                units.append(Region(kind="figure", reason="scan_figure_side", needs_ink=True,
                    bbox=(max(x0, min(ink[0]-4, caption.bbox[0])),
                          max(cursor, ink[1]-4),
                          min(x1, max(ink[2]+4, caption.bbox[2])),
                          max(ink[3], caption.bbox[3]))))
                cursor = caption.bbox[3]
            if len(units) != len(anchors):
                continue
            # A final unlabelled picture must not disappear just because the
            # pictures above it carried readable captions.
            if bottom > cursor:
                ink = pixel_probe.ink_bounds((x0, cursor, x1, bottom))
                if ink and ink[3]-ink[1] >= raw.height * SCAN_GAP:
                    units.append(Region(kind="figure", reason="scan_figure_side", needs_ink=True,
                        bbox=(max(x0, ink[0]-4), max(cursor, ink[1]-4),
                              min(x1, ink[2]+4), min(bottom, ink[3]+4))))
        except (ValueError, RuntimeError, AttributeError):
            continue
        # Avoid churning already complete single-caption crops. Repartition
        # multiple seeds/units, or repair a single crop only when source ink
        # proves that its current bounds exclude printed content.
        if len(members) == 1 and len(units) == 1:
            box = ink_extents[0]
            if not (box[0] < left-2 or box[1] < top-2
                    or box[2] > right+2 or box[3] > bottom+2):
                continue
        replacements[id(members[0])] = units
        removed.update(id(c) for c in members)
    result = []
    for candidate in candidates:
        if id(candidate) in replacements:
            result.extend(replacements[id(candidate)])
        elif id(candidate) not in removed:
            result.append(candidate)
    return result


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

    def bounds():
        top = raw.height * HEADER_BAND
        bottom = raw.height * FOOTER_BAND
        for _, _, boxes in rows:
            for box in boxes:
                if not (box[2] > x0 and box[0] < x1):
                    continue
                if box[1] < run[0][1]:
                    top = max(top, box[3])
                if box[3] > run[-1][3]:
                    bottom = min(bottom, box[1])
        return top, bottom

    top, bottom = bounds()
    captions = [ln for blk in getattr(raw, "text_blocks", ()) for ln in blk.lines
                if _squashed_caption(ln.stripped)
                and x0 <= (ln.bbox[0] + ln.bbox[2]) / 2.0 <= x1]
    # Repair a sub-glyph column-edge mismatch only when one printed caption
    # establishes a figure unit beyond the existing caption-attachment reach.
    # Multiple captions establish independent units: expanding across them would
    # merge stacked charts. A complete or unlabelled candidate needs no change.
    detached_caption = len(captions) == 1 and captions[0].bbox[1] > bottom + 30.0
    if detached_caption:
        edge_slop = min(median(box[3] - box[1] for box in run) * 0.15,
                        (x1 - x0) * 0.02)
        boxes = [box for _, _, row_boxes in rows for box in row_boxes]
        if side == "right":
            x0 = max([x0] + [box[2] for box in boxes
                             if box[0] < x0 and x0 < box[2] <= x0 + edge_slop])
        else:
            x1 = min([x1] + [box[0] for box in boxes
                             if box[2] > x1 and x1 - edge_slop <= box[0] < x1])
        top, bottom = bounds()
    if side == "left":
        add(x0, top + 2.0, x1, bottom - 2.0, "scan_figure_side",
            pad_x0=4.0, pad_x1=0.0, side=side)
    else:
        add(x0, top + 2.0, x1, bottom - 2.0, "scan_figure_side",
            pad_x0=0.0, pad_x1=4.0, side=side)


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


def _squashed_caption(text):
    """A caption line even when the printer letter-spaced it: ``F IG U R E 4 4 .``
    is FIGURE 44, ``f ig u r e 68.`` is figure 68."""
    return CAPTION_LINE.match(text.replace(" ", ""))


def _caption_order(line):
    # Different faces on one baseline have different ascenders. Keep fragments
    # on that baseline in left-to-right order, even across extraction blocks.
    return (round(max((sp.origin_y or line.bbox[3]) for sp in line.spans), 1),
            line.bbox[0])


def _caption_reading_order(lines):
    """Read distinct neighboring printed captions by column, including subtitles.

    Shared or spanning text has no unambiguous column association and keeps its
    physical order. This does not infer captions or alter their style/words.
    """
    anchors = sorted((ln for ln in lines if _squashed_caption(ln.stripped)),
                     key=lambda ln: ln.bbox[0])
    if len(anchors) < 2:
        return lines
    if max(ln.bbox[1] for ln in anchors) >= min(ln.bbox[3] for ln in anchors):
        return lines
    if any(a.bbox[2] >= b.bbox[0] for a, b in zip(anchors, anchors[1:])):
        return lines
    boundaries = [(a.bbox[2] + b.bbox[0]) / 2
                  for a, b in zip(anchors, anchors[1:])]
    groups = [[] for _ in anchors]
    for ln in lines:
        if any(ln.bbox[0] < boundary < ln.bbox[2] for boundary in boundaries):
            return lines
        column = sum(ln.bbox[0] >= boundary for boundary in boundaries)
        if ln.bbox[1] < anchors[column].bbox[1] - 1:
            return lines
        groups[column].append(ln)
    return [ln for group in groups for ln in sorted(group, key=_caption_order)]


def _caption_has_gap(lines):
    for left, right in zip(lines, lines[1:]):
        overlap = min(left.bbox[3], right.bbox[3]) - max(left.bbox[1], right.bbox[1])
        if overlap > min(left.bbox[3] - left.bbox[1],
                         right.bbox[3] - right.bbox[1]) * 0.5 \
                and right.bbox[0] - left.bbox[2] > max(left.size, right.size):
            return True
    return False


def _attach_caption(candidate, kept_blocks, style):
    """A figure's printed title, with its display subtitle, just under the
    territory and inside its column rides with the figure.

    The circular charts this is for (book 569's figure 44/45 pair, figure 68)
    print their titles one step below the artwork; left in the prose they
    render as stray fragments far from the image. A title must read as a
    caption after the squash; the lines right under it attach while they are
    set smaller than the body (the subtitle), never a body-sized explanation.
    """
    found = []
    last_y = None
    # Extractors group blocks independently of printed vertical order.
    for ln in sorted((ln for _, lines in kept_blocks for ln in lines),
                     key=_caption_order):
        x0, y0, x1, y1 = ln.bbox
        if y0 < candidate.bbox[1] or (not found and y0 > candidate.bbox[3] + 30.0):
            continue
        if x0 < candidate.bbox[0] - 4 or x1 > candidate.bbox[2] + 4:
            continue
        if found and y0 > last_y + 16.0:
            break
        if not found and _squashed_caption(ln.stripped):
            found.append(ln)
            last_y = y1
            continue
        if found and y0 <= last_y + 16.0 \
                and style.body_size and ln.size < style.body_size * 0.98:
            found.append(ln)
            last_y = y1
    if not found:
        return kept_blocks
    wanted = {id(ln) for ln in found}
    out = []
    for blk, lines in kept_blocks:
        remainder = [ln for ln in lines if id(ln) not in wanted]
        if remainder:
            out.append((blk, remainder))
    candidate.caption_lines = list(candidate.caption_lines) + found
    candidate.bbox = (candidate.bbox[0], candidate.bbox[1], candidate.bbox[2],
                      max(candidate.bbox[3], max(ln.bbox[3] for ln in found)))
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
        image_box = candidate.bbox
        kept_blocks = _attach_caption(candidate, kept_blocks, style)
        captions = list(candidate.caption_lines)
        kept = []
        for blk, lines in kept_blocks:
            inside, outside = [], []
            for ln in lines:
                (inside if _inside(ln.bbox, candidate.bbox) else outside).append(ln)
            for ln in inside:
                if _squashed_caption(ln.stripped):
                    captions.append(ln)
                elif _chart_lettering(ln, style):
                    candidate.lines.append(ln)
                else:
                    outside.append(ln)
            if outside:
                kept.append((blk, outside))
        kept_blocks = kept
        captions.sort(key=_caption_order)
        candidate.caption_lines = _caption_reading_order(captions)
        candidate.uncertain = _caption_has_gap(captions)
        if captions and not candidate.uncertain:
            candidate.bbox = _crop_around_caption(image_box, captions)
        elif captions:
            # A broken baseline can hide a symbol absent from the text layer.
            # Keep its printed pixels and qualify the transcription instead of
            # inventing a character. Include the complete caption, not a sliver.
            candidate.bbox = (min(candidate.bbox[0], min(l.bbox[0] for l in captions)),
                              min(candidate.bbox[1], min(l.bbox[1] for l in captions)),
                              max(candidate.bbox[2], max(l.bbox[2] for l in captions)),
                              max(candidate.bbox[3], max(l.bbox[3] for l in captions)) + 2)


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
