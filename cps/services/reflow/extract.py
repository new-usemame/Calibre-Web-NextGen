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

_WS = re.compile(r"\s+")


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
                "origin_y": round(self.origin_y, 2)}


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
        return {"bbox": [round(v, 2) for v in self.bbox],
                "area_ratio": round(self.area_ratio, 4)}


@dataclass
class RawPage(object):
    """Everything stage 2 is allowed to look at, for one page."""

    pno: int
    width: float
    height: float
    blocks: List[Block] = field(default_factory=list)
    images: List[Image] = field(default_factory=list)
    drawings: int = 0

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
        return any(img.full_page for img in self.images)

    def to_dict(self):
        return {"pno": self.pno, "width": round(self.width, 2),
                "height": round(self.height, 2), "drawings": self.drawings,
                "blocks": [b.to_dict() for b in self.blocks],
                "images": [i.to_dict() for i in self.images]}


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
                          origin_y=float((sp.get("origin") or (0, 0))[1]))
                     for sp in ln.get("spans", [])]
            if any(sp.text.strip() for sp in spans):
                lines.append(Line(spans=spans, bbox=tuple(ln.get("bbox", bbox))))
        if lines:
            raw.blocks.append(Block(number=blk.get("number", 0), bbox=bbox, lines=lines))

    raw.drawings = count_drawings(page)
    raw.blocks.sort(key=lambda b: (round(b.bbox[1], 1), round(b.bbox[0], 1)))
    return raw


def count_drawings(page):
    """Vector path count. Zero on every page of a scan; non-zero on born-digital
    pages with rules, tables or diagrams."""
    try:
        return len(page.get_cdrawings())
    except Exception:
        try:
            return len(page.get_drawings())
        except Exception:
            return 0


def read_pages(doc, page_numbers=None):
    if page_numbers is None:
        page_numbers = range(doc.page_count)
    return [read_page(doc, pno) for pno in page_numbers]


def render_page_jpeg(doc, pno, scale=1.5, quality=80, max_bytes=None, clip=None):
    """A JPEG of the page for the vision model.

    ``max_bytes`` steps the scale down rather than the quality: a model reading small
    superscripts needs resolution more than it needs smooth gradients.

    ``clip`` narrows the picture to part of the page -- the pipeline uses it to leave
    the running head and the folio out, because they were taken out of the words.
    Cropping also buys resolution: the same byte budget over fewer pixels.
    """
    rect = pymupdf.Rect(*clip) if clip else None
    for attempt_scale in _scale_ladder(scale):
        matrix = pymupdf.Matrix(attempt_scale, attempt_scale)
        pix = doc[pno].get_pixmap(matrix=matrix, alpha=False, clip=rect)
        buf = io.BytesIO(pix.tobytes("jpg", jpg_quality=quality))
        data = buf.getvalue()
        if max_bytes is None or len(data) <= max_bytes:
            return data
    return data


def _scale_ladder(scale):
    step = scale
    while step >= 0.5:
        yield step
        step -= 0.25


def crop_jpeg(doc, pno, rect, scale=2.0, quality=85):
    """A JPEG of one region — a chart, a table, a full-page scan used as a figure."""
    clip = pymupdf.Rect(*rect)
    pix = doc[pno].get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip, alpha=False)
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
