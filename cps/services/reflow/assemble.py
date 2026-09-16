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

import bisect
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

#: A damaged marker is a short run of those glyphs standing tight against the word it
#: marks -- no space between the two -- and nothing but space after it. Both halves of
#: that shape are load-bearing. A degree token is ``16°`` after a space (the book
#: prints 207 of them); a marker is ``fourth.’°`` with none. The run must be at least
#: two glyphs long, so a sentence that ends ``, I`` cannot be read as note 1.
#: Read where the note zone first needs them, one layer down.
MARKER_GLYPHS = skeleton.MARKER_GLYPHS
GLYPH_DIGITS = skeleton.GLYPH_DIGITS
glyph_number = skeleton.glyph_number

GLYPH_MARKER = re.compile("(?<=[.,;:!?)\\]])([%s]{2,4})(?=\\s|$)"
                          % re.escape("".join(sorted(GLYPH_DIGITS))))

#: How far above its own face a note number may be printed and still be that note.
#: OCR loses the leading digit of a 3-digit marker; it does not lose two.
NOTE_WINDOW = (0, 100, 200, 300)

_WORD = re.compile(r"[A-Za-z][A-Za-z'’]*")
_HEAD_WORD = re.compile(r"[A-Za-z'’]+")
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
    #: A figure whose position was settled by geometry (a chart found in a page
    #: scan) needs no caption to place it by; one from an embedded image still
    #: raises figure_without_caption, because the caption is its only anchor.
    placed: str = ""
    band: int = 0
    column: int = 0
    #: A measured mirror-table row: one unit of paired cells. Rows are never
    #: joined into paragraphs -- the join across a row boundary fuses whole
    #: rows into each other (book 569 p547's 'Sextile t a u r u s ...').
    table_row: bool = False

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
    #: The exact characters the repair took out of the text layer. The conservation
    #: check subtracts these, itemised, rather than forgiving a whole category of
    #: loss: a repair that eats a real word is then visible as the word it ate.
    consumed: str = ""

    def to_dict(self):
        return {"kind": self.kind, "pno": self.pno, "detail": self.detail,
                "confidence": self.confidence, "consumed": self.consumed}


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
    pages: dict = field(default_factory=dict)         # pno -> [Element] as printed
    body_boxes: dict = field(default_factory=dict)    # pno -> the page minus its furniture
    figures: List[dict] = field(default_factory=list)
    #: Lettering the text layer read off a figure, kept with the figure instead of
    #: the prose: pno, text, bbox. Counted into conservation so the words are
    #: accounted -- in the crop and here -- never silently discarded.
    artwork: List[dict] = field(default_factory=list)
    reasons: dict = field(default_factory=dict)       # pno -> [reason, ...]
    style: Optional[skeleton.BookStyle] = None
    source_words: Counter = field(default_factory=Counter)
    #: pno -> [(what the text layer returned, what the book's numbering says), ...]
    renumbered: dict = field(default_factory=dict)
    conservation: Optional[ConservationReport] = None
    stats: dict = field(default_factory=dict)

    def page_reasons(self, pno):
        return list(self.reasons.get(pno, []))

    def page_box(self, pno):
        """What to photograph for this page: the part its words came from."""
        return self.body_boxes.get(pno)

    def unmarked_notes(self, pno):
        """Notes this page prints that nothing on this page points at.

        Almost always a marker the scanner destroyed rather than a note the author
        forgot to call: the number is printed under the rule, its superscript is
        printed in the body, and only the text layer disagrees. The model is shown
        the scan, so this is the short list it is allowed to put back (G1).
        """
        return sorted(note.num for note in self.notes
                      if note.pno == pno and note.num is not None and not note.marked)

    def swept_notes(self, pno):
        """Numbers this page printed under the rule that the text layer never returned.

        A different loss from ``unmarked_notes`` and invisible to it: there is no note
        to be unmarked. MEASURED on the acceptance book, 29 notes on 29 pages -- the
        page prints 58, 59 and 60, and 59's text comes back glued to the end of 58's
        with a stray quotation mark where its number belongs. The reader gets one note
        where the page printed two, and the second citation is buried in the first.

        The page's own numbering is the evidence. A number missing between two the
        page prints in ascending order was printed on this page, unless something else
        printed on the page could be that number misread -- which is the whole of the
        second condition, and what keeps page 157 out: it returns ``26, 27, 28, 29, 3,
        31`` and that 3 is the 30, damaged, not a note that went missing.

        Only a gap of exactly one is reported. Two numbers missing between the same
        two neighbours leaves nothing on the page to say where one note ends and the
        next begins, and a converter that splits them there is inventing a citation.
        """
        order = [note.num for note in self.notes
                 if note.pno == pno and note.num is not None]
        if len(order) < 2:
            return []
        backbone = set(_ascending_backbone(order))
        spine = [order[i] for i in sorted(backbone)]
        damaged = [order[i] for i in range(len(order)) if i not in backbone]
        return [left + 1 for left, right in zip(spine, spine[1:])
                if right - left == 2
                and not any(_ocr_could_read(str(left + 1), str(seen))
                            for seen in damaged)]

    def renumbered_notes(self, pno):
        """The note numbers this page had repaired, as ``(returned, kept)`` pairs.

        The text now carries the kept number and says nothing about the other one,
        and that is a problem for the second reader of the same small print. MEASURED
        on the acceptance book, page 125 prints 190 under the rule; the scanner
        returned 198; the model sent the repaired text alongside a raster of that
        superscript read 198 off the image exactly as the scanner had, and its whole
        page -- markers, asides and all -- was refused over the two digits we had
        already settled with better evidence than either reading.
        """
        return list(self.renumbered.get(pno, []))


# ------------------------------------------------------------------- run primitives

def plain_text(runs):
    """The readable text of a run list. A marker is ``[9]``, never a glued digit.

    That holds for a marker whose note was found and for one whose note was not: the
    model is shown the same shape either way, and the gate measures the same shape
    back.
    """
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


#: A hyphen an OCR layer set as its own span, possibly after space spans of
#: its own: ``[Anti][ ][­]`` belongs to the word it ends before any of the
#: rules below read it (book 570's shape).
_HYPHEN_RUN = re.compile(r"[" + HYPHENS + r"]+$")


