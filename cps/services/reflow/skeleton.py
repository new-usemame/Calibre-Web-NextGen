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
away ``Workshop of Birchwood (First Edition AB?)`` (four clean words out of six, the
other two being ``(First`` and ``CE?)``) and 27 more like it in one book. Here the
typography decides, and the junk filter is demoted to a veto that looks for what
chart labels actually are — stray single glyphs and columns of numbers.
"""

import re
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from statistics import median
from typing import List, Optional, Tuple

from . import extract, unit_recovery

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
#: Group small type-size differences as one printed rung; OCR measurements can
#: vary around a single real size.
LADDER_TOL = 0.06
#: Use recurrent source typography for heading levels; sparse diagram type
#: does not define the ladder.
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
#: prose column is sidebar figure territory (a circular wheel next to its reading).
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
# A compact diagram can have only a few area-built paths (circles and
# diagonals); its ticks and labels are thin paths whose boxes fail the filter
# above. Require both a substantial core and nearby supporting strokes.
VEC_COMPACT_MIN_PATHS = 6
VEC_COMPACT_MIN_AREA = 0.02
VEC_COMPACT_MIN_STROKES = 12
VEC_COMPACT_REACH = 12.0

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
    continued_from: Optional[tuple] = None
    initial_join: dict = field(default_factory=dict)
    display_group: dict = field(default_factory=dict)
    list_groups: list = field(default_factory=list)
    visual_evidence: dict = field(default_factory=dict)
    #: Detached disputed scan markers attached at a uniquely measured line end.
    #: Original line objects stay in ownership; assembly projects their pixels.
    inline_marker_boxes: list = field(default_factory=list)

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
    title_unit: bool = False

    def body_box(self):
        """Bounds of source body content after qualified furniture is removed."""
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
    folio_boxes: dict = field(default_factory=dict)
    contents_folio_boxes: dict = field(default_factory=dict)
    local_running_boxes: dict = field(default_factory=dict)
    repeated_head_boxes: dict = field(default_factory=dict)
    paired_margin_head_boxes: dict = field(default_factory=dict)
    portrait_head_boxes: dict = field(default_factory=dict)
    scan_spread_head_boxes: dict = field(default_factory=dict)
    scan_table_label_boxes: dict = field(default_factory=dict)
    scan_note_lane_boxes: dict = field(default_factory=dict)
    scan_note_footer_profiles: list = field(default_factory=list)

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
    return _heading_ladder(body_size, census, pages, page_count)


def _source_heading_ladder(body_size, census, pages, page_count):
    controller=unit_recovery.current()
    if controller is None:return heading_ladder(body_size,census,pages,page_count)
    try:return heading_ladder(body_size,census,pages,page_count)
    except Exception as error:
        if unit_recovery.fatal(error):raise
        result=_heading_ladder(body_size,census,pages,page_count)
        controller.events.append(dict(stage='native_heading_ladder',transition='DETERMINISTIC_SOURCE_REPAIR',
            reason=type(error).__name__,proof='Exact original ladder reconstructed from admitted per-page font census and occurrence sets'))
        return result


def _heading_ladder(body_size, census, pages, page_count):
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


#: A scanner outline made only of page labels is an index of leaves, not a
#: chapter table of contents.
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


def _sequence_folios(raw_pages, body_size):
    """Prove deep-margin folios from repeated geometry and page progression.

    Large PDF margins can place the printed footer well inside a percentage
    band. An isolated body number is insufficient: require three neighboring
    pages with the same page/folio offset and detached bottom-row geometry.
    """
    candidates=[]
    damaged=[]
    footer_peers={}
    for raw in raw_pages:
        with unit_recovery.guard(raw.pno, "native_style_source_metrics"):
            lines=sorted((line for block in raw.text_blocks for line in block.lines
                          if line.stripped),key=lambda line:line.bbox[1])
            if len(lines)<2:continue
            line=lines[-1]
            gap=line.bbox[1]-max(other.bbox[3] for other in lines[:-1])
            printed_size=median(sp.size for sp in line.spans if sp.text.strip())
            deep=line.bbox[1]>=raw.height*.88
            if (line.bbox[1]>=raw.height*.88 and line.size<=body_size*1.5
                    and gap>=line.size*.8):
                if re.fullmatch(r'[0-9]{1,5}',line.stripped):
                    footer_peers[raw.pno]=(int(line.stripped)-raw.pno,line.bbox[1]/raw.height,line.bbox)
                elif re.fullmatch(r'[0-9SsOoIl]{1,5}',line.stripped) and any(ch.isdigit() for ch in line.stripped):
                    damaged.append((raw,line))
            if (line.bbox[1]<raw.height*.7
                    or printed_size>body_size*(1.5 if deep else 1.02)
                    or gap<printed_size*(.8 if deep else 1.5)):continue
            if not re.fullmatch(r"[0-9]{1,5}",line.stripped):
                # A damaged folio is retained verbatim. Its furniture role can come
                # from the surrounding print geometry, never from guessing digits.
                continue
            candidates.append((raw.pno,int(line.stripped)-raw.pno,
                               line.bbox[1]/raw.height,line.bbox))
    proved={}
    for page,offset,y,box in candidates:
        peers=sorted(p for p,o,py,b in candidates if o==offset and abs(py-y)<.03)
        if any(peers[i+2]-peers[i]==2 and peers[i]<=page<=peers[i+2]
               for i in range(len(peers)-2)):
            proved[page]=box
    numeric=footer_peers
    widths={raw.pno:raw.width for raw in raw_pages}
    for raw,line in damaged:
        peers=[numeric.get(raw.pno+delta) for delta in (-2,-1,1,2)]
        if any(peer is None for peer in peers):continue
        offsets={peer[0] for peer in peers}
        if len(offsets)!=1 or any(abs(peer[1]-line.bbox[1]/raw.height)>.03 for peer in peers):continue
        # Alternating verso/recto folios may live on opposite sides. The two
        # observations of this page's parity must share its horizontal home.
        if any(abs(peer[2][0]/widths[raw.pno+delta]-line.bbox[0]/raw.width)>.05
               for delta,peer in ((-2,peers[0]),(2,peers[3]))):continue
        expected=raw.pno+next(iter(offsets))
        if expected>=0 and len(str(expected))==len(line.stripped):
            proved[raw.pno]=line.bbox
    return proved


_LEADING_PRINTED_FOLIO = re.compile(r'^([0-9]{1,5})\s+(.+)$')
_TRAILING_PRINTED_FOLIO = re.compile(r'^(.+?)\s+([0-9]{1,5})$')


def _local_running_folios(raw_pages, body_size):
    """Prove a short alternating running head by its margin row and folio run.

    Three consecutive observations at page stride one or two are required.
    The label, folio/page offset, type size, and row geometry must all agree;
    repeated words elsewhere on a page do not inherit furniture status.
    """
    if not body_size:
        return {}
    groups = defaultdict(list)
    for raw in raw_pages:
        with unit_recovery.guard(raw.pno, "native_style_source_metrics"):
            lines = sorted((line for block in raw.text_blocks for line in block.lines
                            if line.stripped), key=lambda line: (line.bbox[1], line.bbox[0]))
            if len(lines) < 2:
                continue
            line, following = lines[0], lines[1]
            if (line.bbox[3] > raw.height * HEADER_BAND or
                    line.size > body_size * .98 or
                    following.bbox[1] - line.bbox[3] < line.size * .8 or
                    len(line.stripped) > BAND_TEXT_MAX):
                continue
            match = _LEADING_PRINTED_FOLIO.fullmatch(line.stripped)
            if match:
                folio, label, edge = int(match[1]), match[2], 'left'
            else:
                match = _TRAILING_PRINTED_FOLIO.fullmatch(line.stripped)
                if not match:
                    continue
                label, folio, edge = match[1], int(match[2]), 'right'
            label = ' '.join(label.split())
            if (len(label.split()) < 2 or sum(ch.isalpha() for ch in label) < 12 or
                    CAPTION_LINE.match(label)):
                continue
            groups[(label.casefold(), edge, folio - raw.pno)].append((raw, line))
    proved = {}
    for rows in groups.values():
        rows.sort(key=lambda pair: pair[0].pno)
        for raw, line in rows:
            peers = [(other, candidate) for other, candidate in rows
                     if abs(candidate.bbox[1] / other.height - line.bbox[1] / raw.height) <= .006
                     and abs(candidate.bbox[0] / other.width - line.bbox[0] / raw.width) <= .025
                     and abs(candidate.size - line.size) <= body_size * .08
                     and abs((candidate.bbox[2] - candidate.bbox[0]) / other.width -
                             (line.bbox[2] - line.bbox[0]) / raw.width) <= .04]
            pages = sorted(other.pno for other, _ in peers)
            if any(raw.pno in pages[i:i+3] and
                   pages[i+1] - pages[i] == pages[i+2] - pages[i+1] and
                   pages[i+1] - pages[i] in (1, 2)
                   for i in range(len(pages) - 2)):
                proved[raw.pno] = line.bbox
    return proved


def _native_margin_frame(raw):
    """The original, unrotated native frame used by portrait margin profiles."""
    geometry = getattr(raw, 'source_geometry', {})
    if not geometry:
        return True
    rect = geometry.get('page_rect', ())
    return (geometry.get('space') == 'native' and geometry.get('orientation') == 0
            and geometry.get('source_rotation') == 0 and len(rect) == 4
            and all(abs(a-b) <= .01 for a, b in
                    zip(rect, (0, 0, raw.width, raw.height))))


def _margin_row_measure(row, raw):
    """Measure original ink-built spans, excluding expanded whitespace tabs."""
    spans = [span for span in row.spans if span.text.strip()]
    if not spans or any(span.size <= 0 for span in spans):
        return None
    box = (min(span.bbox[0] for span in spans), min(span.bbox[1] for span in spans),
           max(span.bbox[2] for span in spans), max(span.bbox[3] for span in spans))
    if (box[0] < 0 or box[1] < 0 or box[2] > raw.width or box[3] > raw.height
            or box[0] >= box[2] or box[1] >= box[3]):
        return None
    baseline = median(span.origin_y if span.bbox[1] <= span.origin_y <= span.bbox[3]
                      else span.bbox[3] - span.size*.2 for span in spans)
    return box, median(span.size for span in spans), baseline


def _paired_margin_head_profile(raw_pages, body_size):
    """Learn exact margin pairs from other independently recurrent source heads.

    An unfamiliar title may use a book's established margin template. It cannot
    teach that template: teachers must have the same literal label on at least
    four source leaves, and each target needs three other measured teacher pages.
    Only reliable original numeric folios teach a constant page offset and a
    measured stride. The target's folio can remain uncertain: its number is
    neither inferred nor adopted from that independent source template.
    """
    if not body_size:
        return {}
    em = body_size
    candidates = []
    for raw in raw_pages:
        with unit_recovery.guard(raw.pno, "native_style_source_metrics"):
            if (not getattr(raw, 'is_page_scan', False) or raw.width >= raw.height*1.2
                    or not _native_margin_frame(raw)):
                continue
            rows = [row for block in raw.text_blocks for row in block.lines if row.stripped]
            measured = {id(row): _margin_row_measure(row, raw) for row in rows}
            for head in rows:
                if (not _is_caps(head.stripped) or CAPTION_LINE.match(head.stripped)
                        or len(head.stripped) > BAND_TEXT_MAX
                        or sum(char.isalpha() for char in head.stripped) < 3):
                    continue
                heading = measured[id(head)]
                if not heading:
                    continue
                hbox, hsize, hbaseline = heading
                if (hbox[3] > raw.height*.12 or hsize >= em*.98
                        or hbox[3]-hbox[1] > em*1.5):
                    continue
                pairs = []
                for folio in rows:
                    if folio is head or not FOLIO.fullmatch(folio.stripped):
                        continue
                    marking = measured[id(folio)]
                    if not marking:
                        continue
                    box, size, baseline = marking
                    if (box[3] > raw.height*.12 or box[2]-box[0] > raw.width*.075
                            or not em*.6 <= size <= em*1.3 or box[3]-box[1] > em*1.5
                            or not (box[2] <= raw.width*.2 or box[0] >= raw.width*.8)
                            or abs(baseline-hbaseline) > em*.35
                            or not (box[2]+em*.4 < hbox[0] or hbox[2]+em*.4 < box[0])):
                        continue
                    others = [row for row in rows if row is not head and row is not folio]
                    bottom = max(head.bbox[3], folio.bbox[3])
                    if not others or min(row.bbox[1] for row in others)-bottom < em*.8:
                        continue
                    pairs.append((folio, box))
                if len(pairs) == 1:
                    folio, box = pairs[0]
                    candidates.append((raw, head, folio, hbox, hsize, hbaseline,
                                       'left' if box[2] <= raw.width*.2 else 'right'))
    recurring = defaultdict(set)
    for raw, head, *_ in candidates:
        recurring[head.stripped.casefold()].add(raw.pno)
    teachers = [candidate for candidate in candidates
                if len(recurring[candidate[1].stripped.casefold()]) >= 4
                and candidate[2].stripped.isdigit()
                and not candidate[2].transcription_uncertain
                and not any(span.uncertain or span.transcription_uncertain
                            or span.encoding_unresolved
                            for span in candidate[2].spans if span.text.strip())]
    proved = defaultdict(list)
    for raw, head, folio, box, size, baseline, side in candidates:
        peers = defaultdict(set)
        for other, _, numeric, obox, osize, obaseline, oside in teachers:
            if (other.pno == raw.pno or oside != side
                    or abs(osize-size) > em*.15
                    or abs(obaseline/other.height-baseline/raw.height) > em*.35/raw.height
                    or abs((obox[0]+obox[2])/(2*other.width)
                           -(box[0]+box[2])/(2*raw.width)) > .04):
                continue
            peers[int(numeric.stripped)-other.pno].add(other.pno)
        progression = False
        for pages in peers.values():
            ordered = sorted(pages)
            if any(ordered[i+1]-ordered[i] == ordered[i+2]-ordered[i+1]
                   and ordered[i+1]-ordered[i] in (1, 2)
                   for i in range(len(ordered)-2)):
                progression = True
                break
        if progression:
            proved[raw.pno].extend((head.bbox, folio.bbox))
    return dict(proved)


def _repeated_detached_heads(raw_pages, body_size):
    """Find chapter-local heads without treating the whole top band as furniture.

    Scans have variable margins and often put a folio on a separate line. Require
    three occurrences of a short, detached top row at the same position and size.
    The result names exact line boxes, so equal words in prose stay in prose.
    """
    groups = defaultdict(list)
    footer_boxes = _sequence_folios(raw_pages,body_size)
    for raw in raw_pages:
        with unit_recovery.guard(raw.pno, "native_style_source_metrics"):
            lines = sorted((line for block in raw.text_blocks for line in block.lines
                            if line.stripped), key=lambda line: (line.bbox[1], line.bbox[0]))
            if len(lines) < 2 or not body_size:
                continue
            top = lines[0].bbox[1]
            row = [line for line in lines if line.bbox[1] < top + body_size]
            following = [line for line in lines if line not in row]
            footer = next((line for line in lines if line.bbox==footer_boxes.get(raw.pno)),None)
            if raw.width >= raw.height*1.2:footer=None
            if not following or max(line.bbox[3] for line in row) > raw.height * .20:
                continue
            if min(line.bbox[1] for line in following) - max(line.bbox[3] for line in row) < body_size * .8:
                continue
            for line in row:
                text = line.stripped
                folios = [peer for peer in row if peer is not line
                    and re.fullmatch(r'\d{1,5}',peer.stripped)
                    and abs(peer.bbox[1]-line.bbox[1]) <= body_size*.35
                    and (peer.bbox[2] < line.bbox[0]-body_size*.4
                         or peer.bbox[0] > line.bbox[2]+body_size*.4)
                    and peer.size <= body_size*(1.3 if raw.width<raw.height*1.2
                        and line.bbox[3]<=raw.height*.12 else 1.2)]
                folio = folios[0] if len(folios)==1 else None
                if (len(text) > BAND_TEXT_MAX or sum(c.isalpha() for c in text) < (3 if footer is not None else 6)
                        or line.size > body_size * (1.5 if footer is not None else 1.2 if folio is not None else 1.02)
                        or CAPTION_LINE.match(text)):
                    continue
                leading = _LEADING_PRINTED_FOLIO.fullmatch(text)
                trailing = _TRAILING_PRINTED_FOLIO.fullmatch(text)
                if leading:
                    key = (leading[2].casefold(), 'left', int(leading[1])-raw.pno)
                elif trailing:
                    key = (trailing[1].casefold(), 'right', int(trailing[2])-raw.pno)
                elif folio is not None:
                    # A detached margin row with a progressing separate folio is
                    # independent evidence when scan font-size estimates wobble.
                    key = (text.casefold(), 'separate', int(folio.stripped)-raw.pno)
                elif footer is not None:
                    key = (text.casefold(), 'footer', int(footer.stripped)-raw.pno)
                else:
                    key = (text.casefold(), None, None)
                    if line.size >= body_size * .98:
                        continue
                groups[key].append((raw, line, folio if key[1]=='separate' else footer if key[1]=='footer' else None))
    proved = defaultdict(list)
    for key,rows in groups.items():
        for raw, line, folio in rows:
            paired=(key[1]=='separate' and raw.width<raw.height*1.2
                    and line.bbox[3]<=raw.height*.12 and len(line.stripped.split())>=3
                    and sum(c.isalpha() for c in line.stripped)>=12)
            letters=re.sub(r'[^a-z]','',line.stripped.casefold())
            candidates=list(rows)
            if paired:
                for other_key,other_rows in groups.items():
                    if other_key!=key and other_key[1:] == key[1:] and SequenceMatcher(
                            None,letters,re.sub(r'[^a-z]','',other_key[0]),autojunk=False).ratio()>=.92:
                        candidates.extend(other_rows)
            peers = {other.pno for other, candidate, _ in candidates
                     if (not paired or (abs(other.pno-raw.pno)<=8 and candidate.bbox[3]<=other.height*.12))
                     and abs(candidate.bbox[1]/other.height - line.bbox[1]/raw.height) <= (.012 if paired else .006)
                     and abs(candidate.bbox[0]/other.width - line.bbox[0]/raw.width) <= .035
                     and abs(candidate.size - line.size) <= body_size * .10}
            pages=sorted(peers)
            progressing=folio is None or any(raw.pno in pages[i:i+3]
                and pages[i+1]-pages[i]==pages[i+2]-pages[i+1]
                and pages[i+1]-pages[i] in (1,2) for i in range(len(pages)-2))
            if len(peers) >= 3 and progressing:
                proved[raw.pno].append(line.bbox)
                if folio is not None:proved[raw.pno].append(folio.bbox)
    from .portrait_heads import boxes as portrait_head_boxes
    for pno, boxes in portrait_head_boxes(raw_pages,body_size).items():
        for box in boxes:
            if box not in proved[pno]:proved[pno].append(box)
    return dict(proved)


def _repeated_scan_spread_heads(raw_pages, *, table_labels=None):
    """Qualify damaged marginal lettering above two measured prose panels.

    OCR type-size estimates and exact spellings are unreliable here. Require a
    detached row, an actual empty central gutter, matching placement and similar
    source lettering on three nearby leaves. Body-sized or deeper lettering
    also requires the printed folios to progress across the two-up leaves.
    Only exact original occurrences acquire the role; no text is repaired.
    """
    candidates = []
    measured = {}
    small_margin_rows = []
    grouped_rows = {}
    for raw in raw_pages:
        with unit_recovery.guard(raw.pno, "native_style_source_metrics"):
            if not raw.is_page_scan or raw.width < raw.height * 1.2:
                continue
            lines = [line for block in raw.text_blocks for line in block.lines if line.stripped]
            wide = [line for line in lines if raw.width*.25 <= line.bbox[2]-line.bbox[0] <= raw.width*.45
                    and line.bbox[3] > raw.height*.05 and line.bbox[1] < raw.height*.85
                    # A wide OCR box alone is not prose: column labels must stay
                    # with short table/list rows even when their boxes span a panel.
                    and len(line.stripped.split()) >= 6
                    and len(re.findall(r'\b\d+(?:st|nd|rd|th)?\b',line.stripped)) < 3]
            panels = [[line for line in wide if (line.bbox[0]+line.bbox[2])/2 < raw.width*.5],
                      [line for line in wide if (line.bbox[0]+line.bbox[2])/2 > raw.width*.5]]
            if any(len(panel) < 4 for panel in panels):
                continue
            gutter = (max(line.bbox[2] for line in panels[0]), min(line.bbox[0] for line in panels[1]))
            if gutter[1]-gutter[0] < min(raw.width*GUTTER_MIN,
                    2*median(line.bbox[3]-line.bbox[1] for line in wide)) or any(
                    line.bbox[0] < gutter[0] and line.bbox[2] > gutter[1] for line in lines):
                continue
            for side, panel in enumerate(panels):
                height = median(line.bbox[3]-line.bbox[1] for line in panel)
                panel_ids = {id(line) for line in panel}
                wrapped = 0
                for block in raw.text_blocks:
                    paragraph = sorted((line for line in block.lines if id(line) in panel_ids),
                                       key=lambda line: line.bbox[1])
                    wrapped += sum(0 < following.bbox[1]-prior.bbox[1] <= height*1.5
                        and abs(prior.bbox[0]-following.bbox[0]) <= raw.width*.02
                        and not prior.stripped.endswith(('.',':',';','!','?','”','’','"'))
                        for prior,following in zip(paragraph,paragraph[1:]))
                # Long descriptions in separate table/list rows also have wide
                # boxes. Demand source-measured paragraph wraps, not just words.
                if wrapped < 3:
                    continue
                x0, x1 = min(line.bbox[0] for line in panel), max(line.bbox[2] for line in panel)
                members = [line for line in lines if
                           ((line.bbox[0]+line.bbox[2])/2 < raw.width*.5 if side==0
                            else (line.bbox[0]+line.bbox[2])/2 > raw.width*.5)]
                if not members or height <= 0:
                    continue
                first = min(members, key=lambda line: line.bbox[1])
                remaining = [line for line in members if line is not first]
                # Separate short keys beside wrapped descriptions establish rows,
                # even when each description looks exactly like ordinary prose.
                keys = [key for key in remaining
                        if len(key.stripped.split()) <= 4 and key.bbox[2]-key.bbox[0] <= raw.width*.16
                        and any(description.bbox[0]-key.bbox[2] >= height*.3
                                and abs((description.bbox[1]+description.bbox[3]-
                                         key.bbox[1]-key.bbox[3])/2) <= height*.4
                                for description in panel)]
                keyed = any(len({round(key.bbox[1]/height) for key in keys
                                 if abs(key.bbox[0]-anchor.bbox[0]) <= raw.width*.02}) >= 3
                            for anchor in keys)
                # OCR can split a running title from its disputed outer folio, or
                # split a damaged opening across several spans. Establish the
                # complete physical row before requiring a literal numeric edge.
                # These small rows require independent title/placement recurrence;
                # no isolated disputed mark qualifies from its spelling.
                row = [line for line in members if
                       abs((line.bbox[1]+line.bbox[3]-first.bbox[1]-first.bbox[3])/2)
                       <= height*.5]
                row = sorted(row, key=lambda line: line.bbox[0])
                row_spans = [span for line in row for span in line.spans if span.text.strip()]
                row_text = ' '.join(line.stripped for line in row)
                row_letters = ''.join(char for char in row_text if char.isalpha())
                row_box = (min(line.bbox[0] for line in row),
                           min(line.bbox[1] for line in row),
                           max(line.bbox[2] for line in row),
                           max(line.bbox[3] for line in row))
                row_sizes = [span.size for span in row_spans for char in span.text if char.isalpha()]
                after_row = [line for line in members if line not in row]
                if (not keyed and len(row_letters) >= 6
                        and len(row_text) <= BAND_TEXT_MAX
                        and row_box[3] <= raw.height*HEADER_BAND
                        and row_box[3]-row_box[1] <= height*.8
                        and row_box[2]-row_box[0] <= raw.width*.32
                        and (row_box[0] <= raw.width*.12 if side == 0
                             else row_box[2] >= raw.width*.88)
                        and row_sizes and median(row_sizes) <= height*.75
                        and sum(char.isupper() for char in row_letters) >= len(row_letters)*.65
                        and any(span.uncertain or span.transcription_uncertain
                                or span.encoding_unresolved for span in row_spans)
                        and not any(any(char.islower() for char in span.text) and not (
                            span.uncertain or span.transcription_uncertain
                            or span.encoding_unresolved) for span in row_spans)
                        and not CAPTION_LINE.match(row_text) and '°' not in row_text
                        and after_row
                        and min(line.bbox[1] for line in after_row)-row_box[3] >= (row_box[3]-row_box[1])*.5
                        and all(height*.3 <= right.bbox[0]-left.bbox[2] <= height*4
                                for left,right in zip(row,row[1:]))
                        and not any(line.bbox in (table_labels or {}).get(raw.pno, ()) for line in row)):
                    small_margin_rows.append((raw, side, row, row_box,
                                              re.sub(r'[^a-z]', '', row_text.lower())))
                # OCR may put the outer folio in a separate source row. Analyse
                # that physical title/folio band together, but retain only the
                # original source occurrences if the spread template proves it.
                printed_edge = (_LEADING_PRINTED_FOLIO if side == 0
                                else _TRAILING_PRINTED_FOLIO).fullmatch(row_text)
                row_edge = row_spans[0] if side == 0 else row_spans[-1]
                if (len(row) > 1 and printed_edge and not keyed
                        and not (row_edge.uncertain or row_edge.transcription_uncertain
                                 or row_edge.encoding_unresolved)
                        and raw.source_geometry.get('space') == 'reading'
                        and all(span.font == 'ocr' for span in row_spans)
                        and row_letters and sum(c.isupper() for c in row_letters) >= len(row_letters)*.65
                        and not any(any(c.islower() for c in span.text) and not (
                            span.uncertain or span.transcription_uncertain or span.encoding_unresolved)
                            for span in row_spans)
                        and not CAPTION_LINE.match(row_text) and '°' not in row_text
                        and not any(line.bbox in (table_labels or {}).get(raw.pno, ()) for line in row)
                        and row_box[3]-row_box[1] <= height*1.25
                        and row_box[2]-row_box[0] <= raw.width*.32
                        and after_row and min(line.bbox[1] for line in after_row)-row_box[3] >= height):
                    joined = []
                    for part in row:
                        joined.extend(part.spans[:-1])
                        joined.append(replace(part.spans[-1], text=part.spans[-1].text+' '))
                    first = replace(first, spans=joined, bbox=row_box)
                    grouped_rows[id(first)] = row
                    remaining = after_row
                outer_alignment = min(abs(first.bbox[0]-min(line.bbox[0] for line in remaining)),
                                      abs(first.bbox[2]-max(line.bbox[2] for line in remaining)))
                alignments = [abs(first.bbox[0]-x0), abs(first.bbox[2]-x1)]
                if keyed:
                    alignments.extend((abs(first.bbox[0]-min(line.bbox[0] for line in remaining)),
                                       abs(first.bbox[2]-max(line.bbox[2] for line in remaining))))
                letters = re.sub(r'[^a-z]', '', first.stripped.lower())
                leading = _LEADING_PRINTED_FOLIO.fullmatch(first.stripped)
                trailing = _TRAILING_PRINTED_FOLIO.fullmatch(first.stripped)
                folio = int(leading[1]) if leading else int(trailing[2]) if trailing else None
                folio_offset = folio-2*raw.pno-side if folio is not None else None
                edge_span = first.spans[0] if side == 0 else first.spans[-1]
                damaged_edge = (edge_span.uncertain and
                                re.fullmatch(r'[A-Za-z0-9]{1,5}', edge_span.text.strip()) and
                                any(ch.isdigit() for ch in edge_span.text))
                has_edge = folio is not None or damaged_edge
                needs_folio = first.bbox[3] > raw.height*.08 or first.bbox[3]-first.bbox[1] > height*.75
                if (len(letters) < 6 or len(first.stripped) > BAND_TEXT_MAX or CAPTION_LINE.match(first.stripped)
                        or first.bbox[3] > raw.height*(.12 if has_edge else .08)
                        or first.bbox[3]-first.bbox[1] > height*(1.25 if has_edge else .75)
                        or first.bbox[2]-first.bbox[0] > raw.width*.32 or not remaining
                        or min(line.bbox[1] for line in remaining)-first.bbox[3] < height*.5
                        or min(alignments) > raw.width*.03):
                    continue
                candidates.append((raw, first, side, letters, keyed,
                                   keyed and outer_alignment > raw.width*.03,folio_offset,needs_folio))
                measured[(raw.pno, id(first))] = (height,
                    min(line.bbox[1] for line in remaining)-first.bbox[3],
                    folio is not None and not (edge_span.uncertain or
                        edge_span.transcription_uncertain or edge_span.encoding_unresolved))
    proved = defaultdict(list)
    vetoes = []
    for raw, line, side, letters, keyed, interior_label, folio_offset, needs_folio in candidates:
        local = [(other, candidate, text, row_keys, interior)
                 for other, candidate, panel, text, row_keys, interior, offset, _ in candidates
                 if panel == side and abs(other.pno-raw.pno) <= 12
                 and (not needs_folio or offset==folio_offset)
                 and abs(candidate.bbox[1]/other.height-line.bbox[1]/raw.height) <= .02
                 and abs(candidate.bbox[0]/other.width-line.bbox[0]/raw.width) <= .025
                 and abs((candidate.bbox[2]-candidate.bbox[0])/other.width-
                         (line.bbox[2]-line.bbox[0])/raw.width) <= .045]
        # Missing OCR row keys cannot turn that table into a prose witness.
        # A locally repeated label aligned to an interior description column
        # is positive contrary evidence, even on its apparently unkeyed peers.
        contradicted = any(interior and SequenceMatcher(None,letters,text,autojunk=False).ratio() >= .55
                           for _,_,text,_,interior in local)
        peers = [(other,candidate,text) for other,candidate,text,row_keys,_ in local if not row_keys]
        matching = {other.pno for other, _, text in peers
                    if SequenceMatcher(None, letters, text, autojunk=False).ratio() >= .55}
        # A badly damaged reading may not resemble its peers. Five matching
        # marginal placements plus a three-leaf repeated label establish the
        # local printed template without correcting that occurrence's words.
        template = len({other.pno for other, _, _ in peers}) >= 5 and any(
            len({other.pno for other, _, text in peers
                 if SequenceMatcher(None, anchor, text, autojunk=False).ratio() >= .55}) >= 3
            for _, _, anchor in peers)
        numbered = sorted(matching)
        progression = not needs_folio or any(raw.pno in numbered[i:i+3]
            and numbered[i+1]-numbered[i]==numbered[i+2]-numbered[i+1]
            and numbered[i+1]-numbered[i] in (1,2) for i in range(len(numbered)-2))
        if not contradicted and progression and (len(matching) >= 3 or template):
            proved[raw.pno].append(line.bbox)
        elif keyed or contradicted:
            vetoes.append((raw,line,letters))
    # A short chapter can print its running label on only one leaf. The
    # opposing recurrent head and consecutive outer folios establish the
    # physical spread template without requiring that unique label to repeat.
    # Use only the independently proved heads above; new pairs cannot bootstrap
    # each other. All candidates still have measured wrapped prose and gutters.
    original={pno:{tuple(box) for box in boxes} for pno,boxes in proved.items()}
    vetoed={(raw.pno,tuple(line.bbox)) for raw,line,_ in vetoes}
    # Chapter openings can interrupt the eligible running-head sequence. A
    # smaller, well-separated margin row can still have a measured two-sided
    # template: three other reliable folios on each physical side establish
    # the same offset, and the opposing title recurs independently. Uncertain
    # edge ink can use this placement proof, but never teaches a folio reading.
    for raw,line,side,letters,keyed,interior,offset,_ in candidates:
        height,gap,reliable = measured[(raw.pno,id(line))]
        if (keyed or interior or (raw.pno,tuple(line.bbox)) in vetoed
                or line.bbox[3]-line.bbox[1] > 1.25*height or gap < height
                or any(tuple(box)==tuple(line.bbox)
                       for box in (table_labels or {}).get(raw.pno,[]))):
            continue
        peers = []
        for other,head,panel,text,row_keys,inside,other_offset,_ in candidates:
            other_height,other_gap,trust = measured[(other.pno,id(head))]
            edge = 0 if panel == 0 else 2
            if (other.pno == raw.pno or abs(other.pno-raw.pno)>12 or row_keys or inside
                    or head.bbox[3]-head.bbox[1] > 1.25*other_height or other_gap < other_height
                    or abs(head.bbox[edge]/other.width-line.bbox[0 if side==0 else 2]/raw.width)
                        > (.025 if panel==side else 1)
                    or abs(head.bbox[1]/other.height-line.bbox[1]/raw.height)>.02
                    or (panel==side and abs((head.bbox[2]-head.bbox[0])/other.width-
                        (line.bbox[2]-line.bbox[0])/raw.width)>.045)
                    or any(tuple(box)==tuple(head.bbox)
                           for box in (table_labels or {}).get(other.pno,[]))):
                continue
            peers.append((other,head,panel,text,other_offset,trust))
        offsets = {off for _,_,panel,_,off,trust in peers if trust and panel==side}
        qualified = []
        for off in offsets:
            if reliable and off != offset:
                continue
            if any(len({other.pno for other,_,panel,_,o,trust in peers
                        if panel==which and trust and o==off})<3 for which in (0,1)):
                continue
            opposite = [text for _,_,panel,text,o,trust in peers
                        if panel!=side and trust and o==off]
            matching = {other.pno for other,_,panel,text,o,_ in peers
                        if panel==side and o==off and
                        SequenceMatcher(None,letters,text,autojunk=False).ratio()>=.55}
            # The target and two independent matching leaves establish an
            # authored-title template; folio placement still needs three other
            # reliable leaves on each side.
            if len(matching)<2 and not any(
                    sum(SequenceMatcher(None,anchor,text,autojunk=False).ratio()>=.55
                        for text in opposite)>=3 for anchor in opposite):
                continue
            following = [ln for block in raw.text_blocks for ln in block.lines
                         if ln.stripped and ln.bbox[1]>=line.bbox[3]
                         and ((ln.bbox[0]+ln.bbox[2])/2<raw.width*.5 if side==0
                              else (ln.bbox[0]+ln.bbox[2])/2>raw.width*.5)]
            if len(matching)<2 and id(line) not in grouped_rows and (not following or
                    not min(following,key=lambda ln:ln.bbox[1]).stripped[:1].islower()):
                continue
            qualified.append(off)
        if len(qualified)==1 and line.bbox not in proved[raw.pno]:
            proved[raw.pno].append(line.bbox)
    paired=[]
    for row in candidates:
        raw,line,side,letters,keyed,interior,offset,_=row
        if keyed or interior or offset is None or (raw.pno,tuple(line.bbox)) in vetoed:continue
        if not (_LEADING_PRINTED_FOLIO.fullmatch(line.stripped) if side==0
                else _TRAILING_PRINTED_FOLIO.fullmatch(line.stripped)):continue
        opposites=[other for other in candidates if other[0].pno==raw.pno
                   and other[2]!=side and other[6]==offset and not other[4] and not other[5]
                   and tuple(other[1].bbox) in original.get(raw.pno,set())]
        if len(opposites)!=1:continue
        opposite=opposites[0];a,b=line.bbox,opposite[1].bbox
        if abs((a[1]+a[3]-b[1]-b[3])/2)>.8*max(a[3]-a[1],b[3]-b[1]):continue
        paired.append((row,opposite))
    for row,opposite in paired:
        raw,line,side,letters,keyed,interior,offset,_=row
        if tuple(line.bbox) in original.get(raw.pno,set()):continue
        # A same-band title and an outer folio can resemble a unique head.
        # Only extend the template where this panel actually opens with a
        # lowercase continuation. A fresh paragraph/chapter stays ambiguous.
        following=[ln for block in raw.text_blocks for ln in block.lines
                   if ln.stripped and ln.bbox[1]>=line.bbox[3]
                   and ((ln.bbox[0]+ln.bbox[2])/2<raw.width*.5 if side==0
                        else (ln.bbox[0]+ln.bbox[2])/2>raw.width*.5)]
        if not following or not min(following,key=lambda ln:ln.bbox[1]).stripped[:1].islower():continue
        peers=[]
        for candidate,other_head in paired:
            other,head,panel,_,_,_,other_offset,_=candidate
            if (panel!=side or other_offset!=offset or abs(other.pno-raw.pno)>8
                    or SequenceMatcher(None,opposite[3],other_head[3],autojunk=False).ratio()<.55):continue
            edge=0 if side==0 else 2
            if (abs(head.bbox[edge]/other.width-line.bbox[edge]/raw.width)>.025
                    or abs(head.bbox[1]/other.height-line.bbox[1]/raw.height)>.02
                    or abs((head.bbox[3]-head.bbox[1])/other.height-
                           (line.bbox[3]-line.bbox[1])/raw.height)>.008):continue
            peers.append(other.pno)
        pages=sorted(set(peers))
        if any(raw.pno in pages[i:i+3] and pages[i+1]-pages[i]==pages[i+2]-pages[i+1]
               and pages[i+1]-pages[i] in (1,2) for i in range(len(pages)-2)):
            proved[raw.pno].append(line.bbox)
    if table_labels is not None:
        for raw,line,letters in vetoes:
            for other in raw_pages:
                if not other.is_page_scan or abs(other.pno-raw.pno)>12:continue
                for block in other.text_blocks:
                    for candidate in block.lines:
                        if (abs(candidate.bbox[1]/other.height-line.bbox[1]/raw.height)<=.02
                                and abs(candidate.bbox[0]/other.width-line.bbox[0]/raw.width)<=.025
                                and abs((candidate.bbox[2]-candidate.bbox[0])/other.width-
                                        (line.bbox[2]-line.bbox[0])/raw.width)<=.045
                                and SequenceMatcher(None,letters,re.sub(r'[^a-z]','',candidate.stripped.lower()),
                                                    autojunk=False).ratio()>=.55):
                            boxes=table_labels.setdefault(other.pno,[])
                            if candidate.bbox not in boxes:boxes.append(candidate.bbox)
    # A chart can replace one panel of an otherwise established spread. It
    # cannot teach a prose template, but its same-band title/edge row can match
    # two independently measured prose spreads and a three-leaf repeated title.
    margin_targets = []
    folio_margin_targets = []
    for raw in raw_pages:
        with unit_recovery.guard(raw.pno, "native_style_source_metrics"):
            if not raw.is_page_scan or raw.width < raw.height*1.2:
                continue
            lines = [line for block in raw.text_blocks for line in block.lines if line.stripped]
            prose = [line for line in lines if len(line.stripped.split()) >= 6
                     and raw.width*.25 <= line.bbox[2]-line.bbox[0] <= raw.width*.45
                     and raw.height*.05 < line.bbox[3] < raw.height*.85]
            if len(prose) < 8 or any(line.bbox[0] < raw.width*.48
                                    and line.bbox[2] > raw.width*.52 for line in lines):
                continue
            alphabetic_sizes = [span.size for line in prose for span in line.spans
                                for word in span.text.split() if any(char.isalpha() for char in word)]
            if not alphabetic_sizes:
                continue
            em = median(alphabetic_sizes)
            for side in (0,1):
                members = [line for line in lines if
                           ((line.bbox[0]+line.bbox[2])/2 < raw.width*.5) == (side == 0)]
                if not members:
                    continue
                first = min(members,key=lambda line:line.bbox[1])
                row = sorted([line for line in members if
                              abs((line.bbox[1]+line.bbox[3]-first.bbox[1]-first.bbox[3])/2)
                              <= em*.5],key=lambda line:line.bbox[0])
                spans = [span for line in row for span in line.spans if span.text.strip()]
                text = ' '.join(line.stripped for line in row)
                letters = ''.join(char for char in text if char.isalpha())
                box = (min(line.bbox[0] for line in row),min(line.bbox[1] for line in row),
                       max(line.bbox[2] for line in row),max(line.bbox[3] for line in row))
                sizes = [span.size for span in spans for char in span.text if char.isalpha()]
                following = [line for line in members if line not in row]
                printed = (_LEADING_PRINTED_FOLIO if side == 0
                           else _TRAILING_PRINTED_FOLIO).fullmatch(text)
                edge = spans[0] if side == 0 else spans[-1]
                glyph_heights = [span.bbox[3]-span.bbox[1] for span in spans
                                 if any(c.isalpha() for c in span.text)]
                # A separately extracted reliable folio can prove a unique short
                # running title in a list panel. This panel never teaches prose;
                # the ordinary wrapped-prose spreads supply the folio template.
                if (len(row) > 1 and printed and not (edge.uncertain or
                        edge.transcription_uncertain or edge.encoding_unresolved)
                        and raw.source_geometry.get('space') == 'reading'
                        and all(span.font == 'ocr' for span in spans)
                        and letters and sum(c.isupper() for c in letters) >= len(letters)*.65
                        and not any(any(c.islower() for c in span.text) and not (
                            span.uncertain or span.transcription_uncertain or span.encoding_unresolved)
                            for span in spans)
                        and len(text) <= BAND_TEXT_MAX and sizes and glyph_heights
                        and median(sizes) <= em*1.1 and median(glyph_heights) <= em*.8
                        and box[3] <= raw.height*.12 and box[3]-box[1] <= em*1.25
                        and box[2]-box[0] <= raw.width*.32
                        and (box[0] <= raw.width*.14 if side == 0 else box[2] >= raw.width*.86)
                        and following and min(line.bbox[1] for line in following)-box[3] >= em
                        and not CAPTION_LINE.match(text) and '°' not in text
                        and not any(line.bbox in (table_labels or {}).get(raw.pno, ()) for line in row)):
                    folio_margin_targets.append((raw,side,row,box,
                        int(printed[1] if side == 0 else printed[2])-2*raw.pno-side))
                if (len(letters) < 6 or len(text) > BAND_TEXT_MAX or not sizes
                        or box[3] > raw.height*HEADER_BAND or box[3]-box[1] > em*.8
                        or box[2]-box[0] > raw.width*.32 or median(sizes) > em*.75
                        or (box[0] > raw.width*.12 if side == 0 else box[2] < raw.width*.88)
                        or sum(char.isupper() for char in letters) < len(letters)*.65
                        or not any(span.uncertain or span.transcription_uncertain
                                   or span.encoding_unresolved for span in spans)
                        or any(any(char.islower() for char in span.text) and not (
                            span.uncertain or span.transcription_uncertain
                            or span.encoding_unresolved) for span in spans)
                        or CAPTION_LINE.match(text) or '°' in text or not following
                        or min(line.bbox[1] for line in following)-box[3] < (box[3]-box[1])*.5
                        or any(not em*.3 <= right.bbox[0]-left.bbox[2] <= em*4
                               for left,right in zip(row,row[1:]))
                        or any(line.bbox in (table_labels or {}).get(raw.pno, ()) for line in row)):
                    continue
                margin_targets.append((raw,side,row,box,re.sub(r'[^a-z]', '',text.lower())))
    for raw,side,row,box,letters in margin_targets:
        def matches(other,panel,parts,other_box,text):
            return (panel == side and abs(other.pno-raw.pno) <= 12
                    and abs(other_box[1]/other.height-box[1]/raw.height) <= .02
                    and abs(other_box[0]/other.width-box[0]/raw.width) <= .025
                    and abs((other_box[2]-other_box[0])/other.width-
                            (box[2]-box[0])/raw.width) <= .045
                    and SequenceMatcher(None,letters,text,autojunk=False).ratio() >= .55
                    and not any(line.bbox in (table_labels or {}).get(other.pno, ()) for line in parts))
        peers = {other.pno for other,panel,parts,other_box,text in margin_targets
                 if matches(other,panel,parts,other_box,text)}
        teachers = {other.pno for other,panel,parts,other_box,text in small_margin_rows
                    if matches(other,panel,parts,other_box,text)}
        leading = _LEADING_PRINTED_FOLIO.fullmatch(' '.join(line.stripped for line in row))
        trailing = _TRAILING_PRINTED_FOLIO.fullmatch(' '.join(line.stripped for line in row))
        edge = row[0].spans[0] if side == 0 else row[-1].spans[-1]
        # Reliable numeric ink cannot override a contrary folio progression.
        numeric = leading if side == 0 else trailing
        if numeric and not edge.uncertain:
            offset = int(numeric[1] if side == 0 else numeric[2])-2*raw.pno-side
            agreeing = set()
            for other,panel,parts,other_box,text in small_margin_rows:
                if not matches(other,panel,parts,other_box,text):
                    continue
                printed = ' '.join(line.stripped for line in parts)
                marker = (_LEADING_PRINTED_FOLIO if side == 0 else _TRAILING_PRINTED_FOLIO).fullmatch(printed)
                if marker and int(marker[1] if side == 0 else marker[2])-2*other.pno-side == offset:
                    agreeing.add(other.pno)
            teachers &= agreeing
        if len(peers) >= 3 and len(teachers) >= 2:
            for line in row:
                if line.bbox not in proved[raw.pno]:
                    proved[raw.pno].append(line.bbox)
    for raw,side,row,box,offset in folio_margin_targets:
        peers = [(other,head,panel,text,off) for other,head,panel,text,keyed,inside,off,_ in candidates
                 if other.pno != raw.pno and abs(other.pno-raw.pno) <= 12
                 and not keyed and not inside and off == offset
                 and measured[(other.pno,id(head))][2]
                 and abs(head.bbox[1]/other.height-box[1]/raw.height) <= .02
                 and not any(head.bbox == bound for bound in (table_labels or {}).get(other.pno,()))]
        if any(len({other.pno for other,_,panel,_,_ in peers if panel == which}) < 3
               for which in (0,1)):
            continue
        opposite = [text for _,_,panel,text,_ in peers if panel != side]
        if not any(sum(SequenceMatcher(None,anchor,text,autojunk=False).ratio() >= .55
                       for text in opposite) >= 3 for anchor in opposite):
            continue
        for line in row:
            if line.bbox not in proved[raw.pno]:
                proved[raw.pno].append(line.bbox)
    for raw,line,*_ in candidates:
        if id(line) in grouped_rows and line.bbox in proved.get(raw.pno,[]):
            proved[raw.pno].remove(line.bbox)
            for original_line in grouped_rows[id(line)]:
                if original_line.bbox not in proved[raw.pno]:
                    proved[raw.pno].append(original_line.bbox)
    return dict(proved)


def _source_style_profile(helper, raw_pages, *args, **kwargs):
    """An optional aggregate heuristic never owns an entire source book."""
    controller = unit_recovery.current()
    if controller is None:return helper(raw_pages, *args, **kwargs)
    from copy import deepcopy
    copied = deepcopy(kwargs)
    try:
        result = helper(raw_pages, *args, **copied)
    except unit_recovery.UnitFailure:
        raise
    except Exception as error:
        if unit_recovery.fatal(error):raise
        pieces = []
        admitted = []
        for raw in raw_pages:
            local = deepcopy(kwargs)
            with unit_recovery.guard(raw.pno, 'native_style_profile'):
                value = helper([deepcopy(raw)], *deepcopy(args), **local)
                if isinstance(value, dict):
                    if any(key != raw.pno for key in value):
                        raise ValueError('Style profile crosses its isolated source page')
                elif isinstance(value, list):
                    if any(not isinstance(item, dict) or item.get('page') != raw.pno for item in value):
                        raise ValueError('Style profile has no isolated source owner')
                else:raise ValueError('Unsupported style profile result')
            pieces.append(value);admitted.append(local)
        result = [] if pieces and isinstance(pieces[0], list) else {}
        for piece in pieces:
            if isinstance(result, list):result.extend(piece)
            else:result.update(piece)
        for key, original in kwargs.items():
            if isinstance(original, dict):
                for local in admitted:original.update(local[key])
        controller.events.append(dict(stage='native_style_profile', transition='DETERMINISTIC_SOURCE_REPAIR',
            reason=type(error).__name__, proof='Only independently verified per-page heuristic entries retained; no cross-page owner guessed'))
        return result
    for key, original in kwargs.items():
        if isinstance(original, dict):original.clear();original.update(copied[key])
    return result


def book_style(raw_pages, outline=None):
    return _book_style(raw_pages, outline)


def _book_style(raw_pages, outline=None):
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
        with unit_recovery.guard(raw.pno, "native_style_source_metrics"):
            top, bottom = raw.height * HEADER_BAND, raw.height * FN_ZONE_NUMBERED
            for blk in raw.text_blocks:
                for ln in blk.lines:
                    chars = len(ln.stripped)
                    if not chars:
                        continue
                    size = ln.size
                    if unit_recovery.current() and (not isinstance(size, (int,float)) or not math.isfinite(size)):
                        raise ValueError('Native style source size is not finite')
                    everything[size] += chars
                    if ln.bbox[3] > top and ln.bbox[1] < bottom:
                        central[size] += chars
                        pages.setdefault(size, set()).add(raw.pno)
                    if ln.bbox[3] <= top or ln.bbox[1] >= raw.height * FOOTER_BAND:
                        if len(ln.stripped) <= BAND_TEXT_MAX:
                            bands[band_key(ln.stripped)] += 1

    census = central or everything
    body_size = census.most_common(1)[0][0] if census else 0.0

    ladder = _source_heading_ladder(body_size, census, pages, len(raw_pages))

    table_labels = {}
    spread_heads = _source_style_profile(_repeated_scan_spread_heads,raw_pages,table_labels=table_labels)
    paired_heads = _source_style_profile(_paired_margin_head_profile,raw_pages,body_size)
    repeated_heads = _source_style_profile(_repeated_detached_heads,raw_pages,body_size)
    for pno, boxes in paired_heads.items():
        for box in boxes:
            if box not in repeated_heads.setdefault(pno,[]):
                repeated_heads[pno].append(box)
    from .portrait_heads import boxes as portrait_head_boxes
    return BookStyle(body_size=body_size, ladder=ladder, band_hits=dict(bands),
                     page_count=len(raw_pages), outline=list(outline or []),
                     folio_boxes=_source_style_profile(_sequence_folios,raw_pages,body_size),
                     contents_folio_boxes=_source_style_profile(_repeated_contents_folios,raw_pages,body_size),
                     local_running_boxes=_source_style_profile(_local_running_folios,raw_pages,body_size),
                     repeated_head_boxes=repeated_heads,
                     paired_margin_head_boxes=paired_heads,
                     portrait_head_boxes=_source_style_profile(portrait_head_boxes,raw_pages,body_size),
                     scan_spread_head_boxes=spread_heads, scan_table_label_boxes=table_labels,
                     scan_note_lane_boxes=_source_style_profile(_repeated_scan_note_lanes,raw_pages,body_size,table_labels),
                     scan_note_footer_profiles=_source_style_profile(_scan_note_footer_profiles,raw_pages,body_size,table_labels))


# ------------------------------------------------------------------ heading vetoes

def looks_like_chart_junk(text):
    """Reject sparse diagram glyphs that exceed real headings in apparent size.

    Large isolated symbols and figure keys are not section headings merely
    because their measured type size is large."""
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
    """Classify a prospective heading token as a word, number or debris.

    OCR fragments from a diagram or table do not become heading words."""
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


#: Reject truncated prose as a heading when its final word requires a
#: continuation.
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
        # A heading begins at a source sentence boundary; lowercase
        # continuations remain prose.
        return False
    last = _TRAILING_NON_WORD.sub("", text).rsplit(" ", 1)[-1].lower()
    if last in _NOT_A_LAST_WORD:
        return False
    return not looks_like_chart_junk(text)


def continues_lowercase(line):
    text = line.stripped
    return bool(text) and text[0].islower()


def heading_ish(line, style):
    """Judge a heading by source typography and the book heading ladder.

    Bold weight can remain useful when OCR sizes wobble around the body size.
    Small diagram labels below the body floor are a separate typographic rung."""
    if not style.body_size:
        return False
    size = line.size
    if line.bold:
        return size >= style.body_size * (1.0 - LADDER_TOL)
    return size >= style.body_size * HEAD_RATIO and style.on_the_ladder(size)


# ----------------------------------------------------------------------- the skeleton

def _embedded_source_mark(image, raw, kept_blocks):
    """Keep a wide, shallow raster in a sparse centered title composition.

    The PDF has pixels here but no Unicode atoms. Size alone previously dropped
    printed imprints. Sparse page structure and interior placement separate this
    source mark from narrow running furniture and small images in dense prose.
    Its words remain pixels; no transcription is inferred from the image.
    """
    if raw.is_page_scan or image.full_page or getattr(image, 'page_background', False):
        return False
    if not (image.width >= max(extract.MIN_FIG_PX, raw.width * .24)
            and 8 <= image.height < extract.MIN_FIG_PX
            and image.width >= image.height * 6):
        return False
    x0, y0, x1, y1 = image.bbox
    if not (abs((x0 + x1) / 2 - raw.width / 2) <= raw.width * .15
            and raw.height * .12 <= (y0 + y1) / 2 <= raw.height * .8):
        return False
    lines = [line for _, group in kept_blocks for line in group]
    if not 2 <= len(lines) <= 8:
        return False
    above = [line for line in lines if line.bbox[3] < y0
             and abs((line.bbox[0] + line.bbox[2]) / 2 - raw.width / 2)
             <= raw.width * .18]
    return len(above) >= 2


_ORDINAL_ROW = re.compile(r'^\s*\d{1,3}(?:st|nd|rd|th)(?=\s|[:.])', re.I)


def _uncertain_aligned_scan_list(raw, kept_blocks):
    """Own an OCR list as one image when its measured labels disagree.

    This recognizes the physical row object, not any particular word or book.
    A fully legible numbered list stays text; a few plausible OCR labels do
    not license filling in the others from a sequence.
    """
    if not raw.is_page_scan or raw.source_geometry.get('space') != 'reading':
        return None
    lines = [line for _, group in kept_blocks for line in group
             if line.stripped and len(line.stripped.split()) <= 4
             and line.bbox[2]-line.bbox[0] < raw.width*.24]
    best = []
    for seed in lines:
        aligned = sorted((line for line in lines
                          if abs(line.bbox[0]-seed.bbox[0]) <= 3),
                         key=lambda line: line.bbox[1])
        group = []
        for line in aligned:
            if group and line.bbox[1]-group[-1].bbox[1] > raw.height*.04:
                if len(group) > len(best): best = group
                group = []
            group.append(line)
        if len(group) > len(best): best = group
    if len(best) < 10:
        return None
    numbered = sum(bool(_ORDINAL_ROW.match(line.stripped)) for line in best)
    if numbered < len(best)*.5 or numbered == len(best):
        return None
    box = _lines_bbox(best, best[0].bbox)
    pad = max(4.0, min(8.0, raw.height*.012))
    return ((max(0,box[0]-pad), max(0,box[1]-pad),
             min(raw.width,box[2]+pad),min(raw.height,box[3]+pad)), best)


def _coalesce_nested_scan_list_figures(raw, skel):
    """A list already inside a source crop has one primary image owner.

    The measured list keeps its artwork lines for source accounting. A wider
    scan figure can also be inferred from the prose gap around that list; when
    it contains the list's crop, emitting both repeats the same printed rows.
    Keep that wider source region, including any immediately printed heading.
    """
    if not raw.is_page_scan:
        return
    figures = [region for region in skel.regions if region.kind == 'figure']
    for narrow in figures:
        if narrow.reason != 'uncertain_aligned_scan_list':
            continue
        x0, y0, x1, y1 = narrow.bbox
        carriers = [region for region in figures if region is not narrow
                    and region.reason in ('ocr_uncertain_region', 'scan_figure_band')
                    and region.bbox[2]-region.bbox[0] < raw.width*.75
                    and region.bbox[0] <= x0+1 and region.bbox[1] <= y0+1
                    and region.bbox[2] >= x1-1 and region.bbox[3] >= y1-1
                    and (region.bbox[2]-region.bbox[0]) *
                        (region.bbox[3]-region.bbox[1]) >
                        1.5 * (x1-x0) * (y1-y0)]
        if len(carriers) != 1:
            continue
        carrier = carriers[0]
        a, b, c, d = carrier.bbox
        carrier.bbox = (min(a,x0), min(b,y0), max(c,x1), max(d,y1))
        carrier.reason = 'uncertain_aligned_scan_list'
        skel.regions.remove(narrow)


def _sparse_scan_spread_panels(raw):
    """Split a proven two-up scan with independent sparse printed panels.

    The OCR may entirely omit a narrow page-number column. Each crop therefore
    reaches its panel's outer paper edge; its text is counted once as artwork.
    Paired cross-gutter rows and full-width headings veto the split.
    """
    if (not raw.is_page_scan or raw.source_geometry.get('space') != 'reading'
            or raw.width < raw.height*1.2):
        return None
    lines = [line for block in raw.text_blocks for line in block.lines if line.stripped]
    if len(lines) < 16:
        return None
    intervals = sorted((line.bbox[0], line.bbox[2]) for line in lines)
    merged = []
    for left, right in intervals:
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
        else:
            merged.append((left,right))
    gaps = [(b[0]-a[1], (a[1]+b[0])/2) for a,b in zip(merged,merged[1:])
            if raw.width*.3 < (a[1]+b[0])/2 < raw.width*.7]
    if not gaps:
        return None
    gap, split = max(gaps)
    if gap < raw.width*.07:
        return None
    left = [line for line in lines if line.bbox[2] < split]
    right = [line for line in lines if line.bbox[0] > split]
    if len(left)+len(right) != len(lines) or min(len(left),len(right)) < 6:
        return None
    short = lambda group: sum(len(line.stripped.split()) <= 5 for line in group)
    if max(short(left),short(right)) < 12 or max(short(left)/len(left),short(right)/len(right)) < .7:
        return None
    paired = sum(any(abs((line.bbox[1]+line.bbox[3])-
                         (peer.bbox[1]+peer.bbox[3])) < raw.height*.016
                     for peer in right) for line in left)
    if paired >= min(len(left),len(right))*.6:
        return None
    panels = []
    for x0,x1,group in ((0,split,left),(split,raw.width,right)):
        top = max(0,min(line.bbox[1] for line in group)-12)
        bottom = min(raw.height,max(line.bbox[3] for line in group)+12)
        panels.append(((x0,top,x1,bottom),group))
    return panels


def _uncertain_scan_key_panel(raw):
    """Keep a scanned symbol key's printed relationships in one source crop.

    Recognition often groups a column of symbols into a few tall, uncertain
    lines while reading the adjacent labels as many independent short lines.
    Reflowing either column separately destroys the row associations. OCR
    bounds cannot establish how far an unreadable mark extends, so the source
    owner is the complete page even when all recognized lines form one panel.
    This requires both geometries and refuses pages with independent prose.
    """
    if not raw.is_page_scan:
        return None
    lines = [ln for block in raw.text_blocks for ln in block.lines if ln.stripped]
    if len(lines) < 12:
        return None
    strips = [ln for ln in lines if (ln.bbox[2]-ln.bbox[0] <= raw.width*.035
              and ln.bbox[3]-ln.bbox[1] >= raw.height*.05
              and all(sp.font == 'ocr' and sp.uncertain for sp in ln.spans))]
    if len(strips) < 2:
        return None
    groups = []
    for strip in sorted(strips, key=lambda ln: ln.bbox[0]):
        group = next((group for group in groups
                      if abs(group[0].bbox[0]-strip.bbox[0]) <= raw.width*.025), None)
        if group is None:
            groups.append([strip])
        else:
            group.append(strip)
    matches = []
    for group in groups:
        left = min(ln.bbox[0] for ln in group)
        right = max(ln.bbox[2] for ln in group)
        top = min(ln.bbox[1] for ln in group)
        bottom = max(ln.bbox[3] for ln in group)
        if bottom-top < raw.height*.35:
            continue
        adjacent = [ln for ln in lines if ln not in group
                    and right < ln.bbox[0] <= right+raw.width*.11
                    and top-8 <= (ln.bbox[1]+ln.bbox[3])/2 <= bottom+8
                    and ln.bbox[3]-ln.bbox[1] < raw.height*.045]
        if len(adjacent) < 9:
            continue
        starts = [ln.bbox[0] for ln in adjacent]
        aligned = [ln for ln in adjacent
                   if abs(ln.bbox[0]-median(starts)) <= raw.width*.025
                   and len(ln.stripped.split()) <= 5]
        if len(aligned) < 9 or len(aligned) < len(adjacent)*.7:
            continue
        panel = [ln for ln in lines if ln.bbox[0] >= left-raw.width*.025
                 and ln.bbox[1] >= top-raw.height*.12
                 and ln.bbox[3] <= bottom+raw.height*.04]
        if (len(panel) < len(lines)*.75 or
                any(len(ln.stripped.split()) > 12 or
                    ln.bbox[2]-ln.bbox[0] > raw.width*.5 for ln in panel)):
            continue
        # A second independent text area makes panel ownership uncertain.
        if any(ln not in panel for ln in lines):
            continue
        matches.append(((0, 0, raw.width, raw.height), panel))
    return matches[0] if len(matches) == 1 else None


def _complete_captioned_scan_top(raw, skel, pixel_probe):
    """Recover source ink immediately above an OCR-bounded chart and caption.

    OCR geometry can begin at the first label below a chart's upper rim. A
    neighboring printed caption and a tall crop identify the object; the
    preceding figure and a short source-ink query bound any extension.
    """
    if not raw.is_page_scan or pixel_probe is None:
        return
    captions = [line for block in raw.text_blocks for line in block.lines
                if _squashed_caption(line.stripped)]
    figures = [r for r in skel.regions if r.kind == 'figure']
    for figure in figures:
        x0,y0,x1,y1 = figure.bbox
        if y1-y0 < raw.height*.2 or not any(
                0 <= caption.bbox[1]-y1 <= 6 and
                x0 <= (caption.bbox[0]+caption.bbox[2])/2 <= x1
                for caption in captions):
            continue
        previous = max((r.bbox[3] for r in figures if r is not figure
                        and r.bbox[3] <= y0 and r.bbox[0] < x1
                        and x0 < r.bbox[2]), default=0)
        top = max(previous+2, y0-min(18,raw.height*.035))
        if top >= y0-3:
            continue
        try:
            ink = pixel_probe.ink_bounds((x0,top,x1,y0+2))
        except (ValueError,RuntimeError,AttributeError):
            continue
        if ink and ink[1] < y0-2:
            figure.bbox = (x0,max(top,ink[1]-2),x1,y1)


def split_scan_note_columns(raw, style):
    """Separate source spans when OCR joins a spread note to another column.

    Keep every literal span and its measured box. Only a raised short opening
    in the lower scan margin qualifies; a large measured horizontal gap, not
    a word reading, separates the note from a neighboring caption or label.
    Returning a new source page lets source inventory account for each fragment.
    """
    if (not raw.is_page_scan or getattr(raw,'source_geometry',{}).get('space')!='reading'
            or raw.width < raw.height*1.2 or not style.body_size):
        return raw
    blocks=[];changed=False
    for block in raw.blocks:
        groups=[]
        for line in block.lines:
            spans=[s for s in line.spans if s.text.strip()]
            eligible=(line.bbox[1]>=raw.height*.8 and line.bbox[2]-line.bbox[0]>=raw.width*.5
                and len(spans)>=2 and len(spans[0].text.strip())<=3
                and spans[0].size<=style.body_size*.65
                and spans[1].text.lstrip()[:1].isalpha())
            parts=[[]]
            for span in line.spans:
                if eligible and parts[-1] and span.bbox[0]-max(s.bbox[2] for s in parts[-1])>=raw.width*.15:
                    parts.append([])
                parts[-1].append(span)
            if len(parts)==1:
                groups.append([line]);continue
            changed=True
            for part in parts:
                box=(min(s.bbox[0] for s in part),min(s.bbox[1] for s in part),
                     max(s.bbox[2] for s in part),max(s.bbox[3] for s in part))
                groups.append([replace(line,spans=part,bbox=box)])
        if any(len(group)==1 and group[0] not in block.lines for group in groups):
            blocks.extend(replace(block,lines=group,bbox=_lines_bbox(group,block.bbox)) for group in groups)
        else:blocks.append(block)
    return replace(raw,blocks=blocks) if changed else raw


class _OpaquePixelQueries:
    """Use strict immutable source pixels only for opaque partition authority.

    The original probe keeps its calibrated legacy source measurement methods
    for existing figure/marker callers. All other methods, masks and source
    document geometry remain those of the supplied probe.
    """
    def __init__(self, probe):
        self.probe = probe

    def __getattr__(self, name):
        if name == 'source_has_ink':
            return self.probe.original_has_ink
        if name == 'source_ink_bounds':
            return self.probe.original_ink_bounds
        return getattr(self.probe, name)


def _opaque_pixel_queries(probe):
    if (probe is None or isinstance(probe, _OpaquePixelQueries)
            or not hasattr(probe, 'original_ink_bounds')):
        return probe
    return _OpaquePixelQueries(probe)


def _opaque_short_note_divider(pixel_probe, left, right, bottom, top, em, small):
    """Prove the complete short source stroke when a raised footer is close.

    A clipped bounding box alone cannot establish a rule. Expand below the
    proposed stroke, measure its true thickness and continuity without text
    masks, and require a blank lower boundary so a thicker painted object
    cannot pass by ending at the original query edge.
    """
    pixel_probe = _opaque_pixel_queries(pixel_probe)
    bounds = getattr(pixel_probe, 'source_ink_bounds', None)
    separator = getattr(pixel_probe, 'thin_separator', None)
    if bounds is None or separator is None:
        return None
    try:
        ink = bounds((left-em, bottom+.5, right+em, top-.5))
        if (not ink or not (right-left)*.18 <= ink[2]-ink[0] <= (right-left)*.85
                or abs(ink[0]-left) > em or ink[1]-bottom < em*.5):
            return None
        query = (ink[0]-2, bottom+.5, ink[2]+2, top+min(2, small*.2))
        whole = bounds(query)
        if (not whole or whole[3] >= query[3]-.5
                or whole[1]-bottom < em*.5):
            return None
        proof = separator(query, max_thickness=min(3, em*.2))
        if not proof or proof['occupied_fraction'] < .8:
            return None
        return whole
    except (AttributeError, ValueError, RuntimeError):
        return None


def _source_footer_words(rows):
    """Measure original lexical type without counting expanded whitespace."""
    return [sp for line in rows for sp in line.spans
            if any(c.isalpha() for c in sp.text) and sp.size > 0]


def _opaque_unowned_note_marker(opening, prose, pixel_probe, em, small, left):
    """Measure a compact source mark outside the first lexical word, without reading it."""
    pixel_probe = _opaque_pixel_queries(pixel_probe)
    words = _source_footer_words([opening])
    if not words:
        return None
    first = words[0]
    if first.bbox[0]-left > em*2.5:
        return None
    baseline_edge = max(sp.bbox[3] for sp in words)
    lexical_height = first.bbox[3]-first.bbox[1]
    query = (max(0, min(left-em, first.bbox[0]-em*3)),
             first.bbox[1]-lexical_height*.5, first.bbox[0]-.5,
             baseline_edge+small*.5)
    # Expanded whitespace or a continuation's slightly wider line box is not
    # ink in the marker lane. Only original nonempty span footprints can bound
    # that lane; otherwise a distinct raised mark is clipped by an unrelated
    # lexical row to its right.
    following = [sp.bbox[1] for line in prose if line is not opening
                 for sp in line.spans if sp.text.strip()
                 and sp.bbox[1] > opening.bbox[1]+small*.5
                 and sp.bbox[0] < query[2] and sp.bbox[2] > query[0]]
    if following:
        query = (*query[:3], min(query[3], min(following)-.2))
    if query[2] <= query[0]:
        return None
    try:
        component_query = getattr(pixel_probe, 'source_ink_components', None)
        components = (component_query(query) if component_query else
                      [pixel_probe.source_ink_bounds(query)])
    except (AttributeError, ValueError, RuntimeError):
        return None
    candidates = [mark for mark in components if mark and not (
            not small*.15 <= mark[2]-mark[0] <= small*1.1
            or not small*.15 <= mark[3]-mark[1] <= small*1.25
            or mark[2]-mark[0] > (mark[3]-mark[1])*1.15
            or mark[0] <= query[0] or mark[1] <= query[1]
            or mark[2] >= query[2]-.2 or mark[3] >= query[3]-.2
            or not -small*.4 <= baseline_edge-mark[3] <= small*1.1
            or any(sp.text.strip() and sp.bbox[0] < mark[2] and mark[0] < sp.bbox[2]
                   and sp.bbox[1] < mark[3] and mark[1] < sp.bbox[3]
                   for line in prose for sp in line.spans))]
    if len(candidates) == 1:
        return candidates[0]
    if 2 <= len(candidates) <= 3:
        candidates.sort()
        box = (min(b[0] for b in candidates), min(b[1] for b in candidates),
               max(b[2] for b in candidates), max(b[3] for b in candidates))
        # A compact source prefix can have disconnected strokes. They must
        # form one unique adjacent baseline group within the same whole-mark
        # bounds; no component is decoded, chained to another lane, or allowed
        # to borrow pixels already owned by source text.
        if (box[2]-box[0] <= small*1.1 and box[3]-box[1] <= small*1.25
                and max(b[1] for b in candidates)-min(b[1] for b in candidates) <= small*.15
                and max(b[3] for b in candidates)-min(b[3] for b in candidates) <= small*.15
                and all(0 <= following[0]-previous[2] <= small*.25
                        for previous, following in zip(candidates,candidates[1:]))):
            return box
    return None


def _opaque_note_rule(pixel_probe, left, right, bottom, top, em):
    """Find a complete thin stroke in the original blank gap, excluding clipped ink."""
    pixel_probe = _opaque_pixel_queries(pixel_probe)
    height = min(em*.65, top-bottom-1)
    if height <= 1:
        return None
    y = bottom+.5
    while y+height <= top-.5+.01:
        try:
            query = (left-em, y, right+em, y+height)
            components = getattr(pixel_probe, 'source_ink_components', None)
            footprints = (components(query) if components else
                          [pixel_probe.source_ink_bounds(query)])
            # Extent padding protects retained crops. Separator clearance is
            # measured from the original painted stroke, not that added pad.
            for ink in footprints:
                if not (ink and (right-left)*.18 <= ink[2]-ink[0] <= (right-left)*1.08
                        and abs(ink[0]-left) <= em):
                    continue
                width = ink[2]-ink[0]
                if not any(pixel_probe.source_has_ink((ink[0]+width*.1, edge-.2,
                           ink[2]-width*.1, edge+.2)) for edge in (y, y+height)):
                    proof = pixel_probe.thin_separator((ink[0]-1, y, ink[2]+1, y+height),
                        max_thickness=min(3, em*.2))
                    if proof and proof.get('occupied_fraction', 0) >= .8:
                        return ink
        except (AttributeError, ValueError, RuntimeError):
            return None
        y += em*.1
    return None


def _opaque_merged_symbol_geometry(opening, pixel_probe, small):
    """Prove a distinct compact upper mark before larger source lexical ink.

    A literal apostrophe is not note authority. A merged or separate OCR prefix
    needs an isolated compact source component, either broad or a taller slender
    stem, raised and separated from larger lexical ink. Neither its character
    nor a numeric identity is decoded.
    """
    spans = [sp for sp in opening.spans if sp.text.strip()]
    if not spans:
        return False
    if re.match(r"^[*†‡'’]\s*[A-Za-z]", spans[0].text):
        first_box = spans[0].bbox
    elif (len(spans) >= 2 and re.fullmatch(r"[*†‡'’]", spans[0].text.strip())
            and spans[1].text.lstrip()[:1].isalpha()):
        first_box = spans[0].bbox
        first_box = (min(first_box[0], spans[1].bbox[0]),
                     min(first_box[1], spans[1].bbox[1]),
                     max(first_box[2], spans[1].bbox[2]),
                     max(first_box[3], spans[1].bbox[3]))
    else:
        return False
    document = getattr(pixel_probe, '_doc', None)
    page = getattr(pixel_probe, '_pno', None)
    if document is None or page is None:
        return False
    try:
        clip = extract.pymupdf.Rect(first_box)+(-small, -small*.5, .5, .5)
        clip &= document[page].rect
        pix = document[page].get_pixmap(matrix=extract.pymupdf.Matrix(3, 3),
            clip=clip, colorspace=extract.pymupdf.csGRAY, alpha=False)
        data = pix.samples
        dark = {(x, y) for y in range(pix.height) for x in range(pix.width)
                if data[y*pix.width+x] < 230}
        components = []
        while dark:
            seed = dark.pop(); stack = [seed]; points = [seed]
            while stack:
                x, y = stack.pop()
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        neighbor = (x+dx, y+dy)
                        if neighbor in dark:
                            dark.remove(neighbor); stack.append(neighbor); points.append(neighbor)
            if len(points) >= 4:
                components.append((clip.x0+min(x for x, y in points)/3,
                    clip.y0+min(y for x, y in points)/3,
                    clip.x0+(max(x for x, y in points)+1)/3,
                    clip.y0+(max(y for x, y in points)+1)/3))
        components.sort()
        if len(components) < 2:
            return False
        mark, lexical = components[0], components[1:]
        width, height = mark[2]-mark[0], mark[3]-mark[1]
        return bool(small*.1 <= width <= small*.7
            and small*.2 <= height <= small*.7
            and (width >= height*.7 or height >= small*.4)
            and mark[0] > clip.x0+.1 and mark[1] > clip.y0+.1
            and mark[0] <= first_box[0]+small*.2
            and lexical[0][0]-mark[2] >= small*.08
            and median(box[3] for box in lexical)-mark[3] >= small*.2
            and max(box[3]-box[1] for box in lexical) >= height*1.4)
    except (AttributeError, ValueError, RuntimeError):
        return False


def _source_contents_rows(raw, style):
    """Measure repeated entry/destination lanes, without reading their meaning."""
    rows = [line for block in raw.text_blocks for line in block.lines]
    em = style.body_size
    if not em:
        return set()
    lexical = [line for line in rows if _source_footer_words([line])]
    if not lexical:
        return set()
    left = min(line.bbox[0] for line in lexical)
    right = max(line.bbox[2] for line in lexical)
    destinations = []
    for line in rows:
        if not re.fullmatch(r'\d{1,4}', line.stripped) or line.bbox[0] < (left+right)/2:
            continue
        peers = [peer for peer in lexical
            if peer.bbox[2]+em <= line.bbox[0]
            and abs((peer.bbox[1]+peer.bbox[3]-line.bbox[1]-line.bbox[3])/2) <= em*.5]
        if peers:
            destinations.append((line, peers))
    aligned = [item for item in destinations if sum(
        abs(other[0].bbox[2]-item[0].bbox[2]) <= em
        for other in destinations) >= 3]
    if len(aligned) < 3:
        return set()
    # A repeated right destination lane is not a footnote-marker lane. Retain
    # all intervening entries and their subordinate original source rows.
    top = min(min([line.bbox[1]]+[peer.bbox[1] for peer in peers]) for line, peers in aligned)
    bottom = max(max([line.bbox[3]]+[peer.bbox[3] for peer in peers]) for line, peers in aligned)
    return {id(line) for line in rows if top-em <= line.bbox[1] and line.bbox[3] <= bottom+em}


def _source_parallel_table_rows(raw, style):
    """Keep repeated aligned lexical cells and their measured continuations."""
    em = style.body_size
    if not em or raw.width >= raw.height*1.2:
        return set()
    rows = sorted([line for block in raw.text_blocks for line in block.lines
                   if _source_footer_words([line])], key=lambda line: (line.bbox[1],line.bbox[0]))
    pairs = [(left,right) for i,left in enumerate(rows) for right in rows[i+1:]
        if left.bbox[2]+em <= right.bbox[0]
        and abs((left.bbox[1]+left.bbox[3]-right.bbox[1]-right.bbox[3])/2) <= em*.5]
    teachers = [(left,right) for left,right in pairs if sum(
        abs(peer_left.bbox[0]-left.bbox[0]) <= em
        and abs(peer_right.bbox[0]-right.bbox[0]) <= em
        for peer_left,peer_right in pairs) >= 3]
    if len(teachers) < 3:
        return set()
    top = min(line.bbox[1] for pair in teachers for line in pair)
    bottom = max(line.bbox[3] for pair in teachers for line in pair)
    selected = {id(line) for line in rows if top <= line.bbox[1] and line.bbox[3] <= bottom+em*.25}
    # Only the original final cells can teach following rows. A separate
    # paragraph/footnote gap or a new lexical lane ends the table component.
    final = [line for pair in teachers for line in pair if line.bbox[3] >= bottom-em*.5]
    for anchor in final:
        edge = anchor.bbox[3]
        for line in rows:
            if line.bbox[1] < edge or abs(line.bbox[0]-anchor.bbox[0]) > em*.5:
                continue
            if line.bbox[1]-edge > em*.75:
                break
            selected.add(id(line));edge=line.bbox[3]
    return selected


def _opaque_complete_note_territory(raw, style, prose, pixel_probe, x_bounds=None):
    """Prove a complete terminal source footer before partitioning its pixels.

    Already established source notes can survive rejected transcription only
    when every other body row precedes them and an unmasked blank cut exists.
    Additional territories require original marker geometry or a painted
    divider, lexical typography and the complete terminal source column.
    Neither a marker reading nor a rule alone establishes ownership.
    """
    pixel_probe = _opaque_pixel_queries(pixel_probe)
    if (pixel_probe is None or not raw.is_page_scan
            or raw.width >= raw.height*1.2 or not _native_margin_frame(raw)):
        return None
    em = style.body_size
    if not em:
        return None
    # No pixel probe here: the older source opening proof must be independent
    # of this fallback partition, which its pixel-aware path also consumes.
    _, established = _split_off_notes(raw, style,
        PageSkeleton(raw.pno, raw.width, raw.height), pixel_probe=None)
    known = {id(line) for note in established for line in note.lines}
    fields = _scan_numeric_field_rows(
        [(block, block.lines) for block in raw.text_blocks], style)
    fields |= _source_contents_rows(raw, style) | _source_parallel_table_rows(raw, style)
    possible = []
    for index, opening in enumerate(prose):
        above, below = prose[:index], prose[index:]
        words = _source_footer_words(below)
        if not above or not words or opening.bbox[1] < raw.height*.2:
            continue
        # A source caption, timeline field, or registered table row cannot be
        # reclaimed as commentary merely because it lies below a rule.
        if any(id(line) in fields or CAPTION_LINE.match(line.stripped)
               or _squashed_caption(line.stripped)
               or any(box[0] <= line.bbox[0] and box[1] <= line.bbox[1]
                      and box[2] >= line.bbox[2] and box[3] >= line.bbox[3]
                      for box in style.scan_table_label_boxes.get(raw.pno, []))
               for line in below):
            continue
        lexical_rows = [line for line in below if _source_footer_words([line])]
        # Dense short labels in parallel chart/table rows are structural data,
        # even when a true printed note follows that table. Its later marker
        # cannot authorize reclaiming the intervening label run.
        if (len(lexical_rows) >= 6 and sum(
                len(re.findall(r'[A-Za-z]{2,}', line.stripped)) < 3
                for line in lexical_rows) > len(lexical_rows)/2):
            continue
        bottom = max(line.bbox[3] for line in above)
        top = min(line.bbox[1] for line in below)
        if top-bottom < em*.5:
            continue
        body_words = _source_footer_words(above)
        if not body_words:
            continue
        large = max(em, median(sp.size for sp in body_words))
        small = median(sp.size for sp in words)
        left = min(line.bbox[0] for line in above
                   if _source_footer_words([line]))
        right = max(line.bbox[2] for line in above
                    if _source_footer_words([line]))
        numeric_tails = {id(line) for previous, line in zip(below, below[1:])
            if re.fullmatch(r'\d{1,4}[.,;:]?', line.stripped)
            and _source_footer_words([previous])
            and abs(line.bbox[0]-previous.bbox[0]) <= em
            and -em*.2 <= line.bbox[1]-previous.bbox[3] <= em
            and line.bbox[3]-line.bbox[1] <= small*1.5}
        if (right-left < em*12
                or any(line.bbox[0] < left-em*3 or line.bbox[2] > right+em
                       for line in below)
                or any(id(line) not in numeric_tails and not re.fullmatch(r'\d{1,3}', line.stripped)
                       and not _source_footer_words([line]) for line in below)):
            continue
        try:
            ink = pixel_probe.source_ink_bounds(
                (left-em, bottom+.5, right+em, top-.5))
        except (AttributeError, ValueError, RuntimeError):
            continue
        ruled = False
        if (ink and em*.18 <= ink[2]-ink[0]
                and (right-left)*.18 <= ink[2]-ink[0] <= (right-left)*1.08
                and abs(ink[0]-left) <= em
                and ink[1]-bottom >= em*.5 and top-ink[3] >= .5):
            try:
                query_top = min(top-.5, ink[1]+em*.65)
                rule_ink = pixel_probe.source_ink_bounds(
                    (ink[0]-1, bottom+.5, ink[2]+1, query_top))
                if not rule_ink:
                    continue
                separator = pixel_probe.thin_separator(
                    (rule_ink[0]-1, bottom+.5, rule_ink[2]+1, query_top),
                    max_thickness=min(3, em*.2))
                width = rule_ink[2]-rule_ink[0]
                boundary_ink = pixel_probe.source_has_ink((
                    rule_ink[0]+width*.1, query_top-.5,
                    rule_ink[2]-width*.1, query_top))
                ruled = bool((right-left)*.18 <= width <= (right-left)*1.08
                    and abs(rule_ink[0]-left) <= em and not boundary_ink
                    and separator and separator.get('occupied_fraction', 0) >= .8)
                if ruled:
                    ink = rule_ink
            except (AttributeError, ValueError, RuntimeError):
                pass
        spans = [sp for sp in opening.spans if sp.text.strip()]
        block = extract.Block(-1, _lines_bbox(below, opening.bbox), below)
        pairs = _source_note_marker_pairs(block)
        separately_marked = id(opening) in pairs or any(line is opening for line in pairs.values())
        companion_rows = list(pairs.values())
        # A bare extracted marker need not be superscript in the source. Its
        # original baseline, detached smaller territory and hanging lexical
        # lane can prove the group without granting a typed numeric identity.
        for marker in below:
            if not re.fullmatch(r'\d{1,3}', marker.stripped):
                continue
            parts = [sp for sp in marker.spans if sp.text.strip()]
            if len(parts) != 1:
                continue
            mark = parts[0]
            peers = [line for line in below if line is not marker
                     and (alpha := _source_footer_words([line]))
                     and 0 < alpha[0].bbox[0]-mark.bbox[2] <= em*2.5
                     and abs((mark.origin_y or mark.bbox[3])-
                             (alpha[0].origin_y or alpha[0].bbox[3])) <= small*.35
                     and mark.size <= small*1.1
                     and mark.bbox[3]-mark.bbox[1] <= small*1.2]
            if len(peers) == 1:
                companion_rows.append(peers[0])
                if marker is opening or peers[0] is opening:
                    separately_marked = True
        raised = bool(_scan_note_source_openings(opening))
        merged_symbol = _opaque_merged_symbol_geometry(opening, pixel_probe, small)
        smaller = (small <= large*.90 and all(
            not (alpha := _source_footer_words([line]))
            or median(sp.size for sp in alpha) <= large*.95 for line in below))
        claimed = all(id(line) in known for line in below)
        opening_words = _source_footer_words([opening])
        companion_shape = bool(smaller and opening_words and any(
            (alpha := _source_footer_words([peer]))
            and abs(alpha[0].bbox[0]-opening_words[0].bbox[0]) <= small*.5
            and median(sp.size for sp in opening_words) <= median(sp.size for sp in alpha)*1.05
            for peer in companion_rows if peer is not opening))
        source_marker = (_opaque_unowned_note_marker(opening, prose, pixel_probe,
            em, small, left) if smaller else None)
        if not ruled and (raised or source_marker or claimed or companion_shape):
            ink = _opaque_note_rule(pixel_probe, left, right, bottom, top, em)
            ruled = bool(ink)
        # A markerless continuation may precede another measured footer
        # opening. Its later marker cannot authorize a differently indented
        # or larger paragraph: all preceding lexical rows must share the
        # original hanging word lane and smaller type, after a double body
        # em of separation. The original blank source cut is still required.
        companion = companion_shape and (ruled or (
            top-bottom >= em*2 and small <= large*.86
            and opening_words[0].bbox[0]-left >= em
            and any((alpha := _source_footer_words([peer]))
                and all(not (prior_words := _source_footer_words([prior])) or (
                    abs(prior_words[0].bbox[0]-alpha[0].bbox[0]) <= small*.5
                    and median(sp.size for sp in prior_words) <=
                        max(sp.size for sp in alpha)*1.05)
                    for prior in below if (prior_words := _source_footer_words([prior]))
                    and median(sp.origin_y or sp.bbox[3] for sp in prior_words) <
                        median(sp.origin_y or sp.bbox[3] for sp in alpha)-small*.5)
                for peer in companion_rows if peer is not opening)))
        if top-bottom < em and not (ruled and (raised or source_marker or claimed or companion)):
            continue
        if not (claimed or (ruled and (merged_symbol or raised or source_marker))
                or companion or (smaller and (separately_marked or raised))):
            # A missing extracted marker can still be a distinct compact
            # raised source glyph left of the first lexical word. It is never
            # decoded, and no existing text box may own those pixels.
            if not source_marker:
                continue
        # A source cut must not cross chart, glyph, table or prose ink. The
        # divider remains wholly with the note strip; a blank cut precedes it.
        cut = (bottom+(ink[1] if ruled else top))/2
        try:
            x0, x1 = x_bounds if x_bounds is not None else (0, raw.width)
            if pixel_probe.source_has_ink((x0, cut-.5, x1, cut+.5)):
                continue
        except (AttributeError, ValueError, RuntimeError):
            continue
        possible.append((above, below, cut))
    return min(possible, key=lambda item: item[2]) if possible else None


def _opaque_lateral_note_regions(raw, style, lines, furniture, pixel_probe):
    """Partition independently proved columns without splitting neighboring artwork."""
    pixel_probe = _opaque_pixel_queries(pixel_probe)
    if (pixel_probe is None or not raw.is_page_scan or not _native_margin_frame(raw)
            or raw.width >= raw.height*1.2):
        return None
    prose = [line for line in lines if line not in furniture]
    if not prose:
        return None
    first = min(line.bbox[1] for line in prose)
    top_furniture = [line for line in furniture if line.bbox[3] < first]
    if len(top_furniture) != len(furniture):
        return None
    start = (max(line.bbox[3] for line in top_furniture)+first)/2 if top_furniture else 0
    try:
        if start and pixel_probe.source_has_ink((0, start-.5, raw.width, start+.5)):
            return None
        # Source row bounds propose a gutter. Unmasked original ink must prove
        # that it separates complete components for the entire retained page.
        edges = sorted({v for line in prose for v in (line.bbox[0], line.bbox[2])})
        gutters = []
        for left, right in zip(edges, edges[1:]):
            if right-left < style.body_size*.5:
                continue
            # Text boxes only propose a channel; a wider chart can occupy its
            # center. Search the proposed gap for a truly blank source strip.
            x = left+style.body_size*.25
            while x <= right-style.body_size*.25:
                if not pixel_probe.source_has_ink((x-style.body_size*.25, start,
                        x+style.body_size*.25, raw.height)):
                    gutters.append(x)
                    break
                x += style.body_size*.1
        for x in gutters:
            groups = ([line for line in prose if line.bbox[2] <= x],
                      [line for line in prose if line.bbox[0] >= x])
            if (min(map(len, groups)) < 2 or sum(map(len, groups)) != len(prose)
                    or pixel_probe.source_has_ink((x-.5, start, x+.5, raw.height))):
                continue
            territories = [_opaque_complete_note_territory(raw, style, group,
                pixel_probe, bounds) for group, bounds in
                zip(groups, ((0, x), (x, raw.width)))]
            if sum(territory is not None for territory in territories) != 1:
                continue
            regions = []
            def add(box, owned, reason):
                regions.extend([Region('artwork', lines=owned, bbox=box, reason=reason),
                                Region('figure', bbox=box, reason=reason)])
            if start:
                add((0, 0, raw.width, start), top_furniture, 'source_furniture_context')
            for group, bounds, territory in zip(groups, ((0, x), (x, raw.width)), territories):
                x0, x1 = bounds
                if territory is None:
                    add((x0, start, x1, raw.height), group, 'unrecovered_scan_layer')
                else:
                    above, below, cut = territory
                    add((x0, start, x1, cut), above, 'unrecovered_scan_layer')
                    add((x0, cut, x1, raw.height), below, 'ocr_uncertain_note')
            return regions
    except (AttributeError, ValueError, RuntimeError):
        return None
    return None


def opaque_page_regions(raw, style, pixel_probe=None):
    """Separate proven page furniture/notes without trusting their words.

    A rejected hidden transcript still supplies source geometry. A detached,
    smaller raised opening below larger prose, with a painted note divider,
    proves a note territory without establishing any numeral or transcription.
    Source-proved strips or complete columns partition the original pixels;
    an adjacent chart is never divided merely to isolate another column's note.
    """
    pixel_probe = _opaque_pixel_queries(pixel_probe)
    from statistics import median
    lines = sorted([ln for b in raw.text_blocks for ln in b.lines],
                   key=lambda ln: (ln.bbox[1], ln.bbox[0]))
    def size(rows):
        values = [s.size for ln in rows for s in ln.spans
                  for _ in range(min(200, len(s.text.strip()))) if s.size > 0]
        return median(values) if values else 0
    furniture = [ln for ln in lines if _furniture_reason(ln, raw, style)]
    prose = [ln for ln in lines if ln not in furniture]
    notes = []; cut = raw.height
    if pixel_probe is not None:
        candidates = []
        for i, opening in enumerate(prose):
            above, below = prose[:i], prose[i:]
            spans = [s for s in opening.spans if s.text.strip()]
            em, small = size(above), size(below)
            raised = (len(spans) >= 2
                      and re.fullmatch(r"[1-9]\d{0,2}|[*†‡'’]", spans[0].text.strip())
                      and spans[0].size <= small*.75)
            # OCR can merge the raised marker into the note's first word span.
            # Its literal numeric opening identifies a candidate territory only;
            # the smaller type, detached gap and painted rule must still prove it.
            merged = (bool(spans) and re.match(r"[1-9]\d{0,2}\s+\S", opening.stripped)
                      and spans[0].size > small*.75
                      and small <= em*.85)
            # A note continuing from the preceding page can begin with an
            # ordinary name or word. A later independently raised note opening
            # anchors that smaller territory; a printed continuation rule must
            # independently separate it from the larger body above.
            continuation = (not raised and not merged and bool(spans)
                and any(len(parts := [s for s in ln.spans if s.text.strip()]) >= 2
                    and re.fullmatch(r"[1-9]\d{0,2}|[*†‡'’]", parts[0].text.strip())
                    and parts[0].size <= size([ln])*.75 for ln in below[1:]))
            if (len(above) < 2 or not em or not small or not (raised or merged or continuation)
                    or small > em*.9
                    or any(size([ln]) > em*.95 for ln in below)):
                continue
            bottom = max(ln.bbox[3] for ln in above)
            top = min(ln.bbox[1] for ln in below)
            left = min(ln.bbox[0] for ln in above)
            right = max(ln.bbox[2] for ln in above)
            if (right-left < em*12
                    or any(ln.bbox[0] < left-em or ln.bbox[2] > right+em for ln in below)):
                continue
            if top-bottom < em:
                # The ordinary full-em clearance is unchanged. Only a raised
                # smaller opening and a complete thin source stroke can prove
                # the narrower blank gap; merged/continuing words cannot.
                if not raised or top-bottom < em*.5:
                    continue
                ink = _opaque_short_note_divider(
                    pixel_probe, left, right, bottom, top, em, small)
                if ink:
                    candidates.append((above, below, (bottom+ink[1])/2))
                continue
            # Stay outside the glyph boxes. A short footnote rule need not be
            # wide enough to satisfy the table/grid separator detector.
            try:
                bounds = getattr(pixel_probe, 'source_ink_bounds', pixel_probe.ink_bounds)
                ink = bounds((left-em, bottom+.5, right+em, top-.5))
            except (AttributeError, ValueError, RuntimeError):
                continue
            width = right-left
            if (ink and ink[3]-ink[1] <= em*.3
                    and width*.18 <= ink[2]-ink[0]
                    and ink[2]-ink[0] <= width*(1.08 if continuation else .85)
                    and abs(ink[0]-left) <= em
                    and ink[1]-bottom >= em*.5 and top-ink[3] >= .5):
                candidates.append((above, below, (bottom+ink[1])/2))
        if len(candidates) == 1:
            prose, notes, cut = candidates[0]
        complete = _opaque_complete_note_territory(raw, style,
            [ln for ln in lines if ln not in furniture], pixel_probe)
        if complete and (not notes or complete[2] < cut):
            prose, notes, cut = complete
        if not notes:
            lateral = _opaque_lateral_note_regions(raw, style, lines, furniture, pixel_probe)
            if lateral is not None:
                return lateral
    if not prose:
        return [Region('artwork', lines=lines, bbox=(0,0,raw.width,raw.height),
                       reason='unrecovered_scan_layer'),
                Region('figure', bbox=(0,0,raw.width,raw.height), reason='unrecovered_scan_layer')]
    first = min(ln.bbox[1] for ln in prose)
    last = max(ln.bbox[3] for ln in prose)
    top_furniture = [ln for ln in furniture if ln.bbox[3] < first]
    bottom_furniture = [ln for ln in furniture if ln.bbox[1] > last and ln not in notes]
    start = (max(ln.bbox[3] for ln in top_furniture)+first)/2 if top_furniture else 0
    end = cut if notes else ((last+min(ln.bbox[1] for ln in bottom_furniture))/2
                            if bottom_furniture else raw.height)
    # Geometry does not authorize clipping source art that crosses a margin
    # gap. When original pixels are available, keep the page intact unless the
    # proposed partition is outside all painted ink, without text masks.
    source_has_ink = getattr(pixel_probe, 'source_has_ink', None)
    if start and source_has_ink is not None:
        try:
            if source_has_ink((0, start-.5, raw.width, start+.5)):
                start = 0
                prose = [ln for ln in lines if ln not in notes]
        except (ValueError, RuntimeError):
            start = 0
            prose = [ln for ln in lines if ln not in notes]
    # Furniture whose geometry overlaps body cannot be separated safely.
    if any(ln not in top_furniture+bottom_furniture for ln in furniture):
        start = 0; end = cut; prose = [ln for ln in lines if ln not in notes]
    note_end = raw.height
    if notes and bottom_furniture:
        lower = max(ln.bbox[3] for ln in notes)
        upper = min(ln.bbox[1] for ln in bottom_furniture)
        # Source boxes propose a gap; they do not prove that painted chart or
        # decoration ink does not cross it. A complete blank one-point source
        # channel must separate the whole note from the margin furniture.
        proposed = [(lower+upper)/2]
        y = lower+.5
        while y <= upper-.5:
            proposed.append(y); y += .5
        try:
            blank = next((y for y in proposed if lower+.5 <= y <= upper-.5
                and source_has_ink is not None
                and not source_has_ink((0,y-.5,raw.width,y+.5))), None)
        except (ValueError, RuntimeError):
            blank = None
        if blank is None:
            box = (0,0,raw.width,raw.height)
            return [Region('artwork', lines=lines, bbox=box,
                           reason='unrecovered_scan_layer'),
                    Region('figure', bbox=box, reason='unrecovered_scan_layer')]
        note_end = blank
    strips = []
    if start: strips.append((0,start,top_furniture,'source_furniture_context'))
    strips.append((start,end,prose,'unrecovered_scan_layer'))
    if notes:
        strips.append((end,note_end,notes,'ocr_uncertain_note'))
        if note_end < raw.height:
            strips.append((note_end,raw.height,bottom_furniture,'source_furniture_context'))
    elif end < raw.height: strips.append((end,raw.height,bottom_furniture,'source_furniture_context'))
    regions = []
    for y0,y1,owned,reason in strips:
        box = (0,y0,raw.width,y1)
        regions.extend([Region('artwork', lines=owned, bbox=box, reason=reason),
                        Region('figure', bbox=box, reason=reason)])
    return regions


def page_skeleton(raw, style, layer_trusted=True, pixel_probe=None, visual_objects=None):
    """Classify one page's regions, recording a reason for every uncertain call.

    ``pixel_probe`` answers the questions geometry cannot (an
    ``extract.ScanPixelProbe``): whether an edge gap's pixels hold marks at
    all, where a band's truly empty columns split two side-by-side charts, and
    which horizontal rules a scan's raster still carries. Without one, edge
    gaps stay proposals for the build's ink proof, bands are never split, and
    a raster's ruling cannot veto a column reading."""
    skel = PageSkeleton(pno=raw.pno, width=raw.width, height=raw.height,
                        is_scan=raw.is_page_scan)

    if raw.is_page_scan and (getattr(raw, "text_layer_invisible", False) or getattr(raw, "text_layer_overpainted", False)):
        from .source import needs_recovery
        if needs_recovery(raw) or getattr(raw, "transcript_unverified", False):
            # Recognition was disabled or could not establish a source layer.
            # Preserve the complete page, not an invisible garbage transcript.
            skel.regions.extend(opaque_page_regions(raw, style, pixel_probe))
            return skel

    unverified_columns = _unverified_column_region(raw)
    if unverified_columns == (0,0,raw.width,raw.height):
        skel.regions.append(Region(kind='artwork',lines=[ln for b in raw.text_blocks for ln in b.lines],
            bbox=unverified_columns,reason='unverified_scan_layout'))
        skel.regions.append(Region(kind='figure',bbox=unverified_columns,reason='unverified_scan_layout'))
        return skel

    if not raw.text_blocks:
        skel.reasons.append("no_text_layer" if raw.images else "empty_page")
        for img in raw.images:
            # A textless leaf may be a plate or may be blank paper; the build
            # proves ink before either is emitted as a figure.
            skel.regions.append(Region(kind="figure", bbox=img.bbox, image=img,
                                       needs_ink=True))
        return skel

    key_panel = _uncertain_scan_key_panel(raw)
    if key_panel:
        box, lines = key_panel
        skel.regions.append(Region(kind='artwork', lines=lines, bbox=box,
                                   reason='uncertain_scan_key_panel'))
        skel.regions.append(Region(kind='figure', bbox=box,
                                   reason='uncertain_scan_key_panel'))
        skel.reasons.append('uncertain_scan_key_panel')
        return skel

    from . import scan_keys
    captioned_keys = scan_keys.panels(raw, pixel_probe)
    if captioned_keys:
        for box, lines, reason, column in captioned_keys:
            skel.regions.append(Region(kind='artwork', lines=lines, bbox=box,
                                       reason=reason, column=column))
            skel.regions.append(Region(kind='figure', bbox=box,
                                       reason=reason, column=column))
        skel.reasons.append('captioned_scan_keys')
        skel.regions.sort(key=_region_order)
        return skel

    panels = _sparse_scan_spread_panels(raw)
    if panels:
        for panel_index, (box, lines) in enumerate(panels):
            skel.regions.append(Region(kind='artwork', lines=lines, bbox=box,
                                       reason='sparse_scan_spread_panel', band=panel_index))
            skel.regions.append(Region(kind='figure', bbox=box,
                                       reason='sparse_scan_spread_panel', band=panel_index))
        skel.reasons.append('sparse_scan_spread_panel')
        skel.regions.sort(key=_region_order)
        return skel

    # A sequence-proven margin folio belongs to furniture before a note zone
    # can absorb its PDF block. Keep original raw lines for source accounting.
    note_raw = raw
    folio_box = getattr(style, "folio_boxes", {}).get(raw.pno)
    contents_folios = {id(line) for block in raw.text_blocks for line in block.lines
                       if _contents_footer_folio(line, raw, style)}
    if folio_box is not None or contents_folios:
        blocks = []
        for block in raw.blocks:
            if block.kind != "text" or not block.lines:
                blocks.append(block)
                continue
            kept = []
            for line in block.lines:
                if line.bbox == folio_box or id(line) in contents_folios:
                    skel.regions.append(Region(kind="furniture", lines=[line],
                        bbox=line.bbox, reason=("contents_footer_folio" if id(line) in contents_folios
                                               else "sequence_folio")))
                else:
                    kept.append(line)
            if len(kept) == len(block.lines):
                blocks.append(block)
            elif kept:
                blocks.append(replace(block, lines=kept,
                    bbox=_lines_bbox(kept, block.bbox)))
        note_raw = replace(raw, blocks=blocks)
    body_blocks, note_regions = _split_off_notes(note_raw, style, skel, pixel_probe)

    top_y = min((ln.bbox[1] for blk in raw.text_blocks for ln in blk.lines),
                default=None)

    from .heading_units import opening_display_unit
    opening_lines=[ln for blk in body_blocks for ln in blk.lines]
    opening=opening_display_unit([ln.to_dict() for ln in opening_lines],style.body_size,raw.width,raw.height,scan=raw.is_page_scan)
    opening_owned={id(opening_lines[i]) for i in opening.get('indices',[])}
    opening_owned.update(id(line) for block in body_blocks
                         for group in _numbered_source_groups(block.lines) for line in group)
    kept_blocks = []
    for blk in body_blocks:
        kept = []
        for ln in blk.lines:
            reason = None if id(ln) in opening_owned else _furniture_reason(ln, raw, style, top_y)
            if reason:
                skel.regions.append(Region(kind="furniture", lines=[ln], reason=reason,
                                           bbox=ln.bbox, column=(int((ln.bbox[0]+ln.bbox[2])/2 > raw.width*.5)
                                               if reason=='repeated_scan_spread_head' else 0)))
            else:
                kept.append(ln)
        if kept:
            kept_blocks.append((blk, kept))

    aligned = _uncertain_aligned_scan_list(raw, kept_blocks)
    if aligned:
        box, lines = aligned
        owned = {id(line) for line in lines}
        kept_blocks = [(blk, [line for line in group if id(line) not in owned])
                       for blk, group in kept_blocks]
        kept_blocks = [(blk, group) for blk, group in kept_blocks if group]
        skel.regions.append(Region(kind='artwork', lines=lines, bbox=box,
                                   reason='uncertain_aligned_scan_list'))
        skel.regions.append(Region(kind='figure', bbox=box,
                                   reason='uncertain_aligned_scan_list'))
        skel.reasons.append('uncertain_aligned_scan_list')

    source_marks = {id(img) for img in raw.images
                    if _embedded_source_mark(img, raw, kept_blocks)}
    embedded = [img for img in raw.images if (img.substantial or id(img) in source_marks)
                and not img.full_page and not getattr(img,'page_background',False)]

    # A full-bleed cover or plate is the page itself with a little text over
    # it: one figure, nothing left to slice. Evidence is the pixels and the
    # prose together, so a text page with a scan behind it can never qualify.
    cover = _full_bleed_plate(raw, kept_blocks, pixel_probe)
    if cover is not None and (getattr(raw,'text_layer_invisible',False)
                              or getattr(raw,'text_layer_overpainted',False)):
        # Ink across the page proves artwork even when its hidden transcript is
        # a long, plausible blurb. Preserve the design once, including portraits.
        owned=[ln for _,lines in kept_blocks for ln in lines]
        owned.extend(ln for note in note_regions for ln in note.lines)
        skel.regions.append(Region(kind='artwork',lines=owned,bbox=cover.bbox,
                                   reason='unverified_scan_layout'))
        skel.regions.append(Region(kind='figure',bbox=cover.bbox,
                                   reason='unverified_scan_layout'))
        return skel
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

    # Keep text-layer fragments inside the original page frame; treating them
    # as full-page figures crops prose.
    if raw.is_page_scan and layer_trusted and cover is None:
        if note_regions:
            candidates = _scan_figures(kept_blocks, raw, style, pixel_probe,
                                       note_regions=note_regions)
        else:
            candidates = _scan_figures(kept_blocks, raw, style, pixel_probe)
    elif raw.drawings and not raw.is_page_scan:
        candidates = _vector_figures(raw)
    else:
        candidates = []
    from . import figure_titles
    kept_blocks, embedded, titled_figures, title_artwork = figure_titles.regions(
        raw, kept_blocks, embedded, candidates, style, pixel_probe)
    candidates.extend(titled_figures)
    skel.regions.extend(title_artwork)
    from . import scan_grids
    kept_blocks, candidates, grid_artwork, note_regions = scan_grids.regions(
        raw, kept_blocks, pixel_probe, candidates, note_regions)
    skel.regions.extend(grid_artwork)
    kept_blocks, candidates, captioned_artwork, note_regions = _complete_captioned_scan_diagrams(
        raw, kept_blocks, candidates, style, pixel_probe, note_regions)
    skel.regions.extend(captioned_artwork)
    from . import ruled_tables
    kept_blocks, candidates, table_regions = ruled_tables.regions(raw, kept_blocks, pixel_probe, candidates)
    skel.regions.extend(table_regions)
    if unverified_columns:
        # One owner for intersecting source regions; never repeat a figure below
        # a new table crop or leave its final source row outside the crop.
        box=unverified_columns
        retained=[]
        for candidate in candidates:
            b=candidate.bbox
            if b[0]<box[2] and box[0]<b[2] and b[1]<box[3] and box[1]<b[3]:
                box=(min(box[0],b[0]),min(box[1],b[1]),max(box[2],b[2]),max(box[3],b[3]))
            else:retained.append(candidate)
        candidates=retained+[Region(kind='figure',bbox=box,reason='unverified_scan_layout')]
    artwork = []
    if unverified_columns:
        retained=[]
        for block,lines in kept_blocks:
            owned=[ln for ln in lines if box[0]<=ln.bbox[0] and ln.bbox[2]<=box[2]
                   and box[1]<=ln.bbox[1] and ln.bbox[3]<=box[3]]
            if owned:artwork.append(Region(kind='artwork',lines=owned,
                bbox=_lines_bbox(owned,block.bbox),reason='unverified_scan_layout'))
            other=[ln for ln in lines if ln not in owned]
            if other:retained.append((block,other))
        kept_blocks=retained
        skel.regions.extend(artwork)
    if candidates:
        kept_blocks, connected_artwork = _complete_unverified_figure_tops(raw, kept_blocks, candidates, style)
        skel.regions.extend(connected_artwork)
        kept_blocks, artwork = _absorb_figure_content(kept_blocks, candidates,
                                                      style, raw=raw, pixel_probe=pixel_probe)
        skel.regions.extend(artwork)
        for owner, figure in zip(title_artwork, titled_figures):
            # A below-figure caption may extend the crop. The title's immutable
            # source occurrence still belongs to that exact final image region.
            owner.bbox=figure.bbox

    # Preserve source-established columns before uncertain prose becomes images;
    # the remaining confident words alone may no longer prove the reading order.
    source_layout = None
    if any(sp.font=='ocr' and sp.uncertain for _,lines in kept_blocks
           for ln in lines for sp in ln.spans):
        source_layout = _column_layout(kept_blocks, embedded, candidates, raw,
                                       pixel_probe=pixel_probe)
        if isinstance(source_layout, _RowTable):
            source_layout = None

    # Confidence is not a probability of correctness. Once OCR doubts a word,
    # its paragraph is not a trustworthy transcription, including confident
    # neighbors. Keep that source region as pixels rather than corrected prose.
    kept_blocks, note_regions = _preserve_uncertain_ocr_regions(
        raw, kept_blocks, note_regions, skel, cover, candidates)

    kept_blocks = _preserve_paired_scan_columns(raw, kept_blocks, skel, candidates)
    if any(r.kind=='figure' and r.reason=='unverified_paired_columns'
           and tuple(r.bbox)==(0,0,raw.width,raw.height) for r in skel.regions):
        for region in note_regions:
            region.kind='artwork';region.reason='unverified_paired_columns'
            skel.regions.append(region)
        note_regions=[]
    kept_blocks = _preserve_unverified_scan_layout(raw, kept_blocks, skel)
    kept_blocks = _preserve_tracked_native_lines(raw, kept_blocks, skel)

    layout = _column_layout(kept_blocks, embedded, candidates, raw,
                            pixel_probe=pixel_probe)
    if layout is None and source_layout is not None:
        layout = source_layout
    if isinstance(layout, _RowTable):
        # The rows are measured, and the page keeps the printed (y, x) order --
        # but each row's cells are emitted as one region, because the paragraph
        # join across a row boundary fuses whole rows into each other.
        skel.reasons.append("mirror_table")
        kept_blocks = _emit_table_rows(layout, kept_blocks, skel)
        layout = None
    from .heading_units import opening_display_unit
    flat=[ln for _,lines in kept_blocks for ln in lines]
    display=opening_display_unit([ln.to_dict() for ln in flat],style.body_size,raw.width,raw.height,scan=raw.is_page_scan) if layout is None else {}
    if display:
        title=[flat[i] for i in display['indices']]
        owned={id(ln) for ln in title}
        kept_blocks=[(blk,[ln for ln in lines if id(ln) not in owned]) for blk,lines in kept_blocks]
        kept_blocks=[(blk,lines) for blk,lines in kept_blocks if lines]
        skel.regions.append(Region(kind='heading',lines=title,
            level=style.level_for(max(ln.size for ln in title)),bbox=_lines_bbox(title,title[0].bbox),
            display_group=display))
    if not raw.is_page_scan:
        kept_blocks = _regroup_native_titles(kept_blocks, style, getattr(pixel_probe,"_doc",None),raw.pno)
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

    if layout is not None:
        for region in skel.regions:
            if region.reason in ("ocr_uncertain_region", "native_spacing_uncertain", "unverified_scan_layout", "unverified_paired_columns"):
                region.band, region.column = layout.place(region.bbox)

    for img in embedded:
        band, column = layout.place(img.bbox) if layout else (0, 0)
        skel.regions.append(Region(kind="figure", bbox=img.bbox, image=img,
                                   band=band, column=column,
                                   reason="embedded_source_mark" if id(img) in source_marks else "",
                                   needs_ink=id(img) in source_marks))
    for candidate in candidates:
        if layout is not None:
            candidate.band, candidate.column = layout.place(candidate.bbox)
        skel.regions.append(candidate)

    skel.regions.extend(note_regions)
    if getattr(raw,'text_layer_invisible',False):
        # Invisible transcript fonts cannot preserve the printed display face.
        for region in list(skel.regions):
            if region.kind=='heading' and region.lines:
                region.kind='artwork';region.reason='unverified_scan_layout'
                skel.regions.append(Region(kind='figure',bbox=region.bbox,
                    reason='unverified_scan_layout',band=region.band,column=region.column))
    _preserve_conflicting_outline_heading(raw, style, skel)
    _complete_captioned_scan_top(raw, skel, pixel_probe)
    _coalesce_nested_scan_list_figures(raw, skel)
    _attach_disputed_scan_markers(raw, skel)
    if visual_objects is not None:
        from .visual_objects import append_regions
        append_regions(raw, skel, visual_objects, layout)
    skel.regions.sort(key=_region_order)
    _join_numbered_regions(skel)
    skel.title_unit = _sparse_title_unit(raw, skel, style)
    return skel


