# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Stage 3: join what the page breaks split, bind markers to their notes, and prove
no printed word was lost doing it.

Three measured defects shaped this module.

*Footnotes are a side channel.* The previous converter emitted footnote elements into
the same linear stream as the prose, then refused to join a paragraph across a page
turn unless the previous element was itself a paragraph. 268 of 270 blocked joins in
one book were blocked by nothing but the notes printed between the two halves. Notes
belong to their page; the prose stream is prose.

*One page-numbering convention.* That same converter stored 0-based page numbers on
paragraphs and 1-based on footnotes, so a marker looked its note up on the wrong
page. Everything here is 0-based, and a test says so.

*Marker damage is repaired, never invented.* Commercial OCR renders small
superscripts wrong in three measured ways; each repair is recorded with its page and
its evidence, and each is paired with printed text it must leave alone.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from . import extract, skeleton

HYPHENS = "-­‐‑"
DASHES = "‒–—―"
DEHYPH = re.compile(r"(\w)[" + HYPHENS + r"]$")
ENDDASH = re.compile(r"[" + DASHES + r"]$")
SENT_END = re.compile(r'[.!?;:"”’\)\]]\s*$')

#: A superscript the OCR layer replaced with quote punctuation: ``practice.' The`` or
#: ``luminaries.'" Rhetorius``. The reference pattern stopped at the single quote and
#: so matched neither of the 62 measured ``.'"`` sites.
GLYPH_RESIDUE = re.compile(r"(?<=[.,;:!?])['’](?:[\"”])?(?=\s|$)")

#: OCR reads a leading 1 as an apostrophe (``CE.'`` + ``56`` for note 156) only where
#: the marker had to be resolved through the note window; a marker that resolves on
#: its own face is beside a printed apostrophe.
STRAY_APOSTROPHE = re.compile(r"(?<=[.,;:!?])['’]\s*$")

#: How far above its own face a note number may be printed and still be that note.
#: OCR loses the leading digit of a 3-digit marker; it does not lose two.
NOTE_WINDOW = (0, 100, 200, 300)

_WORD = re.compile(r"[A-Za-z][A-Za-z'’]*")
_LINEBREAK_HYPHEN = re.compile(r"(\w)[" + HYPHENS + r"]\s*\n\s*([a-z])")


@dataclass
class Element(object):
    """One block of the reading order. ``runs`` is JSON-able on purpose."""

    kind: str                 # p | h | caption | fig
    runs: list = field(default_factory=list)
    pno: int = 0
    level: int = 0
    bbox: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    pages: List[int] = field(default_factory=list)

    @property
    def text(self):
        return plain_text(self.runs)

    def to_dict(self):
        return {"kind": self.kind, "runs": self.runs, "pno": self.pno,
                "level": self.level, "pages": self.pages or [self.pno]}


@dataclass
class Note(object):
    num: Optional[int]
    text: str
    pno: int
    marked: bool = False

    def to_dict(self):
        return {"num": self.num, "text": self.text, "pno": self.pno,
                "marked": self.marked}


@dataclass
class Repair(object):
    """A deliberate change to what the text layer said, with its evidence."""

    kind: str
    pno: int
    detail: str
    confidence: str = "high"

    def to_dict(self):
        return {"kind": self.kind, "pno": self.pno, "detail": self.detail,
                "confidence": self.confidence}


@dataclass
class ConservationReport(object):
    ok: bool
    source_total: int
    output_total: int
    missing: List[str] = field(default_factory=list)
    added: List[str] = field(default_factory=list)

    def to_dict(self):
        return {"ok": self.ok, "source_total": self.source_total,
                "output_total": self.output_total,
                "missing": self.missing[:40], "added": self.added[:40]}


@dataclass
class Book(object):
    elements: List[Element] = field(default_factory=list)
    notes: List[Note] = field(default_factory=list)
    repairs: List[Repair] = field(default_factory=list)
    furniture: List[str] = field(default_factory=list)
    figures: List[dict] = field(default_factory=list)
    reasons: dict = field(default_factory=dict)       # pno -> [reason, ...]
    style: Optional[skeleton.BookStyle] = None
    source_words: Counter = field(default_factory=Counter)
    conservation: Optional[ConservationReport] = None
    stats: dict = field(default_factory=dict)

    def page_reasons(self, pno):
        return list(self.reasons.get(pno, []))