def stitch_runs(prev, nxt, heal=True, vocab=None):
    """Join two run lists the way a typesetter's line break joins two lines.

    A line-break hyphen before a lowercase continuation is dropped (``conjunc-`` +
    ``tion``); a printed compound keeps its hyphen (``Sun-`` + ``Moon``); an em dash
    takes no space; anything else takes one.

    A heal has to be earned. The joined form must be a word *of this book* --
    printed whole somewhere in it -- because ``eighth-is`` is a sentence break,
    not a wrapped ``eighthis``, and no shipped dictionary knows ``Albubater``.
    The halves stay words either way; what the book never says whole is never
    invented. The heal also eats any space runs the span segmentation put
    between the halves (book 562's shape: the space as its own span), and does
    nothing when a non-space run (a note marker) follows the hyphen.

    ``heal`` is False only at a page turn: the source counter never joins a
    hyphenated word across pages, so the output may not either -- a wrap-break
    and a printed compound cannot be told apart at the turn, and the hyphen the
    page printed stays (``spear-bearing``, never ``spearbearing``).
    """
    prev = [list(r) for r in prev]
    nxt = [list(r) for r in nxt]
    if prev:
        # A hyphen the OCR set as its own span, possibly with space spans of its
        # own around it: ``[acron][-][ ]`` and ``[Anti][ ][­]`` both belong to
        # the word they end (books 570's and 631's shapes).
        k = len(prev) - 1
        while k >= 0 and prev[k][0] == "t" and not prev[k][1].strip():
            k -= 1
        if k >= 0 and prev[k][0] == "t" and _HYPHEN_RUN.fullmatch(prev[k][1].strip()):
            j = k - 1
            while j >= 0 and prev[j][0] == "t" and not prev[j][1].strip():
                j -= 1
            if j >= 0 and prev[j][0] == "t":
                prev[j][1] = prev[j][1].rstrip() + prev[k][1].strip()
                del prev[j + 1:]
    head = next((r[1] for r in nxt if r[0] == "t" and r[1].strip()), "")
    index = next((k for k in range(len(prev) - 1, -1, -1)
                  if prev[k][0] == "t" and prev[k][1].strip()), None)
    if index is not None:
        tail = prev[index][1].rstrip()
        hyphen = DEHYPH.search(tail)
        if heal and hyphen and head.lstrip()[:1].islower() and all(
                run[0] == "t" and not run[1].strip()
                for run in prev[index + 1:]):
            # The check is on the exact string the join would print, apostrophes
            # and all: "Aphrodite’" is earned only if the book prints it so,
            # and "people’s" is not "people". No prefix word, no heal either --
            # "are" alone is not evidence of anything.
            head_word = _first_word(nxt)
            prefix_text = "".join(
                run[1] for run in prev[:index + 1] if run[0] == "t").rstrip()
            prefix = re.search(r"[\w'’]+$", prefix_text.rstrip(HYPHENS))
            if vocab is None or (prefix and head_word and
                                 (prefix.group(0) + head_word).lower() in vocab):
                prev[index] = ["t", tail[:hyphen.end(1)]]
                del prev[index + 1:]
                return prev + nxt
        if hyphen or ENDDASH.search(tail):
            prev[index] = ["t", tail]
            return prev + nxt
    return prev + [["t", " "]] + nxt



def _first_word(nxt):
    """The first word of a run list, including a trailing contraction.

    The span segmentation cuts ``ple’s`` as ``[ple][’][s]``; judged as ``ple``
    it heals into ``people`` against a book that only ever prints ``people’s``.
    But the next word is not part of it either: letter-spaced ``s a t u r n``
    must not glue ``nals`` out of a wrapped ``Diur-/nal``.
    """
    word = ""
    for run in nxt:
        if run[0] != "t":
            break
        text = run[1].strip()
        if not text:
            if word:
                break
            continue
        if not word:
            token = _HEAD_WORD.match(text)
            if not token:
                return ""
            word = token.group(0)
            if token.end() < len(text):
                return word
            continue
        if text in ("’", "'") and not word.endswith(("’", "'")):
            word += text
            continue
        if text == "s" and word.endswith(("’", "'")):
            word += text
            continue
        break
    return word


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


def fit_note(seen, unclaimed, exact_only=False, printed=None):
    """Which of the page's unreferenced notes the scanner was reading, if one.

    A run that reads back as a note number exactly is that note. A run one
    substitution away from exactly one of them is that note only once the better
    evidence has had its turn -- see the order the three recovery passes run in --
    because ``\'"`` reads as 111 and is one edit from 161, and a page that prints
    one unreferenced note and holds one residue has already answered the question
    by counting. Two candidates is not a near miss, it is a coin flip, and the page
    is routed instead.

    ``printed`` is the page's own span of note numbers. A run of plain digits whose
    value falls inside it is not damage at all: MEASURED, page 104 marks 59 and
    prints notes 58, 60, 61, because 59's text was swept into 58's. ``59`` is one
    digit from ``58``, and binding it there prints a citation the book does not
    make. The page is missing a note, and saying so is the answer.
    """
    if not seen:
        return None
    exact = [n for n in unclaimed if str(n) == seen]
    if exact:
        return exact[0] if len(exact) == 1 else None
    if exact_only:
        return None
    if printed and seen.isdigit() and printed[0] - 1 <= int(seen) <= printed[1] + 1:
        return None
    near = [n for n in unclaimed if _ocr_could_read(str(n), seen)]
    return near[0] if len(near) == 1 else None


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
                    _strip_marker_prefix(runs, repairs, pno, text.strip(), number)
                if how == "degree":
                    texts[index + 1] = _repair_degree(texts[index + 1], repairs, pno,
                                                      text.strip(), number)
                claimed.add(number)
                runs.append(["sup", str(number), pno])
                index += 1
                continue
            if raised and number is None:
                # The note is set on another page, or was never found. The digits are
                # still a marker: left as text they glue onto the word before them and
                # the page reads "set overleaf204." A marker with nothing to point at
                # is a superscript, not a link, and the reader is told which ones.
                reasons.append("unresolved_marker")
                runs.append(["mark", text.strip()])
                index += 1
                continue
        runs.append(["t", text])
        index += 1
    return runs