def _sparse_title_unit(raw, skel, style):
    """Recognize an early, centered bibliographic composition from printed boxes.

    This marks a source-page boundary for the chapter builder; it does not join,
    demote, or transcribe any of the page's elements. A prose opening or two
    equally prominent sections must keep their ordinary chapter structure.
    """
    if raw.is_page_scan or raw.pno > 6 or 'columns_reordered' in skel.reasons:
        return False
    if any(r.kind not in ('body', 'heading', 'figure', 'furniture') or
           (r.kind == 'figure' and r.reason not in ('embedded', 'embedded_source_mark')) or
           r.list_groups for r in skel.regions):
        return False
    lines = sorted((ln for region in skel.regions
                    if region.kind in ('body', 'heading') for ln in region.lines),
                   key=lambda ln: (ln.bbox[1], ln.bbox[0]))
    if not 4 <= len(lines) <= 12:
        return False
    if (lines[0].bbox[1] > raw.height * .30 or
            not raw.height * .35 <= lines[-1].bbox[3] <= raw.height * .9):
        return False
    for line in lines:
        x0, _, x1, _ = line.bbox
        if (abs((x0 + x1) / 2 - raw.width / 2) > raw.width * .10 or
                x1 - x0 > raw.width * .75 or
                len(line.stripped) > 65 or len(line.stripped.split()) > 8):
            return False
    sizes = [line.size for line in lines]
    if (min(sizes) <= 0 or max(sizes) < max(18, style.body_size * 1.2) or
            min(sizes) > max(sizes) * .8):
        return False
    gaps = [b.bbox[1] - a.bbox[3] for a, b in zip(lines, lines[1:])]
    separation = max(range(len(gaps)), key=gaps.__getitem__)
    if (gaps[separation] < max(32, 1.5 * median(sizes)) or
            max(sizes[separation + 1:]) > max(sizes[:separation + 1]) * .85):
        return False
    return True