# ------------------------------------------------------------------- run primitives

def plain_text(runs):
    """The readable text of a run list. A marker is ``[9]``, never a glued digit."""
    text = "".join(r[1] if r[0] == "t" else "[%s]" % r[1] for r in runs)
    return re.sub(r"[^\S\n]{2,}", " ", text).strip()


def tidy(runs):
    """Drop empty text runs and merge neighbours, so run indices stay meaningful."""
    out = []
    for run in runs:
        if run[0] == "t":
            if not run[1]:
                continue
            if out and out[-1][0] == "t":
                out[-1] = ["t", out[-1][1] + run[1]]
                continue
        out.append(list(run))
    while out and out[0][0] == "t" and not out[0][1].strip():
        out.pop(0)
    while out and out[-1][0] == "t" and not out[-1][1].strip():
        out.pop()
    return out


def stitch_runs(prev, nxt):
    """Join two run lists the way a typesetter's line break joins two lines.

    A line-break hyphen before a lowercase continuation is dropped (``conjunc-`` +
    ``tion``); a printed compound keeps its hyphen (``Sun-`` + ``Moon``); an em dash
    takes no space; anything else takes one.
    """
    prev = [list(r) for r in prev]
    nxt = [list(r) for r in nxt]
    head = next((r[1] for r in nxt if r[0] == "t" and r[1].strip()), "")
    index = next((k for k in range(len(prev) - 1, -1, -1)
                  if prev[k][0] == "t" and prev[k][1].strip()), None)
    if index is not None:
        tail = prev[index][1].rstrip()
        hyphen = DEHYPH.search(tail)
        if hyphen and head.lstrip()[:1].islower():
            prev[index] = ["t", tail[:hyphen.end(1)]]
            return prev + nxt
        if hyphen or ENDDASH.search(tail):
            prev[index] = ["t", tail]
            return prev + nxt
    return prev + [["t", " "]] + nxt


def continues(prev_text, next_text):
    """True when ``next_text`` is the tail of the paragraph ``prev_text`` started.

    Typeset books break paragraphs across pages constantly. Without this every page
    turn produces a spurious paragraph break mid-sentence; with it too loose, every
    page turn fuses two real paragraphs — so a finished sentence always wins.
    """
    if not prev_text or not next_text:
        return False
    if SENT_END.search(prev_text):
        return False
    return next_text[:1].islower() or next_text[:1] in ",;"


# ------------------------------------------------------------------- marker binding

def resolve_marker(digits, page_notes, claimed, following_text):
    """Which note this run of digits refers to, and how we decided.

    Returns ``(number, how)`` where *how* is ``plain`` (the digits are the number),
    ``window`` (OCR dropped a leading digit) or ``degree`` (OCR read the trailing
    zero as a degree sign). ``None`` means we do not know, which is a routing reason,
    not a licence to guess.
    """
    value = int(digits)
    for offset in NOTE_WINDOW:
        candidate = value + offset
        if candidate in page_notes and candidate not in claimed:
            return candidate, ("plain" if offset == 0 else "window")
    if following_text.lstrip()[:1] == "°":
        candidate = int(digits + "0")
        if candidate in page_notes and candidate not in claimed:
            return candidate, "degree"
    return None, None


def _line_runs(line, pno, page_notes, claimed, repairs, reasons):
    """One physical line's runs, with inline markers bound to their notes."""
    spans = [sp for sp in line.spans if sp.text]
    texts = [sp.text for sp in spans]
    dom = line.size
    runs = []

    index = 0
    while index < len(spans):
        span = spans[index]
        text = texts[index]
        if skeleton.is_marker_span(span, dom):
            following = "".join(texts[index + 1:])
            number, how = resolve_marker(text.strip(), page_notes, claimed, following)
            raised = span.superscript or _is_raised(span, line)
            opens_line = not any(r[0] == "t" and r[1].strip() for r in runs)
            if number is not None and (raised or not opens_line):
                if how == "window":
                    _repair_stray_apostrophe(runs, repairs, pno, text.strip(), number)
                if how == "degree":
                    texts[index + 1] = _repair_degree(texts[index + 1], repairs, pno,
                                                      text.strip(), number)
                claimed.add(number)
                runs.append(["sup", str(number), pno])
                index += 1
                continue
            if raised and number is None:
                reasons.append("unresolved_marker")
        runs.append(["t", text])
        index += 1
    return runs