def _is_raised(span, line):
    """A superscript with no flag set: its glyphs sit above the line's own baseline."""
    height = line.bbox[3] - line.bbox[1]
    if height <= 0:
        return False
    return span.bbox[3] < line.bbox[3] - 0.25 * height


def _strip_marker_prefix(runs, repairs, pno, digits, number):
    """C1: the digits the scanner dropped are still on the page, as punctuation.

    A marker that resolved through the note window lost its leading digits -- 103
    came back as ``3``. Those digits were printed, so something stands where they
    were: MEASURED, ``fourth.'°`` in front of the ``3`` and ``CE.'`` in front of a
    ``56``. The run in front of the marker is taken only when it reads back as
    exactly the digits that are missing, which is what tells it apart from the
    printed apostrophe in ``the astrologers'`` beside a marker that needed no window.
    """
    want = str(number)[:-len(digits)] if str(number).endswith(digits) else ""
    if not want:
        return
    index = next((k for k in range(len(runs) - 1, -1, -1)
                  if runs[k][0] == "t" and runs[k][1].strip()), None)
    if index is None:
        return
    text = runs[index][1]
    tail = text.rstrip()
    for width in range(1, len(want) + 2):
        residue = tail[-width:]
        if len(residue) != width or glyph_number(residue) != want:
            continue
        runs[index] = ["t", tail[:-width]]
        repairs.append(Repair("marker_prefix", pno,
                              "read %r before marker %s as the %s of note %d"
                              % (residue, digits, want, number), consumed=residue))
        return


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


def _marker_order_allows(elements, skel, at_element, at_run, number):
    """Would reading this run as *number* cite the notes out of the page's order?

    A book numbers its notes in the order it cites them, so a recovered marker has
    to fall between the markers already resolved either side of it. MEASURED, page
    117 of the acceptance book returns ``a teacher he found in Egypt."\'`` -- no
    digit at all -- ahead of the markers for 138 and 140. The residue reads back as
    111, one substitution from the 141 this page prints and leaves unreferenced, so
    it was bound there; the note the sentence cites is 136, whose text is about the
    teacher in Egypt, and no reading of two quotation marks could ever have reached
    it. Refusing the reading leaves the residue standing and 141 unreferenced, which
    is what the page can support, and the page is routed carrying both.

    The order is taken from the ascending backbone of the markers already bound, not
    from the neighbours themselves, because one marker whose own digits the scan
    truncated must not be allowed to veto a good reading -- page 157 marks
    ``26, 27, 28, 29, 3, 31`` and the 3 is a damaged 30.

    None of it applies to a page whose printed notes do not ascend. This book
    restarts its numbering at every chapter, so a page carrying the end of one
    chapter and the start of the next prints 111 and then 1, and a marker for 111
    correctly stands in front of a marker for 1 (MEASURED, page 380). The note zone
    says so, and it says the same thing about a page whose numbers came back too
    damaged to order.
    """
    printed = skel.note_numbers
    if any(later <= earlier for earlier, later in zip(printed, printed[1:])):
        return True
    rows = [((position, index), int(run[1]))
            for position, element in enumerate(elements)
            for index, run in enumerate(element.runs)
            if run[0] == "sup" and run[1].isdigit()]
    if not rows:
        return True
    values = [value for _, value in rows]
    backbone = _ascending_backbone(values)
    here = (at_element, at_run)
    below = [values[i] for i in backbone if rows[i][0] < here]
    above = [values[i] for i in backbone if rows[i][0] > here]
    if below and number <= max(below):
        return False
    if above and number >= min(above):
        return False
    return True


def _recover_glyph_markers(elements, skel, claimed, repairs, reasons,
                           exact_only=False):
    """C4: the scanner read the whole superscript as letters, and the page says so.

    This is the damage that costs the most. MEASURED over pages 100-129 of the
    acceptance book, 17 of 21 refused model answers differed from the deterministic
    text in nothing but this: the page prints ``Hephaestio`` and a superscript 50,
    the text layer returns ``Hephaestio.s°``, and the model -- which is shown the
    page -- writes the 50. There is no digit left for a marker span to recognise, so
    every one of those notes was also left unreferenced.

    The evidence for the reading is the page's own footnote zone. A run of glyphs
    that a digit is mistaken for, set tight against the word it marks, reading back
    as a number this page prints as a note and leaves unreferenced, is that marker.
    Where two of the page's notes would fit, nothing is repaired -- pairing them off
    by position prints a citation the page does not make.

    The page's own order of citation is the second half of that evidence, and
    ``_marker_order_allows`` holds every reading to it. A reading it refuses is not
    the end of the search: the run after it is tried for the same note, which is how
    page 119's 151 leaves the sentence about a rising sign -- where the page's other
    markers say 146 belongs -- and lands on the sentence about Hermes, Nechepso and
    Petosiris, after 150. MEASURED over the whole acceptance book it changes five
    pages: 53, 117 and 372 drop a reading their page's order contradicts, and 119
    and 207 move one onto the run that fits. Recovered markers go from 202 to 199,
    and pages citing their notes out of order from 28 to 24.
    """
    unclaimed = [n for n in sorted(skel.note_numbers) if n not in claimed]
    if not unclaimed:
        return
    numbers = sorted(skel.note_numbers)
    printed = (numbers[0], numbers[-1]) if numbers else None
    read_back = False

    for position_element, element in enumerate(elements):
        if element.kind not in ("p", "caption", "h") or not unclaimed:
            continue
        index = 0
        while index < len(element.runs) and unclaimed:
            run = element.runs[index]
            if run[0] != "t":
                index += 1
                continue
            text = run[1]
            found, position = None, 0
            while True:
                match = GLYPH_MARKER.search(text, position)
                if match is None:
                    break
                digits = glyph_number(match.group(1))
                number = fit_note(digits, unclaimed, exact_only=exact_only,
                                  printed=None if match.group(1) != digits else printed)
                if number is not None and _marker_order_allows(
                        elements, skel, position_element, index, number):
                    found = (match, number)
                    break
                position = match.end()
            if found is None:
                index += 1
                continue
            match, number = found
            element.runs[index:index + 1] = [["t", text[:match.start()]],
                                             ["sup", str(number), skel.pno],
                                             ["t", text[match.end():]]]
            claimed.add(number)
            unclaimed.remove(number)
            repairs.append(Repair("marker_glyphs", skel.pno,
                                  "read %r as the marker for note %d"
                                  % (match.group(1), number),
                                  confidence="medium", consumed=match.group(1)))
            read_back = True
            index += 2

    if read_back:
        reasons.append("glyph_markers_read")
        for element in elements:
            element.runs = tidy(element.runs)


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