def _preserve_conflicting_outline_heading(raw, style, skel):
    # Outline labels are independent, authored navigation, not body text. A
    # disagreement cannot tell us which Unicode is right: preserve printed pixels.
    if raw.is_page_scan:
        return
    labels = [entry for entry in style.outline if entry.get('internal') is True
              and entry.get('pno') == raw.pno]
    headings = [r for r in skel.regions if r.kind == 'heading' and r.display_group]
    if len(labels) != 1 or len(headings) != 1:
        return
    heading = headings[0]
    x0, y0, x1, y1 = heading.bbox
    if not (0 <= x0 < x1 <= raw.width and 0 <= y0 < y1 <= raw.height
            and y0 < raw.height * .5 and y1-y0 <= raw.height * .3
            and (x1-x0)*(y1-y0) <= raw.width*raw.height*.35):
        return
    tokens = lambda text: re.findall(r'\w+', text.casefold())
    label, reading = tokens(labels[0]['title']), tokens(heading.text)
    if not label or any(reading[i:i+len(label)] == label for i in range(len(reading))):
        return
    heading.kind = 'artwork'
    heading.reason = 'native_outline_conflict'
    skel.regions.append(Region(kind='figure',bbox=heading.bbox,
                               reason='native_outline_conflict'))
    skel.reasons.append('native_outline_conflict')


