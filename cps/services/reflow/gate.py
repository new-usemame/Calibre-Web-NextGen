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

The comparison is an alignment, not a bag of words, so the order of the page is
part of what is preserved: a model that moves a block has changed the page even
though every word survived. The allow-list is deliberately tiny and every hit is
reported, because an allow-list is where a safety gate goes to die. It permits
three classes of difference, all of which move characters around and none of
which can add a word or lose one:

* a *re-tokenisation*, where the same characters are split or joined differently
  (``caution.160`` -> ``caution. 160``);
* a *marker recovery*, where one punctuation character that the scanner left in
  place of a superscript note number becomes that number again — and only a
  number this page prints, leaves unreferenced, and has not already used;
* a *marker addition*, the same repair where nothing is given up for it: the
  wreckage stays exactly as the scanner left it and the number is added beside
  it, which is the only shape available when that wreckage holds a letter or a
  digit no rule here will delete;
* an *orphan punctuation* difference, where neither side holds a word at all.

The middle two are off unless the caller passes ``recoverable_markers``, so on a
page that has lost nothing a quotation mark is still a quotation mark.

**G3, structural schema.** Allowed tags only; heading levels drawn from the
deterministic ladder rather than the model's opinion (probes showed models
disagree about absolute heading level on identical pages); the headings
themselves drawn from the deterministic reader, which has the type ladder and the
page geometry to know one (see ``check_structure``); every noteref has an aside;
every figure carries a caption.
"""

import difflib
import html as html_module
import html.parser
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


def _is_letter_residue(residue):
    """Wreckage that includes a letter: ``ms`` for 105, ``si`` for 51, ``s°`` for 50.

    MEASURED across thirty pages of the acceptance book, the scanner renders a
    superscript as letters at least as often as it renders one as a quotation mark.
    Digits are excluded because a run of digits is a number the page already has,
    and turning one number into another is not a repair.
    """
    return (not any(c.isdigit() or c.isspace() for c in residue)
            and any(c.isalpha() for c in residue))


def _restores_eaten_punctuation(before, head):
    """Did the model put back punctuation the scanner ate along with the marker?

    MEASURED on page index 104: the page prints ``brief.`` and a superscript 68, and
    the text layer returns ``brief"`` -- one straight quote standing for the stop and
    the number together. A model that answers ``brief.`` and note 68 has said exactly
    what the page says, and has supplied a full stop that is not in the text it was
    given. A full stop is not a word, and on its own that difference is already
    allowed as punctuation; refusing it here only because it arrives in the same
    token as the number is an accident of where the tokeniser drew the boundary.

    Only additions count. Punctuation the scan *did* return still has to survive, so
    ``luminaries.'"'`` may not quietly lose a quotation mark to a note number -- the
    two-character rule above is what bounds the damage a repair may claim, and a
    model deleting printed punctuation is not repairing anything.
    """
    return (before == head
            or (before.startswith(head)
                and not any(c.isalnum() for c in before[len(head):])))


def _marker_recovery(a, b, available):
    """A note marker the scanner destroyed, read back off the scan.

    Returns the note number the model restored, or ``None``. The rule is at most
    two characters wide — the measured damage is never more — and what replaces
    them must be the digits of exactly one note that this page prints and this page
    leaves unreferenced. The letters and digits on both sides still have to match, so
    the substitution can neither add a word nor lose one, and ``available`` is
    consumed: a page cannot hand the same missing note to two different residues.

    Every other character has to match, except that the model may supply punctuation
    the scanner ate along with the marker -- its one quote often stands for a full
    stop and a number together (see ``_restores_eaten_punctuation``).

    Wreckage that contains a letter is held to three further conditions, because a
    letter can be a word and a quotation mark cannot. It must sit at the very end
    of a single token and leave something in front of it — which is where a
    superscript is printed, after the word it annotates — and nothing else in the
    token may be a candidate marker in its own right: a token holding a quotation
    mark has already explained its superscript, so the letters in front of that mark
    are the word. So ``reasons.ms`` may become ``reasons.105`` and ``its°`` may
    become ``it.80``, while the standalone word ``ms`` may not become ``105``, the
    chart label ``Tl la`` may not become ``T11a``, and ``brief"`` may not become
    ``brie.68``.
    """
    if not available or not a or not b:
        return None
    left, right = "".join(a), "".join(b)
    for index in range(len(left)):
        for width in (1, 2):
            residue = left[index:index + width]
            if len(residue) != width:
                continue
            if all(c in _MARKER_RESIDUE for c in residue):
                pass
            elif _is_letter_residue(residue):
                if len(a) != 1 or index == 0 or index + width != len(left):
                    continue
                if any(c in _MARKER_RESIDUE for c in left):
                    continue
            else:
                continue
            head, tail = left[:index], left[index + width:]
            for number in available:
                digits = str(number)
                if not right.endswith(digits + tail):
                    continue
                before = right[:len(right) - len(digits) - len(tail)]
                if _restores_eaten_punctuation(before, head):
                    return number
    return None


def _added_marker(b, available):
    """A note number put back where nothing on the page could be given up for it.

    Returns the number, or ``None``. This is the safe half of the marker repair:
    the model adds the noteref and the text layer keeps every character it had, so
    no word can go missing and the only thing that arrives is one number this page
    prints, this page never refers to, and no other residue has already claimed.

    It exists because the wreckage of a superscript is often not punctuation.
    MEASURED on pages 116, 118 and 124 of the acceptance book the scanner returned
    ``places.'23`` for 125, ``Anthology.''s`` for 135 and ``r's`` for 175 --
    digits and letters, which ``_marker_recovery`` will not let the model delete
    and should not. Without this the model's only way to restore those markers is
    a deletion the gate refuses, and the page is thrown away over the scan's
    nonsense rather than over anything the model did wrong.
    """
    if len(b) != 1 or not b[0].isdigit():
        return None
    number = int(b[0])
    return number if number in available else None


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
            if not a:
                number = _added_marker(b, available)
                if number is not None:
                    available.remove(number)
                    self.recovered.append(number)
                    self.allowed.append(Difference("marker_added", a, b))
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

    Order counts. A block that comes back somewhere else on the page aligns as a
    deletion here and an insertion there, and is refused: where a block belongs is
    something the reader measured off the page and the gate cannot check, so the
    answer keeps the order it was given (see
    ``TestTheOrderOfTheBlocksIsNotTheModelsToChange``).
    """
    src = normalise(source_text or "")
    out = normalise(drop_contract_line(model_html or ""), markup=True)

    if len(src) < MIN_SOURCE_TOKENS:
        return GateResult("NOT_APPLICABLE", len(src), len(out), 0.0, [], [], [], [], [])


    available = [int(n) for n in recoverable_markers or ()]
    comparison = _Comparison(src, out, list(available))

    verdict = "PASS" if comparison.clean else "FAIL"
    return GateResult(verdict, len(src), len(out), comparison.similarity,
                      comparison.missing, comparison.invented, comparison.case_only,
                      comparison.allowed, comparison.unexplained,
                      recovered_markers=comparison.recovered if verdict == "PASS" else [])