#: How far a printed note number can be from what the OCR returned before the repair
#: stops being a reading and starts being a guess. MEASURED on the acceptance book:
#: every one of the damaged numbers is a dropped digit off one end (10 -> "1",
#: 50 -> "5", 100 -> "1", 118 -> "18", 115 -> "1", 112 -> "12") or a single
#: substitution (130 -> "138", 165 -> "163", 101 -> "161", 235 -> "233",
#: 280 -> "288"). Nothing needs more licence than that.
MAX_DROPPED_DIGITS = 2


def _ocr_could_read(printed, seen):
    """Could a scanner have returned *seen* from a page that printed *printed*?

    Digits go missing off either end. A note number set in the outer margin loses
    its last digit to the trim; one set tight against the previous note's full stop
    loses its first digit to the stop -- MEASURED, 118 came back as ``18`` and 115
    as ``1``. Both directions are one kind of damage, and widening the reading only
    makes the repairs that use it *less* willing to fire, because each of them
    demands that exactly one candidate survive.
    """
    if printed == seen:
        return True
    dropped = len(printed) - len(seen)
    if 0 < dropped <= MAX_DROPPED_DIGITS and (printed.startswith(seen)
                                              or printed.endswith(seen)):
        return True
    if len(printed) == len(seen):
        return sum(1 for a, b in zip(printed, seen) if a != b) == 1
    return False


def _page_marker_digits(skel):
    """Digit runs printed in the body as inline markers, damaged or not.

    A marker the scanner read as letters is still a marker, and a note whose own
    number is damaged is often marked by one: MEASURED, note 130 came back as 138
    and the only thing pointing at it was ``life.'3°``. Reading the glyph runs here
    too is what lets the note-number repair see its evidence.
    """
    found = set()
    for region in skel.body_regions:
        for line in region.lines:
            for span in line.spans:
                if skeleton.is_marker_span(span, line.size):
                    found.add(span.text.strip())
        for line in region.lines:
            for glyphs in GLYPH_MARKER.findall("".join(sp.text for sp in line.spans)):
                digits = glyph_number(glyphs)
                if digits:
                    found.add(digits)
    return found


def _is_marked(printed, markers):
    """Does the body mark this note? The marker can be damaged the same way the
    note's own number was — MEASURED: on page 49 the note printed 40 came back as
    "4" and so did the marker pointing at it. Demanding an undamaged marker as proof
    would refuse the repair on exactly the pages that need one."""
    return any(_ocr_could_read(printed, marker) for marker in markers)


#: A note's own printed number, at the head of its own text, where the scanner
#: returned it as digits.
_OPENING_NUMBER = re.compile(r"^\d{1,3}[ \t.)\]]*")


def _opening_number(text, number, repairs, pno):
    """Take the note's own printed number off the head of the note's text.

    The number reaches us twice: once as the number this note carries, and once in
    whatever the scanner returned for the raised digits in front of its first word.
    Where that was digits it has always come off here. Where the scan of a raised
    number is letters and quotation marks it did not, and the note shipped with the
    wreckage of its own number in front of its first word -- ``Is' Pingree,
    Yavanajataka`` for note 151, ``"1 Edited in CCAG`` for note 111.

    A run comes off only when it spells the number this note already has, which is
    evidence and not a guess: the glyphs are the number's own digits, so nothing is
    being decided here that the number did not already decide. A run that spells
    some other number (MEASURED: ``1"`` in front of note 105, two digits short and
    reading as 111) is wreckage of something this page cannot prove, and stays where
    the scanner put it for a model to read. A run that is nothing but letters is a
    word, however well it reads as a number.
    """
    run = text.split(" ", 1)[0]
    core = run.rstrip(".)]")
    if (core and not core.isdigit() and not core.isalpha()
            and skeleton.glyph_number(core) == str(number)):
        if repairs is not None:
            repairs.append(Repair(
                kind="note_number_glyphs", pno=pno,
                detail="read %r at the head of note %d as the scan of that number"
                       % (core, number),
                consumed=core))
        return text[len(run):].lstrip(" \t")
    return _OPENING_NUMBER.sub("", text, count=1)


def note_text(region, repairs=None, pno=None, vocab=None):
    """A footnote's text, assembled the way a paragraph is.

    Notes go down a side channel, and for a while that meant they skipped the line
    joining the body text gets. MEASURED: five words of the acceptance book were lost
    that way -- ``non-standard`` printed across two lines of a note came out as
    ``non- standard`` -- so this folds the note's lines through the same stitcher
    rather than keeping a second, quietly different one.
    """
    runs = []
    for index, line in enumerate(region.lines):
        text = line.stripped
        if index == 0 and region.number is not None:
            text = _opening_number(text, region.number, repairs, pno)
        if not text:
            continue
        piece = [["t", text]]
        runs = stitch_runs(runs, piece, vocab=vocab) if runs else piece
    return plain_text(tidy(runs))


def _ascending_backbone(values):
    """Indices of the longest strictly ascending run through *values*.

    A book's note numbers ascend, so the ones that break the ascent are the damaged
    ones -- but which ones those are is not a left-to-right question. MEASURED, page
    117 of the acceptance book came back as ``27, 28, 29, 38, 39, 32, 33``: two
    numbers near the middle are damaged, and reading left to right trusts those two
    and blames the four correct citations after them. The longest ascending
    subsequence is the book's real numbering and everything outside it is damage.

    Where two runs are equally long the earlier reading wins, which is what lets a
    page that opens ``9, 1, 11`` keep its 9 instead of keeping its 1.
    """
    total = len(values)
    # Longest ascending run *starting* at each index, by patience sorting over the
    # reversed sequence. O(n log n), because a long book has thousands of notes.
    onward = [1] * total
    tails = []
    for index in range(total - 1, -1, -1):
        position = bisect.bisect_left(tails, -values[index])
        onward[index] = position + 1
        if position == len(tails):
            tails.append(-values[index])
        else:
            tails[position] = -values[index]
    longest = max(onward) if onward else 0
    kept, need, last = [], longest, None
    for index, value in enumerate(values):
        if onward[index] == need and (last is None or value > last):
            kept.append(index)
            need -= 1
            last = value
    return kept