def _is_raised(span, line):
    """A superscript with no flag set: its glyphs sit above the line's own baseline."""
    height = line.bbox[3] - line.bbox[1]
    if height <= 0:
        return False
    return span.bbox[3] < line.bbox[3] - 0.25 * height


def _repair_stray_apostrophe(runs, repairs, pno, digits, number):
    """C1: the OCR read the marker's leading ``1`` as an apostrophe."""
    index = next((k for k in range(len(runs) - 1, -1, -1)
                  if runs[k][0] == "t" and runs[k][1].strip()), None)
    if index is None:
        return
    text = runs[index][1]
    match = STRAY_APOSTROPHE.search(text)
    if not match:
        return
    runs[index] = ["t", text[:match.start()] + text[match.end():]]
    repairs.append(Repair("marker_apostrophe", pno,
                          "read %r before marker %s as the leading digit of note %d"
                          % (match.group(0).strip(), digits, number)))


def _repair_degree(following, repairs, pno, digits, number):
    """C2: the OCR read the marker's trailing ``0`` as a degree sign."""
    stripped = following.lstrip()
    offset = len(following) - len(stripped)
    if stripped[:1] != "°":
        return following
    repairs.append(Repair("marker_degree", pno,
                          "read the degree sign after %s as the trailing digit of note %d"
                          % (digits, number)))
    return following[:offset] + stripped[1:]


def _recover_residue_markers(elements, skel, claimed, repairs, reasons):
    """C3: the OCR replaced the whole superscript with quote punctuation.

    Per page, the notes with no rendered marker and the leftover quote residue are
    paired off in order, and only when the two counts agree. A page with one marker
    lost outright, or a real elided quotation, has unequal counts and is left exactly
    as read — and routed, because the model can see what the digits were.
    """
    missing = sorted(n for n in skel.note_numbers if n not in claimed)
    if not missing:
        return
    slots = [(el, i, m) for el in elements if el.kind in ("p", "caption")
             for i, run in enumerate(el.runs) if run[0] == "t"
             for m in GLYPH_RESIDUE.finditer(run[1])]
    if len(slots) != len(missing):
        reasons.append("note_marker_mismatch")
        return

    for (element, index, match), number in zip(reversed(slots), reversed(missing)):
        text = element.runs[index][1]
        element.runs[index:index + 1] = [["t", text[:match.start()]],
                                         ["sup", str(number), skel.pno],
                                         ["t", text[match.end():]]]
        claimed.add(number)
        repairs.append(Repair("marker_residue", skel.pno,
                              "paired quote residue %r with unmarked note %d"
                              % (match.group(0), number), confidence="medium"))
    reasons.append("residue_markers_paired")
    for element in elements:
        element.runs = tidy(element.runs)


# ------------------------------------------------------------------------ assembly

def _page_elements(skel, repairs, reasons):
    page_notes = skel.note_numbers
    claimed = set()
    elements = []

    for region in skel.regions:
        if region.kind == "figure":
            elements.append(Element(kind="fig", runs=[], pno=skel.pno,
                                    bbox=region.bbox))
            continue
        if region.kind not in ("heading", "body", "caption"):
            continue
        runs = []
        for line in region.lines:
            line_runs = _line_runs(line, skel.pno, page_notes, claimed, repairs, reasons)
            runs = line_runs if not runs else stitch_runs(runs, line_runs)
        runs = tidy(runs)
        if not plain_text(runs):
            continue
        kind = {"heading": "h", "caption": "caption"}.get(region.kind, "p")
        elements.append(Element(kind=kind, runs=runs, pno=skel.pno,
                                level=region.level, bbox=region.bbox,
                                pages=[skel.pno]))

    _recover_residue_markers(elements, skel, claimed, repairs, reasons)
    return elements, claimed


