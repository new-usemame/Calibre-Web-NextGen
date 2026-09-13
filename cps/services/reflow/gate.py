# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Hard gates. A page that does not pass ships its deterministic text instead.

Two gates live here.

**G1, word preservation.** Case-sensitive sequence alignment of the deterministic
page text against the model's HTML. It is case-sensitive on purpose: a measured
probe caught a frontier model normalising the printed brand ``olmOCR`` to
``OLMOCR`` — output that reads more correct and is wrong about what is printed.
A punctuation-insensitive comparison missed it.

The allow-list is deliberately tiny and every hit is reported, because an
allow-list is where a safety gate goes to die. It permits two classes of
difference, both of which move characters around and neither of which can add a
word or lose one:

* a *re-tokenisation*, where the same characters are split or joined differently
  (``caution.160`` -> ``caution. 160``);
* a *marker recovery*, where one punctuation character that the scanner left in
  place of a superscript note number becomes that number again — and only a
  number this page prints, leaves unreferenced, and has not already used.

The second is off unless the caller passes ``recoverable_markers``, so on a page
that has lost nothing a quotation mark is still a quotation mark.

**G3, structural schema.** Allowed tags only; heading levels drawn from the
deterministic ladder rather than the model's opinion (probes showed models
disagree about absolute heading level on identical pages); every noteref has an
aside; every figure carries a caption.
"""

import difflib
import html as html_module
import re
import unicodedata
from collections import namedtuple

# A marker is written ``[160]`` in our deterministic text and rendered as a bare
# ``160`` inside a noteref anchor. Normalising both sides is a declared rewrite,
# not an allowance: it applies symmetrically and can neither add nor drop a word.
_MARKER_NOTATION = re.compile(r"\[(\d{1,3})\]")
_TAG = re.compile(r"<[^>]+>")
_CONTRACT_LINE = re.compile(r'\{\s*"uncertain"\s*:.*\}\s*$', re.S)
# Line-break hyphenation that survived into either side.
_LINEBREAK_HYPHEN = re.compile(r"(\w)-\s*\n\s*(\w)")

_QUOTE_MAP = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "–": "-", "—": "-", "‒": "-", "―": "-",
    "­": "", "‐": "-", "‑": "-",
}

#: Below this many source tokens a page has nothing to preserve and the gate
#: reports NOT_APPLICABLE rather than a vacuous PASS.
MIN_SOURCE_TOKENS = 5

Difference = namedtuple("Difference", "kind source output")

ALLOWED_TAGS = frozenset({
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "blockquote", "aside", "a",
    "figure", "figcaption", "img", "table", "thead", "tbody", "tr", "td", "th",
    "ul", "ol", "li", "em", "i", "strong", "b", "sup", "sub", "br", "span",
    "section", "div", "caption",
})


class GateResult(object):
    """The verdict plus everything a reviewer needs to second-guess it."""

    __slots__ = ("verdict", "src_tokens", "out_tokens", "similarity",
                 "missing", "invented", "case_only", "allowed_hits", "unexplained",
                 "recovered_markers")

    def __init__(self, verdict, src_tokens, out_tokens, similarity,
                 missing, invented, case_only, allowed_hits, unexplained,
                 recovered_markers=()):
        self.verdict = verdict
        self.src_tokens = src_tokens
        self.out_tokens = out_tokens
        self.similarity = similarity
        self.missing = missing
        self.invented = invented
        self.case_only = case_only
        self.allowed_hits = allowed_hits
        self.unexplained = unexplained
        #: Notes whose marker the model read back off the page image (G1 allowance).
        self.recovered_markers = sorted(recovered_markers)

    @property
    def ok(self):
        """True when the page may ship the model's structure.

        NOT_APPLICABLE is not ok for the *edit* path: a page with no source text
        was transcribed, not edited, and travels the R4 route instead.
        """
        return self.verdict == "PASS"

    @property
    def missing_count(self):
        return sum(len(d.source) for d in self.missing)

    @property
    def invented_count(self):
        return sum(len(d.output) for d in self.invented)

    @property
    def case_only_count(self):
        return sum(len(d.source) for d in self.case_only)

    def as_dict(self):
        return {
            "verdict": self.verdict,
            "src_tokens": self.src_tokens,
            "out_tokens": self.out_tokens,
            "similarity": round(self.similarity, 4),
            "missing": [" ".join(d.source) for d in self.missing][:20],
            "invented": [" ".join(d.output) for d in self.invented][:20],
            "case_only": [[" ".join(d.source), " ".join(d.output)] for d in self.case_only][:20],
            "allowed_hits": [[" ".join(d.source), " ".join(d.output)] for d in self.allowed_hits][:20],
            "unexplained": [[" ".join(d.source), " ".join(d.output)] for d in self.unexplained][:20],
            "missing_count": self.missing_count,
            "invented_count": self.invented_count,
            "case_only_count": self.case_only_count,
            "recovered_markers": list(self.recovered_markers),
        }


class StructureResult(object):
    __slots__ = ("ok", "reasons", "counts")

    def __init__(self, ok, reasons, counts):
        self.ok = ok
        self.reasons = reasons
        self.counts = counts

    def as_dict(self):
        return {"ok": self.ok, "reasons": self.reasons, "counts": self.counts}


def strip_markup(text):
    """Readable text of an HTML fragment, with tag boundaries becoming spaces.

    Replacing a tag with a space (never with nothing) is what stops ``a</p><p>b``
    from fusing into one token and reading as an invented word.
    """
    text = _TAG.sub(" ", text)
    return html_module.unescape(text)


#: An aside that identifies itself as note N but does not open with N. The
#: back-reference inside the lookahead is what makes "already numbered" mean this
#: note's own number rather than any number.
_ASIDE_UNNUMBERED = re.compile(r'(<aside[^>]*\bid="fn_(\d+)"[^>]*>\s*)(?!\2\b)', re.I)


def number_the_notes(html):
    """An aside prints the number it is identified by.

    The page prints "58" under the rule, and the deterministic reader hands the model
    ``[58] Cumont, ...`` -- so the number is one of the page's words. Where a provider
    writes it back is not: MEASURED on the acceptance book, the same model on one run
    wrote ``<aside id="fn_33">33 Heilen, ...`` for one page and
    ``<aside id="fn_58">Cumont, ...`` for another, and the second was refused for
    losing a word that was sitting in the markup all along. Worse, the reader's EPUB
    would have carried an unnumbered note.

    So the number goes back into the text of any aside that left it in the id. This
    can only ever restore a note's own number -- an aside that opens with a different
    number is left exactly as it is, and fails the gate for saying it.
    """
    return _ASIDE_UNNUMBERED.sub(lambda m: "%s%s " % (m.group(1), m.group(2)), html)


def drop_contract_line(text):
    """The prompt's required trailing JSON object is scaffolding, not page text."""
    return _CONTRACT_LINE.sub("", text)