def repair_note_numbers(skeletons, repairs):
    """Read the damaged note numbers of a book off the undamaged ones around them.

    The evidence a repair needs is the gap the surviving numbers leave, the fact
    that a scanner could have returned what we got from the numbers that fill it,
    and a marker in the body pointing at each one. Where the gap holds exactly as
    many numbers as there are damaged notes in it, the page has answered by
    counting and each damaged number takes its place in order. Where it does not --
    a note whose whole region the scanner swept into its neighbour, a book that
    restarts its numbering every chapter -- nothing is repaired, the page says so,
    and a model gets to look at it.

    This runs over the whole book rather than a page at a time because the damage
    does not respect page breaks: MEASURED, note 115 came back as ``1`` at the head
    of its own zone with no earlier number on that page to fail to ascend from, and
    note 190 came back as ``198`` whose error is only visible from the 192 on the
    page after it.
    """
    renumbered = {}
    regions = [(skel, region) for skel in skeletons for region in skel.note_regions
               if region.number is not None]
    values = [region.number for _, region in regions]
    kept = _ascending_backbone(values)
    markers = {}
    for left, right in zip(kept, kept[1:]):
        damaged = list(range(left + 1, right))
        if not damaged:
            continue
        window = list(range(values[left] + 1, values[right]))
        if len(window) != len(damaged):
            continue
        pairs = list(zip(damaged, window))
        if not all(_ocr_could_read(str(number), str(values[index]))
                   for index, number in pairs):
            continue
        for index, number in pairs:
            skel = regions[index][0]
            if skel.pno not in markers:
                markers[skel.pno] = _page_marker_digits(skel)
        if not all(_is_marked(str(number), markers[regions[index][0].pno])
                   for index, number in pairs):
            continue
        for index, number in pairs:
            skel, region = regions[index]
            repairs.append(Repair(
                kind="note_number", pno=skel.pno,
                detail="note %s reads as %d between notes %d and %d"
                       % (region.number, number, values[left], values[right]),
                confidence="high"))
            renumbered.setdefault(skel.pno, []).append((region.number, number))
            region.number = number
    return renumbered


def _x_overlaps(a, b):
    return a[0] < b[2] and b[0] < a[2]


def _join_within_page(elements, vocab):
    """The page as it reads, not as the printer's blocks broke it.

    The stream below has always joined paragraphs across regions; the page the
    reader gets had not -- one <p> per scanned line, a sentence chopped around
    every column break (book 569's 'as'/'pects'). The same rules join it here:
    a finished sentence always wins, a wrap heals only into a word of this
    book, and a mirror-table row never joins. A figure (or its caption) breaks
    a sentence only where the text after it crosses its ground; text that
    flows *beside* it does not break at all -- the figure keeps its printed
    spot and the joined paragraph is simply whole (book 569 p471, where
    'phras-' and 'es such as' are one panel's word with the diagram to its
    left).
    """
    out = []
    for element in elements:
        if element.kind == "p" and not element.table_row:
            anchor = None
            barriers = []
            for prev in reversed(out):
                if prev.kind == "p" and not prev.table_row:
                    anchor = prev
                    break
                if prev.kind in ("fig", "caption"):
                    barriers.append(prev.bbox)
                else:
                    break
            if anchor is not None \
                    and all(not _x_overlaps(element.bbox, box) for box in barriers) \
                    and continues(anchor.text, element.text):
                anchor.runs = tidy(stitch_runs(
                    anchor.runs, element.runs, heal=True, vocab=vocab))
                anchor.pages = sorted(set(anchor.pages + element.pages))
                continue
        out.append(element)
    return out


def _copy_element(element):
    return Element(kind=element.kind, runs=[list(run) for run in element.runs],
                   pno=element.pno, level=element.level, bbox=element.bbox,
                   pages=list(element.pages), placed=element.placed,
                   band=element.band, column=element.column,
                   table_row=element.table_row)


def _runover_note(elements, book, skel):
    """The tail of the previous page's last note, printed above this page's notes.

    A note too long for the page that prints its number finishes at the top of the
    next page's footnote zone, with no number in front of it. The zone is found by
    where the page's small type starts, and this line is above it, so it is read as
    the last paragraph of the body. MEASURED on the acceptance book, 16 pages of 698
    print one.

    Shipping a bibliographic citation inside the chapter is the smaller half of the
    damage. The paragraph it lands under is the one the page turn cut in half, so it
    reads as that sentence's continuation and is joined onto it -- and the real
    continuation, on the next page, is orphaned. MEASURED, 11 of the 16: page 139's
    "insight into the social climate of astrology during" is finished by "occurs in
    The Error, 8: 4, although it is not terribly overt" and page 140 opens a new
    paragraph at "the rise of Christianity in the fourth century". That is the defect
    the previous converter shipped, arrived at from the other direction.

    The evidence is the evidence the page-turn join itself runs on, asked of the note
    instead of the paragraph: the previous page's last note stops mid-sentence and
    this reads on from it. Small type under the body that finishes nothing is left
    where it is -- a page can set a paragraph small.

    The tail stays on the page that prints it, as a note with no number. It is not
    moved back to the page its number is on: the model is shown a picture of the page
    the words are printed on, and its answer is measured against that page's text.
    """
    if len(elements) < 2 or not book.notes or not skel.note_regions:
        return None
    tail = elements[-1]
    if tail.kind != "p" or not tail.bbox:
        return None
    if tail.bbox[1] >= min(region.bbox[1] for region in skel.note_regions):
        return None
    previous = book.notes[-1]
    if previous.pno != skel.pno - 1 or not continues(previous.text, tail.text):
        return None
    elements.pop()
    return Note(num=None, text=tail.text, pno=skel.pno)