def assemble(skeletons, style, raw_pages=None):
    """Turn per-page skeletons into one reading order plus its side channels."""
    book = Book(style=style)
    stitched = 0
    refused = 0

    for skel in skeletons:
        page_reasons = list(skel.reasons)
        elements, claimed = _page_elements(skel, book.repairs, page_reasons)

        for region in skel.regions:
            if region.kind == "furniture":
                book.furniture.append(region.text)
            elif region.kind == "note":
                book.notes.append(Note(num=region.number, text=region.text,
                                       pno=skel.pno,
                                       marked=region.number in claimed))
            elif region.kind == "figure":
                book.figures.append({"pno": skel.pno, "bbox": list(region.bbox),
                                     "full_page": bool(region.image and region.image.full_page)})

        for position, element in enumerate(elements):
            previous = book.elements[-1] if book.elements else None
            if previous is not None and element.kind == "p" and previous.kind == "p" \
                    and continues(previous.text, element.text):
                previous.runs = tidy(stitch_runs(previous.runs, element.runs))
                previous.pages = sorted(set(previous.pages + element.pages))
                stitched += 1
                continue
            if position == 0 and previous is not None and previous.kind == "p" \
                    and element.kind == "p" and not SENT_END.search(previous.text):
                # The tail of the previous page does not finish its sentence and this
                # page does not read as its continuation. Somebody should look.
                page_reasons.append("page_boundary_join_uncertain")
                refused += 1
            book.elements.append(element)

        if page_reasons:
            book.reasons[skel.pno] = sorted(set(page_reasons))

    if raw_pages is not None:
        book.source_words = source_word_counter(raw_pages)
        book.conservation = check_conservation(book.source_words, book.elements,
                                               book.notes, book.furniture)

    unmarked = [n for n in book.notes if n.num is not None and not n.marked]
    book.stats = {
        "pages": len(skeletons),
        "elements": len(book.elements),
        "paragraphs": sum(1 for el in book.elements if el.kind == "p"),
        "headings": sum(1 for el in book.elements if el.kind == "h"),
        "figures": len(book.figures),
        "notes": len([n for n in book.notes if n.num is not None]),
        "notes_unmarked": len(unmarked),
        "markers": sum(1 for el in book.elements for run in el.runs if run[0] == "sup"),
        "page_joins": stitched,
        "page_joins_refused": refused,
        "repairs": len(book.repairs),
        "routed_reasons": Counter(r for reasons in book.reasons.values() for r in reasons),
    }
    return book


def deterministic_book(doc, page_numbers=None):
    """The whole no-model pass: open pages, measure the book, read every page."""
    raw_pages = extract.read_pages(doc, page_numbers)
    style = skeleton.book_style(raw_pages, outline=extract.outline(doc))
    skeletons = [skeleton.page_skeleton(raw, style) for raw in raw_pages]
    return assemble(skeletons, style, raw_pages)


# ---------------------------------------------------------------- the invariant

def source_word_counter(raw_pages):
    """Every alphabetic word the text layer printed, with line-break hyphens healed.

    Digits and punctuation are excluded deliberately: the marker repairs move those
    around on purpose, and a conservation check that trips on its own repairs is a
    check nobody will read.
    """
    counter = Counter()
    for raw in raw_pages:
        text = _LINEBREAK_HYPHEN.sub(r"\1\2", raw.text)
        counter.update(_WORD.findall(text))
    return counter


def check_conservation(source_words, elements, notes, furniture):
    """SPEC §3: the assembled output's words are the source's words.

    Furniture is removed on purpose, so it is counted here rather than forgiven — a
    running head that stops being recognised as furniture shows up as *added*, and a
    paragraph that falls out of the stream shows up as *missing*.
    """
    output = Counter()
    for element in elements:
        output.update(_WORD.findall(element.text))
    for note in notes:
        output.update(_WORD.findall(note.text))
    for line in furniture:
        output.update(_WORD.findall(line))

    missing = sorted((source_words - output).elements())
    added = sorted((output - source_words).elements())
    return ConservationReport(ok=not missing and not added,
                              source_total=sum(source_words.values()),
                              output_total=sum(output.values()),
                              missing=missing, added=added)