def normalise(text, markup=False):
    """Tokens of a page, after the normalisations that are *not* content changes."""
    if markup:
        text = strip_markup(text)
    text = unicodedata.normalize("NFKC", text)
    text = _LINEBREAK_HYPHEN.sub(r"\1\2", text)
    for bad, good in _QUOTE_MAP.items():
        text = text.replace(bad, good)
    text = _MARKER_NOTATION.sub(r" \1 ", text)
    return text.split()


def _is_case_only(a, b):
    return bool(a) and bool(b) and [x.lower() for x in a] == [x.lower() for x in b]


#: What is left of a superscript note number after a scanner has read it. The
#: acceptance book returns a quotation mark for 424 of its markers and an
#: apostrophe for others; both arrive here already folded to their straight form
#: by ``_QUOTE_MAP``.
_MARKER_RESIDUE = "\"'"


def _marker_recovery(a, b, available):
    """A note marker the scanner turned into punctuation, read back off the scan.

    Returns the note number the model restored, or ``None``. The rule is at most
    two characters wide — the measured damage is ``"`` or ``\'"``, never more —
    and what replaces them must be the digits of exactly one note that this page
    prints and this page leaves unreferenced. Every other character on both sides
    still has to match, so the substitution can neither add a word nor lose one,
    and ``available`` is consumed: a page cannot hand the same missing note to two
    different quotation marks.
    """
    if not available or not a or not b:
        return None
    left, right = "".join(a), "".join(b)
    for index, char in enumerate(left):
        if char not in _MARKER_RESIDUE:
            continue
        for width in (1, 2):
            residue = left[index:index + width]
            if len(residue) != width or any(c not in _MARKER_RESIDUE for c in residue):
                continue
            for number in available:
                if left[:index] + str(number) + left[index + width:] == right:
                    return number
    return None