def _unverified_column_region(raw):
    """Three sustained disjoint text columns in an unverified scan need layout.

    Numeric cells retain their bounded region; prose around unrepresented artwork
    or forms retains the complete page. Neither implies a recovered transcription.
    Visible native text and confidence-built recognition are unaffected.
    """
    if not raw.is_page_scan or not (getattr(raw,'text_layer_invisible',False)
            or getattr(raw,'text_layer_overpainted',False)):
        return None
    lines=[ln for block in raw.text_blocks for ln in block.lines if ln.stripped]
    if not lines:return None
    em=median(ln.size for ln in lines)
    # Some PDFs store each printed label and its right-aligned page number as
    # separate lines. Preserve the connected row region, not only occasional
    # rows whose text extraction happened to join both cells.
    rows=[]
    for line in sorted(lines,key=lambda ln:(ln.bbox[1]+ln.bbox[3])/2):
        center=(line.bbox[1]+line.bbox[3])/2
        if rows and abs(center-rows[-1][0])<=em*.4:
            rows[-1][1].append(line)
        else:rows.append((center,[line]))
    runs=[];run=[]
    for center,cells in rows:
        cells=sorted(cells,key=lambda ln:ln.bbox[0])
        last=cells[-1]
        numeric=re.fullmatch(r'\s*\d{1,5}\s*',last.text)
        labels=[ln for ln in cells[:-1] if any(ch.isalpha() for ch in ln.text)]
        valid=(numeric and labels and last.bbox[0]-max(ln.bbox[2] for ln in labels)>=2*em)
        if (not valid or (run and (center-run[-1][0]>3*em
                or abs(last.bbox[2]-run[-1][1][-1].bbox[2])>em))):
            if len(run)>=4:runs.append(run)
            run=[]
        if valid:run.append((center,cells))
    if len(run)>=4:runs.append(run)
    if runs:
        selected=[ln for _,cells in max(runs,key=len) for ln in cells]
        box=_lines_bbox(selected,selected[0].bbox)
        return (max(0,box[0]-3),max(0,box[1]-3),min(raw.width,box[2]+3),min(raw.height,box[3]+3))
    tabular=[ln for ln in lines if '\t' in ln.text and re.search(r'\d+\s*$',ln.text)]
    if len(tabular)>=4:
        right=median(ln.bbox[2] for ln in tabular)
        aligned=[ln for ln in tabular if abs(ln.bbox[2]-right)<=em*2]
        if len(aligned)>=4:
            box=_lines_bbox(aligned,aligned[0].bbox)
            return (max(0,box[0]-3),max(0,box[1]-3),min(raw.width,box[2]+3),min(raw.height,box[3]+3))
    clusters=[]
    for ln in sorted(lines,key=lambda ln:ln.x0):
        if ln.bbox[2]-ln.x0 > raw.width*.42:continue
        if clusters and abs(ln.x0-clusters[-1][0].x0)<=em*1.6:
            clusters[-1].append(ln)
        else:clusters.append([ln])
    columns=[]
    for group in clusters:
        typical=median(ln.bbox[2]-ln.bbox[0] for ln in group)
        group=[ln for ln in group if ln.bbox[2]-ln.bbox[0]<=typical*1.5+em]
        if len(group)<6:continue
        box=_lines_bbox(group,group[0].bbox)
        if columns and box[0] < columns[-1][0][2]+em*.5:continue
        columns.append((box,group))
    if len(columns)==2:
        overlap=min(box[3] for box,_ in columns)-max(box[1] for box,_ in columns)
        selected=[ln for _,group in columns for ln in group]
        numbered=sum(bool(re.search(r'\d[\d, n\-.]*$',ln.text)) for ln in selected)
        digits=sum(ch.isdigit() for ln in selected for ch in ln.text)
        letters=sum(ch.isalpha() for ln in selected for ch in ln.text)
        # Dense reference columns are source associations, not aligned prose
        # rows to interleave. Preserve their printed entries and page numbers.
        if overlap>=8*em and numbered*2>=len(selected) and digits>letters*.25:
            box=_lines_bbox(selected,selected[0].bbox)
            return (max(0,box[0]-3),max(0,box[1]-3),min(raw.width,box[2]+3),min(raw.height,box[3]+3))
    for start in range(len(columns)-2):
        group=columns[start:start+3]
        overlap=min(box[3] for box,_ in group)-max(box[1] for box,_ in group)
        if overlap < em*8:continue
        top=max(box[1] for box,_ in group)
        selected=[ln for _,ls in columns for ln in ls if ln.bbox[1]>=top-em*.5]
        numeric=sum(ch.isdigit() for ln in selected for ch in ln.text)
        letters=sum(ch.isalpha() for ln in selected for ch in ln.text)
        if numeric > letters:
            box=_lines_bbox(selected,selected[0].bbox)
            return tuple(max(0,v-3) if i<2 else min(raw.width if i==2 else raw.height,v+3)
                         for i,v in enumerate(box))
        return (0,0,raw.width,raw.height)
    return None