_NOTEREF = re.compile(r'<a[^>]*class="[^"]*noteref[^"]*"[^>]*href="#(fn_[^"]+)"', re.I)
_ASIDE_ID = re.compile(r'<aside[^>]*id="(fn_[^"]+)"', re.I)
_FIGURE = re.compile(r"<figure\b.*?</figure>", re.I | re.S)
_HEADING = re.compile(r"<h([1-6])\b", re.I)
_HEADING_BLOCK = re.compile(r"<h([1-6])\b[^>]*>(.*?)</h\1\s*>", re.I | re.S)


# ── the attribute and URL contract ─────────────────────────────────────────────
#
# "Which elements" is no longer a separate list: the schema's keys ARE the allowed
# tags, so a caller that checks only this parser -- the final packaging boundary
# does -- still refuses a disallowed element. The attributes and URLs of the
# allowed ones are checked against the shapes this pipeline itself writes, on a
# real parse of the fragment rather than another regex over its bytes: an <img>
# pulling https://example.invalid/pixel.gif with an onerror handler is a fragment
# whose every word is the page's own, so the word gate cannot see it, and the
# EPUB reader's webview is where the request would fire.
#
# The schema is per tag. A missing rule for an attribute is a refusal, not an
# allowance: the prompt contract (prompts/structure.txt) and the deterministic
# fragment writer (build_epub.page_fragment) between them produce every attribute
# listed here, so anything else in an answer is something the model invented.