def page_source_text(book, pno):
    """The page as it was printed: what the model is shown, and what its answer is
    measured against. Furniture is already gone; the notes come after the body, the
    way the page sets them."""
    parts = [element.text for element in book.pages.get(pno, [])]
    for note in book.notes:
        if note.pno != pno:
            continue
        # A note whose number was never read keeps whatever the page printed in its
        # text; inventing a "?" for it would put a word in the model's mouth and
        # then fail the gate for saying it back.
        parts.append("[%d] %s" % (note.num, note.text) if note.num is not None
                     else note.text)
    return "\n\n".join(part for part in parts if part.strip())


def page_headings(book, pno):
    """What this page sets as a heading, as the model is told it and judged on it.

    Which lines of a page are headings is a fact the deterministic reader measures
    -- off the book's type ladder, the geometry of a run-in head and
    ``skeleton.acceptable_heading`` -- and the model is not asked to have an opinion
    about it: it is told the answer in ``prompts.user_prompt`` and refused by
    ``gate.check_structure`` if it marks anything else. The page membership is
    ``page_source_text``'s, so the list describes exactly the text the model is sent.
    """
    return [(int(element.level or 1), element.text)
            for element in book.pages.get(pno, []) if element.kind == "h"]


def _page_elements(skel, repairs, reasons, vocab=None):
    page_notes = skel.note_numbers
    claimed = set()
    elements = []

    for region in skel.regions:
        if region.kind == "figure":
            elements.append(Element(kind="fig", runs=[], pno=skel.pno,
                                    bbox=region.bbox,
                                    placed="geometry" if region.reason else ""))
            if region.caption_lines:
                runs = []
                for line in region.caption_lines:
                    line_runs = _line_runs(line, skel.pno, page_notes, claimed,
                                           repairs, reasons)
                    runs = line_runs if not runs else stitch_runs(runs, line_runs,
                                                                  vocab=vocab)
                runs = tidy(runs)
                if plain_text(runs):
                    elements.append(Element(kind="caption", runs=runs, pno=skel.pno,
                                            bbox=_region_caption_box(region),
                                            pages=[skel.pno]))
            continue
        if region.kind not in ("heading", "body", "caption"):
            continue
        runs = []
        for line in region.lines:
            line_runs = _line_runs(line, skel.pno, page_notes, claimed, repairs, reasons)
            runs = line_runs if not runs else stitch_runs(runs, line_runs,
                                                          vocab=vocab)
        runs = tidy(runs)
        if not plain_text(runs):
            continue
        kind = {"heading": "h", "caption": "caption"}.get(region.kind, "p")
        elements.append(Element(kind=kind, runs=runs, pno=skel.pno,
                                level=region.level, bbox=region.bbox,
                                pages=[skel.pno],
                                band=region.band, column=region.column,
                                table_row=region.reason == "table_row"))

    # A 'heading' that ends with a hyphen is prose misread by size: headings do
    # not end mid-word, and paragraphs cannot join into headings, so the wrap
    # would print as two halves of a word forever. Demotion needs the next
    # region in the same printed column to continue the word (lowercase) and
    # the joined form to be a word of this book -- the same proof the stitcher
    # asks for. On a two-up spread the pages interleave in the element list, so
    # 'next' is found by column geometry, never by list position.
    for index, element in enumerate(elements):
        if element.kind != "h" or not element.text.rstrip().endswith(tuple(HYPHENS)):
            continue
        prefix = re.search(r"[\w'’]+$",
                           element.text.rstrip().rstrip("".join(HYPHENS)))
        for nxt in elements[index + 1:]:
            if nxt.kind not in ("p", "caption"):
                continue
            if nxt.bbox[0] >= element.bbox[2] or nxt.bbox[2] <= element.bbox[0]:
                continue
            head_word = _first_word(nxt.runs)
            if (prefix and head_word and nxt.text[:1].islower()
                    and (prefix.group(0) + head_word).lower() in vocab):
                element.kind = "p"
            break

    # Strongest evidence first. A run that reads back as a note number exactly is
    # that note; then a page whose residue count matches its unreferenced notes has
    # answered by counting; only then is a single near miss allowed to decide.
    _recover_glyph_markers(elements, skel, claimed, repairs, reasons, exact_only=True)
    _recover_residue_markers(elements, skel, claimed, repairs, reasons)
    _recover_glyph_markers(elements, skel, claimed, repairs, reasons)
    # A plate with no caption under it has nothing to place it by, which is a page
    # worth a second look rather than a silent <figcaption></figcaption>. A figure
    # found by geometry is placed already: its position is the evidence.
    for index, element in enumerate(elements):
        if element.kind != "fig" or element.placed == "geometry":
            continue
        following = elements[index + 1] if index + 1 < len(elements) else None
        if following is None or following.kind != "caption":
            reasons.append("figure_without_caption")
    return elements, claimed