def _preserve_paired_scan_columns(raw, kept_blocks, skel, candidates):
    if not raw.is_page_scan or not getattr(raw,'text_layer_overpainted',False):return kept_blocks
    boxes=[_lines_bbox(lines,block.bbox) for block,lines in kept_blocks]
    em=median(line.size for _,lines in kept_blocks for line in lines) if kept_blocks else 0
    if em<=0:return kept_blocks
    pairs=[]
    for i,left in enumerate(boxes):
        for j,right in enumerate(boxes):
            if (left[2]+.6*em < right[0] and abs(left[1]-right[1]) <= .3*em
                    and left[3]-left[1]>=1.5*em and right[2]-right[0]>=3*em):
                pairs.append((i,j))
    for seed_left,seed_right in pairs:
        left,right=boxes[seed_left],boxes[seed_right]
        aligned=[(i,j) for i,j in pairs if abs(boxes[i][0]-left[0])<=em
                 and abs(boxes[j][0]-right[0])<=em]
        if len({i for i,_ in aligned})<3:continue
        members={i for pair in aligned for i in pair}
        top=min(boxes[i][1] for i in members);bottom=max(boxes[i][3] for i in members)
        x0=min(boxes[i][0] for i in members);x1=max(boxes[i][2] for i in members)
        # A two-column label row immediately above belongs to the same source
        # region. It must itself contain separate source lines at both starts.
        for index,(_,lines) in enumerate(kept_blocks):
            if top-3*em<=boxes[index][1]<top and any(abs(ln.x0-left[0])<=em for ln in lines) and any(abs(ln.x0-right[0])<=em for ln in lines):
                top=min(top,boxes[index][1])
        absorbed_figure=False
        for candidate in list(candidates):
            b=candidate.bbox
            if b[0]<x1 and x0<b[2] and b[1]<bottom and top<b[3]:
                x0,top,x1,bottom=min(x0,b[0]),min(top,b[1]),max(x1,b[2]),max(bottom,b[3])
                candidates.remove(candidate);absorbed_figure=True
        if absorbed_figure:
            # An unverified paired text layout shares its page with artwork.
            # Preserve the complete designed region, including text the hidden
            # layer may have misplaced above the inferred image boundary.
            x0,top,x1,bottom=0,0,raw.width,raw.height
            candidates.clear()
        owned={id(line) for _,lines in kept_blocks for line in lines
               if x0-em<=line.bbox[0] and line.bbox[2]<=x1+em
               and top-.3*em<=line.bbox[1] and line.bbox[3]<=bottom+.3*em}
        lines=[ln for _,group in kept_blocks for ln in group if id(ln) in owned]
        if not lines:continue
        box=(x0,top,x1,bottom);pad=min(4.0,.4*em)
        crop=(max(0,box[0]-pad),max(0,box[1]-pad),min(raw.width,box[2]+pad),min(raw.height,box[3]+pad))
        skel.regions.append(Region(kind='artwork',lines=lines,bbox=box,reason='unverified_paired_columns'))
        skel.regions.append(Region(kind='figure',bbox=crop,reason='unverified_paired_columns'))
        remaining=[(b,[ln for ln in ls if id(ln) not in owned]) for b,ls in kept_blocks]
        return [(b,ls) for b,ls in remaining if ls]
    return kept_blocks


def _preserve_unverified_scan_layout(raw, kept_blocks, skel):
    if not raw.is_page_scan or not (getattr(raw, 'text_layer_overpainted', False)
                                   or getattr(raw, 'text_layer_invisible', False)):
        return kept_blocks
    from .assess import looks_like_prose
    # Ordinary continuous prose keeps its reflow. Sparse non-prose runs with
    # repeated tiny labels do not prove symbols or their empty-cell relations.
    # Preserve the complete run between prose boundaries, never guessed cells.
    retained, group = [], []
    def flush():
        lines = [line for _, items in group for line in items]
        small = [line for line in lines if 0 < len(line.stripped) <= 3]
        if len(small) < 3 or len({round(line.bbox[1]/max(1,line.size)) for line in small}) < 3:
            retained.extend(group); return
        box = _lines_bbox(lines, lines[0].bbox)
        x0,y0,x1,y1 = box
        area = (x1-x0)*(y1-y0)
        occupied = sum((ln.bbox[2]-ln.bbox[0])*(ln.bbox[3]-ln.bbox[1]) for ln in lines)
        if (not (0 <= x0 < x1 <= raw.width and 0 <= y0 < y1 <= raw.height)
                or y1-y0 < max(1,median(ln.size for ln in lines))*8
                or area <= 0 or occupied/area > .25):
            retained.extend(group); return
        pad = max(2.0,min(6.0,median(ln.size for ln in lines)*.4))
        crop = (max(0,x0-pad),max(0,y0-pad),min(raw.width,x1+pad),min(raw.height,y1+pad))
        skel.regions.append(Region(kind='artwork',lines=lines,bbox=box,reason='unverified_scan_layout'))
        skel.regions.append(Region(kind='figure',bbox=crop,reason='unverified_scan_layout'))
    for block,lines in kept_blocks:
        if (looks_like_prose(' '.join(line.text for line in lines))
                or any(len(line.stripped.split()) >= 8 for line in lines)):
            flush(); group=[]; retained.append((block,lines))
        else:
            group.append((block,lines))
    flush()
    return retained


def _preserve_tracked_native_lines(raw, kept_blocks, skel):
    # PDF extraction can split a single tracked paragraph at each spaced name.
    # Preserve that printed territory together, so disclosure does not interrupt
    # its lines. Text, line ownership and source order remain unchanged.
    from .assemble import continues
    groups=[]
    for block,lines in kept_blocks:
        if groups and lines and groups[-1][1]:
            previous,prior=groups[-1]
            last,first=prior[-1],lines[0]
            em=max(1,median(line.size for line in prior+lines))
            gap=first.bbox[1]-last.bbox[3]
            if (any(getattr(line,'spacing_uncertain',False) for line in prior+lines)
                    and -.25*em<=gap<=.8*em
                    and abs(first.x0-prior[0].x0)<=.4*em
                    and not CAPTION_LINE.match(first.stripped)
                    and continues(last.stripped,first.stripped)):
                merged=prior+lines
                groups[-1]=(extract.Block(previous.number,_lines_bbox(merged,previous.bbox),merged),merged)
                continue
        groups.append((block,lines))
    retained = []
    for block, lines in groups:
        # Keep the complete paragraph/list block: removing just its first line
        # can strand a list number or move the continuation before its source.
        box = _lines_bbox(lines, block.bbox)
        x0,y0,x1,y1 = box
        if (not any(getattr(line, 'spacing_uncertain', False) for line in lines)
                or not (0 <= x0 < x1 <= raw.width and 0 <= y0 < y1 <= raw.height)
                or y1-y0 > raw.height*.3 or (x1-x0)*(y1-y0) > raw.width*raw.height*.35):
            retained.append((block,lines))
            continue
        pad = max(1.0, min(4.0, median(line.size for line in lines)*.25))
        crop = (max(0,x0-pad),max(0,y0-pad),min(raw.width,x1+pad),min(raw.height,y1+pad))
        skel.regions.append(Region(kind='artwork',lines=list(lines),bbox=box,
                                   reason='native_spacing_uncertain'))
        skel.regions.append(Region(kind='figure',bbox=crop,reason='native_spacing_uncertain'))
    return retained


def _reliable_scan_note_number(region):
    """Confirm an existing identity from a separate reliable raised source span.

    Note territory does not establish its OCR label. This only validates an
    identity already proposed upstream; it never creates or decodes a number.
    """
    if type(region.number) is not int or region.number <= 0 or not region.lines:
        return False
    line = region.lines[0]
    spans = [span for span in line.spans if span.text.strip()]
    words = _scan_note_words([line])
    if len(spans) < 2 or not words:
        return False
    marker, following = spans[:2]
    token = marker.text.strip()
    if (not re.fullmatch(r'\d{1,3}[.)]?', token)
            or int(token.rstrip('.)')) != region.number
            or marker.uncertain or marker.transcription_uncertain
            or marker.encoding_unresolved
            or not any(char.isalpha() for char in following.text)):
        return False
    small = median(word.size for word in words)
    baseline = median(word.bbox[3] for word in words)
    gap = following.bbox[0] - marker.bbox[2]
    return bool(marker.size <= small*.75
                and marker.bbox[1] <= following.bbox[1]+small*.2
                and marker.bbox[3] <= min(following.bbox[3], baseline)-small*.15
                and small*.1 <= gap <= small*1.5)


def _preserve_uncertain_ocr_regions(raw, kept_blocks, note_regions, skel, cover, candidates):
    from .assemble import HYPHENS
    regions = []
    # OCR uncertainty changes the representation of body text, not an already
    # proven source role. Reclaiming a margin row here would put a running head
    # back into the reading flow after the furniture pass had removed it.
    claimed = {id(line) for region in skel.regions
               if region.reason in ('uncertain_aligned_scan_list', 'source_scan_grid') or
                  region.kind=='furniture'
               for line in region.lines}
    claimed.update(id(line) for region in candidates
                   if region.reason == 'source_scan_grid'
                   for line in region.caption_lines)
    # The complete note territory was proved before artwork discovery. Its
    # recognition uncertainty can require source pixels, but cannot turn that
    # territory back into body artwork, even in a mixed OCR block.
    for region in note_regions:
        claimed.update(id(line) for line in region.lines)
        spans = [span for line in region.lines for span in line.spans]
        if (spans and all(span.font == 'ocr' for span in spans)
                and (region.uncertain
                     or any(line.transcription_uncertain for line in region.lines)
                     or any(span.uncertain or span.transcription_uncertain
                            or span.encoding_unresolved for span in spans))):
            if not _reliable_scan_note_number(region):
                region.number = None
            region.reason = 'pixel_note_territory'
            region.uncertain = True
    eligible = {id(line) for _, lines in kept_blocks for line in lines}
    blocks = []
    for block in raw.text_blocks:
        lines = [line for line in block.lines if id(line) not in claimed]
        if not lines:
            continue
        current = replace(block, lines=lines, bbox=_lines_bbox(lines,block.bbox))
        if blocks:
            prior = blocks[-1]
            last, first = prior.lines[-1], lines[0]
            joined = prior.lines + lines
            spans = [sp for ln in joined for sp in ln.spans]
            em = max(1, median(ln.size for ln in joined))
            # Recognition engines may split one printed paragraph at the word
            # they doubt. Preserve its complete source pixels, including the
            # confident half of that same word, without transcribing the crop.
            if (all(id(ln) in eligible for ln in joined)
                    and spans and all(sp.font == 'ocr' for sp in spans)
                    and any(sp.uncertain for sp in spans)
                    and re.search('[' + HYPHENS + ']$',last.stripped)
                    and re.match('[a-z]',first.stripped)
                    and len(last.stripped.split()) >= 8
                    and len(first.stripped.split()) >= 8
                    and -.25*em <= first.bbox[1]-last.bbox[3] <= .8*em
                    and abs(first.x0-prior.lines[0].x0) <= .4*em
                    and abs(last.bbox[2]-first.bbox[2]) <= .8*em
                    and abs((last.bbox[3]-last.bbox[1])-
                            (first.bbox[3]-first.bbox[1])) <= .2*em):
                blocks[-1] = replace(prior, lines=joined,
                    bbox=_lines_bbox(joined,prior.bbox))
                continue
        blocks.append(current)
    for block in blocks:
        lines = block.lines
        spans = [sp for ln in lines for sp in ln.spans]
        if any(getattr(ln,"transcription_uncertain",False) for ln in lines):
            regions.append(replace(block, lines=lines,
                                   bbox=_lines_bbox(lines,block.bbox)));continue
        if not spans or not all(sp.font == "ocr" for sp in spans):
            continue
        if cover is not None or any(sp.uncertain for sp in spans) or not any(c.isalnum() for c in ''.join(ln.text for ln in lines)):
            regions.append(replace(block, lines=lines,
                                   bbox=_lines_bbox(lines,block.bbox)))
    if not regions:
        return kept_blocks, note_regions
    note_owned = {id(line) for region in note_regions for line in region.lines}
    owned = {id(ln) for block in regions for ln in block.lines}
    kept_blocks = [(b, [ln for ln in lines if id(ln) not in owned])
                   for b, lines in kept_blocks]
    kept_blocks = [(b, lines) for b, lines in kept_blocks if lines]
    # These are the same source-line objects after the note/furniture split.
    # A guessed note label must not survive beside the faithful crop.
    for collection in (skel.regions, note_regions, candidates):
        for region in collection:
            region.lines = [ln for ln in region.lines if id(ln) not in owned]
            region.caption_lines = [ln for ln in region.caption_lines if id(ln) not in owned]
        collection[:] = [r for r in collection if r.lines or r.kind == "figure"]
    for block in regions:
        carriers = [r for r in skel.regions + candidates if r.kind == "figure"
                    and r.bbox[0] <= block.bbox[0] and r.bbox[1] <= block.bbox[1]
                    and r.bbox[2] >= block.bbox[2] and r.bbox[3] >= block.bbox[3]]
        note_context = (raw.is_page_scan and cover is None and not carriers
                        and all(id(line) in note_owned for line in block.lines))
        art = Region(kind="artwork", lines=list(block.lines),
                     bbox=block.bbox, reason="ocr_uncertain_region")
        skel.regions.append(art)
        if carriers:
            for region in carriers:
                region.reason = "ocr_uncertain_region"
        elif cover is None:
            # OCR boxes may omit antialiased edges/descenders. Retain a small
            # type-relative source margin, bounded by the actual page.
            pad = max(1.0, min(6.0, median(ln.size for ln in block.lines) * 0.3))
            box = (max(0.0, block.bbox[0] - pad), max(0.0, block.bbox[1] - pad),
                   min(raw.width, block.bbox[2] + pad), min(raw.height, block.bbox[3] + pad))
            if note_context:
                proposed=list(box)
                current_ids={id(line) for line in block.lines}
                for other in raw.text_blocks:
                    for line in other.lines:
                        if id(line) in current_ids:continue
                        b=line.bbox
                        if not (b[0]<proposed[2] and proposed[0]<b[2]
                                and b[1]<proposed[3] and proposed[1]<b[3]):continue
                        # Adjacent notes already have independent source roles.
                        # Their antialiased edges can overlap this padding without
                        # introducing prose or changing either note's reading.
                        if id(line) in note_owned:continue
                        # Only wholly detached source lines can bound padding.
                        # A collision with the note's own box is not resolvable.
                        above=block.bbox[1]-b[3]
                        below=b[1]-block.bbox[3]
                        if above>=1:
                            proposed[1]=max(proposed[1],(b[3]+block.bbox[1])/2)
                        elif below>=1:
                            proposed[3]=min(proposed[3],(block.bbox[3]+b[1])/2)
                        else:note_context=False
                if note_context:box=tuple(proposed)
            reason = 'ocr_uncertain_note' if note_context else 'ocr_uncertain_region'
            if note_context:
                # The already measured note keeps its opaque role. The exact
                # artwork/asset pair permits current inventory ownership; no
                # recognized digit or year becomes an admitted note reading.
                art.bbox = box
                art.reason = reason
            skel.regions.append(Region(kind="figure", bbox=box,
                reason=reason))
        else:
            for region in skel.regions:
                if region.kind == "figure" and region.image is cover:
                    region.reason = "ocr_uncertain_region"
    skel.reasons.append("ocr_uncertain_region")
    return kept_blocks, note_regions


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
    """Bounds of the retained lines, excluding surrounding page furniture."""
    boxes = [ln.bbox for ln in lines if ln.bbox]
    if not boxes:
        return fallback
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


#: A full-bleed cover or plate holds at most this many prose lines; more and
#: the page is text with a picture behind it, which is the background's job.
PLATE_PROSE_MAX = 8
#: A full-bleed cover may combine whole-page artwork with only a few title
#: lines.
PLATE_INK_COVER = 0.4


def _credible_line(line):
    return (sum(len(sp.text.split()) for sp in line.spans if not sp.uncertain)
            > sum(len(sp.text.split()) for sp in line.spans) / 2)


def _full_bleed_plate(raw, kept_blocks, pixel_probe):
    """Recognize a full-page artwork frame independently of its sparse text.

    A cover can have readable title lines over full-bleed art. Text density
    alone must not shred the original image into strips."""
    if pixel_probe is None or not raw.is_page_scan:
        return None
    images = [img for img in raw.images if img.full_page]
    if not images:
        return None
    # Low-confidence OCR fragments over a design are not evidence of prose.
    # Native lines count normally; the pixel gate still proves full-page art.
    credible = sum(_credible_line(ln) for _, kept in kept_blocks for ln in kept)
    if credible > PLATE_PROSE_MAX and not (getattr(raw,'text_layer_invisible',False)
            or getattr(raw,'text_layer_overpainted',False)):
        return None
    rect = (0.0, 0.0, raw.width, raw.height)
    try:
        if pixel_probe.coverage(rect) < PLATE_INK_COVER:
            return None
        if raw.text_layer_invisible or raw.text_layer_overpainted:
            # A dense interior chart can fill many sampling cells. A plate
            # extends to the page's edges as well; masked surrounding prose
            # cannot grant that role to the chart between its paragraphs.
            w,h=raw.width,raw.height
            edges=((0,0,w,h*.1),(0,h*.9,w,h),
                   (0,0,w*.1,h),(w*.9,0,w,h))
            if sum(pixel_probe.coverage(edge)>=PLATE_INK_COVER for edge in edges)<3:
                return None
    except Exception:
        return None
    return images[0]


def _attach_disputed_scan_markers(raw, skel):
    """Retain a disputed raised digit at its unique printed word boundary.

    Neither its transcription nor a note association is adopted. The complete
    original digit span is rendered as source pixels inside the measured body
    line, instead of a separate block sorted before that line.
    """
    if not raw.is_page_scan or not (raw.text_layer_invisible or raw.text_layer_overpainted):return
    for art in list(skel.regions):
        if art.kind!='artwork' or art.reason!='ocr_uncertain_region' or len(art.lines)!=1:continue
        marker=art.lines[0];spans=[sp for sp in marker.spans if sp.text.strip()]
        if not marker.transcription_uncertain or len(spans)!=1 or not re.fullmatch(r'\d{1,3}',spans[0].text):continue
        small=spans[0];targets=[]
        for region in skel.regions:
            if region.kind!='body':continue
            for index,line in enumerate(region.lines):
                em=line.size
                terminal=next((sp for sp in reversed(line.spans) if sp.text.strip()),None)
                if terminal is None or small.size>em*.72:continue
                if (abs(terminal.bbox[2]-small.bbox[0])<=em*.35
                        and line.bbox[1]-em*.5<=small.bbox[1]<line.bbox[1]+em*.2
                        and line.bbox[1]<small.bbox[3]<line.bbox[3]-em*.2):
                    targets.append((region,index))
        if len(targets)!=1:continue
        carriers=[r for r in skel.regions if r.kind=='figure' and r.reason==art.reason
            and r.bbox[0]<=marker.bbox[0] and r.bbox[1]<=marker.bbox[1]
            and r.bbox[2]>=marker.bbox[2] and r.bbox[3]>=marker.bbox[3]]
        if len(carriers)!=1 or carriers[0].caption_lines:continue
        carrier=carriers[0]
        if any(other is not art and other.kind=='artwork' and any(
                _overlap_share(line.bbox,carrier.bbox)>0 for line in other.lines)
                for other in skel.regions):continue
        region,index=targets[0]
        region.lines.insert(index+1,marker)
        region.inline_marker_boxes.append(marker.bbox)
        region.bbox=_lines_bbox(region.lines,region.bbox)
        skel.regions.remove(art);skel.regions.remove(carrier)


def _scan_note_words(lines):
    return [sp for line in lines for sp in line.spans
            if any(c.isalpha() for c in sp.text)]


def _corroborated_raised_scan_note_openings(block, small):
    """A later aligned raised digit corroborates a damaged opening's territory.

    This proves ownership only. Neither the damaged opening nor the later OCR
    digit is adopted as the note's number.
    """
    if not _raised_scan_note_opening(block, small):
        return False
    first = [sp for sp in block.lines[0].spans if sp.text.strip()]
    for line in block.lines[1:]:
        later = [sp for sp in line.spans if sp.text.strip()]
        if (len(later) < 2 or not later[0].text.strip().isdecimal()
                or line.bbox[1] <= block.lines[0].bbox[1]):
            continue
        row = extract.Block(block.number, line.bbox, [line])
        if (_raised_scan_note_opening(row, small, allow_numeric=True)
                and abs(later[0].bbox[0]-first[0].bbox[0]) <= small*.25
                and abs(later[1].bbox[0]-first[1].bbox[0]) <= small*.25):
            return True
    return False


def _corroborated_numeric_scan_note_openings(block, small):
    """Repeated aligned source digits corroborate modest physical glyph raise.

    Both openings must independently sit above the median word baseline. This
    pair-only proof does not lower the single-marker raise requirement, and its
    caller still requires the ordinary detached-footer body clearance.
    """
    openings = []
    for index, line in enumerate(block.lines):
        spans = [sp for sp in line.spans if sp.text.strip()]
        if len(spans) < 2:
            continue
        marker, following = spans[:2]
        if not re.fullmatch(r'\d{1,2}[.)]?', marker.text.strip()):
            if index == 0:
                return False
            continue
        words = _scan_note_words([line])
        if (len(words) < 2 or not following.text.lstrip()[:1].isalpha()
                or marker.size > small*.75
                or marker.bbox[1] > following.bbox[1]+small*.2
                or marker.bbox[3] > median(sp.bbox[3] for sp in words)-small*.05):
            return False
        if openings and (abs(marker.bbox[0]-openings[0][0].bbox[0]) > small*.25
                         or abs(following.bbox[0]-openings[0][1].bbox[0]) > small*.25):
            return False
        openings.append((marker, following))
    return len(openings) >= 2


def _scan_note_small_block(raw, block, style):
    """Measure OCR words, rather than an outlying line-size scalar."""
    alpha = _scan_note_words(block.lines)
    if (not alpha or not block.lines
            or any(sp.font != 'ocr' for line in block.lines for sp in line.spans)
            or any(CAPTION_LINE.match(line.stripped) or
                   _squashed_caption(line.stripped) for line in block.lines)
            or any(box[0] <= line.bbox[0] and box[1] <= line.bbox[1]
                   and box[2] >= line.bbox[2] and box[3] >= line.bbox[3]
                   for line in block.lines
                   for box in style.scan_table_label_boxes.get(raw.pno, []))):
        return None
    small = median(sp.size for sp in alpha)
    height = median(sp.bbox[3]-sp.bbox[1] for sp in alpha)
    if (height > style.body_size*.65
            or (small > style.body_size*.86
                and not (small <= style.body_size*.90
                         and (_corroborated_raised_scan_note_openings(block, small)
                              or _tight_raised_scan_note(block, small,
                                  style.body_size, numeric_only=True))))):
        return None
    return small, height


def _merged_scan_note_prefix(raw, block, style, prior, pixel_probe):
    """Source ink, not a punctuation reading, proves a merged footer prefix."""
    if pixel_probe is None or len(block.lines) != 1:
        return False
    spans = [sp for sp in block.lines[0].spans if sp.text.strip()]
    if not spans:
        return False
    first = spans[0]
    if (not re.match(r'^[^A-Za-z0-9]+[A-Za-z]', first.text)
            or not (first.uncertain or first.transcription_uncertain or first.encoding_unresolved)):
        return False
    try:
        proof = pixel_probe.source_raised_prefix(first.bbox)
    except (AttributeError, ValueError, RuntimeError):
        return False
    if not isinstance(proof, dict):
        return False
    prefix, lexical = proof.get('prefix_bbox'), proof.get('lexical_bbox')
    if (not prefix or not lexical or len(prefix) != 4 or len(lexical) != 4
            or not all(math.isfinite(v) for v in prefix+lexical)):
        return False
    # A slanted source line's far-right word can have an earlier top than its
    # raised opening. Measure clearance against the preceding body at this
    # actual lexical position, rather than that distant word's scalar box.
    local = [sp for sp in prior.spans if sp.text.strip()
             and sp.bbox[0] < lexical[2] and lexical[0] < sp.bbox[2]]
    return bool(local and lexical[1]-max(sp.bbox[3] for sp in local) >=
                style.body_size*1.2-.01)


def _preceding_damaged_scan_note(block, small, height, style, anchors):
    """A raised unknown opening precedes an independently owned source note.

    Degree-shaped OCR stays ineligible on its own. Only complete smaller
    hanging rows, original marker/word alignment and a nearby already owned
    numeric footer establish this additional territory without reading it.
    """
    if len(block.lines) < 2:
        return False
    spans = [sp for sp in block.lines[0].spans if sp.text.strip()]
    if len(spans) < 2:
        return False
    marker, word = spans[:2]
    label = marker.text.strip()
    if (not 1 <= len(label) <= 3 or '°' not in label or any(c.isalnum() for c in label)
            or not (marker.uncertain or marker.transcription_uncertain or marker.encoding_unresolved)
            or marker.size > small*.75 or not word.text.lstrip()[:1].isalpha()
            or marker.bbox[1] > word.bbox[1]+small*.2
            or marker.bbox[3] > word.bbox[3]-small*.2
            or any(not line.stripped[:1].isalpha()
                   or line.bbox[0] > word.bbox[0]-small*.5
                   or abs(line.bbox[0]-block.bbox[0]) > small*.25
                   for line in block.lines[1:])):
        return False
    for anchor in anchors:
        if not anchor.lines or not 0 <= anchor.bbox[1]-block.bbox[3] <= style.body_size*.5:
            continue
        opening = [sp for sp in anchor.lines[0].spans if sp.text.strip()]
        alpha = _scan_note_words(anchor.lines)
        if len(opening) < 2 or not alpha:
            continue
        first, following = opening[:2]
        anchor_size = median(sp.size for sp in alpha)
        baseline = median(sp.bbox[3] for sp in alpha)
        if (re.fullmatch(r'\d{1,2}[.)]?', first.text.strip())
                and all(sp.font == 'ocr' for line in anchor.lines for sp in line.spans)
                and anchor_size <= style.body_size*.9
                and median(sp.bbox[3]-sp.bbox[1] for sp in alpha) <= style.body_size*.65
                and first.size <= anchor_size*.75
                and first.bbox[3] <= baseline-anchor_size*.2
                and abs(first.bbox[0]-marker.bbox[0]) <= small*.25
                and abs(following.bbox[0]-word.bbox[0]) <= small*.5):
            return True
    return False


def _scan_note_candidates(raw, style, pixel_probe=None):
    em = style.body_size
    if (not em or not raw.is_page_scan
            or raw.source_geometry.get('space') != 'reading'):
        return []
    candidates = []
    for block in raw.text_blocks:
        measured = _scan_note_small_block(raw, block, style)
        if not measured or block.bbox[1] < raw.height*.65:
            continue
        small, height = measured
        above = [line for other in raw.text_blocks if other is not block
                 for line in other.lines
                 if line.bbox[3] <= block.bbox[1]
                 and line.bbox[0] < block.bbox[2] and block.bbox[0] < line.bbox[2]
                 and line.bbox[2]-line.bbox[0] >= em*12
                 and (alpha := _scan_note_words([line]))
                 and median(sp.size for sp in alpha) >= em*.95]
        if len(above) < 2:
            continue
        prior = max(above, key=lambda line: line.bbox[3])
        larger = _scan_note_words([prior])
        clearance = (height*.5 if _tight_raised_scan_note(block, small, em)
                     else em*(.5 if _close_raised_scan_note(block, small) else .8))
        if (small > median(sp.size for sp in larger)*.9
                or height > median(sp.bbox[3]-sp.bbox[1] for sp in larger)*.9
                or (block.bbox[1]-prior.bbox[3] < clearance-.01
                    and not _merged_scan_note_prefix(raw, block, style, prior, pixel_probe))
                or block.bbox[0] < prior.bbox[0]-em
                or block.bbox[2] > prior.bbox[2]+em):
            continue
        # A later ordinary paragraph defeats terminal-footer evidence. Font
        # medians over words do not let a lone inflated OCR word hide a note.
        if any(other.bbox[1] >= block.bbox[1]
               and other.bbox[0] < block.bbox[2] and block.bbox[0] < other.bbox[2]
               and (alpha := _scan_note_words(other.lines))
               and median(sp.size for sp in alpha) >= em*.95
               and any(not _furniture_reason(line, raw, style) for line in other.lines)
               and not (_tight_raised_scan_note(block, small, em)
                        and _adjacent_raised_scan_note(raw, other, style,
                            block, small, height, block.bbox, numeric_only=True))
               for other in raw.text_blocks if other is not block):
            continue
        candidates.append((block, prior, small, height))
    return candidates


def _raised_scan_note_opening(block, small, allow_numeric=False,
                              numeric_punctuation=False):
    spans = [sp for sp in block.lines[0].spans if sp.text.strip()]
    if len(spans) < 2:
        return False
    first, following = spans[:2]
    marker = first.text.strip()
    uncertain_mark = (not any(c.isalnum() for c in marker) and '°' not in marker
                      and (first.uncertain or first.transcription_uncertain
                           or first.encoding_unresolved))
    return (1 <= len(marker) <= 3
            and (uncertain_mark or (allow_numeric and (marker.isdecimal()
                 or (numeric_punctuation and re.fullmatch(r'\d{1,2}[.)]', marker)))))
            and first.size <= small*.75 and following.text.lstrip()[:1].isalpha()
            and first.bbox[1] <= following.bbox[1]+small*.2
            and first.bbox[3] <= following.bbox[3]-small*.2)


def _tight_raised_scan_note(block, small, body_size, numeric_only=False):
    """A raised opening and smaller hanging rows prove tight footer clearance.

    Literal digits, a year-only tail and a hanging ampersand are source shapes,
    not corrected readings or note numbers. Unmarked footer policy is unchanged.
    """
    if (len(block.lines) < 2 or not _raised_scan_note_opening(
            block, small, allow_numeric=True, numeric_punctuation=True)):
        return False
    opening = [sp for sp in block.lines[0].spans if sp.text.strip()]
    if numeric_only and not re.fullmatch(r'\d{1,2}[.)]?', opening[0].text.strip()):
        return False
    for line in block.lines[1:]:
        words = [sp for sp in line.spans if any(c.isalnum() for c in sp.text)]
        if (not words or line.bbox[0] > opening[1].bbox[0]-small*.5
                or abs(line.bbox[0]-block.bbox[0]) > small*.25
                or median(sp.size for sp in words) > body_size*.90
                or median(sp.bbox[3]-sp.bbox[1] for sp in words) > body_size*.65):
            return False
    return True


def _close_raised_scan_note(block, small):
    """A raised damaged label and hanging smaller rows prove a closer footer.

    The ordinary unmarked-footer clearance is unchanged. A single row, an
    unraised symbol or a reliable punctuation token supplies no such proof.
    """
    if len(block.lines) < 2 or not _raised_scan_note_opening(block, small):
        return False
    opening = [sp for sp in block.lines[0].spans if sp.text.strip()]
    return all(line.spans and line.stripped[:1].isalpha()
               and line.bbox[0] <= opening[1].bbox[0]-small*.5
               and abs(line.bbox[0]-block.bbox[0]) <= small*.25
               for line in block.lines[1:])