def _is_retokenisation(a, b):
    """Same characters, different word boundaries.

    This is the single allowed structural edit: detaching a footnote marker the
    OCR layer fused onto the preceding word, or separating a note's leading
    number from its text. It cannot hide a lost or invented word because the
    concatenations must match exactly, case included.
    """
    if not a or not b:
        return False
    return "".join(a) == "".join(b)


#: A block the model put somewhere else on the page. Nothing is lost, nothing is
#: gained, and the run stays whole: MEASURED on page 121 of the acceptance book, the
#: text layer returns the section head ``Serapio of Alexandria (First Century CE?)``
#: forty lines below where it is printed, and SPEC 8.2 asks the model to put it back.
#: Four tokens is the floor because three-word runs recur inside a page and a shorter
#: allowance starts forgiving rearrangement rather than relocation; two moves is the
#: ceiling because the measured damage is one misplaced block per page, and a page
#: that needs three has not been repaired, it has been rewritten.
MIN_MOVED_TOKENS = 4
MAX_MOVES = 2


def _is_punctuation(a, b):
    """Neither side holds a word.

    MEASURED on pages 114 and 115 of the acceptance book: the only difference
    between the model's answer and the page is one orphan quotation mark, the
    wreckage of a superscript this page has already resolved by another route. The
    rule is word preservation, and a lone quote is not a word -- but a quote that
    came back as a citation number is, and ``59`` is not punctuation.
    """
    return not any(char.isalnum() for char in "".join(a) + "".join(b))


class _Comparison(object):
    """One pass of the sequence comparison, before any relocation is considered."""

    __slots__ = ("missing", "invented", "case_only", "allowed", "unexplained",
                 "recovered", "similarity")

    def __init__(self, src, out, available):
        self.missing, self.invented, self.case_only = [], [], []
        self.allowed, self.unexplained, self.recovered = [], [], []
        matcher = difflib.SequenceMatcher(None, src, out, autojunk=False)
        self.similarity = matcher.ratio()
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            a, b = src[i1:i2], out[j1:j2]
            if _is_case_only(a, b):
                # Never an allowance: this is the failure mode a laxer gate misses.
                self.case_only.append(Difference("case", a, b))
                continue
            if _is_retokenisation(a, b):
                self.allowed.append(Difference("retokenised", a, b))
                continue
            number = _marker_recovery(a, b, available)
            if number is not None:
                available.remove(number)
                self.recovered.append(number)
                self.allowed.append(Difference("marker_recovered", a, b))
                continue
            if _is_punctuation(a, b):
                self.allowed.append(Difference("punctuation", a, b))
                continue
            if a:
                self.missing.append(Difference("missing", a, b))
            if b:
                self.invented.append(Difference("invented", a, b))
            self.unexplained.append(Difference(tag, a, b))

    @property
    def clean(self):
        return not self.unexplained and not self.case_only


def _run_at(tokens, run):
    """Where *run* sits in *tokens*, or ``None``. First occurrence; they are equal."""
    width = len(run)
    for start in range(len(tokens) - width + 1):
        if tokens[start:start + width] == run:
            return start
    return None


def _find_move(src, out, comparison):
    """A whole run of page text that is present on both sides in different places.

    The candidates are the blocks the comparison could not explain, taken from
    whichever side the aligner left them whole on -- a relocated heading usually
    survives intact as the deleted block, a relocated paragraph opening as the
    inserted one.
    """
    seen = []
    for difference in comparison.unexplained:
        for run in (difference.source, difference.output):
            if len(run) >= MIN_MOVED_TOKENS and run not in seen:
                seen.append(run)
    for run in seen:
        here, there = _run_at(src, run), _run_at(out, run)
        if here is not None and there is not None:
            return run, here, there
    return None