#: An in-book reference only: "#fn_160", "#fnref_160_2", or the disarmed "#" the
#: link binder writes for a note that is not in the book. Never a scheme, never a
#: host, never a path out of the book.
_HREF_FRAGMENT = re.compile(r"#[A-Za-z0-9._-]*\Z")
#: The only image source there is: a figure this pipeline cropped out of the PDF.
_FIGURE_SRC = re.compile(r"images/fig_p\d+_\d+\.jpg\Z")
_NOTE_ID = re.compile(r"fn_\d+(_\d+)?\Z")
_NOTEREF_ID = re.compile(r"fnref_\d+(_\d+)?\Z")
_SMALL_SPAN = re.compile(r"[1-9]\d{0,2}\Z")

#: None as a rule means "any text" -- the words of an alt or a title are content,
#: judged by the word gate like everything else the model wrote.
MARKUP_SCHEMA = {
    "h1": {}, "h2": {}, "h3": {}, "h4": {}, "h5": {}, "h6": {},
    "p": {"class": {"caption"}},
    "blockquote": {},
    "aside": {"class": {"footnote"}, "id": _NOTE_ID, "epub:type": {"footnote"}},
    "a": {"class": {"noteref"}, "href": _HREF_FRAGMENT, "id": _NOTEREF_ID,
          "epub:type": {"noteref"}},
    "figure": {},
    "figcaption": {"class": {"reflow-no-caption"}},
    "img": {"src": _FIGURE_SRC, "alt": None},
    "table": {}, "thead": {}, "tbody": {}, "tr": {}, "caption": {},
    "td": {"colspan": _SMALL_SPAN, "rowspan": _SMALL_SPAN},
    "th": {"colspan": _SMALL_SPAN, "rowspan": _SMALL_SPAN},
    "ul": {}, "ol": {}, "li": {},
    "em": {}, "i": {}, "strong": {}, "b": {}, "sub": {},
    "sup": {"class": {"noteref-unresolved"}},
    "br": {},
    "span": {"class": {"reflow-uncertain"}, "title": None},
    "section": {}, "div": {},
}


#: The tags the contract allows are exactly the tags the schema has rules for:
#: one list, owned here.
ALLOWED_TAGS = frozenset(MARKUP_SCHEMA)