def _region_caption_box(region):
    boxes = [ln.bbox for ln in region.caption_lines if ln.bbox]
    if not boxes:
        return region.bbox
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def assemble(skeletons, style, raw_pages=None):
    """Turn per-page skeletons into one reading order plus its side channels."""
    book = Book(style=style)
    stitched = 0
    refused = 0
    vocab = book_vocabulary(raw_pages) if raw_pages is not None else None

    # Before anything else, because a damaged note number is what a damaged marker
    # would otherwise be fitted to, and the evidence for it is spread over pages.
    book.renumbered = repair_note_numbers(skeletons, book.repairs)

    for skel in skeletons:
        page_reasons = list(skel.reasons)
        elements, claimed = _page_elements(skel, book.repairs, page_reasons,
                                           vocab=vocab)
        runover = _runover_note(elements, book, skel)
        if runover is not None:
            book.notes.append(runover)
            book.repairs.append(Repair("note_runover", skel.pno,
                                       "read %r as the tail of note %s, printed on "
                                       "page %d" % (runover.text[:60],
                                                    book.notes[-2].num, skel.pno - 1),
                                       confidence="medium"))
        # Two views of the same page, and the difference matters. ``pages`` is the
        # page as it was printed, which is what a model is shown and what its answer
        # is gated against; the stream below is the book as it reads, with sentences
        # carried over the page turns. Within the page both views read the same:
        # the wraps the printer broke at a column or region edge are joined before
        # either view is taken. Joining mutates runs, so the stream gets its own
        # copies.
        elements = _join_within_page(elements, vocab)
        book.pages[skel.pno] = elements
        book.body_boxes[skel.pno] = skel.body_box()
        elements = [_copy_element(el) for el in elements]

        for region in skel.regions:
            if region.kind == "furniture":
                book.furniture.append(region.text)
            elif region.kind == "note":
                book.notes.append(Note(num=region.number,
                                       text=note_text(region, book.repairs, skel.pno,
                                                      vocab=vocab),
                                       pno=skel.pno,
                                       marked=region.number in claimed))
            elif region.kind == "artwork":
                book.artwork.append({"pno": skel.pno, "bbox": list(region.bbox),
                                     "text": region.text})
            elif region.kind == "figure":
                book.figures.append({"pno": skel.pno, "bbox": list(region.bbox),
                                     "full_page": bool(region.image and region.image.full_page),
                                     "needs_ink": bool(region.needs_ink),
                                     "found": region.reason or "embedded"})

        for position, element in enumerate(elements):
            previous = book.elements[-1] if book.elements else None
            if previous is not None and element.kind == "p" and previous.kind == "p" \
                    and not previous.table_row and not element.table_row \
                    and continues(previous.text, element.text):
                previous.runs = tidy(stitch_runs(
                    previous.runs, element.runs,
                    heal=element.pno in previous.pages, vocab=vocab))
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

        # Asked here because it is a question about the page's finished note list,
        # and because a page that prints nothing else wrong is exactly where a
        # swept note hides: 6 of the acceptance book's 29 are on pages with no
        # other reason to be looked at.
        if book.swept_notes(skel.pno):
            page_reasons.append("note_number_swept")

        if page_reasons:
            book.reasons[skel.pno] = sorted(set(page_reasons))

    if raw_pages is not None:
        book.source_words = source_word_counter(raw_pages, vocab=vocab,
                                                skeletons=skeletons)
        book.conservation = check_conservation(book.source_words, book.elements,
                                               book.notes, book.furniture,
                                               book.repairs, artwork=book.artwork)

    unmarked = [n for n in book.notes if n.num is not None and not n.marked]
    book.stats = {
        "pages": len(skeletons),
        "elements": len(book.elements),
        "paragraphs": sum(1 for el in book.elements if el.kind == "p"),
        "headings": sum(1 for el in book.elements if el.kind == "h"),
        "figures": len(book.figures),
        "artwork_words": sum(len(_WORD.findall(item["text"]))
                             for item in book.artwork),
        "notes": len([n for n in book.notes if n.num is not None]),
        "notes_unmarked": len(unmarked),
        # Notes the page printed and the text layer never returned. Counted here
        # because it is the one footnote defect no other number in this dict can
        # show: the note is not unmarked, it is not unresolved, and no word was lost.
        "notes_swept": sum(len(book.swept_notes(pno)) for pno in book.pages),
        "markers": sum(1 for el in book.elements for run in el.runs
                       if run[0] in ("sup", "mark")),
        "markers_unresolved": sum(1 for el in book.elements for run in el.runs
                                  if run[0] == "mark"),
        "page_joins": stitched,
        "page_joins_refused": refused,
        "repairs": len(book.repairs),
        "routed_reasons": Counter(r for reasons in book.reasons.values() for r in reasons),
    }
    return book


def deterministic_book(doc, page_numbers=None):
    """The whole no-model pass: open pages, measure the book, read every page."""
    raw_pages = extract.read_pages(doc, page_numbers)
    outline = extract.outline(doc)
    style = skeleton.book_style(
        raw_pages, outline=outline if skeleton.outline_is_useful(outline) else None)
    skeletons = [skeleton.page_skeleton(raw, style) for raw in raw_pages]
    return assemble(skeletons, style, raw_pages)


# ---------------------------------------------------------------- the invariant

def source_word_counter(raw_pages, vocab=None, skeletons=None):
    """Every alphabetic word the text layer printed, with line-break hyphens healed.

    Digits and punctuation are excluded deliberately: the marker repairs move those
    around on purpose, and a conservation check that trips on its own repairs is a
    check nobody will read.

    ``vocab`` gates the heal the same way the element stitcher is gated: only a
    joined form the book itself prints whole is one word. Healing with any other
    rule on either side of the comparison would manufacture mismatches that are
    not losses (``eighthis`` against the printed ``eighth-is``).

    On a page whose columns were proved and reordered, the raw block order can
    interleave the columns line by line (book 569's one-line OCR blocks) or park
    a running head between two half words (book 566's spreads). The reading
    joins those halves, so the counter must read the page in the same proven
    region order -- or it reports the reading's own join as a loss.
    """
    column_pages = {}
    if skeletons is not None and vocab is not None:
        column_pages = {
            skel.pno: skel for skel in skeletons
            if "columns_reordered" in skel.reasons
            or "mirror_table" in skel.reasons
            # A figure element between two paragraphs blocks the reading's join
            # just like a column boundary: the counter has to see the same
            # barrier or it heals a wrap the reading kept (book 569 p471).
            or any(region.kind == "figure" for region in skel.regions)}
    counter = Counter()
    for raw in raw_pages:
        skel = column_pages.get(raw.pno)
        if skel is not None:
            counter.update(_heal_page_columns(skel, vocab))
            continue
        text = _heal_linebreaks(raw.text, None) if vocab is None \
            else _heal_page(raw, vocab)
        counter.update(_WORD.findall(text))
    return counter