def _short_scan_note_tail(raw, block, style, preceding):
    """Keep a short hanging tail despite one inflated OCR word-size estimate."""
    em = style.body_size
    alpha = _scan_note_words(block.lines)
    if (len(block.lines) != 1 or not 1 <= len(alpha) <= 4
            or any(sp.font != 'ocr' for line in block.lines for sp in line.spans)
            or CAPTION_LINE.match(block.lines[0].stripped)
            or _squashed_caption(block.lines[0].stripped)
            or any(box[0] <= block.bbox[0] and box[1] <= block.bbox[1]
                   and box[2] >= block.bbox[2] and box[3] >= block.bbox[3]
                   for box in style.scan_table_label_boxes.get(raw.pno, []))
            or not 0 <= block.bbox[1]-preceding.bbox[3] <= em*.3
            or abs(block.bbox[0]-preceding.bbox[0]) > em*.25
            or not any(sp.size <= em*.86 and not (sp.uncertain or
                       sp.transcription_uncertain or sp.encoding_unresolved)
                       for sp in alpha)
            or any(sp.size > em*1.1 or sp.bbox[3]-sp.bbox[1] > em*.85
                   for sp in alpha)
            or median(sp.bbox[3]-sp.bbox[1] for sp in alpha) > em*.7):
        return False
    return True


def _adjacent_raised_scan_note(raw, block, style, seed, small, height, box,
                              numeric_only=False):
    """An aligned damaged opening continues an independently proved footer.

    A degree-shaped OCR token alone remains ineligible. Here the original
    raised-label and word starts, smaller type, and bounded row clearance
    establish source ownership without reading the damaged token as a digit.
    """
    em = style.body_size
    if (not block.lines or not -.1*em <= block.bbox[1]-box[3] <= em*.5
            or any(sp.font != 'ocr' for line in block.lines for sp in line.spans)
            or any(CAPTION_LINE.match(line.stripped) or
                   _squashed_caption(line.stripped) for line in block.lines)
            or any(bound[0] <= line.bbox[0] and bound[1] <= line.bbox[1]
                   and bound[2] >= line.bbox[2] and bound[3] >= line.bbox[3]
                   for line in block.lines
                   for bound in style.scan_table_label_boxes.get(raw.pno, []))):
        return False
    opening = [sp for sp in seed.lines[0].spans if sp.text.strip()]
    spans = [sp for sp in block.lines[0].spans if sp.text.strip()]
    alpha = _scan_note_words(block.lines)
    if len(opening) < 2 or len(spans) < 2 or not alpha:
        return False
    first, following = spans[:2]
    label = first.text.strip()
    measured = median(sp.size for sp in alpha)
    glyph_height = median(sp.bbox[3]-sp.bbox[1] for sp in alpha)
    marker = (bool(re.fullmatch(r'\d{1,2}[.)]?', label)) if numeric_only
              else (1 <= len(label) <= 3 and not any(c.isalnum() for c in label)
                    and (first.uncertain or first.transcription_uncertain
                         or first.encoding_unresolved)))
    # In an already proved footer, an aligned raised literal digit and smaller
    # physical ink can overcome a body-sized OCR font estimate. The default
    # damaged-symbol policy and its stricter typography remain unchanged.
    font_limit = min(em, small*1.35) if numeric_only else min(em*.95, small*1.25)
    height_limit = min(em*.75, height*1.35) if numeric_only else min(em*.7, height*1.25)
    return (marker
            and following.text.lstrip()[:1].isalpha()
            and measured <= font_limit
            and glyph_height <= height_limit
            and first.size <= measured*.75
            and first.bbox[1] <= following.bbox[1]+measured*.2
            and first.bbox[3] <= following.bbox[3]-measured*.2
            and abs(first.bbox[0]-opening[0].bbox[0]) <= em*.25
            and abs(following.bbox[0]-opening[1].bbox[0]) <= em*.25)


def _repeated_scan_note_lanes(raw_pages, body_size, table_labels):
    """Match unmarked footers to three other independently marked source pages.

    Matching measures type, physical column, hanging indent and footer height.
    An unmarked candidate never trains its own lane, and duplicate pages count
    once. Literal OCR and source pixels are not corrected by this proof.
    """
    style = BookStyle(body_size=body_size, scan_table_label_boxes=table_labels)
    candidates, teachers = [], []
    for raw in raw_pages:
        with unit_recovery.guard(raw.pno, "native_style_source_metrics"):
            for block, prior, small, height in _scan_note_candidates(raw, style):
                alpha = _scan_note_words([block.lines[0]])
                if not alpha:
                    continue
                side = (int((prior.bbox[0]+prior.bbox[2])/2 > raw.width/2)
                        if raw.width > raw.height*1.2 else 0)
                feature = (raw, block, prior, small, height, side,
                           (alpha[0].bbox[0]-prior.bbox[0])/body_size,
                           block.bbox[1]/raw.height)
                candidates.append(feature)
                if (block.bbox[1]-prior.bbox[3] >= body_size*1.2-.01
                        and _raised_scan_note_opening(block, small, allow_numeric=True)):
                    teachers.append(feature)
    matched = {}
    for raw, block, prior, small, height, side, indent, y in candidates:
        spans = [sp for sp in block.lines[0].spans if sp.text.strip()]
        if (not spans or not spans[0].text.lstrip()[:1].isalpha()
                or block.bbox[1]-prior.bbox[3] < body_size*1.2-.01):
            continue
        peers = [f for f in teachers if f[0].pno != raw.pno and f[5] == side
                 and abs(f[0].width/raw.width-1) <= .03
                 and abs(f[0].height/raw.height-1) <= .03
                 and abs(f[3]-small) <= body_size*.2
                 and abs(f[4]-height) <= body_size*.15
                 and abs(f[6]-indent) <= .45
                 and abs(f[7]-y)*raw.height <= body_size*3]
        if len({f[0].pno for f in peers}) >= 3:
            matched.setdefault(raw.pno, []).append(block.bbox)
    return matched


def _scan_note_source_openings(line, allow_degree=False):
    """Locate separately measured raised openings without reading a note number.

    A source row can contain several notes. Keep its original spans and row;
    these positions establish territory, not a corrected numeric transcript.
    """
    words = _scan_note_words([line])
    if not words:
        return []
    small = median(sp.size for sp in words)
    baseline = median(sp.bbox[3] for sp in words)
    spans = [sp for sp in line.spans if sp.text.strip()]
    openings = []
    for index, marker in enumerate(spans[:-1]):
        following = spans[index+1]
        token = marker.text.strip()
        numeric = bool(re.fullmatch(r'\d{1,2}[.)]?', token))
        damaged = (marker.uncertain and len(token) <= 3
                   and not any(c.isalpha() for c in token)
                   and not token.isalnum())
        if (not (numeric or damaged)
                or (any(c in token for c in '°º˚')
                    and not (allow_degree and marker.uncertain and token in '°º˚'))
                or not any(c.isalpha() for c in following.text)
                or marker.size > small*.75
                or marker.bbox[1] > following.bbox[1]+small*.2
                or marker.bbox[3] > min(following.bbox[3], baseline)-small*.15
                or following.bbox[0]-marker.bbox[2] < small*(.1 if numeric else .2)
                or following.bbox[0]-marker.bbox[2] > small*1.5):
            continue
        if index and (marker.bbox[0]-spans[index-1].bbox[2] < small*2):
            continue
        openings.append((marker, following))
    return openings


def _scan_note_source_row(raw, line, style):
    words = _scan_note_words([line])
    if (not words or any(sp.font != 'ocr' for sp in line.spans)
            or CAPTION_LINE.match(line.stripped) or _squashed_caption(line.stripped)
            or any(box[0] <= line.bbox[0] and box[1] <= line.bbox[1]
                   and box[2] >= line.bbox[2] and box[3] >= line.bbox[3]
                   for box in style.scan_table_label_boxes.get(raw.pno, []))):
        return None
    return median(sp.size for sp in words), median(sp.bbox[3]-sp.bbox[1] for sp in words)


def _scan_note_source_prior(raw, line, style):
    em = style.body_size
    above = [row for block in raw.text_blocks for row in block.lines
             if row is not line and row.bbox[3] <= line.bbox[1]
             and row.bbox[0] < line.bbox[2] and line.bbox[0] < row.bbox[2]
             and row.bbox[2]-row.bbox[0] >= em*12
             and (words := _scan_note_words([row]))
             and median(sp.size for sp in words) >= em*.95
             and not _scan_note_source_openings(row)]
    if len(above) < 2:
        return None
    above.sort(key=lambda row: row.bbox[3])
    prior = above[-1]
    window = above[-6:]
    pitches = [b.bbox[1]-a.bbox[1] for a, b in zip(window, window[1:])
               if em*.8 <= b.bbox[1]-a.bbox[1] <= em*2]
    return prior, median(pitches) if pitches else em*1.2


def _scan_note_body_column(raw, line, style):
    """Use preceding ordinary rows to measure a column, including short last rows."""
    em = style.body_size
    side = int((line.bbox[0]+line.bbox[2])/2 > raw.width/2)
    above = [row for block in raw.text_blocks for row in block.lines
             if row.bbox[3] <= line.bbox[1]
             and (raw.width <= raw.height*1.2 or
                  int((row.bbox[0]+row.bbox[2])/2 > raw.width/2) == side)
             and row.bbox[2]-row.bbox[0] >= em*12
             and (words := _scan_note_words([row]))
             and median(sp.size for sp in words) >= em*.95
             and not _scan_note_source_openings(row)
             and not CAPTION_LINE.match(row.stripped) and not _squashed_caption(row.stripped)]
    if len(above) < 2:
        return None
    return min(row.bbox[0] for row in above), max(row.bbox[2] for row in above)


def _scan_note_footer_profiles(raw_pages, body_size, table_labels):
    """Learn marker and word lanes only from independently detached source notes."""
    style = BookStyle(body_size=body_size, scan_table_label_boxes=table_labels)
    profiles = []
    for raw in raw_pages:
        with unit_recovery.guard(raw.pno, "native_style_source_metrics"):
            for block, prior, small, height in _scan_note_candidates(raw, style):
                openings = _scan_note_source_openings(block.lines[0])
                if (not openings or block.bbox[1]-prior.bbox[3] < body_size*1.2-.01
                        or small > body_size*.86 or height > body_size*.65):
                    continue
                marker, word = openings[0]
                column = _scan_note_body_column(raw, block.lines[0], style)
                if not column:
                    continue
                profiles.append(dict(page=raw.pno, width=raw.width, height=raw.height,
                    side=int((prior.bbox[0]+prior.bbox[2])/2 > raw.width/2)
                        if raw.width > raw.height*1.2 else 0,
                    marker_indent=(marker.bbox[0]-column[0])/body_size,
                    word_indent=(word.bbox[0]-column[0])/body_size,
                    font=small, glyph_height=height))
    return profiles


def _scan_note_footer_peers(raw, line, prior, measured, openings, style):
    if not openings:
        return False
    em = style.body_size
    small, height = measured
    marker, word = openings[0]
    column = _scan_note_body_column(raw, line, style)
    if not column:
        return False
    side = int((prior.bbox[0]+prior.bbox[2])/2 > raw.width/2) if raw.width > raw.height*1.2 else 0
    peers = [p for p in getattr(style, 'scan_note_footer_profiles', [])
             if p['page'] != raw.pno and p['side'] == side
             and abs(p['width']/raw.width-1) <= .03
             and abs(p['height']/raw.height-1) <= .03
             and abs(p['marker_indent']-(marker.bbox[0]-column[0])/em) <= .5
             and abs(p['word_indent']-(word.bbox[0]-column[0])/em) <= .5
             and abs(p['font']-small) <= em*.3
             and abs(p['glyph_height']-height) <= em*.2]
    return len({p['page'] for p in peers}) >= 3


def _clustered_scan_note_territories(raw, style, pixel_probe=None, note_anchors=()):
    """Find clustered source note territory without promoting body prose.

    A later smaller note can disprove an apparent earlier note opening."""
    em = style.body_size
    if (not em or not raw.is_page_scan
            or raw.source_geometry.get('space') != 'reading'):
        return []
    rows = [line for block in raw.text_blocks for line in block.lines
            if not _furniture_reason(line, raw, style)]
    rows.sort(key=lambda line: (line.bbox[1], line.bbox[0]))
    already = {id(line) for note in note_anchors for line in note.lines}
    occupied = set()
    notes = []
    for opening in rows:
        if id(opening) in occupied or opening.bbox[1] < raw.height*.25:
            continue
        owner = next(block for block in raw.text_blocks if opening in block.lines)
        prefix = owner.lines[:owner.lines.index(opening)]
        if any(id(line) not in already for line in prefix):
            continue
        measured = _scan_note_source_row(raw, opening, style)
        openings = _scan_note_source_openings(opening)
        context = _scan_note_source_prior(raw, opening, style)
        if not measured or not context:
            continue
        prior, pitch = context
        column_bounds = _scan_note_body_column(raw, opening, style)
        if not column_bounds:
            continue
        left, right = column_bounds
        small, height = measured
        ruled = (not prefix and _scan_note_small_block(raw, owner, style)
                 and _pixel_divided_scan_note(raw, owner, style, prior, pixel_probe,
                                              short_rule=True))
        if not openings and not ruled:
            continue
        peers = _scan_note_footer_peers(raw, opening, prior, measured, openings, style)
        ordinary = small <= em*.86 and height <= em*.65
        numeric_rows = [pair for row in owner.lines
                        for pair in _scan_note_source_openings(row)
                        if re.fullmatch(r'\d{1,2}[.)]?', pair[0].text.strip())]
        paired = (openings and len(numeric_rows) >= 2
                  and openings[0][0] is numeric_rows[0][0]
                  and openings[0][1] is numeric_rows[0][1]
                  and small <= em*1.1 and height <= em*.8
                  and all(abs(a.bbox[0]-numeric_rows[0][0].bbox[0]) <= em*.25
                          and abs(b.bbox[0]-numeric_rows[0][1].bbox[0]) <= em*.25
                          for a,b in numeric_rows[:2])
                  and opening.bbox[1]-prior.bbox[3] >= em*1.2-.01)
        # A separate OCR block must not hide the second source opening. Only
        # an independently owned, smaller numeric note supplies this context;
        # this candidate cannot create the anchor used to prove itself.
        if (not paired and openings
                and re.fullmatch(r'\d{1,2}[.)]?', openings[0][0].text.strip())
                and small <= em*1.1 and height <= em*.8
                and opening.bbox[1]-prior.bbox[3] >= em*1.2-.01):
            for anchor in note_anchors:
                if not anchor.lines or not 0 <= anchor.bbox[1]-opening.bbox[3] <= em*.5:
                    continue
                row = anchor.lines[0]
                metrics = _scan_note_source_row(raw, row, style)
                marks = _scan_note_source_openings(row)
                if (metrics and metrics[0] <= em*.86 and metrics[1] <= em*.65
                        and marks and re.fullmatch(r'\d{1,2}[.)]?', marks[0][0].text.strip())
                        and abs(marks[0][0].bbox[0]-openings[0][0].bbox[0]) <= em*.25
                        and abs(marks[0][1].bbox[0]-openings[0][1].bbox[0]) <= em*.25):
                    paired = True
                    break
        if (not ordinary and not ((peers or ruled or paired)
                                  and small <= em*1.1 and height <= em*.8)
                or opening.bbox[0] < left-em
                or opening.bbox[2] > right+em):
            continue
        gap = opening.bbox[1]-prior.bbox[3]
        if gap < (height*.2 if peers else height*1.1)-.01:
            continue
        if len(openings) > 1 and gap < em*1.2-.01:
            continue
        marker, word = openings[0] if openings else (opening.spans[0], _scan_note_words([opening])[0])
        column = [line for line in rows if line.bbox[1] >= opening.bbox[1]
                  and line.bbox[0] < right and left < line.bbox[2]]
        kept = [opening]
        for following in column:
            if following is opening:
                continue
            last = kept[-1]
            measured_next = _scan_note_source_row(raw, following, style)
            next_openings = _scan_note_source_openings(following, allow_degree=True)
            alpha = _scan_note_words([following])
            aligned = bool(next_openings and
                abs(next_openings[0][0].bbox[0]-marker.bbox[0]) <= em*.5 and
                abs(next_openings[0][1].bbox[0]-word.bbox[0]) <= em*.5)
            if ruled and next_openings:
                aligned = (abs(next_openings[0][0].bbox[0]-marker.bbox[0]) <= em
                           and em*.3 <= next_openings[0][1].bbox[0]-marker.bbox[0] <= em*1.5)
            parts = [sp for sp in following.spans if sp.text.strip()]
            degree_opening = bool(parts and any(c in parts[0].text for c in '°º˚'))
            prior_words = _scan_note_words([last])
            leading = (median(sp.bbox[1] for sp in alpha)-
                       median(sp.bbox[1] for sp in prior_words)) if alpha and prior_words else 0
            overlapping_fragment = (following in owner.lines and
                opening.bbox[0] <= following.bbox[0] < following.bbox[2] <= opening.bbox[2]
                and opening.bbox[1] <= following.bbox[1] < following.bbox[3] <= opening.bbox[3]+em*.1)
            short_tail = (bool(peers or ruled) and 1 <= len(alpha) <= 4
                          and 0 <= following.bbox[1]-last.bbox[3] <= em*.3
                          and abs(alpha[0].bbox[0]-left) <= em*.75
                          and measured_next and measured_next[0] <= em*.9
                          and measured_next[1] <= em*.7)
            if (not measured_next or not alpha
                    or following.bbox[1]-last.bbox[3] > em*.65
                    or (not overlapping_fragment and not short_tail and not 0 < leading <= pitch*.95)
                    or following.bbox[0] < left-em
                    or following.bbox[2] > right+em
                    or (degree_opening and not aligned)):
                break
            font_next, height_next = measured_next
            smaller = font_next <= em*.90 and height_next <= em*.70
            outlying = (peers or ruled or paired) and font_next <= em*1.1 and height_next <= em*.8
            continuation_indent = (left-em*.5 <= alpha[0].bbox[0]
                                   <= word.bbox[0]+em*.25)
            continuation_indent = continuation_indent or (overlapping_fragment and
                all(sp.uncertain or sp.transcription_uncertain or sp.encoding_unresolved for sp in alpha))
            if not ((aligned or continuation_indent) and (smaller or outlying)):
                break
            kept.append(following)
        # Ordinary source prose after this territory defeats footer ownership.
        last = kept[-1]
        if any(line not in kept and line.bbox[1] >= last.bbox[1]
               and (words := _scan_note_words([line]))
               and median(sp.size for sp in words) >= em*.95
               for line in column):
            continue
        # A smaller multi-row block immediately before an independently proved
        # opening can be the unmarked beginning of the printed footer. Keep
        # its literal rows only with ample clearance above that complete block.
        for block in raw.text_blocks:
            if (len(block.lines) < 2 or block.bbox[3] > opening.bbox[1]
                    or opening.bbox[1]-block.bbox[3] > em*.65
                    or block.bbox[0] < left-em
                    or block.bbox[2] > right+em
                    or not block.bbox[0] < right
                    or not left < block.bbox[2]):
                continue
            metrics = [_scan_note_source_row(raw, line, style) for line in block.lines]
            before = _scan_note_source_prior(raw, block.lines[0], style)
            if (any(not m or m[0] > em*.86 or m[1] > em*.65 for m in metrics)
                    or not before or block.bbox[1]-before[0].bbox[3] < em*1.2-.01
                    or any(_scan_note_source_openings(line) for line in block.lines)
                    or not _pixel_divided_scan_note(raw, block, style, before[0], pixel_probe)):
                continue
            kept = list(block.lines)+kept
        occupied.update(id(line) for line in kept)
        unowned = [line for line in kept if id(line) not in already]
        if unowned:
            notes.append(Region('note', lines=unowned, number=None,
                bbox=_lines_bbox(unowned, opening.bbox), uncertain=True,
                reason='pixel_note_territory'))
    return notes


def _pixel_divided_scan_note(raw, block, style, prior, pixel_probe, short_rule=False):
    """Prove an unmarked smaller terminal footer with an isolated source rule.

    The caller supplies the existing OCR, typography, terminal-column and
    caption/table vetoes. The rule query stays below every preceding glyph,
    including a short body row after the last wide typography reference.
    """
    em = style.body_size
    first = next((span for span in block.lines[0].spans if span.text.strip()), None) if block.lines else None
    if (pixel_probe is None or len(block.lines) < 2
            or not all(_scan_note_words([line]) for line in block.lines)
            or not first or not (first.text.lstrip()[:1].isalpha()
                or (first.uncertain and re.fullmatch(r'\d{1,2}[^A-Za-z0-9\s]{1,2}', first.text.strip())))):
        return False
    earlier = [sp for other in raw.text_blocks if other is not block
               for line in other.lines for sp in line.spans
               if sp.text.strip() and sp.bbox[3] <= block.bbox[1]
               and sp.bbox[0] < block.bbox[2] and block.bbox[0] < sp.bbox[2]]
    if not earlier:
        return False
    bottom = max(sp.bbox[3] for sp in earlier)
    if block.bbox[1]-bottom < em*1.2-.01:
        return False
    column = _scan_note_body_column(raw, block.lines[0], style) if short_rule else None
    left, right = column if column else (prior.bbox[0], prior.bbox[2])
    query = (left-em, bottom+.5,
             max(right, block.bbox[2])+em, block.bbox[1]-.5)
    try:
        # Divider authority uses the complete immutable original ink extent.
        # Legacy clipped bounds remain calibrated for compact marker callers.
        bounds = getattr(pixel_probe, 'original_ink_bounds', None)
        if bounds is None:
            bounds = pixel_probe.source_ink_bounds
        ink = bounds(query)
        separator = pixel_probe.thin_separator(query, max_thickness=em*.2)
        if short_rule and ink:
            width_ratio = (ink[2]-ink[0])/max(1,right-left)
            if .35 <= width_ratio <= .60 and abs(ink[0]-left) <= em:
                separator = pixel_probe.thin_separator(
                    (ink[0],query[1],ink[2],query[3]),max_thickness=em*.2)
    except (AttributeError, ValueError, RuntimeError):
        return False
    width = right-left
    return bool(ink and isinstance(separator, dict)
                and separator.get('max_thickness', em) <= em*.2
                and separator.get('occupied_fraction', 0) >= .8
                and (width*.8 <= ink[2]-ink[0] <= width*1.08
                     or (short_rule and width*.35 <= ink[2]-ink[0] <= width*.60))
                and abs(ink[0]-left) <= em
                and query[0] <= ink[0] < ink[2] <= query[2]
                and ink[1] >= bottom+em*.5
                and ink[3] <= block.bbox[1]-.5)


def _detached_scan_note_territories(raw, style, pixel_probe=None, note_anchors=()):
    """Retain complete source-proved OCR footers as unnumbered uncertain notes."""
    em = style.body_size
    notes, ownership = [], set()
    for block, prior, small, height in _scan_note_candidates(raw, style, pixel_probe):
        if id(block) in ownership:
            continue
        spans = [sp for sp in block.lines[0].spans if sp.text.strip()]
        if not spans:
            continue
        first, box = spans[0], block.bbox
        gap = box[1]-prior.bbox[3]
        proven = (gap >= em*1.2-.01 and _raised_scan_note_opening(block, small))
        proven = proven or (gap >= em*1.2-.01
                            and _corroborated_numeric_scan_note_openings(block, small))
        close_raised = (_close_raised_scan_note(block, small)
                        or _tight_raised_scan_note(block, small, em))
        proven = proven or (close_raised and gap >= em*.5-.01)
        proven = proven or (_tight_raised_scan_note(block, small, em)
                            and gap >= height*.5-.01)
        proven = proven or (box in style.scan_note_lane_boxes.get(raw.pno, [])
                           and gap >= em*1.2-.01)
        proven = proven or _pixel_divided_scan_note(raw, block, style, prior, pixel_probe)
        preceding = (gap >= em*1.2-.01 and
                     _preceding_damaged_scan_note(block, small, height, style, note_anchors))
        proven = proven or preceding or _merged_scan_note_prefix(raw, block, style, prior, pixel_probe)
        if not proven:
            if pixel_probe is None or not first.text.lstrip()[:1].isalpha():
                continue
            query = (max(min(prior.bbox[0], box[0]), first.bbox[0]-em*2),
                     min(block.lines[0].bbox[1], first.bbox[1])-em*.4,
                     first.bbox[0]-.5, first.bbox[3])
            if query[2] <= query[0] or any(
                    sp.bbox[0] < query[2] and query[0] < sp.bbox[2]
                    and sp.bbox[1] < query[3] and query[1] < sp.bbox[3]
                    for other in raw.text_blocks for line in other.lines for sp in line.spans):
                continue
            try:
                ink = pixel_probe.source_ink_bounds(query)
            except (AttributeError, ValueError, RuntimeError):
                continue
            if (not ink or not (small*.2 <= ink[2]-ink[0] <= small*.55
                    and small*.2 <= ink[3]-ink[1] <= small*.55
                    and ink[0] > query[0] and ink[1] > query[1]
                    and ink[2] < query[2]-.2 and ink[3] < query[3]
                    and first.bbox[3]-ink[3] >= small*.2
                    and 0 < first.bbox[0]-ink[2] <= em)):
                continue
            box = (min(box[0], ink[0]), min(box[1], ink[1]),
                   max(box[2], ink[2]), max(box[3], ink[3]))
        lines = list(block.lines)
        ownership.add(id(block))
        # Continue only tight, uniformly smaller source blocks in this same
        # terminal column. A new numeral is neither required nor inferred.
        for following in sorted(raw.text_blocks, key=lambda b: b.bbox[1]):
            if preceding and any(line in anchor.lines for line in following.lines
                                 for anchor in note_anchors):
                continue
            if id(following) in ownership or following.bbox[1] < block.bbox[1]:
                continue
            if not (following.bbox[0] < prior.bbox[2]
                    and prior.bbox[0] < following.bbox[2]):
                continue
            if (following.bbox[1]-box[3] > em*.5
                    or following.bbox[0] < prior.bbox[0]-em
                    or following.bbox[2] > prior.bbox[2]+em
                    or not (_scan_note_small_block(raw, following, style)
                            or (close_raised and _short_scan_note_tail(
                                raw, following, style, lines[-1]))
                            or (close_raised and _adjacent_raised_scan_note(
                                raw, following, style, block, small, height, box))
                            or (close_raised and _adjacent_raised_scan_note(
                                raw, following, style, block, small, height, box,
                                numeric_only=True)))):
                break
            lines.extend(following.lines)
            ownership.add(id(following))
            box = (min(box[0], following.bbox[0]), min(box[1], following.bbox[1]),
                   max(box[2], following.bbox[2]), max(box[3], following.bbox[3]))
        notes.append(Region('note', lines=lines, number=None,
            bbox=box, uncertain=True, reason='pixel_note_territory'))
    notes.extend(_clustered_scan_note_territories(raw, style, pixel_probe, tuple(note_anchors)+tuple(notes)))
    return notes


def _pixel_proved_native_note_groups(blocks, style):
    """Keep source-qualified native openings and complete pixel-proved groups.

    Territory is already independently proved by original pixels. A number
    requires its own separate reliable raised literal span; OCR and decoded
    glyph readings grant no identity. Original measured continuations stay in
    their native source lane, while unqualified rows remain unknown notes.
    """
    groups = []
    for block in blocks:
        if any(span.font == 'ocr' for line in block.lines for span in line.spans):
            groups.append(Region('note', lines=list(block.lines), number=None,
                bbox=block.bbox, uncertain=True, reason='pixel_note_territory'))
            continue
        for line in block.lines:
            parts = [span for span in line.spans if span.text.strip()]
            number, opening = None, False
            if (len(parts) >= 2
                    and re.fullmatch(r'[1-9]\d{0,2}[.)]?', parts[0].text.strip())
                    and (words := _scan_note_words([line]))):
                marker, following = parts[:2]
                small = median(word.size for word in words)
                baseline = median(word.bbox[3] for word in words)
                gap = following.bbox[0]-marker.bbox[2]
                # Uncertainty removes numeric authority, not the separately
                # measured raised opening and its complete source boundary.
                opening = (any(c.isalpha() for c in following.text)
                    and marker.size <= small*.75
                    and marker.bbox[1] <= following.bbox[1]+small*.2
                    and marker.bbox[3] <= min(following.bbox[3], baseline)-small*.15
                    and small*.1 <= gap <= small*1.5)
                if opening and not (marker.uncertain
                        or marker.transcription_uncertain or marker.encoding_unresolved):
                    candidate = Region('note', lines=[line], bbox=line.bbox,
                        number=int(marker.text.strip().rstrip('.)')))
                    if _reliable_scan_note_number(candidate):
                        number = candidate.number
            previous = groups[-1] if groups else None
            same_native_lane = (previous is not None
                and all(span.font != 'ocr' for row in previous.lines for span in row.spans)
                and all(span.font != 'ocr' for span in parts)
                and abs(line.bbox[0]-previous.bbox[0]) <= style.body_size
                and line.bbox[0] < previous.bbox[2]
                and previous.bbox[0] < line.bbox[2])
            if not opening and number is None and same_native_lane:
                previous.lines.append(line)
                previous.bbox = _lines_bbox(previous.lines, previous.bbox)
            else:
                groups.append(Region('note', lines=[line], number=number,
                    bbox=line.bbox, uncertain=number is None,
                    reason='pixel_note_territory'))
    return groups