class _MarkupContract(html.parser.HTMLParser):
    """Collects every way a fragment's markup leaves the contract."""

    def __init__(self):
        html.parser.HTMLParser.__init__(self, convert_charrefs=True)
        self.reasons = []
        self._unknown_tags = set()

    def handle_starttag(self, tag, attrs):
        if self._disallowed(tag):
            return
        self._check(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        if self._disallowed(tag):
            return
        self._check(tag, attrs)

    def handle_endtag(self, tag):
        # A stray closing tag of a disallowed element is the same refusal: left
        # alone it is invalid XHTML in the finished book.
        self._disallowed(tag)

    def _disallowed(self, tag):
        """Unknown elements are refused here, once each.

        This used to be left to ``check_structure``'s tag pass -- which meant the
        final packaging boundary, calling this parser directly, let a ``<script>``
        straight through. The last boundary must hold the whole contract.
        """
        if tag in MARKUP_SCHEMA:
            return False
        if tag not in self._unknown_tags:
            self._unknown_tags.add(tag)
            self.reasons.append("disallowed tag <%s>" % tag)
        return True

    def _check(self, tag, attrs):
        schema = MARKUP_SCHEMA[tag]
        seen = set()
        for raw_name, value in attrs:
            name = (raw_name or "").lower()
            if name.startswith("on"):
                self.reasons.append("event attribute %r on <%s>" % (name, tag))
                continue
            if name in seen:
                self.reasons.append("<%s> repeats the attribute %r" % (tag, name))
                continue
            seen.add(name)
            if name not in schema:
                # This covers the dangerous classes by construction: style, srcset,
                # formaction, xmlns and namespace attributes, data-*, tabindex --
                # none of them are anything this pipeline writes.
                self.reasons.append("<%s> attribute %r is not in the markup contract"
                                    % (tag, name))
                continue
            rule = schema[name]
            if rule is None:
                continue
            text = (value or "").strip()
            if isinstance(rule, frozenset) or isinstance(rule, set):
                if text not in rule:
                    self.reasons.append("<%s> %s %r is not one of %s"
                                        % (tag, name, text, sorted(rule)))
            elif not rule.match(text):
                if name in ("href", "src"):
                    self.reasons.append(
                        "<%s> %s %r is not an in-book reference: remote, data and "
                        "script URLs are not allowed" % (tag, name, text[:80]))
                else:
                    self.reasons.append("<%s> %s %r does not fit the markup contract"
                                        % (tag, name, text[:80]))

    def handle_comment(self, _data):
        self.reasons.append("an HTML comment is not in the markup contract")

    def handle_decl(self, _decl):
        self.reasons.append("a DOCTYPE declaration is not in the markup contract")

    def unknown_decl(self, _data):
        self.reasons.append("an unknown declaration is not in the markup contract")

    def handle_pi(self, _data):
        self.reasons.append("a processing instruction is not in the markup contract")


def check_markup_safety(model_html):
    """The attribute and URL half of G3: the reasons a fragment is unsafe.

    Empty when the fragment's markup is exactly the contract. A refusal here is a
    refusal of the whole page further up -- the page ships its deterministic text
    and the reason is recorded -- rather than a stripped version of the answer
    being adopted and reported as the model's own.
    """
    parser = _MarkupContract()
    parser.feed(model_html or "")
    parser.close()
    return parser.reasons


def _heading_key(level, text):
    """A heading as the gate compares it: its level and the words it prints.

    Both sides go through ``normalise``, so the reader's ``[47]`` and the model's
    noteref anchor are the same word, and case still counts -- a head the model
    title-cased is a head it rewrote.
    """
    return int(level or 1), tuple(normalise(text or "", markup=True))


def _excerpt(words, limit=8):
    return " ".join(words[:limit]) + (" ..." if len(words) > limit else "")


def _heading_disagreements(html, headings):
    """Where the answer's headings differ from the ones the page prints.

    What is a heading is a fact about the page, and the deterministic reader is
    what measures it: it has the book's type ladder, the geometry of a run-in head
    and ``skeleton.acceptable_heading``. The model is told the answer in its prompt
    (``prompts.user_prompt``) and judged here on having used it, because a model
    left to decide takes a sentence it finds important and makes it a chapter --
    MEASURED on page index 102 of the acceptance book, three items of a numbered
    list came back as ``<h2>`` and ``build_epub.SPLIT_LEVELS`` would have made
    three chapters of them.
    """
    reasons = []
    remaining = [_heading_key(level, text) for level, text in headings]
    for level, body in _HEADING_BLOCK.findall(html):
        key = _heading_key(level, body)
        if key in remaining:
            remaining.remove(key)
            continue
        same_words = next((h for h in remaining if h[1] == key[1]), None)
        if same_words is not None:
            remaining.remove(same_words)
            reasons.append("the answer sets %r as h%d and the page sets it as h%d"
                           % (_excerpt(key[1]), key[0], same_words[0]))
            continue
        reasons.append("the answer makes a heading of %r, which the page sets as "
                       "body text" % _excerpt(key[1]))
    for level, words in remaining:
        reasons.append("the page's h%d heading %r is body text in the answer"
                       % (level, _excerpt(words)))
    return reasons


def check_structure(model_html, ladder=(1, 2, 3, 4), require_figure_caption=True,
                    figures_expected=None, headings=None):
    """G3. The markup contract the EPUB builder is allowed to trust.

    ``ladder`` is the set of heading levels the deterministic skeleton found on
    this page's book. A model that invents an ``<h4>`` where the book has three
    levels is guessing, and its guess would land in the reader's table of
    contents.

    ``figures_expected`` is how many figures the page prints. The word gate cannot
    see a figure — an illustration has no words — so an answer that drops one would
    pass every other check and take the picture out of the reader's book.

    ``headings`` is ``(level, text)`` for every heading the deterministic reader
    found on this page, and the answer must mark those and only those — see
    ``_heading_disagreements``. ``None`` means the caller is not declaring them and
    only the ladder is checked; ``[]`` is the declaration that this page prints no
    heading at all, which is the case that catches an invented one.
    """
    reasons = []
    html = model_html or ""

    # Tags, attributes and URLs come from one parse of the fragment: the builder
    # runs the same check at packaging, so a tag that is refused here cannot slip
    # into the book through a direct or cached fragment either.
    reasons.extend(check_markup_safety(html))

    allowed_levels = {int(x) for x in ladder} or {1}
    for level in sorted({int(m) for m in _HEADING.findall(html)}):
        if level not in allowed_levels:
            reasons.append("heading level h%d is outside the skeleton ladder %s"
                           % (level, sorted(allowed_levels)))

    refs = set(_NOTEREF.findall(html))
    asides = set(_ASIDE_ID.findall(html))
    for orphan in sorted(refs - asides):
        reasons.append("noteref %s has no matching aside" % orphan.replace("fn_", ""))

    if headings is not None:
        reasons.extend(_heading_disagreements(html, headings))

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
