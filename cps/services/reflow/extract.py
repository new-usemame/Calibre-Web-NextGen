# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Stage 1: PyMuPDF in, typed and JSON-able page primitives out.

This is the only module that knows the shape of PyMuPDF's ``get_text("dict")``
payload. Everything downstream reads :class:`RawPage`, so the rest of the pipeline
can be unit-tested against small synthetic documents and cached to disk as JSON.

The one judgement call made here is what counts as a "line": PyMuPDF's own lines,
unchanged. Re-segmenting them is stage 2's job, and doing it here would hide the
geometry stage 2 needs.
"""

import io
import math
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

try:
    import pymupdf
except ImportError:  # pragma: no cover - exercised only on a broken install
    try:
        import fitz as pymupdf
    except ImportError:
        pymupdf = None

#: Span flag bits PyMuPDF sets (``span["flags"]``).
FLAG_SUPERSCRIPT = 1
FLAG_ITALIC = 2
FLAG_SERIFED = 4
FLAG_MONOSPACED = 8
FLAG_BOLD = 16

#: A line counts as bold when this share of its characters is set in a bold face.
BOLD_SHARE = 0.6

#: An image covering this fraction of the page is a page scan, not an illustration.
FULLPAGE = 0.80

#: Images smaller than this on either axis are rules, bullets and glyph art.
MIN_FIG_PX = 90

#: A sampled crop with less than this share of dark pixels is blank paper, not a
#: figure. MEASURED against the v4 corpus: clean scan margins sit under it, printed
#: ink sits far above it.
#: Obsolete global dark-share floor, kept for callers passing it explicitly.
INK_MIN = 0.004
#: Render scale for ink analysis.
INK_SCALE = 0.35
#: Extent measurement must see pale, thin annotations beyond strong diagram ink.
INK_EXTENT_SCALE = 1.5
#: A 10x10 cell holding at least this share of dark pixels is print, not paper:
#: measured 3.2% on sparse 12pt figure lettering, 2.8% on the worst blank-paper
#: margin sliver in the corpus audit, 28%+ on real line art.
INK_BLOCK_MIN = 0.03

#: Above this many vector paths a page is a dense table or a traced scan, and its
#: path boxes are noise rather than diagram evidence.
MAX_DRAWING_RECTS = 2500

#: The most pixels one render may allocate. The check belongs BEFORE get_pixmap:
#: a pixmap is the allocation, and measuring after it exists is how a hostile
#: geometry (a 20000pt page at 1.5x is ~2.7 GB) gets to consume it. 4096x4096 is
#: ~50 MB of RGB -- far above any page a book printer has ever set (a letter page
#: at the pipeline's 1.5x is 1.1 Mpx), low enough to be a working bound rather
#: than a formality.
MAX_RASTER_PIXELS = 4096 * 4096

_WS = re.compile(r"\s+")


class RasterTooLarge(Exception):
    """The page or its encoding exceeds what Reflow will hold in memory."""


def _is_bold_font(font):
    f = (font or "").lower()
    return "bold" in f or "black" in f or "heavy" in f or "semibold" in f


@dataclass
class Span(object):
    """One run of characters sharing a face, a size and a baseline."""

    text: str
    size: float
    font: str
    flags: int
    bbox: Tuple[float, float, float, float]
    origin_y: float = 0.0
    uncertain: bool = False  # OCR engine confidence, never inferred for native text
    punctuation_uncertain: bool = False  # distinct native quote glyphs share ASCII Unicode

    @property
    def bold(self):
        return bool(self.flags & FLAG_BOLD) or _is_bold_font(self.font)

    @property
    def italic(self):
        return bool(self.flags & FLAG_ITALIC)

    @property
    def superscript(self):
        return bool(self.flags & FLAG_SUPERSCRIPT)

    def to_dict(self):
        return {"text": self.text, "size": round(self.size, 2), "font": self.font,
                "flags": self.flags, "bbox": [round(v, 2) for v in self.bbox],
                "origin_y": round(self.origin_y, 2),
                "punctuation_uncertain": self.punctuation_uncertain}


@dataclass
class Line(object):
    """A physical line as PyMuPDF segmented it."""

    spans: List[Span]
    bbox: Tuple[float, float, float, float]

    @property
    def text(self):
        return "".join(sp.text for sp in self.spans)

    @property
    def stripped(self):
        return _WS.sub(" ", self.text).strip()

    @property
    def size(self):
        """Dominant type size, weighted by characters — not the mean.

        A line with a two-character footnote marker in it is still a body line; an
        average would drag its size down and misclassify it.
        """
        sizes = Counter()
        for sp in self.spans:
            n = len(sp.text.strip())
            if n:
                sizes[round(sp.size, 1)] += n
        return sizes.most_common(1)[0][0] if sizes else 0.0

    @property
    def bold(self):
        total = sum(len(sp.text.strip()) for sp in self.spans)
        if not total:
            return False
        heavy = sum(len(sp.text.strip()) for sp in self.spans if sp.bold)
        return heavy / total >= BOLD_SHARE

    @property
    def x0(self):
        return self.bbox[0]

    def to_dict(self):
        return {"bbox": [round(v, 2) for v in self.bbox],
                "spans": [sp.to_dict() for sp in self.spans]}


@dataclass
class Block(object):
    """A PyMuPDF block: a paragraph-ish cluster of lines, or one image."""

    number: int
    bbox: Tuple[float, float, float, float]
    lines: List[Line] = field(default_factory=list)
    kind: str = "text"

    @property
    def text(self):
        return "\n".join(ln.text for ln in self.lines)

    @property
    def size(self):
        sizes = Counter()
        for ln in self.lines:
            for sp in ln.spans:
                n = len(sp.text.strip())
                if n:
                    sizes[round(sp.size, 1)] += n
        return sizes.most_common(1)[0][0] if sizes else 0.0

    @property
    def indented_first_line(self):
        """A first line pushed right of the rest opens a new paragraph or note."""
        if len(self.lines) < 2:
            return False
        return self.lines[0].x0 > self.lines[1].x0 + 6

    def to_dict(self):
        return {"number": self.number, "kind": self.kind,
                "bbox": [round(v, 2) for v in self.bbox],
                "lines": [ln.to_dict() for ln in self.lines]}


@dataclass
class Image(object):
    bbox: Tuple[float, float, float, float]
    area_ratio: float
    page_background: bool = False

    @property
    def width(self):
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self):
        return self.bbox[3] - self.bbox[1]

    @property
    def full_page(self):
        return self.area_ratio >= FULLPAGE

    @property
    def substantial(self):
        return self.width >= MIN_FIG_PX and self.height >= MIN_FIG_PX

    def to_dict(self):
        value = {"bbox": [round(v, 2) for v in self.bbox],
                 "area_ratio": round(self.area_ratio, 4)}
        if self.page_background:value['page_background'] = True
        return value


@dataclass
class RawPage(object):
    """Everything stage 2 is allowed to look at, for one page."""

    pno: int
    width: float
    height: float
    blocks: List[Block] = field(default_factory=list)
    images: List[Image] = field(default_factory=list)
    drawings: int = 0
    drawing_rects: List[Tuple[float, float, float, float]] = field(
        default_factory=list)
    source_geometry: dict = field(default_factory=dict)

    @property
    def text_blocks(self):
        return [b for b in self.blocks if b.kind == "text" and b.lines]

    @property
    def text(self):
        return "\n".join(b.text for b in self.text_blocks)

    @property
    def chars(self):
        return sum(len(ln.stripped) for b in self.text_blocks for ln in b.lines)

    @property
    def is_page_scan(self):
        """A single image covering the page: the page is a picture of itself."""
        return any(img.full_page or getattr(img,'page_background',False) for img in self.images)

    def to_dict(self):
        value = {"pno": self.pno, "width": round(self.width, 2),
                "height": round(self.height, 2), "drawings": self.drawings,
                "blocks": [b.to_dict() for b in self.blocks],
                "images": [i.to_dict() for i in self.images]}
        if getattr(self,'source_geometry',{}):value['source_geometry'] = self.source_geometry
        return value


def open_document(source):
    """Open a PDF from a path or bytes. Failures are plain messages, not tracebacks."""
    if pymupdf is None:
        raise RuntimeError("Reflow needs PyMuPDF: pip install pymupdf")
    if isinstance(source, (bytes, bytearray)):
        return pymupdf.open(stream=bytes(source), filetype="pdf")
    return pymupdf.open(source)


def read_page(doc, pno):
    """Turn one page into a :class:`RawPage`."""
    page = doc[pno]
    rect = page.rect
    parea = rect.get_area() or 1.0
    raw = RawPage(pno=pno, width=rect.width, height=rect.height)

    payload = page.get_text("dict")
    quote_fonts = _native_quote_variants(page)
    for blk in payload.get("blocks", []):
        bbox = tuple(blk.get("bbox", (0, 0, 0, 0)))
        if blk.get("type") == 1:
            area = max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])
            raw.images.append(Image(bbox=bbox, area_ratio=area / parea))
            raw.blocks.append(Block(number=blk.get("number", 0), bbox=bbox, kind="image"))
            continue
        lines = []
        for ln in blk.get("lines", []):
            spans = [Span(text=sp.get("text", ""), size=float(sp.get("size", 0.0)),
                          font=sp.get("font", ""), flags=int(sp.get("flags", 0)),
                          bbox=tuple(sp.get("bbox", (0, 0, 0, 0))),
                          origin_y=float((sp.get("origin") or (0, 0))[1]),
                          punctuation_uncertain=sp.get("font", "") in quote_fonts
                          and '"' in sp.get("text", ""))
                     for sp in ln.get("spans", [])]
            if any(sp.text.strip() for sp in spans):
                lines.append(Line(spans=spans, bbox=tuple(ln.get("bbox", bbox))))
        if lines:
            raw.blocks.append(Block(number=blk.get("number", 0), bbox=bbox, lines=lines))

    raw.drawings, raw.drawing_rects = drawing_rects(page)
    raw.blocks.sort(key=lambda b: (round(b.bbox[1], 1), round(b.bbox[0], 1)))
    return raw


def _native_quote_variants(page):
    """Fonts whose distinct printed double-quote glyphs collapse to ASCII.

    Glyph IDs are not alternate Unicode readings. This only signals that the
    extracted punctuation may not preserve the printed forms; it never repairs
    a token. Repeated copies of one glyph and correctly mapped curly quotes do
    not trigger. Apostrophe variants alone are common and are not this signal.
    """
    glyphs = {}
    try:
        traces = page.get_texttrace()
    except (AttributeError, RuntimeError):
        return set()
    for span in traces:
        if span.get("type") == 3 or span.get("opacity", 1) == 0:
            continue
        for char in span.get("chars", ()):
            if char[0] == 34 and char[1] >= 0:
                glyphs.setdefault(span.get("font", ""), set()).add(char[1])
    return {font for font, ids in glyphs.items() if len(ids) > 1}


def drawing_rects(page):
    """(count, boxes) of the page's vector paths.

    The count alone says a page carries rules or a diagram; the boxes say where.
    Above ``MAX_DRAWING_RECTS`` the page is a dense table or a traced scan and the
    boxes would be noise, so the count is kept and the boxes are not."""
    try:
        drawings = page.get_cdrawings()
    except Exception:
        try:
            drawings = page.get_drawings()
        except Exception:
            return 0, []
    rects = [tuple(d["rect"]) for d in drawings
             if d.get("rect") is not None] if len(drawings) <= MAX_DRAWING_RECTS \
        else []
    return len(drawings), rects


#: How far past a line's box its glyphs reach: antialiasing and ascenders
#: overshoot the measured rectangle, so a mask padded by this much does not
#: leave a sliver of the line behind.
MASK_PAD = 2.0


def ink_channel(doc, pno, rect, min_px=8, mask=()):
    """The centre x of a full-height empty channel inside ``rect``, if one exists.

    Two charts side by side are two figures; one chart never has a truly empty
    column from top to bottom of its territory (antialiasing sees to that).
    The split line is measured from pixels, not guessed from whitespace
    geometry: columns of the bounded render holding no print at all, with up
    to a dusting of single-pixel crossings allowed for a circle's edge passing
    through. ``mask`` whites out the page's text lines first -- a running head
    that crosses the gap between two charts is furniture, not a third artwork.
    Returns PDF-space x positions of every qualifying channel (usually none or
    one), empty when the territory has no marks or no clean channel.
    """
    clip = pymupdf.Rect(*rect)
    scale = _bounded_scale(clip, INK_SCALE)
    pix = doc[pno].get_pixmap(matrix=pymupdf.Matrix(scale, scale),
                              clip=clip, alpha=False)
    n, w, h = pix.n, pix.width, pix.height
    if not w or not h:
        return []
    data = bytearray(pix.samples)
    for box in mask:
        x0 = max(0, int((box[0] - MASK_PAD - clip.x0) * scale))
        y0 = max(0, int((box[1] - MASK_PAD - clip.y0) * scale))
        x1 = min(w, int((box[2] + MASK_PAD - clip.x0) * scale + 0.5) + 1)
        y1 = min(h, int((box[3] + MASK_PAD - clip.y0) * scale + 0.5) + 1)
        for y in range(y0, max(y0, y1)):
            row = y * w * n
            for x in range(x0, max(x0, x1)):
                data[row + x * n] = 255
    dust = max(2, int(h * 0.08))
    counts = []
    any_dark = False
    for x in range(w):
        dark = sum(1 for y in range(h) if data[y * w * n + x * n] < 200)
        any_dark = any_dark or dark > dust
        counts.append(dark)
    if not any_dark:
        return []
    channels = []
    run = None
    for x, dark in enumerate(counts):
        if dark > dust:
            if run is not None and x - run >= min_px:
                channels.append((run + x) / 2.0)
            run = None
        elif run is None:
            run = x
    inset = w * 0.1
    return [clip.x0 + (centre / scale) for centre in channels
            if centre >= inset and centre <= w - inset]


class ScanPixelProbe(object):
    """The pixel questions a scan figure pass asks of one page, answered from
    the document: does this rectangle hold marks, where are its full-height
    empty channels, which horizontal rules it carries, and what share is printed?
    The page's text lines are masked for these probes -- prose and furniture are not artwork, whatever they
    print over."""

    def __init__(self, doc, pno, mask=()):
        self._doc = doc
        self._pno = pno
        self._mask = list(mask)

    def channels(self, rect):
        return ink_channel(self._doc, self._pno, rect, mask=self._mask)

    def has_ink(self, rect):
        return region_has_ink(self._doc, self._pno, rect, mask=self._mask)

    def ink_bounds(self, rect):
        """Conservative PDF-space extent of source artwork in a bounded clip.

        Text masks remove surrounding prose and captions; one raster-pixel pad
        keeps faint antialiased edges inside the reported territory.
        """
        if len(rect) != 4 or not all(math.isfinite(float(v)) for v in rect):
            raise ValueError("invalid ink geometry")
        if rect[2] <= rect[0] or rect[3] <= rect[1]:
            raise ValueError("empty ink geometry")
        clip = pymupdf.Rect(*rect) & self._doc[self._pno].rect
        if clip.is_empty:
            return None
        data, n, w, h = _ink_render(self._doc, self._pno, clip, self._mask,
                                    scale=INK_EXTENT_SCALE)
        xs, ys = [], []
        channels = min(n, 3)
        for y in range(h):
            dark = [x for x in range(w)
                    if min(data[(y*w+x)*n:(y*w+x)*n+channels]) < 230]
            if dark:
                xs.extend((dark[0], dark[-1]))
                ys.append(y)
        if not xs:
            return None
        scale = _bounded_scale(clip, INK_EXTENT_SCALE)
        return (max(clip.x0, clip.x0 + (min(xs) - 1) / scale),
                max(clip.y0, clip.y0 + (ys[0] - 1) / scale),
                min(clip.x1, clip.x0 + (max(xs) + 2) / scale),
                min(clip.y1, clip.y0 + (ys[-1] + 2) / scale))

    def rules(self, rect):
        return rule_rows(self._doc, self._pno, rect, mask=self._mask)

    def coverage(self, rect):
        """The share of this rectangle's blocks that hold print: near 1.0 on a
        full-bleed cover, near 0 on a text or blank page."""
        data, n, w, h = _ink_render(self._doc, self._pno, rect, self._mask)
        if not w or not h:
            return 0.0
        cells = 10
        inking = 0
        for cy in range(cells):
            y0, y1 = h * cy // cells, h * (cy + 1) // cells
            for cx in range(cells):
                x0, x1 = w * cx // cells, w * (cx + 1) // cells
                dark = 0
                for y in range(y0, y1):
                    row = y * w * n
                    for x in range(x0, x1):
                        if data[row + x * n] < 200:
                            dark += 1
                if dark / float((y1 - y0) * (x1 - x0) or 1) >= INK_BLOCK_MIN:
                    inking += 1
        return inking / float(cells * cells)


def _ink_render(doc, pno, rect, mask=(), scale=INK_SCALE):
    """The bounded raster of a clip with the page's text whited out.

    One render for every ink question -- has_ink, channel, coverage -- so the
    scale bound and the mask pad are set in exactly one place. Prose and
    furniture are not artwork, whatever they print over.
    """
    clip = pymupdf.Rect(*rect)
    scale = _bounded_scale(clip, scale)
    pix = doc[pno].get_pixmap(matrix=pymupdf.Matrix(scale, scale),
                              clip=clip, alpha=False)
    n, w, h = pix.n, pix.width, pix.height
    data = bytearray(pix.samples)
    for box in mask:
        x0 = max(0, int((box[0] - MASK_PAD - clip.x0) * scale))
        y0 = max(0, int((box[1] - MASK_PAD - clip.y0) * scale))
        x1 = min(w, int((box[2] + MASK_PAD - clip.x0) * scale + 0.5) + 1)
        y1 = min(h, int((box[3] + MASK_PAD - clip.y0) * scale + 0.5) + 1)
        for y in range(y0, max(y0, y1)):
            row = y * w * n
            for x in range(x0, max(x0, x1)):
                data[row + x * n:row + (x + 1) * n] = b"\xff" * n
    return data, n, w, h


def region_has_ink(doc, pno, rect, thresh=INK_MIN, mask=()):
    """True when something is actually printed inside ``rect`` on the page.

    Blankness is the absence of marks, never a low dark share: a 12pt line of
    figure lettering inside a 1000pt box reads 0.04% dark globally and is
    exactly as meaningful as a dense plate, so the question is asked
    block-locally -- does any cell of the territory hold real print (thin
    strokes, small type, line art), rather than is the territory dark overall.
    Blank paper answers no everywhere and still drops. ``mask`` whites out the
    page's own prose boxes first: a margin whose only marks are the clipped
    edge of a line that already reads in the flow is not a figure either. The
    render takes the same pre-allocation bound as every other raster: a
    hostile clip is scaled down on the matrix before a single pixel exists.
    """
    data, n, w, h = _ink_render(doc, pno, rect, mask)
    if not w or not h:
        return False
    cells = 10
    for cy in range(cells):
        y0, y1 = h * cy // cells, h * (cy + 1) // cells
        for cx in range(cells):
            x0, x1 = w * cx // cells, w * (cx + 1) // cells
            dark = 0
            for y in range(y0, y1):
                row = y * w * n
                for x in range(x0, x1):
                    if data[row + x * n] < 200:
                        dark += 1
            total = (y1 - y0) * (x1 - x0)
            if total and dark / total >= INK_BLOCK_MIN:
                return True
    return False


#: Render scale for the rule query. A ruled grid's strokes are a point or two
#: tall and can be printed faint: the held-out register's inter-row rules are
#: 0.45pt of 0.65 gray, which antialiases away below 3x (MEASURED on the
#: frozen pack's raster). The ink proof's 0.35 renders them away entirely,
#: which is why that proof cannot report them.
RULE_SCALE = 3.0
#: A device row at least this dark across at least RULE_ROW_MIN of the
#: territory's width is a rule stroke, not prose. Ruling is printed light on
#: purpose, so the darkness bar sits far above the ink proof's 200; what
#: proves the stroke is the full-width row shape, which no line of set text --
#: masked out here -- and no paper shadow fills the same way.
RULE_ROW_DARK = 240
#: A device row dark across at least this share of the territory's width is a
#: rule stroke, not prose: antialiasing and the mask take the stroke's ends.
RULE_ROW_MIN = 0.6


def rule_rows(doc, pno, rect, mask=()):
    """Y centres (PDF space) of the horizontal rules printed inside ``rect``.

    A scanned register's ruling survives rasterization as thin strokes running
    the width of the measure, and the ink proof's 10x10 cells cannot see them:
    a one-point stroke lights no whole cell. This asks the rule's own question
    -- which device rows stay dark across the territory once the text is
    masked out -- from ONE grayscale render, pre-bounded on the matrix like
    every other raster before a single pixel is allocated.
    """
    clip = pymupdf.Rect(*rect)
    scale = _bounded_scale(clip, RULE_SCALE)
    pix = doc[pno].get_pixmap(matrix=pymupdf.Matrix(scale, scale),
                              clip=clip, alpha=False,
                              colorspace=pymupdf.csGRAY)
    n, w, h = pix.n, pix.width, pix.height
    if not w or not h:
        return []
    data = bytearray(pix.samples)
    for box in mask:
        x0 = max(0, int((box[0] - MASK_PAD - clip.x0) * scale))
        y0 = max(0, int((box[1] - MASK_PAD - clip.y0) * scale))
        x1 = min(w, int((box[2] + MASK_PAD - clip.x0) * scale + 0.5) + 1)
        y1 = min(h, int((box[3] + MASK_PAD - clip.y0) * scale + 0.5) + 1)
        for y in range(y0, max(y0, y1)):
            row = y * w * n
            for x in range(x0, max(x0, x1)):
                data[row + x * n] = 255
    strokes = []
    run = None
    for y in range(h):
        row = y * w * n
        dark = sum(1 for x in range(w) if data[row + x * n] < RULE_ROW_DARK)
        if dark >= RULE_ROW_MIN * w:
            if run is None:
                run = y
        elif run is not None:
            strokes.append((run + y - 1) / 2.0)
            run = None
    if run is not None:
        strokes.append((run + h - 1) / 2.0)
    return [clip.y0 + centre / scale for centre in strokes]


def read_pages(doc, page_numbers=None):
    if page_numbers is None:
        page_numbers = range(doc.page_count)
    return [read_page(doc, pno) for pno in page_numbers]


def _bounded_scale(rect, scale):
    """The largest scale at or under *scale* whose render fits the pixel budget.

    The budget is computed from the rectangle that will actually be rendered -- the
    page's own rect (rotation included) or the crop clip -- so the cap lands on the
    matrix before a single pixel is allocated.
    """
    area = abs(rect.width) * abs(rect.height)
    if area <= 0:
        return scale
    fit = (MAX_RASTER_PIXELS / float(area)) ** 0.5
    return min(float(scale), fit)


def render_page_jpeg(doc, pno, scale=1.5, quality=80, max_bytes=None, clip=None):
    """A JPEG of the page for the vision model.

    ``max_bytes`` steps the scale down rather than the quality: a model reading small
    superscripts needs resolution more than it needs smooth gradients. A page whose
    final attempt still exceeds the limit raises :class:`RasterTooLarge` -- the old
    behavior of returning the oversized bytes anyway made the ceiling advisory.

    ``clip`` narrows the picture to part of the page -- the pipeline uses it to leave
    the running head and the folio out, because they were taken out of the words.
    Cropping also buys resolution: the same byte budget over fewer pixels.

    The rendered geometry is bounded by ``MAX_RASTER_PIXELS`` BEFORE any pixmap is
    allocated: on a page whose size says the render would exceed the budget, the
    scale is brought under it first.
    """
    page = doc[pno]
    rect = pymupdf.Rect(*clip) if clip else None
    budgeted = _bounded_scale(rect if rect is not None else page.rect, scale)
    for attempt_scale in _scale_ladder(budgeted):
        matrix = pymupdf.Matrix(attempt_scale, attempt_scale)
        pix = page.get_pixmap(matrix=matrix, alpha=False, clip=rect)
        buf = io.BytesIO(pix.tobytes("jpg", jpg_quality=quality))
        data = buf.getvalue()
        if max_bytes is None or len(data) <= max_bytes:
            return data
    raise RasterTooLarge(
        "page %d renders over %d bytes at every permitted scale (last: %d bytes at "
        "%.2fx)" % (pno, max_bytes, len(data), attempt_scale))


def _scale_ladder(scale):
    """The starting scale and each quarter-step down, ending at a quarter.

    The ladder always offers the first scale -- the geometry bound may have brought
    it below the old half-scale floor -- and one last step at 0.25 before giving up.
    """
    step = scale
    while True:
        yield step
        step -= 0.25
        if step < 0.25:
            break


def crop_jpeg(doc, pno, rect, scale=2.0, quality=85):
    """A JPEG of one region — a chart, a table, a full-page scan used as a figure."""
    clip = pymupdf.Rect(*rect)
    bounded = _bounded_scale(clip, scale)
    pix = doc[pno].get_pixmap(matrix=pymupdf.Matrix(bounded, bounded), clip=clip,
                              alpha=False)
    return pix.tobytes("jpg", jpg_quality=quality)


def document_fingerprint(source):
    """SHA-256 of the PDF: the stable half of the per-page cache key.

    A path or bytes is hashed as it stands. An already-open document is hashed
    through the file it was opened from when it has one, and otherwise through what
    it contains -- MEASURED: ``Document.tobytes()`` embeds a fresh document id on
    every call, so hashing that would produce a different key each run and a cache
    that never once hit.
    """
    import hashlib

    digest = hashlib.sha256()
    if isinstance(source, (bytes, bytearray)):
        digest.update(source)
        return digest.hexdigest()
    if hasattr(source, "page_count"):
        name = getattr(source, "name", None)
        if name and os.path.exists(name):
            return document_fingerprint(name)
        digest.update(b"reflow-memory-document\x00")
        for pno in range(source.page_count):
            page = source[pno]
            digest.update(("%.2f,%.2f\x00" % (page.rect.width,
                                              page.rect.height)).encode("ascii"))
            digest.update(page.get_text().encode("utf-8", "replace"))
            digest.update(b"\x00")
        return digest.hexdigest()
    with open(source, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def outline(doc):
    """The PDF's own bookmarks, when it has any: free, authored navigation."""
    try:
        toc = doc.get_toc(simple=True) or []
    except Exception:
        return []
    return [{"level": int(lvl), "title": (title or "").strip(), "pno": int(page) - 1}
            for lvl, title, page in toc if (title or "").strip()]


def document_metadata(doc) -> Optional[dict]:
    try:
        return dict(doc.metadata or {})
    except Exception:
        return None