def _split_off_notes(raw, style, skel, pixel_probe=None, _detached_pass=True):
    """Separate note text using source zone, type size and opening evidence.

    A note number can occupy its own line at note size. A body degree or a
    bare number without a following sentence is insufficient."""
    from . import note_evidence
    field_rows = (_scan_numeric_field_rows(
        [(block, block.lines) for block in raw.text_blocks], style)
        if raw.is_page_scan else set())
    field_rows |= _source_contents_rows(raw, style) | _source_parallel_table_rows(raw, style)
    if _detached_pass and raw.is_page_scan:
        # Original-page validation supplies independent anchors before any
        # unknown territory is removed; a candidate cannot teach its own role.
        body, notes = _split_off_notes(raw, style, skel, pixel_probe,
                                      _detached_pass=False)
        unknown = _detached_scan_note_territories(raw, style, pixel_probe, notes)
        if not unknown:
            return body, notes
    else:
        unknown = []
    if unknown:
        # Keep the original page available to the older opening validation.
        # Removing real later notes first would let an earlier chart degree
        # become the only apparent numeric opening and lose its veto.
        owned = {id(line) for note in unknown for line in note.lines}
        body = [replace(block, lines=kept, bbox=_lines_bbox(kept, block.bbox))
                for block in body
                if (kept := [line for line in block.lines if id(line) not in owned])]
        notes = [replace(note, lines=kept, bbox=_lines_bbox(kept, note.bbox))
                 for note in notes
                 if (kept := [line for line in note.lines if id(line) not in owned])]
        order = {id(line): i for i, line in enumerate(
            line for block in raw.text_blocks for line in block.lines)}
        return body, sorted(notes+unknown, key=lambda note: min(
            (order[id(line)] for line in note.lines), default=len(order)))
    # A short body page can leave most of its printed area to footnotes. The
    # isolated divider and smaller raised opening prove that territory even
    # when its first note is above the fixed lower-page percentage.
    if pixel_probe is not None:
        contexts = opaque_page_regions(raw, style, pixel_probe)
        note_lines = {id(ln) for region in contexts
                      if region.kind == 'artwork' and region.reason == 'ocr_uncertain_note'
                      for ln in region.lines if not _furniture_reason(ln, raw, style)}
        if note_lines:
            # Pixel evidence establishes territory, not marker identities or
            # the association of citations across extraction blocks. Retain
            # independently qualified complete groups before projecting newly
            # proved rows into the notes lane.
            _, established = _split_off_notes(raw, style, skel)
            notes = [note for note in established if note.lines
                     and all(id(line) in note_lines for line in note.lines)]
            established_lines = {id(line) for note in notes for line in note.lines}
            body, newly_owned = [], []
            for block in sorted(raw.text_blocks, key=lambda b: (b.bbox[1], b.bbox[0])):
                kept = [ln for ln in block.lines if id(ln) not in note_lines]
                owned = [ln for ln in block.lines if id(ln) in note_lines
                         and id(ln) not in established_lines]
                if kept:
                    body.append(extract.Block(block.number, _lines_bbox(kept, block.bbox), kept))
                if owned:
                    newly_owned.append(extract.Block(block.number,
                        _lines_bbox(owned, block.bbox), owned))
            notes.extend(_pixel_proved_native_note_groups(newly_owned, style))
            order = {id(line): i for i, line in enumerate(
                line for block in raw.text_blocks for line in block.lines)}
            notes.sort(key=lambda note: min(order[id(line)] for line in note.lines))
            return body, notes
    pixel_note = note_evidence.pixel_separated_note(raw,style.body_size,pixel_probe)
    if pixel_note is not None:
        body,block,number,separator = pixel_note
        return body,[Region(kind='note',lines=list(block.lines),number=number,
                            bbox=block.bbox,reason='native_pixel_separated_note')]
    ruled = note_evidence.ruled_region(raw)
    if ruled is not None and note_evidence.raised_opening(ruled[1][0]):
        body, notes = ruled[0], []
        for block in ruled[1]:
            notes.extend(_notes_in_block(block, notes))
        for note in notes:
            note.reason = "native_ruled_note"
        body = note_evidence.bind_callouts(body, {n.number for n in notes if n.number is not None})
        return body, notes
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
    def detached_raised(block):
        # Some scans set notes only slightly smaller than body text. Size alone
        # cannot distinguish them from a quotation or numbered prose: require
        # a literal raised opening digit and a separate lower-page territory.
        if (block.size > style.body_size * .98 or block.bbox[1] < raw.height * .65
                or not note_evidence.raised_opening(block)):
            return False
        preceding = [other.bbox[3] for other in raw.text_blocks
                     if other is not block and other.bbox[3] <= block.bbox[1]]
        return bool(preceding and block.bbox[1]-max(preceding) >= style.body_size * 1.2)

    anchors=[block for block in raw.text_blocks if detached_raised(block)]
    damaged_openings=set()
    for block in raw.text_blocks:
        spans=[sp for sp in block.lines[0].spans if sp.text.strip()] if block.lines else []
        if (len(spans)<2 or block.size>style.body_size*.98
                or len(spans[0].text.strip())>3 or spans[0].text.strip().isalnum()
                or spans[0].size>block.size*.85
                or not spans[1].text.lstrip()[:1].isupper()):continue
        for anchor in anchors:
            label=next(sp for sp in anchor.lines[0].spans if sp.text.strip())
            # Repeated label/word starts below a proven note establish a new
            # note territory. Its damaged label remains unknown and qualified.
            if (anchor.bbox[1]<block.bbox[1] and block.bbox[1]-anchor.bbox[3]<=style.body_size*3
                    and abs(spans[0].bbox[0]-label.bbox[0])<=style.body_size*.5
                    and 0<=spans[1].bbox[0]-spans[0].bbox[2]<=style.body_size):
                damaged_openings.add(id(block));break

    for blk in raw.text_blocks:
        # A folio below the notes is still page furniture. Giving it to the
        # note parser first would append a bare final number to the last note.
        furniture=bool(blk.lines and all(_furniture_reason(ln,raw,style) for ln in blk.lines))
        if furniture or blk.bbox[1] < zone_top or not style.body_size:
            body.append(blk)
        elif (blk.size > style.body_size * FN_SIZE_RATIO
                and not detached_raised(blk) and id(blk) not in damaged_openings):
            body.append(blk)
        else:
            eligible.append(blk)

    openings = []
    for blk in eligible:
        per_block = []
        pairings = _source_note_marker_pairs(blk)
        for pos, ln in enumerate(blk.lines):
            following = pairings.get(id(ln),
                blk.lines[pos + 1] if pos + 1 < len(blk.lines) else None)
            number = _note_number(
                ln, blk.size, opening=(pos == 0), after=None, following=following)
            strength = _opening_strength(ln, blk.size)
            if (number is not None and id(ln) not in field_rows and (not raw.is_page_scan
                    or _scan_note_opening_context(ln, following, strength, blk.size))):
                per_block.append((pos, number, strength))
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
        preceding_measure=[ln for block in raw.text_blocks for ln in block.lines
            if ln.bbox[3]<=first.lines[pos].bbox[1] and ln.size>=style.body_size*.95
            and ln.bbox[2]-ln.bbox[0]>=style.body_size*12
            and ln.bbox[0]<first.bbox[2] and first.bbox[0]<ln.bbox[2]]
        measure=(max(preceding_measure,key=lambda ln:ln.bbox[3]).bbox
                 if preceding_measure else first.bbox)
        if pos and _opening_strength(first.lines[pos], first.size)=='raised':
            label=next(s for s in first.lines[pos].spans if s.text.strip())
            prefix=first.lines[:pos]
            damaged=[]
            for line in prefix:
                parts=[s for s in line.spans if s.text.strip()]
                damaged.append(bool(len(parts)>=2 and len(parts[0].text.strip())<=3
                    and not parts[0].text.strip().isalnum()
                    and parts[0].size<=line.size*.85 and line.size<=style.body_size*.85
                    and abs(parts[0].bbox[0]-label.bbox[0])<=style.body_size*.5
                    and parts[1].text.lstrip()[:1].isalpha()))
            above=[ln for block in raw.text_blocks for ln in block.lines
                if ln.bbox[3]<=prefix[0].bbox[1] and ln not in prefix
                and ln.bbox[0]<first.bbox[2] and first.bbox[0]<ln.bbox[2]
                and ln.size>=style.body_size*.95]
            if (all(damaged) and above
                    and prefix[0].bbox[1]-max(ln.bbox[3] for ln in above)>=style.body_size
                    and first.lines[pos].bbox[1]-prefix[-1].bbox[3]<=style.body_size):
                # The raised neighboring note and detached smaller source zone
                # establish ownership. The damaged opening remains unnumbered.
                notes.append(Region('note',lines=prefix,number=None,uncertain=True,
                    bbox=_lines_bbox(prefix,first.bbox),reason='damaged_note_label'))
                first=replace(first,lines=first.lines[pos:],bbox=_lines_bbox(first.lines[pos:],first.bbox))
                pos=0
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
            # A neighboring page's diagram label/caption does not become a
            # continuation merely because its baseline falls below this note.
            if blk.bbox[0]>=measure[2] or blk.bbox[2]<=measure[0]:
                body.append(blk);continue
            blk = keep_adjacent_captions(blk, notes[-1].bbox)
            if blk is not None:
                notes.extend(_notes_in_block(blk, notes, distinct_opening=id(blk) in damaged_openings))

    if notes:
        # Zone boundaries use extraction indices, which can place a marker
        # after its citation or in the neighboring raw block. Preserve those
        # established markers' original lexical associations before artwork
        # discovery, rather than leaving complete source notes in body crops.
        footer_lines = [line for block in eligible for line in block.lines
                        if id(line) not in field_rows
                        and not _squashed_caption(line.stripped)
                        and not _furniture_reason(line, raw, style)]
        footer = extract.Block(number=-1, lines=footer_lines,
                               bbox=_lines_bbox(footer_lines, (0, 0, 0, 0)))
        pairs = _source_note_marker_pairs(footer)
        body, notes = _complete_source_note_pairs(body, notes, pairs)
        numbers = [n.number for n in notes if n.number is not None]
        if len(numbers) != len(set(numbers)):
            skel.reasons.append("duplicate_note_numbers")
    return body, notes


def _scan_note_opening_context(line, following, strength, block_size):
    """A weak source label does not establish a printed prose-note zone.

    A separately extracted numeral must share a measured baseline and hanging
    word gap with the note text, or precede its next line in the same source
    lane. Reading-order adjacency alone also pairs a contents destination at
    the right margin with the next topic at the left margin. Merged or damaged
    openings lack raised-marker geometry, so they also need a lexical word
    after the label, rather than only short symbols and numerical fields of a
    chart axis or timeline. Separately raised openings keep their own proof.
    """
    if re.fullmatch(r"\d{1,3}", line.stripped):
        if following is None:
            return False
        marks = [sp for sp in line.spans if sp.text.strip()]
        words = [sp for sp in following.spans if any(c.isalpha() for c in sp.text)]
        if len(marks) != 1 or not words:
            return False
        marker, word = marks[0], words[0]
        # Spaced native lettering can make a block's median type smaller
        # than its actual first lexical glyph. Use that original glyph's size
        # for the measured hanging gap, without changing its source spelling.
        scale = word.size or block_size or marker.size
        if not scale:
            return False
        baseline = (word.origin_y or word.bbox[3])-(marker.origin_y or marker.bbox[3])
        gap = word.bbox[0]-marker.bbox[2]
        # Extraction can put a raised marker on a separate source row even
        # though the printed note text shares its baseline. Tabs and other
        # whitespace spans do not enlarge the marker's measured paper lane.
        same_row = (abs(baseline) <= scale*.5
                    and -.25*scale <= gap <= 3*scale)
        next_row = (0 < baseline <= 2.5*scale
                    and abs(word.bbox[0]-marker.bbox[0]) <= 3*scale
                    and -.25*scale <= word.bbox[1]-marker.bbox[3] <= 2*scale)
        return same_row or next_row
    if strength == 'raised':
        return True
    opening = _MERGED_NOTE_NUMBER.match(line.stripped) or _GLYPH_NOTE_NUMBER.match(line.stripped)
    if opening is None:
        return False
    rest = line.stripped[opening.end(1):]
    return any(len(word) >= 3 for word in re.findall(r"[^\W\d_]+", rest))


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


def _source_note_marker_pairs(block):
    """Pair bare source markers with uniquely measured note text in one block.

    Extraction may list all small marker glyphs before their lexical rows.
    Only original source baselines and bounded hanging/next-line geometry can
    associate them; reading-order adjacency cannot establish that association.
    """
    markers = [line for line in block.lines
               if re.fullmatch(r"\d{1,3}", line.stripped)]
    lexical = [line for line in block.lines
               if (line.stripped[:1].isupper() or line.stripped[:1] in "“‘\"'")
               and len(line.stripped.split()) >= 2
               and any(c.isalpha() for c in line.stripped)]
    proposed = {}
    for marker in markers:
        marks = [sp for sp in marker.spans if sp.text.strip()]
        if len(marks) != 1:
            continue
        matches = []
        for line in lexical:
            if not _scan_note_opening_context(marker, line, 'merged', block.size):
                continue
            word = next(sp for sp in line.spans if any(c.isalpha() for c in sp.text))
            delta = abs((word.origin_y or word.bbox[3])
                        -(marks[0].origin_y or marks[0].bbox[3]))
            matches.append((delta, word.size, line))
        matches.sort(key=lambda item: item[0])
        if (matches and (len(matches) == 1
                         or matches[1][0]-matches[0][0] > matches[0][1]*.25)):
            proposed[id(marker)] = matches[0][2]
    # Two labels cannot claim one original text row. Ambiguity leaves the
    # original adjacency policy in charge rather than inventing an association.
    counts = {id(line): sum(peer is line for peer in proposed.values())
              for line in proposed.values()}
    return {key: line for key, line in proposed.items() if counts[id(line)] == 1}


def _complete_source_note_pairs(body, notes, pairs):
    """Retain measured citation rows with already established note markers.

    Only original eligible footer rows enter the pairing proof. It grants no
    new marker authority or numeric identity: source baselines and bounded
    lanes project complete associations across extraction block boundaries.
    """
    owners = {id(line): index for index, note in enumerate(notes) for line in note.lines}
    pair_ids = {key for key in pairs if key in owners}
    affected = {owners[key] for key in pair_ids
                if owners.get(id(pairs[key])) != owners[key]
                or any(other != key and owners.get(id(pairs[other])) == owners[key]
                       for other in pair_ids)}
    if not affected:
        return body, notes
    affected.update(owners[id(pairs[key])] for key in pair_ids
                    if owners[key] in affected and id(pairs[key]) in owners)
    active = {key for key in pair_ids if owners[key] in affected}
    moved = active | {id(pairs[key]) for key in active}
    retained = [replace(block, lines=kept, bbox=_lines_bbox(kept, block.bbox))
                for block in body
                if (kept := [line for line in block.lines if id(line) not in moved])]
    output = []
    for index, note in enumerate(notes):
        if index not in affected:
            output.append(note)
            continue
        markers = [line for line in note.lines if id(line) in active]
        extras = [line for line in note.lines if id(line) not in moved]
        if not markers:
            if extras:
                output.append(replace(note, lines=extras, bbox=_lines_bbox(extras, note.bbox)))
            continue
        groups = [[marker, pairs[id(marker)]] for marker in markers]
        # Existing note continuations retain their role. With several source
        # labels, their measured vertical order identifies the preceding text;
        # an unmatched leading continuation remains a separate original note.
        leading = []
        for line in extras:
            preceding = [group for group in groups if group[1].bbox[1] <= line.bbox[1]]
            if preceding:
                max(preceding, key=lambda group: group[1].bbox[1]).append(line)
            elif len(groups) == 1:
                groups[0].append(line)
            else:
                leading.append(line)
        if leading:
            output.append(replace(note, lines=leading,
                                  bbox=_lines_bbox(leading, note.bbox)))
        for group in groups:
            # A shared marker-only zone supplies no independently assigned
            # number. Keep that uncertainty while preserving each full note.
            number = note.number if (len(groups) == 1
                                     or group[0] is note.lines[0]) else None
            output.append(replace(note, number=number, lines=group,
                                  bbox=_lines_bbox(group, note.bbox)))
    return retained, sorted(output, key=lambda note: (note.bbox[1], note.bbox[0]))


def _notes_in_block(blk, existing, distinct_opening=False):
    """One block can hold several notes; a new one opens with its own small number.

    Lines before the first number continue the note that ran over from the block
    above — a long note printed across two blocks, not a new unnumbered one.
    """
    out = []
    current = None
    if distinct_opening:
        current=Region(kind='note',lines=[],number=None,bbox=blk.bbox,
                       uncertain=True,reason='damaged_note_label')
        out.append(current)
    seen = [r.number for r in existing if r.number is not None]
    pairings = _source_note_marker_pairs(blk)
    bare = [line for line in blk.lines if re.fullmatch(r"\d{1,3}", line.stripped)]
    lines = blk.lines
    if bare and all(id(line) in pairings for line in bare):
        # Preserve lexical/continuation source order; insert each separately
        # extracted marker immediately before its own measured lexical row.
        # The source rows and the original block are never modified.
        labels = {id(pairings[id(line)]): line for line in bare}
        paired_labels = {id(line) for line in bare}
        lines = []
        for line in blk.lines:
            if id(line) in paired_labels:
                continue
            if id(line) in labels:
                lines.append(labels[id(line)])
            lines.append(line)
    for position, ln in enumerate(lines):
        number = _note_number(
            ln, blk.size, opening=(position == 0),
            after=seen[-1] if seen else None,
            following=lines[position + 1] if position + 1 < len(lines)
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


#: OCR note openings can contain punctuation or letter debris. Require
#: compatible geometry and a note sentence.
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


#: Read the opening run, its separating space and the note first word
#: together. A letter alone is not a number.
_GLYPH_NOTE_NUMBER = re.compile("^(\\S{2,4})[ \\t]+(?=[A-Z\u201c\u2018\"\'])")


#: A footnote opening the OCR could not keep apart from its own number: the digits,
#: a space, and then the start of a sentence. The trailing context is what separates
#: it from an endnote entry ("15. Fixture Manual..."), a table row and page-bottom debris.
_MERGED_NOTE_NUMBER = re.compile(r"^(\d{1,3})[ \t]+(?=[A-Z\u201c\u2018\"\'])")


def _note_number(line, block_size, opening=False, after=None, following=None):
    """Read a note opening from its source spans and compatible glyph debris.

    Preserve uncertainty when the text layer cannot prove the printed digits."""
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
        # that keeps a citation's own numbers ("112, trans. Editor") from opening
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
            # with an attribution and comma, rather than a completed sentence.
            # A numeric measurement followed by sentence punctuation remains
            # prose: decoding its degree glyph as zero would start a false zone
            # and consume the body that follows.
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
#: first lexical token after it ends with sentence punctuation.
_PROSE_SENTENCE = re.compile(r"[A-Z\u201c\u2018\"']\S*[.!?](?:\s|$)")


def _damaged_scan_running_head(line, raw, style):
    """Prove a damaged small-cap margin row without reading its folio.

    Reliable lowercase still means prose. Only explicitly disputed fragments
    may supply the few lowercase letters in a small, detached running title.
    A scanner may merge both heads into one line: keep that original line whole
    only when its span boxes prove two separate outer-margin title groups.
    """
    em = style.body_size
    box = line.bbox
    if (not raw.is_page_scan or raw.width < raw.height*1.2 or not em
            or box[1] < 0 or box[3] > raw.height*HEADER_BAND
            or box[3]-box[1] > em*.9 or _is_caps(line.stripped)
            or CAPTION_LINE.match(line.stripped) or '°' in line.stripped):
        return False
    spans = [sp for sp in line.spans if sp.text.strip()]
    middle = raw.width*.5
    if not spans or any(sp.bbox[0] < middle < sp.bbox[2] for sp in spans):
        return False
    groups = [[sp for sp in spans if (sp.bbox[0]+sp.bbox[2])/2 < middle],
              [sp for sp in spans if (sp.bbox[0]+sp.bbox[2])/2 >= middle]]
    if all(groups) and min(sp.bbox[0] for sp in groups[1])-max(
            sp.bbox[2] for sp in groups[0]) < 2*em:
        return False
    others = [other for block in raw.text_blocks for other in block.lines
              if other is not line and other.stripped]
    for side, group in enumerate(groups):
        if not group:
            continue
        text = ''.join(sp.text for sp in group)
        letters = [c for c in text if c.isalpha()]
        words = re.findall(r'[A-Za-z]+', text)
        sizes = [sp.size for sp in group for c in sp.text if c.isalpha()]
        if (len(letters) < 8 or sum(word.isupper() for word in words) < 2
                or sum(c.islower() for c in letters) > len(letters)*.3
                or not sizes or median(sizes) > em*.75
                or any(any(c.islower() for c in sp.text) and not (
                    sp.uncertain or
                    sp.transcription_uncertain or sp.encoding_unresolved)
                    for sp in group)):
            return False
        x0, x1 = min(sp.bbox[0] for sp in group), max(sp.bbox[2] for sp in group)
        if (x1-x0 > raw.width*.32 or
                (x0 > raw.width*.12 if side == 0 else x1 < raw.width*.88)):
            return False
        neighbors = [other for other in others
                     if other.bbox[0] < x1 and x0 < other.bbox[2]]
        if (not neighbors or any(other.bbox[1] < box[3] for other in neighbors)
                or min(other.bbox[1] for other in neighbors)-box[3] <
                   (box[3]-box[1])*.75):
            return False
    return True


def _paired_margin_folio(line, raw, style):
    """Prove an uncertain folio footprint beside an established running head.

    The head's existing source role supplies the authority, not the spelling of
    an uncertain numeral. Lexical spans keep whitespace tabs from changing the
    baseline, type or edge measurements. The entire group must precede every
    other original row by a detached margin gap.
    """
    if (not getattr(raw, 'is_page_scan', False) or raw.width >= raw.height*1.2
            or not style.body_size or not FOLIO.fullmatch(line.stripped)):
        return False
    # An empty descriptor is the extractor's native, unrotated contract.
    # Explicit provenance must establish that same coordinate frame rather
    # than allowing transformed boxes to certify a native margin cut.
    if not _native_margin_frame(raw):
        return False
    em = style.body_size
    def measured(row):
        return _margin_row_measure(row, raw)
    folio = measured(line)
    if not folio:
        return False
    box, size, baseline = folio
    if (box[3] > raw.height*.12 or box[2]-box[0] > raw.width*.075
            or not em*.6 <= size <= em*1.3 or box[3]-box[1] > em*1.5
            or not (box[2] <= raw.width*.2 or box[0] >= raw.width*.8)):
        return False
    rows = [row for block in raw.text_blocks for row in block.lines if row.stripped]
    pairs = []
    for head in rows:
        if (head is line or FOLIO.fullmatch(head.stripped)
                or sum(char.isalpha() for char in head.stripped) < 3
                or CAPTION_LINE.match(head.stripped)):
            continue
        heading = measured(head)
        if not heading:
            continue
        hbox, hsize, hbaseline = heading
        if (hbox[3] > raw.height*.12 or hsize >= em*.98
                or hbox[3]-hbox[1] > em*1.5
                or abs(baseline-hbaseline) > em*.35
                or not (box[2]+em*.4 < hbox[0] or hbox[2]+em*.4 < box[0])):
            continue
        if _furniture_reason(head, raw, style) not in (
                'repeated_scan_spread_head', 'repeated_portrait_head',
                'repeated_detached_head'):
            continue
        others = [row for row in rows if row is not line and row is not head]
        bottom = max(line.bbox[3], head.bbox[3])
        if (not others or min(row.bbox[1] for row in others)-bottom < em*.8):
            continue
        pairs.append(head)
    return len(pairs) == 1


def _roman_folio_literal(text):
    """Read only a canonical source literal, for teacher progression evidence."""
    value = text.upper()
    if not value or not re.fullmatch(
            r'M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})', value):
        return None
    symbols = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}
    total = previous = 0
    for letter in reversed(value):
        number = symbols[letter]
        total += -number if number < previous else number
        previous = max(previous, number)
    return total