def check_word_preservation(source_text, model_html, recoverable_markers=()):
    """G1. Compare the model's page against the deterministic transcription.

    ``source_text`` is our own assembled page text (markers as ``[160]``), not
    the raw PDF layer: the model is given that same text, so a faithful model
    scores zero differences and every difference is a real decision it made.

    ``recoverable_markers`` are the notes this page prints and this page never
    refers to, because the scanner read their superscripts as punctuation. Those
    numbers, and only those, may come back out of the page image — see
    ``_marker_recovery``. Pass nothing and a quotation mark stays a quotation
    mark, which is the right answer on every page that has not lost a marker.
    """
    src = normalise(source_text or "")
    out = normalise(drop_contract_line(model_html or ""), markup=True)

    if len(src) < MIN_SOURCE_TOKENS:
        return GateResult("NOT_APPLICABLE", len(src), len(out), 0.0, [], [], [], [], [])


    available = [int(n) for n in recoverable_markers or ()]
    comparison = _Comparison(src, out, list(available))

    # A relocation is only ever read as one when reading it as one makes the whole
    # page come out clean. A page that still has a lost word after the move had a
    # lost word before it, and is reported the way it was first seen.
    moved, trimmed_src, trimmed_out = [], src, out
    while not comparison.clean and len(moved) < MAX_MOVES:
        move = _find_move(trimmed_src, trimmed_out, comparison)
        if move is None:
            break
        run, here, there = move
        trimmed_src = trimmed_src[:here] + trimmed_src[here + len(run):]
        trimmed_out = trimmed_out[:there] + trimmed_out[there + len(run):]
        moved.append(Difference("moved", run, run))
        comparison = _Comparison(trimmed_src, trimmed_out, list(available))
    if moved and not comparison.clean:
        moved = []
        comparison = _Comparison(src, out, list(available))

    verdict = "PASS" if comparison.clean else "FAIL"
    return GateResult(verdict, len(src), len(out), comparison.similarity,
                      comparison.missing, comparison.invented, comparison.case_only,
                      moved + comparison.allowed, comparison.unexplained,
                      recovered_markers=comparison.recovered if verdict == "PASS" else [])


_TAG_NAME = re.compile(r"<\s*(/?)\s*([A-Za-z][A-Za-z0-9]*)")
_NOTEREF = re.compile(r'<a[^>]*class="[^"]*noteref[^"]*"[^>]*href="#(fn_[^"]+)"', re.I)
_ASIDE_ID = re.compile(r'<aside[^>]*id="(fn_[^"]+)"', re.I)
_FIGURE = re.compile(r"<figure\b.*?</figure>", re.I | re.S)
_HEADING = re.compile(r"<h([1-6])\b", re.I)


def check_structure(model_html, ladder=(1, 2, 3, 4), require_figure_caption=True,
                    figures_expected=None):
    """G3. The markup contract the EPUB builder is allowed to trust.

    ``ladder`` is the set of heading levels the deterministic skeleton found on
    this page's book. A model that invents an ``<h4>`` where the book has three
    levels is guessing, and its guess would land in the reader's table of
    contents.

    ``figures_expected`` is how many figures the page prints. The word gate cannot
    see a figure — an illustration has no words — so an answer that drops one would
    pass every other check and take the picture out of the reader's book.
    """
    reasons = []
    html = model_html or ""

    tags = {name.lower() for _, name in _TAG_NAME.findall(html)}
    for name in sorted(tags - ALLOWED_TAGS):
        reasons.append("disallowed tag <%s>" % name)

    allowed_levels = {int(x) for x in ladder} or {1}
    for level in sorted({int(m) for m in _HEADING.findall(html)}):
        if level not in allowed_levels:
            reasons.append("heading level h%d is outside the skeleton ladder %s"
                           % (level, sorted(allowed_levels)))

    refs = set(_NOTEREF.findall(html))
    asides = set(_ASIDE_ID.findall(html))
    for orphan in sorted(refs - asides):
        reasons.append("noteref %s has no matching aside" % orphan.replace("fn_", ""))

    figures = _FIGURE.findall(html)
    if require_figure_caption:
        for fig in figures:
            if "<figcaption" not in fig.lower():
                reasons.append("figure without a caption")
    if figures_expected is not None and len(figures) < int(figures_expected):
        reasons.append("the page prints %d figures and the answer has %d"
                       % (int(figures_expected), len(figures)))

    counts = {
        "headings": len(_HEADING.findall(html)),
        "noterefs": len(refs),
        "asides": len(asides),
        "figures": len(figures),
    }
    return StructureResult(not reasons, reasons, counts)
