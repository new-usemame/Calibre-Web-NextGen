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
    recovered = []
    matcher = difflib.SequenceMatcher(None, src, out, autojunk=False)
    missing, invented, case_only, allowed_hits, unexplained = [], [], [], [], []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        a, b = src[i1:i2], out[j1:j2]
        if _is_case_only(a, b):
            # Never an allowance: this is the failure mode a laxer gate misses.
            case_only.append(Difference("case", a, b))
            continue
        if _is_retokenisation(a, b):
            allowed_hits.append(Difference("retokenised", a, b))
            continue
        number = _marker_recovery(a, b, available)
        if number is not None:
            available.remove(number)
            recovered.append(number)
            allowed_hits.append(Difference("marker_recovered", a, b))
            continue
        if a:
            missing.append(Difference("missing", a, b))
        if b:
            invented.append(Difference("invented", a, b))
        unexplained.append(Difference(tag, a, b))

    verdict = "PASS" if not unexplained and not case_only else "FAIL"
    return GateResult(verdict, len(src), len(out), matcher.ratio(),
                      missing, invented, case_only, allowed_hits, unexplained,
                      recovered_markers=recovered if verdict == "PASS" else [])


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