def _roman_folio_shape(number):
    """Predict a source pattern from clear teachers; never assign target identity."""
    if not 0 < number < 4000:
        return None
    result = ''
    for count, symbol in ((1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'),
            (100, 'C'), (90, 'XC'), (50, 'L'), (40, 'XL'), (10, 'X'),
            (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I')):
        while number >= count:
            result += symbol
            number -= count
    return result.casefold()


def _contents_folio_candidate(raw, body_size):
    """Measure an isolated compact original footer, without interpreting it."""
    if not body_size or not _native_margin_frame(raw):
        return None
    rows = [line for block in raw.text_blocks for line in block.lines if line.stripped]
    if len(rows) < 2:
        return None
    line = max(rows, key=lambda peer: peer.bbox[3])
    parts = [span for span in line.spans if span.text.strip()]
    if (not parts or len(line.stripped) > 8 or line.bbox[2]-line.bbox[0] > body_size*2
            or line.size > body_size*1.02 or line.bbox[1] < raw.height*.8
            or abs((line.bbox[0]+line.bbox[2]-raw.width)/2) > body_size*2
            or any(peer.bbox[3] > line.bbox[1]-body_size*1.5
                   for peer in rows if peer is not line)):
        return None
    return line


def _repeated_contents_folios(raw_pages, body_size):
    """Prove contents footer territory with clear target-excluded Roman peers.

    Teachers may come from any detached original lower-margin row. A target
    must independently have repeated entry/destination lanes, and at least
    three other clear folios must establish the same progression and measured
    footprint family. An uncertain target keeps its source text and can match
    at most one unresolved character of that pattern; only its bbox is stored.
    """
    candidates = [(raw, line) for raw in raw_pages
                  if (line := _contents_folio_candidate(raw, body_size)) is not None]
    teachers = []
    for raw, line in candidates:
        if (line.transcription_uncertain or any(span.uncertain or span.transcription_uncertain
                or span.encoding_unresolved for span in line.spans if span.text.strip())):
            continue
        number = _roman_folio_literal(line.stripped)
        if number is not None:
            teachers.append((raw, line, number-raw.pno))
    proved = {}
    context = BookStyle(body_size=body_size)
    for raw, line in candidates:
        if not _source_contents_rows(raw, context):
            continue
        peers = defaultdict(set)
        for other, row, offset in teachers:
            if (other.pno == raw.pno or abs(other.width/raw.width-1) > .03
                    or abs(other.height/raw.height-1) > .03
                    or abs((row.bbox[0]+row.bbox[2])/(2*other.width)
                           -(line.bbox[0]+line.bbox[2])/(2*raw.width)) > .02
                    or abs(row.bbox[1]/other.height-line.bbox[1]/raw.height) > .02
                    or abs(row.size-line.size) > body_size*.25):
                continue
            peers[offset].add(other.pno)
        offsets = [offset for offset, pages in peers.items() if len(pages) >= 3]
        if len(offsets) != 1:
            continue
        expected = _roman_folio_shape(raw.pno+offsets[0])
        literal = line.stripped.casefold()
        uncertain = line.transcription_uncertain or any(
            span.uncertain or span.transcription_uncertain or span.encoding_unresolved
            for span in line.spans if span.text.strip())
        if (expected is None or len(literal) != len(expected)
                or sum(a != b for a, b in zip(literal, expected)) > (1 if uncertain else 0)):
            continue
        proved[raw.pno] = [line.bbox]
    return proved


def _contents_footer_folio(line, raw, style):
    return bool(_native_margin_frame(raw)
        and line.bbox in getattr(style, 'contents_folio_boxes', {}).get(raw.pno, ()))


def _furniture_reason(line, raw, style, top_y=None):
    if _contents_footer_folio(line, raw, style):
        return 'contents_footer_folio'
    if line.bbox in getattr(style, 'paired_margin_head_boxes', {}).get(raw.pno, ()):
        # Only the newly learned portrait template has this native-frame
        # contract. Existing independent source profiles keep their authority.
        if not _native_margin_frame(raw):
            return None
        if line.bbox in getattr(style, 'scan_table_label_boxes', {}).get(raw.pno, ()):
            return None
        return 'repeated_detached_head'
    if line.bbox in getattr(style, 'scan_spread_head_boxes', {}).get(raw.pno, ()):
        return 'repeated_scan_spread_head'
    if line.bbox in getattr(style, 'scan_table_label_boxes', {}).get(raw.pno, ()):
        return None
    if line.bbox in getattr(style, 'portrait_head_boxes', {}).get(raw.pno, ()):
        return 'repeated_portrait_head'
    if getattr(style,"folio_boxes",{}).get(raw.pno) == line.bbox:
        return "folio_sequence"
    if getattr(style,"local_running_boxes",{}).get(raw.pno) == line.bbox:
        return "local_running_folio"
    if line.bbox in getattr(style, 'repeated_head_boxes', {}).get(raw.pno, ()):
        return 'repeated_detached_head'
    if _damaged_scan_running_head(line, raw, style):
        return 'damaged_scan_running_head'
    if _paired_margin_folio(line, raw, style):
        return 'paired_margin_folio'
    # A disputed isolated digit can be a raised body marker, even in the
    # margin band. Its spelling alone does not establish a folio. Measured
    # sequence/repetition ownership above still proves damaged furniture.
    if FOLIO.match(line.stripped) and (line.transcription_uncertain or any(
            sp.uncertain or sp.transcription_uncertain or sp.encoding_unresolved
            for sp in line.spans if sp.text.strip())):
        return None
    y0, y1 = line.bbox[1], line.bbox[3]
    in_head = y1 <= raw.height * HEADER_BAND
    in_foot = y0 >= raw.height * FOOTER_BAND
    text = line.stripped
    if not (in_head or in_foot):
        # A recurrent topmost head can lie below the usual margin band.
        # Require page-local margin evidence.
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


def _regroup_native_titles(kept_blocks, style, doc=None, pno=0):
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
        joined=extract.Block(other.number,box,combined)
        from .heading_units import initial_spacing
        first=min((ln for ln in body if abs(ln.size/style.body_size-1)<=LADDER_TOL),key=lambda ln:ln.bbox[1])
        if body[0] is first:
            joined.initial_join=initial_spacing(doc,pno,initial,first)
        out.append((joined,combined))
    for lines in titles:
        box=_lines_bbox(lines,lines[0].bbox)
        out.append((extract.Block(-1,box,lines),lines))
    return out


def _join_numbered_regions(skel):
    """Continue an already proven numbered list across PDF block boundaries.

    Only adjacent same-column source lines qualify: the next sequential item,
    or a hanging-indent continuation of a physically hyphenated last line.
    Pictures, notes, unrelated prose and large vertical gaps remain barriers.
    """
    out = []
    for region in skel.regions:
        prior = out[-1] if out else None
        if (prior is None or prior.kind != 'list' or region.kind != 'body'
                or not prior.lines or not region.lines
                or (prior.band, prior.column) != (region.band, region.column)):
            out.append(region); continue
        first, last = region.lines[0], prior.lines[-1]
        em = median(line.size for line in prior.lines if line.size > 0)
        marker_x = prior.list_groups[0][0].x0
        gap = first.bbox[1] - last.bbox[3]
        marker = re.match(r'^([1-9][0-9]{0,2})[.)](?:\s|$)', first.stripped)
        next_item = bool(marker and int(marker.group(1)) == len(prior.list_groups) + 1
                         and abs(first.x0 - marker_x) <= .4 * em)
        continuation = (last.stripped.endswith(('-', '\u00ad', '‐', '‑'))
                        and first.stripped[:1].islower()
                        and marker_x + .5 * em <= first.x0 <= marker_x + 4 * em)
        if not (-.2 * em <= gap <= 1.6 * em and (next_item or continuation)):
            out.append(region); continue
        # A PDF block can also contain a later independent paragraph. Validate
        # every consumed line, and retain the unproved tail as its own region.
        consumed = []
        previous = last
        expected = len(prior.list_groups) + 1
        for line in region.lines:
            distance = line.bbox[1] - previous.bbox[3]
            item = re.match(r'^([1-9][0-9]{0,2})[.)](?:\s|$)', line.stripped)
            aligned = (abs(line.x0 - marker_x) <= .4 * em and int(item.group(1)) == expected
                       if item else marker_x + .5 * em <= line.x0 <= marker_x + 4 * em)
            # Some PDFs put the numeral and its text in separate line records
            # on the same physical baseline. Prove that pair geometrically.
            marker_row = (re.fullmatch(r'[1-9][0-9]{0,2}[.)]', previous.stripped)
                          and abs((previous.bbox[1] + previous.bbox[3]) / 2
                                  - (line.bbox[1] + line.bbox[3]) / 2) <= .4 * em
                          and previous.bbox[2] <= line.x0 + .1 * em)
            if not (aligned and (marker_row or -.2 * em <= distance <= 1.6 * em)):
                break
            if item:
                expected += 1
            consumed.append(line); previous = line
        combined = prior.lines + consumed
        groups = _numbered_source_groups(combined)
        if not consumed or not groups or len(groups) < len(prior.list_groups):
            out.append(region); continue
        prior.lines = combined; prior.list_groups = groups
        prior.bbox = _lines_bbox(combined, prior.bbox)
        remainder = region.lines[len(consumed):]
        if remainder:
            from dataclasses import replace
            out.append(replace(region, lines=remainder,
                               bbox=_lines_bbox(remainder, region.bbox)))
    skel.regions = out


def _numbered_source_groups(lines):
    starts=[]
    for i,line in enumerate(lines):
        marker=re.match(r'^([1-9][0-9]{0,2})[.)](?:\s|$)',line.stripped)
        if marker:starts.append((i,int(marker.group(1)),line))
    if len(starts)<2 or starts[0][0]!=0 or [n for _,n,_ in starts]!=list(range(1,len(starts)+1)):
        return []
    em=median(line.size for _,_,line in starts)
    if em<=0 or max(line.x0 for _,_,line in starts)-min(line.x0 for _,_,line in starts)>.4*em:
        return []
    groups=[lines[a[0]:(starts[i+1][0] if i+1<len(starts) else len(lines))] for i,a in enumerate(starts)]
    for group in groups:
        if not re.sub(r'^[1-9][0-9]{0,2}[.)]\s*','',' '.join(line.stripped for line in group)).strip():return []
        if any(line.x0 < group[0].x0-.3*em for line in group[1:]):return []
    return groups


def _source_reference_groups(lines):
    """Keep aligned printed dot-leader/reference entries as separate items."""
    if not lines:
        return []
    ending = re.compile(r'\.{3,}\s*(?:\d+(?:\s*[,\u2013-]\s*\d+)*|[ivxlcdm]+)\s*$', re.I)
    groups = []; pending = []
    for line in lines:
        pending.append(line)
        if ending.search(line.stripped):
            groups.append(pending); pending = []
    if pending or len(groups) < 2:
        return []
    sizes = [line.size for line in lines if line.size > 0]
    if not sizes:
        return []
    em = median(sizes)
    if (em <= 0 or max(g[0].x0 for g in groups)-min(g[0].x0 for g in groups) > .5*em
            or max(g[-1].bbox[2] for g in groups)-min(g[-1].bbox[2] for g in groups) > .75*em):
        return []
    for group in groups:
        for prior, line in zip(group, group[1:]):
            if (line.x0 < group[0].x0+.4*em
                    or not -.2*em <= line.bbox[1]-prior.bbox[3] <= 1.6*em):
                return []
    return groups


def _classify_body(lines, blk, style, skel, band=0, column=0):
    """Split a block into headings and prose, honouring run-in sub-headings."""
    groups=_numbered_source_groups(lines)
    if groups:
        skel.regions.append(Region(kind='list',lines=list(lines),list_groups=groups,
            bbox=_lines_bbox(lines,blk.bbox),band=band,column=column,reason='numbered_source_list'))
        return
    groups = _source_reference_groups(lines)
    if groups:
        skel.regions.append(Region(kind='list', lines=list(lines), list_groups=groups,
            bbox=_lines_bbox(lines, blk.bbox), band=band, column=column, reason='source_reference_list'))
        return
    if lines and CAPTION_LINE.match(lines[0].stripped):
        skel.regions.append(Region(kind="caption",lines=list(lines),
            bbox=_lines_bbox(lines,blk.bbox),band=band,column=column))
        return
    # A PDF extractor may put an entire section (including later bold emphasis)
    # in one block. A centered, separated first line still has its own printed
    # heading territory; later emphasis does not make that title prose.
    display_first = (len(lines) >= 2 and lines[0].bold
        and abs((lines[0].bbox[0] + lines[0].bbox[2]
                 - blk.bbox[0] - blk.bbox[2]) / 2) <= lines[0].size
        and lines[0].bbox[0] - blk.bbox[0] >= lines[0].size
        and lines[1].bbox[0] - blk.bbox[0] <= lines[0].size
        and lines[1].bbox[1] - lines[0].bbox[3] >= .65 * lines[0].size)
    if len(lines) >= 2 and heading_ish(lines[0], style) \
            and (display_first or not any(heading_ish(ln, style) for ln in lines[1:])):
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
    groups = _source_reference_groups(lines)
    if groups:
        skel.regions.append(Region(kind='list', lines=list(lines), list_groups=groups,
            bbox=_lines_bbox(lines, blk.bbox), band=band, column=column, reason='source_reference_list'))
        return
    kind = "caption" if CAPTION_LINE.match(lines[0].stripped) else "body"
    skel.regions.append(Region(kind=kind, lines=list(lines),
                               bbox=_lines_bbox(lines, blk.bbox),
                               band=band, column=column,
                               initial_join=getattr(blk,"initial_join",{})))


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
    """Find independently supported column channels.

    Ragged short prose lines do not prove multiple columns, and row tables
    retain their cross-column ownership."""
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
    """Columns and horizontal bands computed once for all source regions.

    A diagram beside a prose panel shares band geometry; short fragments
    cannot each open an independent gutter."""

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

    # A full-page scan background can cross the gutter without merging the two
    # reading panels.
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
        # Do not create gutters from ragged short-line right edges. A single
        # prose column needs one reading lane.
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
            # pretend to be more ('w o r k sh op s'): both are cell content, not
            # flowing prose.
            short[column] += 1
    mirror = _paired_rows(columnar, layout)
    row_groups = mirror
    if sum(1 for count in counts if count >= COLUMN_MIN_LINES) < 2:
        return None
    for column, count in enumerate(counts):
        if count >= COLUMN_MIN_LINES and short[column] * 2 >= count:
            # Preserve complete rows of an unruled label table across the
            # column channel.
            return _RowTable(row_groups) if row_groups else None
    fills.sort()
    if fills and fills[len(fills) // 2] < COLUMN_FILL_MIN:
        return None
    if row_groups:
        # Paired fields on the same row outrank a column-only reading.
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
    a half point apart (one table cell set taller than its paired cell)."""
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
    """Keep opposite cells of a source row together in reading order.

    A pair printed across a table is not two independent lists."""
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
    """A row table whose cell ownership is preserved across columns.

    Letter-spaced lowercase cells and labels stay with their printed row."""

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
    return _overlap_share(bbox,rect) >= share


def _overlap_share(bbox, rect):
    x0 = max(bbox[0], rect[0])
    y0 = max(bbox[1], rect[1])
    x1 = min(bbox[2], rect[2])
    y1 = min(bbox[3], rect[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
    return (x1 - x0) * (y1 - y0) / area if area > 0 else 0.0


def _prose_rows(kept_blocks, raw, style, occupied_boxes=()):
    """The page's prose, merged into horizontal bands of (top, bottom, [line boxes]).

    Chart lettering and caption lines are not prose: the first lives inside the
    figure and must not slice its territory into strips, the second rides with
    the figure it names. What remains is the text a figure cannot overlap, which
    is what the figure territory is measured from.
    """
    top, bottom = raw.height * HEADER_BAND, raw.height * FOOTER_BAND
    spans = []
    field_rows = (_scan_numeric_field_rows(kept_blocks, style)
                  if getattr(raw, 'is_page_scan', False) else set())
    for _, kept in kept_blocks:
        for ln in kept:
            if (id(ln) in field_rows or _chart_lettering(ln, style) or _squashed_caption(ln.stripped)
                    or (ln.transcription_uncertain and len(ln.stripped.split())<8)):
                continue
            y0, y1 = max(ln.bbox[1], top), min(ln.bbox[3], bottom)
            if y1 > y0:
                spans.append([y0, y1, ln.bbox])
    # Independently owned notes occupy source paper just as prose does. Their
    # smaller type and uncertain recognition are not empty artwork channels.
    for bbox in occupied_boxes:
        y0, y1 = max(bbox[1], top), min(bbox[3], bottom)
        if y1 > y0:
            spans.append([y0, y1, bbox])
    spans.sort()
    # Scale the band tolerance to measured leading so loose OCR lines still
    # share a panel channel.
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


def _scan_numeric_field_rows(kept_blocks, style):
    return {id(line) for group in _scan_numeric_field_groups(kept_blocks, style)
            for line in group}


def _scan_numeric_field_groups(kept_blocks, style):
    """Repeated aligned code/date fields are source table ink, not prose.

    A single date remains a body row. At least three neighboring rows must
    share a measured lane and type height, with short alphanumeric field codes
    and numeric date fields but no lexical prose word. This lets existing scan
    figure geometry retain the complete timeline, including unrecognized
    symbols between its rows, instead of treating each field as a paragraph
    that partitions the source artwork.
    """
    field = r"[^\W\d_]{1,2}\d+"
    structure = re.compile(r"(?:(?P<symbol>\S{1,8})\s+)?"
        + field + r"(?:/" + field + r")*\s*[-–—]\s*"
        + r"\d+[/-]\d+[/-]\d+(?:\s*[-–—]\s*[A-Z]{1,2})?\s*$")
    candidates = []
    unresolved = []
    measures = {}
    for _, kept in kept_blocks:
        for line in kept:
            match = structure.fullmatch(line.stripped)
            if not match:
                continue
            symbol = match.group('symbol') or ''
            lexical = any(len(word) >= 3 for word in re.findall(r"[^\W\d_]+", symbol))
            field_parts = [sp for sp in line.spans if sp.text.strip()]
            if lexical:
                # OCR can turn a source symbol into apparent letters, and its
                # box can be taller than the code/date type on the same row.
                # Measure those original field spans separately, retaining the
                # complete original line box for its eventual source crop.
                prefix_end = line.bbox[0]
                parts = []
                remainder = []
                consumed = ''
                for sp in line.spans:
                    if len(consumed.lstrip()) < len(symbol):
                        parts.append(sp)
                        consumed += sp.text
                        prefix_end = max(prefix_end, sp.bbox[2])
                    else:
                        remainder.append(sp)
                letters = [sp for sp in parts if any(c.isalpha() for c in sp.text)]
                field_parts = [sp for sp in remainder if sp.text.strip()]
                if (not letters or not field_parts
                        or any(not (sp.uncertain or sp.transcription_uncertain
                                    or sp.encoding_unresolved) for sp in letters)
                        or prefix_end > field_parts[0].bbox[0]
                        or max(sp.size for sp in letters) > median(
                            sp.size for sp in field_parts)*1.25):
                    continue
            if not field_parts:
                continue
            box = (min(sp.bbox[0] for sp in field_parts),
                   min(sp.bbox[1] for sp in field_parts),
                   max(sp.bbox[2] for sp in field_parts),
                   max(sp.bbox[3] for sp in field_parts))
            height = box[3]-box[1]
            if height <= 0:
                continue
            origins = [sp.origin_y for sp in field_parts
                       if sp.origin_y is not None and sp.bbox[1] <= sp.origin_y <= sp.bbox[3]]
            measures[id(line)] = (box, height,
                                  median(origins) if origins else box[1],
                                  median(sp.size for sp in field_parts))
            # An uncertain lexical-looking prefix never teaches a table run.
            # It can be retained only by already qualified original fields.
            (unresolved if lexical else candidates).append(line)

    def compatible(line, peer):
        box, height, _, size = measures[id(line)]
        other, peer_height, _, peer_size = measures[id(peer)]
        overlap = min(box[2], other[2])-max(box[0], other[0])
        width = min(box[2]-box[0], other[2]-other[0])
        return (width > 0 and overlap >= width*.7
                and .8*peer_height <= height <= 1.25*peer_height
                and .8*peer_size <= size <= 1.25*peer_size)

    groups = []
    for line in sorted(candidates, key=lambda ln: (ln.bbox[0], measures[id(ln)][2])):
        for group in groups:
            if compatible(line, group[0]):
                group.append(line)
                break
        else:
            groups.append([line])
    owned = []
    for group in groups:
        ordered = sorted(group, key=lambda ln: measures[id(ln)][2])
        run = []
        for line in ordered + [None]:
            if line is not None and (not run or 0 < measures[id(line)][2]-measures[id(run[-1])][2]
                    <= 3*max(measures[id(line)][1], measures[id(run[-1])][1])):
                run.append(line)
                continue
            if len(run) >= 3:
                owned.append(run)
            run = [line] if line is not None else []
    # Freeze the strict teachers before extending any run. Accepted uncertain
    # rows cannot train an outward chain or create a run of their own.
    teachers = [tuple(group) for group in owned]
    for line in unresolved:
        _, height, baseline, _ = measures[id(line)]
        for group, original in zip(owned, teachers):
            before = [ln for ln in original if measures[id(ln)][2] < baseline]
            after = [ln for ln in original if measures[id(ln)][2] > baseline]
            if before and after:
                neighbors = [max(before, key=lambda ln: measures[id(ln)][2]),
                             min(after, key=lambda ln: measures[id(ln)][2])]
                allowed = all(compatible(line, peer)
                              and abs(baseline-measures[id(peer)][2]) <= height*1.5
                              for peer in neighbors)
            else:
                side = before or after
                neighbors = sorted(side, key=lambda ln: abs(measures[id(ln)][2]-baseline))[:2]
                allowed = False
                if len(neighbors) == 2 and all(compatible(line, peer) for peer in neighbors):
                    step = abs(baseline-measures[id(neighbors[0])][2])
                    pitch = abs(measures[id(neighbors[0])][2]-measures[id(neighbors[1])][2])
                    allowed = (0 < step <= height*1.5 and 0 < pitch <= height*1.5
                               and abs(step-pitch) <= height*.35)
            if allowed:
                group.append(line)
                group.sort(key=lambda ln: measures[id(ln)][2])
                break
    return owned


def _scan_figures(kept_blocks, raw, style, pixel_probe=None, note_regions=()):
    """Recover source ink regions not represented by reliable text.

    A full-page scan carrier does not disqualify smaller interior diagrams."""
    note_boxes = [region.bbox for region in note_regions if region.lines]
    fields = (_scan_numeric_field_groups(kept_blocks, style)
              if getattr(raw, 'is_page_scan', False) else [])
    # A qualified table is artwork in its own lane, but is still occupied paper
    # when locating a neighboring chart. Removing these obstacles would turn
    # two side-by-side units into a clipped full-width speculative crop.
    rows, top, bottom = _prose_rows(kept_blocks, raw, style,
                                  note_boxes+[line.bbox for group in fields for line in group])
    if not rows:
        return []
    lines = [ln for _, kept in kept_blocks for ln in kept]
    if not lines:
        return []
    left = min(ln.bbox[0] for ln in lines)
    right = max(ln.bbox[2] for ln in lines)
    span = right - left
    if span <= 1:
        return []

    candidates = []
    for group in fields:
        box = (
                             max(0, min(ln.bbox[0] for ln in group)-2),
                             max(top, min(ln.bbox[1] for ln in group)-2),
                             min(raw.width, max(ln.bbox[2] for ln in group)+2),
                             min(bottom, max(ln.bbox[3] for ln in group)+2))
        fragments = [ln for ln in lines if ln not in group
                     and _inside(ln.bbox, box, .99)
                     and len(ln.stripped) <= 8
                     and not any(c.isalpha() for c in ln.stripped)
                     and any(sp.uncertain or sp.transcription_uncertain
                             or sp.encoding_unresolved for sp in ln.spans)]
        candidates.append(Region(kind='figure', reason='scan_figure_band', needs_ink=False,
                                 lines=list(group)+fragments, bbox=box))
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
        for part, (bx0, bx1) in enumerate(boxes):
            art_top = y0
            if len(boxes) > 1 and hasattr(pixel_probe, 'ink_bounds'):
                try:
                    ink = pixel_probe.ink_bounds((bx0, y0, bx1, y1))
                except (ValueError, RuntimeError, AttributeError):
                    ink = None
                if ink and ink[3]-ink[1] >= raw.height * SCAN_GAP:
                    # The masked probe excludes classified running furniture.
                    # Retained chart labels still constrain the crop: artwork
                    # ink alone cannot authorize cutting off their territory.
                    label_tops = [ln.bbox[1] for ln in lines
                        if y0 <= ln.bbox[1] and ln.bbox[3] <= y1
                        and bx0 <= (ln.bbox[0]+ln.bbox[2])/2 <= bx1]
                    art_top = max(y0, min([ink[1]] + label_tops))
            # The pad breathes into empty territory only: padding across the
            # prose's own edge pulls a sliver of every line into the crop.
            # Internal dividers already come from measured source channels.
            # Padding across them can pull in the independently captioned
            # neighboring figure. Breathe only at the outside of the band.
            crop_top, crop_bottom = max(0.0, art_top-2.0), min(raw.height,y1+2.0)
            for occupied in note_boxes:
                if occupied[0] < bx1 and bx0 < occupied[2]:
                    if occupied[1] >= y1-.01:
                        crop_bottom = min(crop_bottom,occupied[1])
                    if occupied[3] <= art_top+.01:
                        crop_top = max(crop_top,occupied[3])
            candidates.append(Region(
                kind="figure", reason=why, needs_ink=True,
                bbox=(max(0.0, bx0 - (pad_x0 if part == 0 else 0)), crop_top,
                      min(raw.width, bx1 + (pad_x1 if part == len(boxes)-1 else 0)), crop_bottom)))
            if side:
                side_seeds.append((side, candidates[-1]))

    # Do not propose readable chapter display text as a figure. An unreadable
    # diagram may still need its source pixels.
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
    # channel empty on one side (a circular wheel beside its reading). One line
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
            table_overlap = any(other not in members and other.lines
                                and _overlap_share(other.bbox, members[0].bbox) > 0
                                for other in candidates)
            if not (table_overlap or box[0] < left-2 or box[1] < top-2
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
    images loses them whole. Guards require enough paths to distinguish
    a drawing from a rule, together with a cluster of believable
    area. A dense native chart's traced lettering is still source artwork.
    """
    all_rects = raw.drawing_rects
    # A traced chart's outer strokes and lettering can consist entirely of
    # sub-point paths. Its dense path inventory is the actual artwork boundary,
    # not supporting debris around a handful of large core shapes.
    dense = raw.drawings > 2500
    rects = [r for r in all_rects if
             ((r[2] > r[0] or r[3] > r[1]) if dense else
              (r[2] - r[0]) > 4 and (r[3] - r[1]) > 4)]
    if len(rects) < VEC_COMPACT_MIN_PATHS:
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
    if dense:
        # A later path can bridge clusters created earlier in extraction order.
        # Close those geometric overlaps before choosing a crop; otherwise one
        # wheel can become several overlapping, individually truncated pieces.
        changed = True
        while changed:
            changed = False
            for i, (a, count) in enumerate(clusters):
                for j in range(i + 1, len(clusters)):
                    b, other = clusters[j]
                    if (max(a[0]-b[2],b[0]-a[2],0)<=VEC_COMPACT_REACH
                            and max(a[1]-b[3],b[1]-a[3],0)<=VEC_COMPACT_REACH):
                        clusters[i] = [(min(a[0], b[0]), min(a[1], b[1]),
                                        max(a[2], b[2]), max(a[3], b[3])), count + other]
                        clusters.pop(j); changed = True; break
                if changed:break
    parea = raw.width * raw.height or 1.0
    out = []
    for box, count in clusters:
        area = (box[2] - box[0]) * (box[3] - box[1]) / parea
        if count >= VEC_MIN_PATHS and VEC_MIN_AREA <= area <= VEC_MAX_AREA:
            out.append(Region(kind="figure", reason="vector_figure",
                              bbox=box))
        elif count >= VEC_COMPACT_MIN_PATHS and VEC_COMPACT_MIN_AREA <= area <= VEC_MAX_AREA:
            reach = VEC_COMPACT_REACH
            # Supporting thin strokes can extend beyond the core, but a page
            # rule that merely touches its edge must not drag the crop across
            # unrelated live prose. Only complete strokes inside the measured
            # core's small outer territory may enlarge the figure.
            nearby = [rect for rect in all_rects
                      if rect[0] >= box[0] - reach and rect[2] <= box[2] + reach
                      and rect[1] >= box[1] - reach and rect[3] <= box[3] + reach]
            if len(nearby) >= VEC_COMPACT_MIN_STROKES:
                extent = (min(r[0] for r in nearby), min(r[1] for r in nearby),
                          max(r[2] for r in nearby), max(r[3] for r in nearby))
                out.append(Region(kind="figure", reason="vector_figure",
                                  bbox=extent))
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
    """Attach a source caption to its own nearby figure footprint.

    Sparse diagram lettering and unrelated display text do not create a caption."""
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


def _complete_captioned_scan_diagrams(raw, kept_blocks, candidates, style, pixel_probe, note_regions):
    """Source ink, not sparse OCR label boxes, bounds a captioned diagram.

    Only an unverified scan with many tiny non-prose labels above a literal
    caption qualifies. Prose and previous captions bound the vertical query;
    a failed pixel probe leaves the previous conservative policy unchanged.
    """
    if (pixel_probe is None or not raw.is_page_scan
            or not (getattr(raw, 'text_layer_invisible', False)
                    or getattr(raw, 'text_layer_overpainted', False))):
        return kept_blocks, candidates, [], note_regions
    from .assess import looks_like_prose
    # A small chart label can resemble a note opening and pull its following
    # caption into that note. Reclaim it only after independent diagram proof.
    available=kept_blocks+[(extract.Block(number=-1,lines=n.lines,bbox=n.bbox),n.lines)
                            for n in note_regions]
    lines=[ln for _,group in available for ln in group]
    captions=[ln for ln in lines if _squashed_caption(ln.stripped)]
    owned=set();reclaimed=set();recovered_captions=[];artwork=[]
    original_ids={id(ln) for _,ls in kept_blocks for ln in ls}
    for caption in captions:
        if any(other is not caption and abs(other.bbox[1]-caption.bbox[1])<style.body_size
               for other in captions):
            continue
        groups=[]
        for block,group in sorted(available,key=lambda item:item[0].bbox[1],reverse=True):
            before=[ln for ln in group if ln.bbox[3]<=caption.bbox[1] and id(ln) not in owned]
            if not before:continue
            if (any(_squashed_caption(ln.stripped) for ln in before)
                    or looks_like_prose(' '.join(ln.text for ln in before))
                    or any(len(ln.stripped.split())>=8 for ln in before)):
                break
            groups.extend(before)
        if len(groups)<8 or sum(len(ln.stripped)<=3 for ln in groups)<3:
            continue
        top=min(ln.bbox[1] for ln in groups)
        if caption.bbox[1]-top<raw.height*.15:
            continue
        query_top=max(raw.height*HEADER_BAND,top-3*max(1,style.body_size))
        try:
            ink=pixel_probe.ink_bounds((0,query_top,raw.width,caption.bbox[1]))
        except (ValueError,RuntimeError,AttributeError):
            continue
        if not ink or ink[3]-ink[1]<raw.height*.15:
            continue
        box=(max(0,min(ink[0],min(ln.bbox[0] for ln in groups))-2),
             max(query_top,min(ink[1],top)-2),
             min(raw.width,max(ink[2],max(ln.bbox[2] for ln in groups))+2),
             min(caption.bbox[1],max(ink[3],max(ln.bbox[3] for ln in groups))+2))
        # A competing crop in this same diagram territory cannot own it twice.
        candidates=[c for c in candidates if not (c.bbox[0]<box[2] and box[0]<c.bbox[2]
                    and c.bbox[1]<box[3] and box[1]<c.bbox[3])]
        candidates.append(Region(kind='figure',bbox=box,reason='scan_figure_band',needs_ink=True))
        owned.update(id(ln) for ln in groups)
        reclaimed.update(id(ln) for ln in groups+[caption])
        if id(caption) not in original_ids:recovered_captions.append(caption)
        artwork.append(Region(kind='artwork',lines=groups,bbox=_lines_bbox(groups,box),
                              reason='captioned_scan_diagram'))
    retained=[(block,[ln for ln in group if id(ln) not in owned]) for block,group in kept_blocks]
    for caption in recovered_captions:
        retained.append((extract.Block(number=-1,lines=[caption],bbox=caption.bbox),[caption]))
    notes=[]
    for note in note_regions:
        note.lines=[ln for ln in note.lines if id(ln) not in reclaimed]
        if note.lines:
            note.bbox=_lines_bbox(note.lines,note.bbox);notes.append(note)
    return [(block,group) for block,group in retained if group],candidates,artwork,notes


def _complete_unverified_figure_tops(raw, kept_blocks, candidates, style):
    """Keep connected scan diagram rows with an already measured figure.

    An invisible/overpainted transcript can split one symbol diagram into several
    blocks, with the measured figure beginning inside a later block. Extend only
    through adjacent large-lettering blocks above that boundary, stopping at prose
    or a different column. Pixels own the whole blocks; their unverified text is
    never repeated as reading prose. Visible native text is outside this policy.
    """
    if not raw.is_page_scan or not (getattr(raw, 'text_layer_invisible', False)
                                   or getattr(raw, 'text_layer_overpainted', False)):
        return kept_blocks, []
    from .assess import looks_like_prose
    owned, artwork = set(), []
    em = max(1.0, style.body_size)
    ordered = sorted(kept_blocks, key=lambda item: _lines_bbox(item[1], item[0].bbox)[1], reverse=True)
    for candidate in candidates:
        if candidate.reason != 'scan_figure_band':
            continue
        box = candidate.bbox
        claimed = []
        for block, lines in ordered:
            if any(id(line) in owned for line in lines):
                continue
            b = _lines_bbox(lines, block.bbox)
            if b[1] >= box[1]:
                continue
            # Start with a block crossing the figure's top, then walk the same
            # source column upward through short gaps. No growth sideways.
            if b[3] < box[1] - 2*em or (not claimed and b[3] < box[1]):
                break
            if (b[0] < box[0] or b[2] > box[2]
                    or looks_like_prose(' '.join(line.text for line in lines))
                    or any(len(line.stripped.split()) >= 8 for line in lines)
                    or not any(_chart_lettering(line, style) for line in lines)):
                break
            expanded = (box[0], max(0, b[1]-2), box[2], box[3])
            if any(other is not candidate and _inside(b, other.bbox) for other in candidates):
                break
            box = expanded
            claimed.extend(lines)
        if claimed:
            candidate.bbox = box
            owned.update(id(line) for line in claimed)
            artwork.append(Region(kind='artwork', lines=claimed,
                bbox=_lines_bbox(claimed, box), reason='connected_scan_figure'))
    retained = [(block, [line for line in lines if id(line) not in owned])
                for block, lines in kept_blocks]
    return [(block, lines) for block, lines in retained if lines], artwork


def _absorb_figure_content(kept_blocks, candidates, style, raw=None, pixel_probe=None):
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
    def unique_lettering_owner(line):
        shares = sorted(((_overlap_share(line.bbox, item.bbox), index)
                         for index,item in enumerate(candidates)), reverse=True)
        if not shares or shares[0][0] < FIG_LINE_OVERLAP:
            return None
        if len(shares)>1 and shares[0][0]-shares[1][0] < .05:
            if raw is not None and raw.is_page_scan and all(
                    span.font=='ocr' for span in line.spans):
                # A later source-region pass will show the competing label as
                # pixels instead of attributing its OCR text to either figure.
                line.transcription_uncertain=True
            return None
        return candidates[shares[0][1]]

    owners = {id(line):unique_lettering_owner(line)
              for _,group in kept_blocks for line in group
              if _chart_lettering(line,style) and not _squashed_caption(line.stripped)}
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
                elif any(ln is owned for owned in candidate.lines):
                    # Repeated measured table fields already belong to this
                    # source crop, regardless of their ordinary text size.
                    pass
                elif _chart_lettering(ln, style) and owners.get(id(ln)) is candidate:
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

        if candidate.lines:
            # Partial geometric overlap is enough to assign chart lettering,
            # but not enough to show every printed glyph. Complete only the
            # lines this figure actually owns; prose and neighboring figures
            # do not authorize a wider crop.
            x0,y0,x1,y1 = candidate.bbox
            candidate.bbox = (min(x0, min(ln.bbox[0] for ln in candidate.lines)-2),
                              min(y0, min(ln.bbox[1] for ln in candidate.lines)-2),
                              max(x1, max(ln.bbox[2] for ln in candidate.lines)+2),
                              max(y1, max(ln.bbox[3] for ln in candidate.lines)+2))
            if raw is not None:
                candidate.bbox = (max(0,candidate.bbox[0]),max(0,candidate.bbox[1]),
                                  min(raw.width,candidate.bbox[2]),min(raw.height,candidate.bbox[3]))
            # Keep the complete expanded source crop. A nearby prose box may
            # contain artwork absent from its transcript, and a neighboring
            # figure may later be omitted after prose masks are applied. Neither
            # is proof that source pixels can be trimmed here.
            neighboring = [other.bbox for other in candidates if other is not candidate]
            neighboring.extend(ln.bbox for _,group in kept_blocks for ln in group)
            if any(_overlap_share(box,candidate.bbox)>0 for box in neighboring):
                candidate.reason = 'unverified_scan_layout'
                # This source image is the preservation owner. Retained prose
                # may mask all of its ink during blank-figure admission; do not
                # drop the crop on that unrelated heuristic.
                candidate.needs_ink = False

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