def _heal_page_columns(skel, vocab):
    """The reordered page's words: the content stream in region (reading) order
    with the stitcher's own seam rule, and every side channel counted the way
    the output side counts it -- notes healed within their region, furniture
    and artwork verbatim. Hard seams are the ones the reading never joins: a
    mirror-table row boundary, and a figure (or caption) whose ground the
    following text crosses. Text that flows *beside* a figure joins right past
    it, so the figure's own caption is held out of the seam's way too and
    counted beside the stream, never between two halves of a word.
    """
    counter = Counter()
    content = []
    frozen = set()
    pending = []          # bboxes of figure/caption regions since the last prose line
    held = []             # their caption lines, counted once the seam is decided
    previous_table = False

    def decide(next_box):
        """Whether the pending figures stand in the seam's way: only where the
        text after them crosses their ground."""
        return bool(pending) and any(_x_overlaps(next_box, seen)
                                     for seen in pending)

    for region in skel.regions:
        if region.kind in ("heading", "body"):
            lines = list(region.lines)
            box = region.bbox
        elif region.kind in ("figure", "caption"):
            pending.append(region.bbox)
            held.extend(region.caption_lines if region.kind == "figure"
                        else region.lines)
            continue
        elif region.kind == "note":
            counter.update(_WORD.findall(_heal_line_stream(
                [ln.text for ln in region.lines], vocab)))
            continue
        elif region.kind in ("furniture", "artwork"):
            counter.update(_WORD.findall(region.text))
            continue
        else:
            continue
        is_table = region.reason == "table_row"
        if lines and content:
            if is_table or previous_table or decide(box):
                frozen.add(len(content) - 1)
        if held:
            # A blocking figure's caption sits in the stream with frozen seams;
            # beside one it never interrupts, so its words count beside it.
            if decide(box):
                if content:
                    frozen.add(len(content) - 1)
                content.extend(held)
                if lines:
                    frozen.add(len(content) - 1)
            else:
                counter.update(_WORD.findall(_heal_line_stream(
                    [ln.text for ln in held], vocab)))
            held = []
        if lines:
            content.extend(lines)
            previous_table = is_table
            if region.kind in ("heading", "body"):
                pending = []
    if held:
        counter.update(_WORD.findall(_heal_line_stream(
            [ln.text for ln in held], vocab)))
    counter.update(_WORD.findall(_heal_line_stream(
        [ln.text for ln in content], vocab, frozen)))
    return counter


def _heal_line_stream(lines, vocab, frozen_seams=()):
    """One seam rule over the reading-ordered lines: a hyphen at the end of a
    line, a lowercase head after it, and a joined form the book prints whole.
    Within a block, at a block seam and at a column seam the output asks the
    same three things, so the count matches the reading word for word. Seams
    in ``frozen_seams`` are never healed: the reading does not join there.
    """
    parts = list(lines)
    for index in range(len(parts) - 1):
        if index in frozen_seams:
            continue
        tail = parts[index].rstrip()
        match = re.search(r"([\w'’]+)[" + HYPHENS + r"]$", tail)
        if not match:
            continue
        nxt = parts[index + 1].lstrip()
        if not nxt[:1].islower():
            continue
        head = _HEAD_WORD.match(nxt)
        if head and (match.group(1) + head.group(0)).lower() in vocab:
            parts[index] = tail[:match.end(1)] + head.group(0)
            parts[index + 1] = nxt[len(head.group(0)):]
    return "\n".join(parts)


def book_vocabulary(raw_pages):
    """The lowercase words this book prints, hyphens unhealed: the heal's proof.

    A wrapped word that appears nowhere whole is never joined on either side of
    the conservation comparison, so one-off wraps cost readability nothing and
    the count stays honest.
    """
    words = set()
    for raw in raw_pages:
        words.update(word.lower() for word in _WORD.findall(raw.text))
    return words


_LINEBREAK_TOKEN = re.compile(r"([\w'’]+)[" + HYPHENS + r"]\s*\n\s*([a-z][A-Za-z'’]*)")


def _heal_linebreaks(text, vocab):
    if vocab is None:
        return _LINEBREAK_HYPHEN.sub(r"\1\2", text)

    def repl(match):
        whole = match.group(1) + match.group(2)
        # The exact string, apostrophes and all, must be a word of this book:
        # "Aphrodite’" only joins if the book prints it so, and "people’s" is
        # not "people".
        return whole if whole.lower() in vocab else match.group(0)

    return _LINEBREAK_TOKEN.sub(repl, text)


def _heal_page(raw, vocab):
    """The page's text with the wraps the reading itself would join, healed.

    Within a block the rule is the stitcher's (vocab-gated). Across blocks the
    rule is the paragraph join's: a block whose last line does not end a
    sentence, a block that opens lowercase, and a joined form the book prints
    whole -- the three things ``continues`` and the stitcher ask together. Any
    other seam keeps its hyphen on both sides of the conservation comparison.
    """
    parts = [_heal_linebreaks(block.text, vocab) for block in raw.text_blocks]
    for index in range(len(parts) - 1):
        tail = parts[index].rstrip()
        nxt = parts[index + 1].lstrip()
        if not nxt[:1].islower() or SENT_END.search(tail):
            continue
        match = re.search(r"([\w'’]+)[" + HYPHENS + r"]$", tail)
        if not match:
            continue
        head = _HEAD_WORD.match(nxt)
        if head and (match.group(1) + head.group(0)).lower() in vocab:
            parts[index] = tail[:match.end(1)] + head.group(0)
            parts[index + 1] = nxt[len(head.group(0)):]
    return "\n".join(parts)


def check_conservation(source_words, elements, notes, furniture, repairs=(),
                       artwork=()):
    """SPEC §3: the assembled output's words are the source's words.

    Furniture is removed on purpose, so it is counted here rather than forgiven — a
    running head that stops being recognised as furniture shows up as *added*, and a
    paragraph that falls out of the stream shows up as *missing*.

    Artwork lettering is counted the same way: words the text layer read off a
    chart travel with the chart's crop, not with the prose, and are disclosed in
    the sidecar. A label that stops being recognised as artwork shows up as
    *missing* rather than vanishing into the picture.

    ``repairs`` are the only subtractions. A marker repair reads ``s°`` as the number
    50 and the ``s`` stops being a word, so the characters each repair declares it
    consumed come off the source side — itemised, one repair at a time, never as a
    category. A repair that eats a real word therefore still shows up here, as the
    word it ate.
    """
    output = Counter()
    for element in elements:
        output.update(_WORD.findall(element.text))
    for note in notes:
        output.update(_WORD.findall(note.text))
    for line in furniture:
        output.update(_WORD.findall(line))
    for item in artwork:
        output.update(_WORD.findall(item["text"] if isinstance(item, dict) else item))

    for repair in repairs:
        for word in _WORD.findall(repair.consumed):
            if source_words[word]:
                source_words = source_words - Counter([word])
    missing = sorted((source_words - output).elements())
    added = sorted((output - source_words).elements())
    return ConservationReport(ok=not missing and not added,
                              source_total=sum(source_words.values()),
                              output_total=sum(output.values()),
                              missing=missing, added=added)
