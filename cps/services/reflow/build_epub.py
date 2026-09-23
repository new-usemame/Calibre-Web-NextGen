# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Stage 5: the file a reader opens.

The unit of work everywhere upstream is one page, because that is the unit a model
can be shown and a gate can check. The unit a reader wants is a book, and the two
differ in three places, which is most of what this module is.

*A page is markup, not runs.* ``page_fragment`` renders one page the way the page was
printed, and it is what the model is asked to improve and what its answer replaces.
So the join across a page turn has to be made again here, on the markup, over
whatever came back — deterministic text or a model's edit of it.

*A footnote has to stay with its marker.* EPUB 3 shows a note as a popup only when
the ``noteref`` resolves; a note number that repeats on another page would collide,
so ids are scoped to their page as the book is assembled, and any link that ends up
in a different document from its target is rewritten to name that document.

*Nothing may be lost in the split.* Chapters exist so a 700-page book is not one
XHTML file. Splitting is also the easiest place to drop a block, so the split moves
blocks and never rewrites them.
"""

import json
import math
import logging
import os
import posixpath
import re
import shutil
import subprocess
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.entities import html5 as HTML5_ENTITIES
from typing import List, Optional
from urllib.parse import unquote
from xml.etree import ElementTree
from xml.sax.saxutils import escape, quoteattr

from . import assemble, extract, gate

log = logging.getLogger(__name__)

CONVERTER = "Reflow"
CONVERTER_VERSION = "1.0"
REFLOW_NS = "https://calibre-web-nextgen.org/ns/reflow#"
SIDECAR_PATH = "META-INF/reflow.json"
OEBPS = "OEBPS"
ABOUT_HREF = "reflow-about.xhtml"
SOURCE_INDEX_HREF = "source-pages.xhtml"

#: Chapters split on the ladder's top two levels, per SPEC §3.
SPLIT_LEVELS = (1, 2)
#: A book with no headings at all still has to be split, or a reader repaginates the
#: whole of it on every page turn.
MAX_BLOCKS_PER_DOC = 300

_VOID = frozenset({"img", "br", "hr", "meta", "link", "source", "col", "area"})
_TOKEN = re.compile(r"<\s*(/?)\s*([A-Za-z][A-Za-z0-9]*)([^>]*?)(/?)\s*>", re.S)
_SPLIT_HEADING = re.compile(r"^<h([%s])\b" % "".join(str(x) for x in SPLIT_LEVELS), re.I)
_PARAGRAPH = re.compile(r"^<p[\s>]", re.I)
_ASIDE = re.compile(r"^<aside[\s>]", re.I)
_TAG = re.compile(r"<[^>]+>")
_HEADING_TEXT = re.compile(r"<h[1-6][^>]*>(.*?)</h[1-6]>", re.I | re.S)
_TRAILING_HYPHEN = re.compile(
    r"(\w)[" + assemble.HYPHENS + r"](\s*(?:</[A-Za-z0-9]+>\s*)*)$")
_ID = re.compile(r'\sid="([^"]+)"')
_HREF = re.compile(r'href="#([^"]+)"')
_IMG_SRC = re.compile(r'<img\b[^>]*\bsrc="([^"]+)"[^>]*/?>', re.I)
_IMG_TAG = re.compile(r"<img\b[^>]*/?>", re.I)

STYLESHEET = """\
body { margin: 0 5%; line-height: 1.45; text-align: justify; }
h1, h2, h3, h4, h5, h6 { text-align: left; page-break-after: avoid; }
p { margin: 0; text-indent: 1.2em; }
p.first, h1 + p, h2 + p, h3 + p, blockquote + p { text-indent: 0; }
blockquote { margin: 1em 2em; font-size: 0.95em; }
aside.footnote { font-size: 0.85em; margin: 0.4em 0; }
a.noteref { text-decoration: none; }
sup.noteref-unresolved { color: inherit; }
/* A word the scan damaged. The page's own reading is what is printed here; the
   likelier one is in the title, and every one of them is listed on the about
   page, because an e-reader has no hover. */
span.reflow-uncertain { border-bottom: 1px dotted currentColor; }
figure { margin: 1em 0; text-align: center; page-break-inside: avoid; }
figcaption { font-size: 0.85em; text-align: center; }
img { max-width: 100%; }
.source-pages { list-style: none; padding: 0; text-align: left; }
.source-pages li { display: inline-block; width: 9em; }
.source-pages a { display: block; padding: 0.35em 0.25em; }
.source-evidence img { width: 100%; height: auto; }
"""


@dataclass
class Chapter(object):
    index: int
    title: str
    blocks: List[str] = field(default_factory=list)
    pages: List[int] = field(default_factory=list)
    continued: bool = False
    """Cut out of the document before it, because that one grew too long."""

    @property
    def href(self):
        return "ch%03d.xhtml" % self.index

    @property
    def item_id(self):
        return "ch%03d" % self.index


@dataclass
class BuildResult(object):
    path: str = ""
    chapters: List[dict] = field(default_factory=list)
    notes: int = 0
    figures: int = 0
    images: int = 0
    page_joins: int = 0
    warnings: List[str] = field(default_factory=list)
    sidecar: dict = field(default_factory=dict)
    epubcheck: Optional[dict] = None


# ------------------------------------------------------------------ one page

def page_fragment(book, pno, style=None, wrappers=None, element_blocks=None):
    """One page as it was printed: the unit the model edits and the gate measures."""
    elements = list(book.pages.get(pno) or [])
    notes = [n for n in book.notes if n.pno == pno]
    ambiguous = book.ambiguous_note_numbers(pno)
    available = {str(n.num) for n in notes if n.num is not None} - ambiguous
    ref_ids = {}
    blocks = []
    figure_index = 0

    index = 0
    while index < len(elements):
        element_index = index
        element = elements[index]
        index += 1
        if element.kind == "fig":
            caption = ""
            if index < len(elements) and elements[index].kind == "caption":
                caption = _runs_html(elements[index].runs, available, ref_ids, ambiguous)
                if elements[index].caption_uncertain:
                    caption = ('<span class="reflow-uncertain" title="Caption '
                               'transcription uncertain; compare the original printed '
                               'caption.">%s (?)</span>' % caption)
                index += 1
            blocks.append(_figure_html(pno, figure_index, caption))
            figure_index += 1
            continue
        if wrappers and element_index in wrappers:
            from .structural_ops import render_element
            blocks.append(render_element(element, wrappers[element_index],
                lambda runs: _runs_html(runs, available, ref_ids, ambiguous)))
            if element.punctuation_uncertain:
                blocks.append(_punctuation_notice(pno, element_index))
            continue
        inner = _runs_html(element.runs, available, ref_ids, ambiguous)
        if not inner.strip():
            continue
        if element_blocks is not None:
            element_blocks[element_index] = len(blocks)
        if element.kind == "h":
            level = min(6, max(1, int(element.level or 1)))
            if getattr(element,'display_lines',[]):
                from .assemble import plain_text
                parts=element.display_lines
                visible=' '.join(plain_text(p['runs']).strip() for p in parts)
                if visible!=element.text:
                    raise ValueError('Source display-line text differs from heading inventory')
                styled=[]
                for part in parts:
                    size=part['scale']
                    if not isinstance(size,(int,float)) or not math.isfinite(size) or not 0<size<=4:
                        raise ValueError('Invalid source display scale')
                    weight='' if part['bold'] is None else ';font-weight:%s' % ('700' if part['bold'] else '400')
                    styled.append('<span class="source-title-line" style="display:block;font-size:%.3fem%s">%s</span>' % (
                        size,weight,_runs_html(part['runs'],available,ref_ids,ambiguous)))
                inner=' '.join(styled)
            attrs=''
            if getattr(element,'display_group',{}):
                alignment=element.display_group.get('alignment')
                if alignment not in ('left','center'):raise ValueError('Invalid source title alignment')
                attrs=' style="text-align:%s;font-weight:normal"' % alignment
            blocks.append("<h%d%s>%s</h%d>" % (level, attrs, inner, level))
            if getattr(element,'display_group',{}):
                copy=('Transcribed title; original typography may differ. ' if element.display_group.get('typography')=='fitted_geometry' else '')
                blocks.append('<p class="source-evidence-notice">%s<a href="original-p%04d.xhtml#title_%d">View original title and layout</a>.</p>' % (copy,pno,element_index))
        elif element.kind == "caption":
            if element.caption_uncertain:
                inner = '<span class="reflow-uncertain">%s (?)</span>' % inner
            blocks.append('<p class="caption">%s</p>' % inner)
        else:
            blocks.append("<p>%s</p>" % inner)
        if element.punctuation_uncertain:
            blocks.append(_punctuation_notice(pno, element_index))

    for note in notes:
        blocks.append(_aside_html(note, ref_ids, available, str(note.num) in ambiguous))
    return "\n".join(blocks)


def _punctuation_notice(pno, index):
    return ('<p class="source-evidence-notice reflow-uncertain">Original punctuation '
            'may differ from this transcription. <a href="original-p%04d.xhtml#text_%d">'
            'View original passage</a>.</p>' % (pno, index))


def _runs_html(runs, available, ref_ids, ambiguous=()):
    parts = []
    for run in runs:
        if run[0] == "t":
            text = escape(run[1])
            parts.append("<em>%s</em>" % text
                         if len(run) > 2 and run[2] == "italic" else text)
            continue
        number = str(run[1])
        # A marker established by a repair over a scan is an uncertain reading:
        # the number the layer gave, kept and marked, never an authoritative one.
        uncertain = number in ambiguous or (len(run) > 3 and run[3] == "uncertain")
        if number in available:
            ref = "fnref_%s" % number
            if number in ref_ids:
                ref = "%s_%d" % (ref, len(ref_ids) + 1)
            ref_ids.setdefault(number, ref)
            if uncertain:
                parts.append('<a class="noteref" epub:type="noteref" id="%s" '
                             'href="#fn_%s"><sup class="reflow-uncertain" '
                             'title="number read from a damaged text layer">%s'
                             '</sup></a>' % (ref, number, escape(number)))
                continue
            parts.append('<a class="noteref" epub:type="noteref" id="%s" '
                         'href="#fn_%s"><sup>%s</sup></a>'
                         % (ref, number, escape(number)))
        else:
            # The note is set on another page, or was never found. A link here is a
            # footnote button that opens nothing, so the marker stays a marker.
            if uncertain:
                parts.append('<sup class="noteref-unresolved"><span class="reflow-uncertain" '
                             'title="number or association read from a damaged text layer">%s (?)'
                             '</span></sup>' % escape(number))
                continue
            parts.append('<sup class="noteref-unresolved">%s</sup>' % escape(number))
    return "".join(parts)


def _aside_html(note, ref_ids, available, ambiguous=False):
    body = escape(note.text)
    if note.num is None:
        return '<aside class="footnote" epub:type="footnote"><p>%s</p></aside>' % body
    number = str(note.num)
    label = escape(number)
    if ambiguous or getattr(note, "uncertain", False):
        label = ('<span class="reflow-uncertain" title="number read from a '
                 'damaged text layer">%s (?)</span>' % label)
    if number in ref_ids:
        label = '<a href="#%s">%s</a>' % (ref_ids[number], label)
    return ('<aside class="footnote" epub:type="footnote" id="fn_%s">'
            '<p>%s %s</p></aside>' % (number, label, body))


def _figure_html(pno, index, caption):
    src = "images/fig_p%04d_%d.jpg" % (pno, index)
    # SPEC §3: a figure always carries a figcaption, empty when the page printed no
    # caption, so "no caption found" is stated rather than left to be inferred.
    alt = "" if caption else "Original figure from PDF page %d" % (pno + 1)
    return ('<figure><img src="%s" alt="%s"/><figcaption%s>%s</figcaption></figure>'
            % (src, alt, "" if caption else ' class="reflow-no-caption"', caption))


# ------------------------------------------------------------- markup primitives

def split_blocks(html):
    """The top-level elements of a fragment, in order, as raw strings.

    Text that is not inside an element is kept as its own block rather than dropped:
    a model that answers with a bare sentence between two paragraphs has made a
    mistake the gate should judge, not one this splitter should hide.
    """
    blocks = []
    depth = 0
    start = None
    cursor = 0

    def flush_text(upto):
        text = html[cursor:upto]
        if text.strip():
            blocks.append(text.strip())

    for match in _TOKEN.finditer(html):
        closing, name, _attrs, selfclose = match.groups()
        name = name.lower()
        void = name in _VOID or bool(selfclose)
        if depth == 0 and start is None and void:
            flush_text(match.start())
            cursor = match.end()
            blocks.append(match.group(0))
            continue
        if void:
            continue
        if not closing:
            if depth == 0:
                flush_text(match.start())
                cursor = match.start()
                start = match.start()
            depth += 1
            continue
        depth -= 1
        if depth <= 0:
            if start is not None:
                blocks.append(html[start:match.end()].strip())
            start = None
            depth = 0
            cursor = match.end()
    flush_text(len(html))
    return [b for b in blocks if b]


def block_text(block):
    return re.sub(r"\s+", " ", _TAG.sub(" ", block or "")).strip()


def _is_paragraph(block):
    return bool(_PARAGRAPH.match(block.strip()))


def _is_aside(block):
    return bool(_ASIDE.match(block.strip()))


def _inner(block):
    opening = block.find(">")
    closing = block.rfind("</")
    if opening < 0 or closing < opening:
        return block
    return block[opening + 1:closing]


def _open_tag(block):
    return block[:block.find(">") + 1]


def merge_paragraphs(left, right):
    """Join two paragraphs the way the typesetter's page turn joined them."""
    head = _inner(right).lstrip()
    tail = _inner(left).rstrip()
    plain = block_text(tail)
    if _TRAILING_HYPHEN.search(tail) and block_text(head)[:1].islower():
        tail = _TRAILING_HYPHEN.sub(r"\1\2", tail)
        glue = ""
    elif _TRAILING_HYPHEN.search(tail) or assemble.ENDDASH.search(plain):
        glue = ""
    else:
        glue = " "
    return "%s%s%s%s</p>" % (_open_tag(left), tail, glue, head)


# --------------------------------------------------------------- the whole book

_NOTEREF_ANCHOR = re.compile(
    r'<a\b(?P<attrs>[^>]*class="[^"]*noteref[^"]*"[^>]*)>(?P<inner>.*?)</a>', re.I | re.S)
_NOTE_ASIDE = re.compile(
    r'<aside\b(?P<attrs>[^>]*class="[^"]*footnote[^"]*"[^>]*)>(?P<inner>.*?)</aside>',
    re.I | re.S)
_NOTE_TARGET = re.compile(r'href="#fn_([^"]+)"')
_HAS_EPUB_TYPE = re.compile(r'\sepub:type="')
_BARE_NUMBER = re.compile(r"^\s*\d+\s*$")
#: The number a note prints under the rule, through whatever the page wraps it in.
#: ``a`` is deliberately not on that list: a number already inside a link is a note
#: that already goes back somewhere, and a link inside a link resolves to neither.
_NOTE_OPENS_WITH_ITS_NUMBER = re.compile(
    r"^(\s*(?:<(?:p|sup|em|strong|b|i)\b[^>]*>\s*)*)(\d+)", re.I)


def _reader_ready_notes(html):
    """Every note in the book the same shape, whoever wrote the page.

    ``prompts/structure.txt`` asks the model for the short form -- ``<a
    class="noteref" href="#fn_N">N</a>`` and ``<aside class="footnote" id="fn_N">N
    ...`` -- and asks small on purpose: every attribute in the ask is another thing
    an answer can get wrong and lose the whole page for. The reader needs more than
    that. ``epub:type`` is what makes a device open a note in a popup instead of
    laying it out as running text at the end of the page, and the number printed at
    the head of the note is how a reader who followed the link gets back to the
    sentence. Without this the pages Reflow *succeeded* on are the ones whose notes
    come out worse than the pages it refused.

    So the short form is finished into the long one here: deterministically, after
    the gate, and on the markup only. Nothing this does changes a word -- the number
    at the head of a note is wrapped where the note already prints it and is never
    written in where the page does not have one, because a number this function
    invented would be a word the source does not have.

    One page can print one number on two notes. Honestly, when a chapter's notes
    restart under the tail of the chapter before; or because the scan misread the
    number, which on book 567 happens on thirteen pages. Either way the short form
    gives both notes the same id, and the EPUB is then invalid (epubcheck RSC-005)
    and, worse for a reader, both notes answer to one anchor and only the first can
    be opened at all. So the notes are counted before anything is rewritten and each
    repeat after the first takes a suffix; the markers for that number are then
    paired with them in the order the page prints both. A marker is only ever pointed
    at a note that is really there -- one marker and two notes leaves the second note
    unmarked, which is a thing the report already counts and says, rather than a link
    into nothing.

    Idempotent by construction: an attribute already present is left alone, a note
    that already links back is not linked again, and a note already carrying its
    suffix counts as its own first occurrence -- so the pages the gate refused, which
    arrive in the long form already, come back unchanged.
    """
    minted = {}
    for found in _NOTE_ASIDE.finditer(html):
        ident = _ID.search(found.group("attrs"))
        if not (ident and ident.group(1).startswith("fn_")):
            continue
        made = minted.setdefault(ident.group(1)[3:], [])
        made.append(ident.group(1) if not made
                    else "fn_%s_%d" % (ident.group(1)[3:], len(made) + 1))

    back = {}
    cited = {}
    placed = {}

    def marker(match):
        attrs, inner = match.group("attrs"), match.group("inner")
        target = _NOTE_TARGET.search(attrs)
        if not target:
            return match.group(0)
        number = target.group(1)
        nth = cited[number] = cited.get(number, 0) + 1
        notes = minted.get(number) or []
        if nth <= len(notes) and notes[nth - 1] != "fn_%s" % number:
            attrs = attrs.replace('href="#fn_%s"' % number,
                                  'href="#%s"' % notes[nth - 1])
        opens = notes[nth - 1] if nth <= len(notes) else "fn_%s" % number
        if not _HAS_EPUB_TYPE.search(attrs):
            attrs = ' epub:type="noteref"' + attrs
        found = _ID.search(attrs)
        if found:
            ident = found.group(1)
        else:
            ident = "fnref_%s" % number
            if nth > 1:
                ident = "%s_%d" % (ident, nth)
            attrs = ' id="%s"%s' % (ident, attrs)
        back.setdefault(opens, ident)
        if _BARE_NUMBER.match(inner):
            inner = "<sup>%s</sup>" % inner.strip()
        return "<a%s>%s</a>" % (attrs, inner)

    def note(match):
        attrs, inner = match.group("attrs"), match.group("inner")
        if not _HAS_EPUB_TYPE.search(attrs):
            attrs = ' epub:type="footnote"' + attrs
        found = _ID.search(attrs)
        number = found.group(1)[3:] if found and found.group(1).startswith("fn_") else None
        ident = None
        if number is not None:
            nth = placed[number] = placed.get(number, 0) + 1
            ident = minted[number][nth - 1]
            if ident != found.group(1):
                attrs = attrs.replace(' id="%s"' % found.group(1),
                                      ' id="%s"' % ident, 1)
        target = back.get(ident)
        if target:
            def link(head):
                if head.group(2) != number:
                    return head.group(0)
                return '%s<a href="#%s">%s</a>' % (head.group(1), target, head.group(2))
            inner = _NOTE_OPENS_WITH_ITS_NUMBER.sub(link, inner, count=1)
        return "<aside%s>%s</aside>" % (attrs, inner)

    # The markers first: a note can only be given a way back to a marker that has an
    # id, and the marker gets its id in this pass.
    return _NOTE_ASIDE.sub(note, _NOTEREF_ANCHOR.sub(marker, html))


#: An ``&`` that does not open an entity reference, and a ``<`` that does not open a
#: tag. Both are ordinary characters in a book -- "9 & 29", "p < 0.05" -- and both are
#: fatal in XML: the reader loses the whole document, not the character.
_LOOSE_AMPERSAND = re.compile(r"&(?!#?\w+;)")
_LOOSE_ANGLE = re.compile(r"<(?![a-zA-Z/!?])")
#: ``&name;``. XML defines five of these and HTML defines two thousand, and a model
#: asked for HTML writes the HTML ones.
_NAMED_ENTITY = re.compile(r"&([a-zA-Z][a-zA-Z0-9]{0,31});")
_XML_ENTITIES = frozenset(("amp", "lt", "gt", "quot", "apos"))


def _named_entities(html):
    """``&mdash;`` as the em dash it names, ``&fnord;`` as the seven characters it is.

    A book is XML, and XML declares five entities. Everything else a model types --
    ``&nbsp;``, ``&mdash;``, ``&sect;``, the whole HTML list -- is undefined there,
    and an undefined entity is not a stray character in the text: the parser stops
    and the reader loses the document. Resolving the name to its character keeps the
    mark the model meant; escaping a name that is not an entity at all keeps the
    literal text it typed. Either way nothing the model wrote is dropped.

    A reference names a *character*, never markup. HTML5 also defines upper-case
    and exotic names for the XML-significant characters -- ``&QUOT;``, ``&LT;``,
    ``&GT;``, ``&AMP;``, and ``&nvlt;`` for ``<`` plus a combining mark -- and
    writing one of those out as a bare ``"`` or ``<`` turns text inside an
    attribute into the end of that attribute and the start of new ones (N1 of the
    7daffa5 security retest: ``title="x&QUOT; onclick=&QUOT;..."``). So a resolved
    character that XML gives a meaning is written as the XML reference for it:
    the reader sees the same character, and the markup is the markup that was
    checked.
    """
    def one(found):
        name = found.group(1)
        if name in _XML_ENTITIES:
            return found.group(0)
        character = HTML5_ENTITIES.get(name + ";")
        if character is None:
            return "&amp;%s;" % name
        return escape(character, {'"': "&quot;", "'": "&apos;"})
    return _NAMED_ENTITY.sub(one, html)


def _well_formed_text(html):
    """The markup a model wrote, made into XML without changing a word.

    The deterministic reader escapes what it reads. A model's answer is markup the
    model wrote, and it reaches the builder exactly as sent: OBSERVED on the
    acceptance book, one note came back carrying a bare ``&`` and epubcheck called
    the document FATAL RSC-016 -- so a reader loses a chapter over an ampersand,
    on one of the pages the gate *accepted*.

    Escaping is not a word change: ``&amp;`` renders as ``&``, which is what the page
    printed and what the word-preservation check already compared. An ``&`` that
    already opens an entity, and a ``<`` that already opens a tag, are left alone, so
    this is safe to run over markup that is well-formed already.

    Named entities are resolved first, so that by the time the loose-ampersand pass
    runs, every remaining ``&x;`` is one XML understands.
    """
    return _LOOSE_ANGLE.sub("&lt;",
                            _LOOSE_AMPERSAND.sub("&amp;", _named_entities(html)))


def _scope_ids(html, pno):
    """Note numbers repeat from page to page; ids in one book may not."""
    prefix = "p%04d_" % pno
    html = re.sub(r'(\sid=")(fn_|fnref_)', r"\1\2%s" % prefix, html)
    html = re.sub(r'(href="#)(fn_|fnref_)', r"\1\2%s" % prefix, html)
    return html


def page_anchor(pno):
    """The EPUB 3 page-break marker for one PDF page.

    It earns its place twice: a reader can show "page 97 of 698" against the print,
    and the about page has somewhere to send a reader who wants to look at an
    uncertain reading in context. The label is the PDF's own page number, which is
    not always the folio printed on the paper.
    """
    return ('<span epub:type="pagebreak" role="doc-pagebreak" id="pg_%04d" '
            'class="reflow-page" aria-label="%d"></span>' % (pno, pno + 1))


def _page_blocks(page_html):
    """Split each page into blocks. ``page_html`` is already well-formed text:
    ``build`` normalizes it once, before the markup boundary, so the bytes the
    boundary checks are the bytes that are written (see ``_well_formed_text``)."""
    pages = []
    for pno in sorted(page_html):
        blocks = split_blocks(
            _scope_ids(_reader_ready_notes(page_html[pno] or ""), pno))
        pages.append({"pno": pno, "anchor": page_anchor(pno),
                      "body": [b for b in blocks if not _is_aside(b)],
                      "asides": [b for b in blocks if _is_aside(b)]})
    return pages


def _join_page_turns(pages):
    """DIAGNOSIS B on the markup: the sentence, not the page, is the unit."""
    joined = 0
    for index in range(1, len(pages)):
        previous, current = pages[index - 1], pages[index]
        if not previous["body"] or not current["body"]:
            continue
        tail, head = previous["body"][-1], current["body"][0]
        if not (_is_paragraph(tail) and _is_paragraph(head)):
            continue
        if not assemble.continues(block_text(tail), block_text(head)):
            continue
        previous["body"][-1] = merge_paragraphs(tail, current["body"].pop(0))
        joined += 1
    return joined


def _chapters(pages):
    chapters = []
    current = None

    def start(title, continued=False):
        chapter = Chapter(index=len(chapters) + 1, title=title, continued=continued)
        chapters.append(chapter)
        return chapter

    for page in pages:
        # The page marker waits for the block it belongs to, so a page that opens a
        # chapter puts its marker in the new document and not the previous one.
        pending = page["anchor"]
        for block in page["body"] + page["asides"]:
            heading = _SPLIT_HEADING.match(block)
            if heading:
                current = start(block_text(block))
            elif current is None:
                current = start("")
            elif len(current.blocks) >= MAX_BLOCKS_PER_DOC:
                current = start("", continued=True)
            if pending:
                current.blocks.append(pending)
                pending = None
            current.blocks.append(block)
            if page["pno"] not in current.pages:
                current.pages.append(page["pno"])
        if pending and current is not None:
            current.blocks.append(pending)
            if page["pno"] not in current.pages:
                current.pages.append(page["pno"])
    _name_the_untitled(chapters)
    return chapters


def _name_the_untitled(chapters):
    """Name a document that carries no heading of its own.

    Nearly every document is named by the heading that opened it. Two kinds are
    not: whatever stands in front of a book's first heading, and the tail of a
    chapter long enough to be cut into several documents. Naming those after
    their own first few words puts whatever the scanner made of a frontispiece at
    the head of the table of contents, and repeats six words of one index entry
    once per part. A document with nothing to say for itself is named for where
    it is instead: the chapter it continues, or the pages it stands on.
    """
    base = ""
    for chapter in chapters:
        if not chapter.title:
            match = _HEADING_TEXT.search("\n".join(chapter.blocks))
            if match:
                chapter.title = block_text(match.group(0))
        if chapter.title:
            base = chapter.title
            continue
        where = _where_it_stands(chapter.pages)
        chapter.title = ("%s (%s)" % (base, where[0].lower() + where[1:])
                         if chapter.continued and base else where)


def _where_it_stands(pnos):
    """The pages a document covers, numbered as its own page markers are."""
    if not pnos:
        return "Text"
    first, last = min(pnos) + 1, max(pnos) + 1
    if first == last:
        return "Page %d" % first
    return "Pages %d–%d" % (first, last)


def _page_homes(chapters):
    """Which document each page marker actually landed in.

    A page whose blocks straddle a chapter break belongs to two documents, so the
    page cannot be asked which one it is in -- only its marker can. Asking the page
    sends a reader to the second document for an anchor written into the first,
    which is a link that goes nowhere: OBSERVED with epubcheck as RSC-012 on the
    acceptance book, where the about page offered ch008 for a marker in ch007.
    """
    homes = {}
    for chapter in chapters:
        for block in chapter.blocks:
            for ident in _ID.findall(block):
                if ident.startswith("pg_"):
                    try:
                        homes.setdefault(int(ident[3:]), chapter.href)
                    except ValueError:                            # pragma: no cover
                        pass
    return homes


def _bind_links(chapters):
    """A link that crosses a document boundary has to name the document."""
    home = {}
    for chapter in chapters:
        for block in chapter.blocks:
            for ident in _ID.findall(block):
                home.setdefault(ident, chapter.href)

    dropped = []
    for chapter in chapters:
        blocks = []
        for block in chapter.blocks:
            def rewrite(match, here=chapter.href):
                target = match.group(1)
                where = home.get(target)
                if where is None:
                    dropped.append(target)
                    return 'href="#"'
                if where == here:
                    return match.group(0)
                return 'href="%s#%s"' % (where, target)
            blocks.append(_HREF.sub(rewrite, block))
        chapter.blocks = blocks
    return dropped


# ------------------------------------------------------------------- packaging

def _document(title, body, language="en"):
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang=%s lang=%s>\n'
        "<head><title>%s</title>"
        '<meta charset="utf-8"/>'
        '<link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
        "<body>\n%s\n</body>\n</html>\n"
        % (quoteattr(language), quoteattr(language), escape(title or ""), body))


def _source_page_items(page_homes):
    # A marker can land before a chapter break within its PDF page. Resolve its
    # actual home, and retain PDF numbering when a sample omits intervening pages.
    return "\n".join(
        '<li><a href=%s>PDF page %d</a></li>'
        % (quoteattr("%s#pg_%04d" % (href, pno)), pno + 1)
        for pno, href in sorted(page_homes.items()))


def _source_index(page_homes, language, evidence=None):
    originals = ""
    if evidence:
        originals = ('<h2>Original printed evidence</h2><p>These original images '
                     'help check uncertain transcriptions and note associations.</p><ol>%s</ol>'
                     % "".join('<li><a href="original-p%04d.xhtml">Original PDF page %d</a></li>'
                               % (pno, pno + 1) for pno in sorted(evidence)))
    return _document("Source PDF pages", (
        '<section epub:type="index"><h1>Source PDF pages</h1>'
        '<p>These numbers count from the first page of the source PDF. They may '
        'differ from its printed page numbers and this reader\'s page count. '
        'Only pages included in this conversion are listed. Choose a link to '
        'go to that page\'s reflowed content.</p>'
        '<ol class="source-pages">%s</ol>%s</section>'
    ) % (_source_page_items(page_homes), originals), language)


def _nav(entries, language="en", page_homes=None):
    items = "\n".join('    <li><a href="%s">%s</a></li>' % (href, escape(title))
                      for href, title in entries)
    body = ('<nav epub:type="toc" id="toc">\n  <h1>Contents</h1>\n  <ol>\n%s\n  </ol>\n'
            "</nav>" % items)
    if page_homes:
        body += ('\n<nav epub:type="page-list" id="page-list" hidden="hidden">'
                 '<h2>Source PDF pages</h2>'
                 '<ol>%s</ol></nav>' % _source_page_items(page_homes))
    return _document("Contents", body, language)


def _ncx(entries, identifier, title):
    points = []
    for index, (href, label) in enumerate(entries, start=1):
        points.append(
            '  <navPoint id="nav%d" playOrder="%d">\n'
            "    <navLabel><text>%s</text></navLabel>\n"
            '    <content src="%s"/>\n'
            "  </navPoint>" % (index, index, escape(label), href))
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
        "<head>\n"
        '  <meta name="dtb:uid" content=%s/>\n'
        '  <meta name="dtb:depth" content="1"/>\n'
        '  <meta name="dtb:totalPageCount" content="0"/>\n'
        '  <meta name="dtb:maxPageNumber" content="0"/>\n'
        "</head>\n"
        "<docTitle><text>%s</text></docTitle>\n"
        "<navMap>\n%s\n</navMap>\n</ncx>\n"
        % (quoteattr(identifier), escape(title or ""), "\n".join(points)))


def _opf(metadata, manifest, spine, identifier, modified, nonlinear=()):
    meta_lines = [
        '    <dc:identifier id="bookid">%s</dc:identifier>' % escape(identifier),
        "    <dc:title>%s</dc:title>" % escape(metadata.get("title") or "Untitled"),
        "    <dc:language>%s</dc:language>" % escape(metadata.get("language") or "en"),
        '    <meta property="dcterms:modified">%s</meta>' % modified,
        '    <meta property="cwng:reflow">%s</meta>' % SIDECAR_PATH,
        '    <meta property="cwng:converter">%s %s</meta>' % (CONVERTER,
                                                              CONVERTER_VERSION),
    ]
    for index, author in enumerate(metadata.get("authors") or []):
        meta_lines.append('    <dc:creator id="au%d">%s</dc:creator>' % (index, escape(author)))
    if metadata.get("publisher"):
        meta_lines.append("    <dc:publisher>%s</dc:publisher>"
                          % escape(metadata["publisher"]))
    if metadata.get("description"):
        meta_lines.append("    <dc:description>%s</dc:description>"
                          % escape(metadata["description"]))
    for tag in metadata.get("tags") or []:
        meta_lines.append("    <dc:subject>%s</dc:subject>" % escape(tag))

    items = "\n".join(
        '    <item id="%s" href="%s" media-type="%s"%s/>'
        % (item["id"], item["href"], item["type"],
           ' properties="%s"' % item["properties"] if item.get("properties") else "")
        for item in manifest)
    refs = "\n".join('    <itemref idref="%s"%s/>'
                     % (ref, ' linear="no"' if ref in nonlinear else '') for ref in spine)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="bookid" prefix="cwng: %s">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n%s\n  </metadata>\n'
        '  <manifest>\n%s\n  </manifest>\n'
        '  <spine toc="ncx">\n%s\n  </spine>\n'
        "</package>\n" % (REFLOW_NS, "\n".join(meta_lines), items, refs))


def _figure_images(chapters, doc, book, package, figure_transform=None, owned_images=()):
    """Crop each figure the fragments referred to; drop the ones we cannot make.

    Each crop goes into ``package`` as it is cut (:class:`_Package`); what comes
    back is the two lists of figures the book does not get.

    Two drops are not the same event. A crop that *fails* is a loss: the page
    printed a figure and the book does not have it, so it is reported. A candidate
    region that proves to hold no ink is blank paper -- source 344 of the
    readiness corpus is a visibly empty leaf that the baseline emitted as one of
    its thirteen 'figures' -- and leaving it out is the correct outcome, disclosed
    in the sidecar rather than mourned on the report page.

    ``figure_transform`` maps a figure's box from the page's reading space into
    unrotated PDF space before cropping: on a rotated spread the skeleton measured
    in the upright reading space, and the ink is stored sideways.
    """
    wanted = []
    for chapter in chapters:
        wanted.extend(_IMG_SRC.findall("\n".join(chapter.blocks)))
    wanted = [src for src in dict.fromkeys(wanted) if src.startswith("images/") and src not in owned_images]
    if not wanted:
        return [], []

    boxes = {}
    for index, figure in enumerate(book.figures):
        seen = boxes.setdefault(figure["pno"], [])
        seen.append(figure)

    missing, blanks = [], []
    for src in wanted:
        match = re.match(r"images/fig_p(\d+)_(\d+)\.jpg$", src)
        if not match or doc is None:
            missing.append(src)
            continue
        pno, index = int(match.group(1)), int(match.group(2))
        page_figures = boxes.get(pno) or []
        if index >= len(page_figures):
            missing.append(src)
            continue
        figure = page_figures[index]
        render_doc=doc
        transform=figure_transform
        geometry=figure.get('source_geometry',{})
        if geometry.get('space')=='reading':
            from .source_display import SourceDisplay
            render_doc=SourceDisplay(doc,pno,dict(geometry,layer='ocr')).query_document()
            transform=None
        bbox = figure["bbox"]
        mask = []
        for element in (getattr(book, "pages", None) or {}).get(pno) or ():
            if element.kind in ("p", "h", "caption"):
                line_boxes = element.line_boxes or ([element.bbox]
                                                    if element.bbox else [])
                for box in line_boxes:
                    if transform is not None:
                        box = transform(pno, box)
                    mask.append(box)
        # Notes are retained reading text too. They must not make an otherwise
        # empty figure territory look like artwork; masks affect ink admission,
        # never the emitted source crop or the note body.
        for note in book.notes:
            if note.pno==pno and note.bbox:
                mask.append(transform(pno,note.bbox) if transform else note.bbox)
        if transform is not None:
            bbox = transform(pno, bbox)
        ink_doc=render_doc
        if figure.get('needs_ink') and geometry.get('space')=='reading':
            ink_doc=SourceDisplay(doc,pno,dict(geometry,layer='ocr')).query_document(isolate=True)
        data = None
        try:
            if figure.get("needs_ink") and not extract.region_has_ink(
                    ink_doc, pno, bbox, mask=mask):
                blanks.append(src)
                continue
            data = extract.crop_jpeg(render_doc, pno, bbox)
        except Exception as exc:                                  # pragma: no cover
            log.warning("reflow: figure %s could not be cropped: %s", src, exc)
            if figure.get("needs_ink"):
                # Fail closed on the visible source, not on silence: when the
                # measured territory cannot be cut, the whole printed page is the
                # honest remainder -- the reader loses nothing that was there.
                try:
                    data = extract.crop_jpeg(render_doc, pno, _page_rect(render_doc, pno))
                except Exception:                                 # pragma: no cover
                    pass
            if data is None:
                missing.append(src)
        finally:
            if ink_doc is not render_doc:ink_doc.close()
        if data is not None:
            # Outside the crop's failure handling: a crop the archive cannot take
            # is a failed build, never a figure reported lost from the source.
            package.image(src, data)
    return missing, blanks


def _page_rect(doc, pno):
    rect = doc[pno].rect
    return (rect.x0, rect.y0, rect.x1, rect.y1)


def _drop_images(chapters, missing):
    if not missing:
        return
    gone = set(missing)


def _drop_images(chapters, missing):
    if not missing:
        return
    gone = set(missing)

    def strip(match):
        src = _IMG_SRC.match(match.group(0))
        inner = re.search(r'src="([^"]+)"', match.group(0))
        if inner and inner.group(1) in gone:
            return ""
        return match.group(0)

    for chapter in chapters:
        chapter.blocks = [_IMG_TAG.sub(strip, block) for block in chapter.blocks]


def _losses(dropped, missing):
    """What the builder had to drop, in the reader's terms rather than the log's.

    Only the builder knows these: a marker whose note is not in the finished book,
    and a plate it could not cut out of the PDF. A reader meets both of them in
    their book -- the number opens nothing, the picture is not there -- so they
    belong with everything else the conversion could not do. Returned to the report
    page and written into the sidecar, not left in a warnings list nobody reads.
    """
    out = []
    if dropped:
        out.append(_count(
            len(dropped),
            "One footnote marker pointed at a note that is not in this book. It is "
            "kept as a printed number rather than made into a link that opens "
            "nothing.",
            "%d footnote markers pointed at notes that are not in this book. They "
            "are kept as printed numbers rather than made into links that open "
            "nothing."))
    if missing:
        out.append(_count(
            len(missing),
            "One picture could not be taken out of the PDF and is not in this book.",
            "%d pictures could not be taken out of the PDF and are not in this "
            "book."))
    return out


def _count(n, one, many):
    return one if n == 1 else many % n


# XML 1.0 cannot carry these characters even as numeric character references.
# A corrupt PDF font mapping can return them among otherwise readable prose.
_UNREPRESENTABLE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def _readable_characters(page_html):
    pages, records = {}, []
    for pno, html in page_html.items():
        counts = {}

        def replace(match):
            codepoint = "U+%04X" % ord(match.group())
            counts[codepoint] = counts.get(codepoint, 0) + 1
            return "\ufffd"

        pages[pno] = _UNREPRESENTABLE.sub(replace, html)
        records.extend({"page": pno, "codepoint": codepoint, "count": count}
                       for codepoint, count in sorted(counts.items()))
    return pages, records


def _refuse_unsafe_pages(page_html, book, source_pages=None, generated_pages=None):
    """The final markup boundary: nothing active or remote reaches the reader.

    A page the pipeline adopted has already passed the gate's markup contract, so
    a violation here means the bytes arrived by another road -- a cache written
    before the contract existed, or a caller that never went through the gate. The
    fragment is not repaired and passed off as the answer: it is refused, and the
    book ships the page's own deterministic text with the refusal disclosed, which
    is the same answer a page that fails the gate gets.
    """
    cleaned, refused = {}, []
    for pno, html in page_html.items():
        # Only bytes generated inside build from the current sealed source and
        # recompiled plan bypass the external HTML grammar. This is exact tree
        # identity, not a style/attribute allowlist transferable to other nodes.
        if generated_pages is not None and pno in generated_pages and html==generated_pages[pno]:
            cleaned[pno]=html
            continue
        reasons = gate.check_markup_safety(html)
        if not reasons:
            cleaned[pno] = html
            continue
        if pno not in book.pages:
            raise ValueError("page %d is not in the book; refusing to drop its text"
                             % pno)
        cleaned[pno] = source_pages[pno].html if source_pages and pno in source_pages else page_fragment(book, pno)
        refused.append((pno, reasons))
    return cleaned, refused


class BuildCancelled(Exception):
    """The requested EPUB was stopped before it was written."""


def _check_cancelled(should_stop):
    if should_stop is not None and should_stop():
        raise BuildCancelled("EPUB assembly was cancelled.")


def _original_evidence(book, page_html, doc, package, figure_transform=None,
                       should_stop=None, progress=None, source_pages=None):
    """Package original pixels, never a re-render of the extracted reading.

    Detail crops retain adjacent printed context; the full original page lets a
    reader resolve associations outside a crop. All rendering uses extract's
    existing pre-allocation and encoded-byte bounds. These links are generated
    after the model markup boundary, not accepted from model output. Every image
    goes into ``package`` as it is rendered (:class:`_Package`), so no more than
    one of them is in memory at a time however long the book is.
    """
    evidence = {}
    recovered = {pno for pno, source in (source_pages or {}).items() if source.report()['uncertain']}
    scanned = {pno for pno, source in (source_pages or {}).items()
               if json.loads(source.provenance_json).get('layer') == 'ocr'}
    wanted = [pno for pno in page_html if book.needs_source_evidence(pno) or pno in recovered or pno in scanned]
    for index, pno in enumerate(wanted):
        _check_cancelled(should_stop)
        if progress is not None:
            progress(index, len(wanted))
        specs = []
        ambiguous = book.ambiguous_note_numbers(pno)
        if ambiguous:
            boxes = [note.bbox for note in book.notes if note.pno == pno
                     and note.bbox[2] > note.bbox[0] and note.bbox[3] > note.bbox[1]]
            box = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                   max(b[2] for b in boxes), max(b[3] for b in boxes)) if boxes else None
            specs.append(("notes", "Original notes and neighboring context", box))
        for element_index, element in enumerate(book.pages.get(pno, [])):
            if getattr(element,'display_group',{}):
                specs.append(("title_%d" % element_index, "Original title and neighboring layout", element.bbox))
            if element.punctuation_uncertain:
                specs.append(("text_%d" % element_index, "Original punctuation and passage", element.bbox))
        caption_keys, caption_counts = [], {}
        figure_index = -1
        for element in book.pages.get(pno, []):
            if element.kind == "fig":
                figure_index += 1
            if element.kind == "caption":
                ordinal = caption_counts.get(figure_index, 0)
                caption_counts[figure_index] = ordinal + 1
                base = "caption_%d" % figure_index if figure_index >= 0 else "caption_orphan"
                key = base + ("_%d" % ordinal if ordinal else "") if element.caption_uncertain else None
                caption_keys.append(key)
                if key is not None:
                    specs.append((key, "Original printed caption", element.bbox))
        if not specs and pno not in recovered and pno not in scanned:
            continue
        if doc is None:
            raise ValueError("Original PDF required for uncertain source evidence on page %d" % pno)
        started = time.monotonic()
        from .source_display import SourceDisplay, inspection_tiles
        provenance = json.loads(source_pages[pno].provenance_json) if source_pages and pno in source_pages else {}
        display = SourceDisplay(doc, pno, provenance)
        full = "images/original_p%04d.jpg" % pno
        package.image(full, display.jpeg(scale=1.5, quality=85))
        details = []
        page_rect = doc[pno].rect * doc[pno].derotation_matrix
        for key, label, box in specs:
            _check_cancelled(should_stop)
            if box and figure_transform:
                box = figure_transform(pno, box)
            # A missing note box falls back to the lower half plus the complete
            # original page, never to an inferred note number or textual rewrite.
            rect = extract.pymupdf.Rect(*box) if box else extract.pymupdf.Rect(
                page_rect.x0, page_rect.y0 + page_rect.height * 0.5,
                page_rect.x1, page_rect.y1)
            rect = rect & page_rect
            if rect.is_empty or rect.is_infinite:
                raise ValueError("Invalid original source evidence geometry on page %d" % pno)
            pad = 12 if key == "notes" else 4
            rect = extract.pymupdf.Rect(rect.x0 - pad, rect.y0 - pad,
                                        rect.x1 + pad, rect.y1 + pad) & page_rect
            if rect.is_empty or rect.is_infinite:
                raise ValueError("Invalid original source evidence geometry on page %d" % pno)
            src = "images/original_p%04d_%s.jpg" % (pno, key)
            reading_rect = display.reading_rect(rect)
            package.image(src, display.jpeg(reading_rect))
            details.append({"id": key, "label": label, "src": src, "bbox": list(rect),
                            "reading_bbox": list(reading_rect)})
        from .source_display import grid_regions
        grids = grid_regions(book, doc, pno, provenance, lambda: _check_cancelled(should_stop))
        for element_index, proof in grids.items():
            if element_index != proof['element_indices'][0]:continue
            key = "layout_%d" % element_index
            src = "images/original_p%04d_%s.jpg" % (pno, key)
            package.image(src, display.jpeg(proof['reading_bbox']))
            details.append({"id": key, "label": "Original layout and labels", "src": src, **proof})
        if provenance.get('layer') == 'ocr':
            for tile_index, tile in enumerate(inspection_tiles(display.rect)):
                _check_cancelled(should_stop)
                key = "inspection_%d" % tile_index
                src = "images/original_p%04d_%s.jpg" % (pno, key)
                package.image(src, display.jpeg(tile))
                details.append({"id": key, "label": "Original detail %d (row order)" % (tile_index + 1),
                    "src": src, "reading_bbox": list(tile), "displayed_pdf_bbox": list(display.source_rect(tile))})
        inspection = [d for d in details if d['id'].startswith('inspection_')]
        for detail in details:
            if detail not in inspection and detail.get('reading_bbox'):
                region = extract.pymupdf.Rect(detail['reading_bbox'])
                detail['inspection_ids'] = [d['id'] for d in inspection
                    if not (region & extract.pymupdf.Rect(d['reading_bbox'])).is_empty]
        evidence[pno] = {"page": pno, "href": "original-p%04d.xhtml" % pno,
                         "full": full, "details": details,
                         "orientation": display.angle, "source_rotation": doc[pno].rotation,
                         "reading_rect": list(display.rect),
                         "ambiguous_notes": sorted(ambiguous),
                         "bytes": package.images[full] + sum(package.images[d["src"]] for d in details),
                         "render_seconds": round(time.monotonic() - started, 4)}
        if pno in recovered:
            evidence[pno]['source_uncertainty'] = source_pages[pno].report()
        html = page_html[pno]
        href = evidence[pno]["href"]
        if ambiguous:
            link = '<a href="%s#notes">Original printed notes and context</a>' % href
            html = ('<div class="source-evidence-notice"><p>Some note labels or associations '
                    'are uncertain. Ambiguous links are not used. %s.</p></div>\n' % link) + html

            def note_link(match):
                ident = _ID.search(match.group("attrs"))
                if ident and ident.group(1).removeprefix("fn_") in ambiguous:
                    return '<aside%s>%s<p>%s</p></aside>' % (
                        match.group("attrs"), match.group("inner"), link)
                return match.group(0)
            html = _NOTE_ASIDE.sub(note_link, html)
        # New canonical callers retain page warnings at heading/nav entry too.
        # These source-backed notices are generated after untrusted markup admission.
        if source_pages and pno in source_pages:
            notices = []
            if ambiguous:
                notices.append('<p class="source-evidence-notice">Some note labels or associations '
                               'are uncertain. %s.</p>' % link)
            if pno in recovered:
                notice = ('<p class="source-evidence-notice reflow-uncertain">OCR readings are uncertain; '
                          'highlighted words preserve the transcription. '
                          '<a href="%s#page">View original page</a>.</p>' % href)
                notices.append(notice)
                html = notice + '\n' + html
            if notices:
                html = re.sub(r'(</h[1-6]>)', lambda m: m.group(0) + '\n' + '\n'.join(notices), html)
        caption_links = iter(caption_keys)

        def caption_link(match):
            key = next(caption_links, None)
            if key is None:
                return match.group(0)
            return ('%s%s<br/><a href="%s#%s">Original printed caption '
                    '(transcription uncertain)</a>%s' % (
                        match.group(1), match.group(2), href, key, match.group(3)))
        html = re.sub(r'(<figcaption\b[^>]*>|<p class="caption">)(.*?)(</figcaption>|</p>)',
                      caption_link, html, flags=re.S)
        if grids and source_pages and pno in source_pages:
            # Canonical marked atoms are retained exactly, after their original
            # layout. Do not infer cells or relabel a chart as a semantic table.
            source = source_pages[pno]
            canonical_blocks = split_blocks(source.html)
            mapping = json.loads(source.blocks_json)
            for element_index, proof in grids.items():
                if element_index != proof['element_indices'][0]:continue
                block = canonical_blocks[mapping[str(element_index)]]
                key = "layout_%d" % element_index
                src = "images/original_p%04d_%s.jpg" % (pno, key)
                prefix = ('<figure class="source-evidence"><img src="%s" alt="Original layout and labels"/>'
                          '<figcaption><a href="%s#%s">Inspect original layout and page details</a></figcaption></figure>'
                          '<p class="source-evidence-notice">OCR transcription. Read the original above for the layout and labels.</p>' % (src, href, key))
                html = html.replace(block, prefix + block, 1)
        page_html[pno] = html
        if progress is not None:
            progress(index + 1, len(wanted))
    _check_cancelled(should_stop)
    return evidence


def _original_document(record, home, language):
    pno = record["page"]
    back = '<p><a href="%s#pg_%04d">Return to reflowed PDF page %d</a></p>' % (home, pno, pno + 1)
    body = ('<h1>Original PDF page %d</h1><p>These are original printed pixels. '
            'Extracted labels and glyphs may be wrong; no note identity is inferred '
            'from the transcription. The details below preserve printed context.</p>%s'
            '<section class="source-evidence" id="page"><h2>Complete original page</h2>'
            '<img src="%s" alt="Complete original PDF page %d"/></section>'
            % (pno + 1, back, record["full"], pno + 1))
    if record["details"]:
        body += '<nav aria-label="Original source details"><h2>Inspect original details</h2><ol>'
        body += ''.join('<li><a href="#%s">%s</a></li>' % (d['id'], escape(d['label'])) for d in record['details'])
        body += '</ol></nav>'
    if record.get('source_uncertainty'):
        report = record['source_uncertainty']
        body += '<section class="source-evidence"><h2>OCR readings to check</h2><p>These tokens are retained as extracted. Some could not be highlighted in the reflowed text.</p><ul>'
        for index, item in enumerate(report['uncertain']):
            state = 'highlighted' if index in report['placed_record_indices'] else 'not highlighted'
            if index in report.get('artwork_record_indices',[]):state='in original figure; not highlighted in reflow text'
            elif index in report.get('qualified_caption_record_indices',[]):state='covered by the caption uncertainty notice'
            body += '<li>%s (%s)</li>' % (escape(item['token']), state)
        body += '</ul></section>'
    for detail in record["details"]:
        links = '<p>Inspect overlapping original details: ' + ' · '.join(
            '<a href="#%s">%s</a>' % (key, escape(next(d['label'] for d in record['details'] if d['id'] == key)))
            for key in detail.get('inspection_ids', [])) + '</p>' if detail.get('inspection_ids') else ''
        body += ('<section class="source-evidence" id="%s"><h2>%s</h2>%s<img src="%s" alt="%s"/>%s</section>'
                 % (detail["id"], escape(detail["label"]), back, detail["src"],
                    escape(detail["label"]), links + back))
    return _document("Original PDF page %d" % (pno + 1), body, language)


def build(book, out_path, page_html=None, metadata=None, doc=None,
          report_html=None, sidecar=None, identifier=None, figure_transform=None,
          should_stop=None, evidence_progress=None, operation_plans=(), source_pages=None):
    """Write one EPUB 3 and say what went into it.

    ``report_html`` is called last, with the document each page marker landed in and
    the list of things the build itself could not place -- neither is known until
    the split and the crops are done.
    """
    metadata = dict(metadata or {})
    language = metadata.get("language") or "en"
    source_pages = dict(source_pages or {})
    for pno, source in source_pages.items():
        from .enriched_source import SourcePage
        if not isinstance(source, SourcePage) or source.page != pno:
            raise ValueError('canonical source pages required')
        source.validate(book)
    canonical = lambda pno: source_pages[pno].html if pno in source_pages else page_fragment(book, pno)
    if page_html is None:
        page_html = {pno: canonical(pno) for pno in sorted(book.pages)}
    current_page_html = dict(page_html) if operation_plans else {}
    # Source evidence also governs direct builder callers. Do this before XML
    # character filtering, so no raw source character is reintroduced afterward.
    page_html = {pno: canonical(pno) if book.needs_source_evidence(pno) or pno in source_pages else html
                 for pno, html in page_html.items()}
    generated_pages={pno:html for pno,html in page_html.items()
                     if book.needs_source_evidence(pno) or pno in source_pages}
    # Inactive opt-in seam: only source-bound wrapper plans are admitted here.
    # Recheck cached plans before producing any output or rendering evidence.
    seen_pages = set()
    for plan in operation_plans:
        from .structural_ops import ContractError, OperationPlan
        if not isinstance(plan, OperationPlan):
            raise ContractError("a validated operation plan is required")
        pno = plan.prepared.page
        if pno in seen_pages or pno not in page_html:
            raise ContractError("duplicate or absent operation page")
        if current_page_html[pno] != canonical(pno):
            raise ContractError("wrapper plan does not bind enriched current HTML")
        seen_pages.add(pno)
        wrappers = plan.compile(book, doc, source_page=source_pages.get(pno))
        if pno in source_pages and wrappers:
            from .source_display import grid_regions
            source_layer = json.loads(source_pages[pno].provenance_json)
            if set(wrappers).intersection(grid_regions(book, doc, pno, source_layer, lambda: _check_cancelled(should_stop))):
                raise ContractError("source grid requires its original layout, not structural wrappers")
        if wrappers:
            page_html[pno] = (source_pages[pno].render(book, wrappers) if pno in source_pages
                              else page_fragment(book, pno, wrappers=wrappers))
        generated_pages[pno]=page_html[pno]
    page_html, unrepresentable = _readable_characters(page_html)
    generated_pages, _ = _readable_characters(generated_pages)
    # Normalize BEFORE the boundary, once: entity resolution and loose-character
    # escaping rewrite bytes, and a check that ran on the pre-rewrite bytes was a
    # check of something that was never written (N1). Idempotent, so builder
    # output -- already escaped -- passes through unchanged and still matches its
    # generated identity below.
    page_html = {pno: _well_formed_text(html or "") for pno, html in page_html.items()}
    generated_pages = {pno: _well_formed_text(html or "")
                       for pno, html in generated_pages.items()}
    page_html, refused = _refuse_unsafe_pages(page_html, book, source_pages, generated_pages)
    # From here the book is written while it is built: each image goes into the
    # archive when it is rendered (_Package), and a build that stops for any reason
    # -- a cancel, a bad page, a full disk -- removes its half-written archive.
    package = _Package(out_path)
    try:
        evidence = _original_evidence(book, page_html, doc, package, figure_transform,
                                      should_stop, evidence_progress, source_pages)

        pages = _page_blocks(page_html)
        joins = _join_page_turns(pages)
        chapters = _chapters(pages)
        dropped = _bind_links(chapters)
        missing, blanks = _figure_images(chapters, doc, book, package,
                                         figure_transform=figure_transform,
                                         owned_images=set(package.images))
        _drop_images(chapters, missing + blanks)
        images = package.images

        identifier = identifier or "urn:uuid:%s" % uuid.uuid4()
        modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        entries = []
        manifest = [
            {"id": "nav", "href": "nav.xhtml", "type": "application/xhtml+xml",
             "properties": "nav"},
            {"id": "ncx", "href": "toc.ncx", "type": "application/x-dtbncx+xml"},
            {"id": "css", "href": "style.css", "type": "text/css"},
        ]
        spine = []
        documents = {}
        page_homes = _page_homes(chapters)

        losses = _losses(dropped, missing)
        if refused:
            losses.append(_count(
                len(refused),
                "One page's markup could not be trusted and the page is kept as its "
                "plain text.",
                "%d pages' markup could not be trusted and they are kept as their "
                "plain text."))
        if unrepresentable:
            count = sum(item["count"] for item in unrepresentable)
            losses.append(
                "%d unprintable characters in the PDF text layer could not be represented "
                "in an EPUB. Each is shown as the replacement character (\ufffd); the "
                "surrounding text is kept. Consult the source PDF at those locations."
                % count)
        if callable(report_html):
            report_html = report_html(page_homes, losses)
        if report_html:
            documents[ABOUT_HREF] = _document("About this conversion", report_html, language)
            manifest.append({"id": "reflow-about", "href": ABOUT_HREF,
                             "type": "application/xhtml+xml"})
            spine.append("reflow-about")
            entries.append((ABOUT_HREF, "About this conversion"))

        for chapter in chapters:
            documents[chapter.href] = _document(chapter.title,
                                                "\n".join(chapter.blocks), language)
            manifest.append({"id": chapter.item_id, "href": chapter.href,
                             "type": "application/xhtml+xml"})
            spine.append(chapter.item_id)
            entries.append((chapter.href, chapter.title))

        # An ordinary spine item also works in readers which ignore EPUB page-list.
        # Keep this generated reference after the book, never inside its prose.
        if page_homes:
            documents[SOURCE_INDEX_HREF] = _source_index(page_homes, language, evidence)
            manifest.append({"id": "source-pages", "href": SOURCE_INDEX_HREF,
                             "type": "application/xhtml+xml"})
            spine.append("source-pages")
            entries.append((SOURCE_INDEX_HREF, "Source PDF pages"))

        for pno, record in sorted(evidence.items()):
            ident = "original-p%04d" % pno
            documents[record["href"]] = _original_document(record, page_homes[pno], language)
            manifest.append({"id": ident, "href": record["href"], "type": "application/xhtml+xml"})
            spine.append(ident)

        for index, src in enumerate(sorted(images)):
            manifest.append({"id": "img%03d" % index, "href": src, "type": "image/jpeg"})

        payload = _sidecar(book, pages, chapters, images, joins, sidecar, blanks)
        if source_pages:
            payload['source_enrichment'] = {str(pno): dict(source.report(), identity=source.identity,
                provenance=json.loads(source.provenance_json), raw_records=json.loads(source.records_json))
                for pno, source in source_pages.items() if pno in page_html}
        if evidence:
            payload["source_evidence"] = list(evidence.values())
        if unrepresentable:
            payload["unrepresentable_characters"] = unrepresentable
        if losses:
            payload["unplaced"] = list(payload.get("unplaced") or []) + losses
        warnings = []
        if dropped:
            warnings.append("%d note links had no target and were disarmed" % len(dropped))
        if missing:
            warnings.append("%d figure images could not be extracted" % len(missing))
        for pno, reasons in refused:
            warnings.append("page %d's markup was not trusted (%s); the page ships as "
                            "its plain text" % (pno, reasons[0]))

        _check_cancelled(should_stop)
        package.finish({
            "opf": _opf(metadata, manifest, spine, identifier, modified,
                        nonlinear={"original-p%04d" % pno for pno in evidence}),
            "nav": _nav(entries, language, page_homes),
            "ncx": _ncx(entries, identifier, metadata.get("title") or ""),
            "documents": documents,
            "sidecar": payload,
        })

        return BuildResult(path=str(out_path), notes=payload["notes"],
                           figures=payload["figures"], images=len(images),
                           page_joins=joins, warnings=warnings, sidecar=payload,
                           chapters=[{"href": c.href, "title": c.title,
                                      "pages": list(c.pages)} for c in chapters])
    except BaseException:
        package.abandon()
        raise


def _sidecar(book, pages, chapters, images, joins, extra, blanks=()):
    artwork_words = sum(len(re.findall(r"[A-Za-z][A-Za-z'’]*", item["text"]))
                        for item in getattr(book, "artwork", []) or [])
    payload = {
        "converter": CONVERTER,
        "converter_version": CONVERTER_VERSION,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pages": len(pages),
        "chapters": len(chapters),
        # A note printed across a page turn is one footnote set in two pieces: the
        # piece with the number is the note, and the unnumbered tail beneath the
        # next page's body is the rest of it, not a footnote of its own.
        "notes": sum(1 for n in book.notes if n.num is not None),
        "notes_unmarked": sum(1 for n in book.notes
                              if n.num is not None and not n.marked),
        "notes_ambiguous": sum(str(n.num) in book.ambiguous_note_numbers(n.pno)
                               for n in book.notes if n.num is not None),
        "figures": len(book.figures),
        "figures_recovered": sum(1 for figure in book.figures
                                 if figure.get("found") not in (None, "embedded")),
        "figures_blank_dropped": len(blanks),
        "images_embedded": len(images),
        "artwork_words": artwork_words,
        # What conservation means here, exactly: every alphabetic word of the PDF
        # text layer is accounted for in the reading flow (body, headings,
        # captions), the notes, the removed running furniture (counted, since it
        # is dropped on purpose), or the preserved artwork crops (counted here as
        # artwork_words) -- minus only the characters a recorded repair declares
        # it consumed. Nothing is silently discarded; a word in none of those
        # places fails the conservation check.
        "conservation_semantics": (
            "every source word is accounted in body/notes/captions, removed "
            "furniture, or preserved artwork crops; only recorded repairs "
            "subtract, itemised"),
        "page_joins": joins,
        "repairs": [r.to_dict() for r in book.repairs[:200]],
        "repair_count": len(book.repairs),
        "headings": sum(1 for el in book.elements if el.kind == "h"),
        "conservation": book.conservation.to_dict() if book.conservation else None,
        "wording_changed": False,
    }
    payload.update(extra or {})
    return payload


class _Package(object):
    """The EPUB archive, written while the book is built rather than after it.

    A scanned page carries about a megabyte and a half of original evidence -- the
    whole printed page and the tiles a reader checks a reading against -- and the
    builder used to hold every image of the book in one dictionary until the last
    page was done: a long scan was gigabytes of JPEG in memory before the first
    byte reached the disk. Each image now goes into the archive the moment it is
    rendered, and only its size stays behind (``images``).

    The archive is ``<out_path>.tmp`` until :meth:`finish` puts it in place; a build
    that stops calls :meth:`abandon`, which removes it. Nothing is left beside the
    target -- in a conversion, a staging folder that holds the candidate and
    nothing else.
    """

    def __init__(self, out_path):
        self.path = str(out_path)
        self.tmp = "%s.tmp" % self.path
        directory = os.path.dirname(self.path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        #: href -> encoded bytes, for the manifest, the sidecar and each evidence
        #: record. The pixels themselves are only ever in the archive.
        self.images = {}
        self._zf = zipfile.ZipFile(self.tmp, "w", zipfile.ZIP_DEFLATED)
        try:
            # The mimetype entry must be first and uncompressed: that is what makes
            # the zip recognisable as an EPUB before anything inside it is read.
            info = zipfile.ZipInfo("mimetype", date_time=time.localtime(time.time())[:6])
            info.compress_type = zipfile.ZIP_STORED
            self._zf.writestr(info, b"application/epub+zip")
        except BaseException:
            self.abandon()
            raise

    def image(self, href, data):
        if href in self.images:
            # A dictionary kept the last of two renders under one name; an archive
            # would keep both, and a reader could open either.
            raise ValueError("the image %s was rendered twice" % href)
        self._zf.writestr(posixpath.join(OEBPS, href), data)
        self.images[href] = len(data)

    def finish(self, parts):
        zf = self._zf
        zf.writestr("META-INF/container.xml",
                    '<?xml version="1.0" encoding="utf-8"?>\n'
                    '<container version="1.0" '
                    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
                    "  <rootfiles>\n"
                    '    <rootfile full-path="%s/content.opf" '
                    'media-type="application/oebps-package+xml"/>\n'
                    "  </rootfiles>\n</container>\n" % OEBPS)
        zf.writestr(SIDECAR_PATH,
                    json.dumps(parts["sidecar"], ensure_ascii=False, indent=1))
        zf.writestr("%s/content.opf" % OEBPS, parts["opf"])
        zf.writestr("%s/nav.xhtml" % OEBPS, parts["nav"])
        zf.writestr("%s/toc.ncx" % OEBPS, parts["ncx"])
        zf.writestr("%s/style.css" % OEBPS, STYLESHEET)
        for href, text in parts["documents"].items():
            zf.writestr(posixpath.join(OEBPS, href), text)
        zf.close()
        os.replace(self.tmp, self.path)
        return self.path

    def abandon(self):
        try:
            self._zf.close()
        except Exception:                                         # pragma: no cover
            pass        # a half-written archive is removed whatever state it is in
        try:
            os.unlink(self.tmp)
        except FileNotFoundError:
            pass


# -------------------------------------------------------------------- validation

#: How many of one kind of fault to name before saying how many more there are. A
#: systematic mistake in a 200-document book produces the same line a thousand times,
#: and a thousand identical lines is not more evidence than twenty.
_REPORT_LIMIT = 20
_XHTML_NS = "{http://www.w3.org/1999/xhtml}"
_NOT_A_LINK = ("http:", "https:", "mailto:", "tel:", "data:", "ftp:")


def _ids_and_links(root):
    """Every id this document declares, and every internal href it follows."""
    ids, links = [], []
    for element in root.iter():
        ident = element.get("id")
        if ident:
            ids.append(ident)
        tag = element.tag
        if tag in ("a", _XHTML_NS + "a"):
            href = (element.get("href") or "").strip()
            if href and href != "#" and not href.lower().startswith(_NOT_A_LINK):
                links.append(href)
    return ids, links


#: What a Reflow XHTML document is made of: the page contract's vocabulary
#: (``gate.MARKUP_SCHEMA``) and the document scaffolding the builder writes
#: around it. Anything else in a written document came by a road no check owns.
_DOCUMENT_TAGS = frozenset(gate.ALLOWED_TAGS) | frozenset(
    ("html", "head", "title", "meta", "link", "body", "nav"))
#: The only inline styles the builder writes (``page_fragment``): a transcribed
#: title line's measured scale and weight, and a title group's alignment.
_WRITTEN_STYLE = re.compile(
    r"(?:display:block;font-size:\d+(?:\.\d+)?em(?:;font-weight:(?:400|700))?"
    r"|text-align:(?:left|center);font-weight:normal)\Z")
#: A URL that leaves the book: any scheme (https:, javascript:, data:), or a
#: protocol-relative or UNC-style path.
_LEAVES_THE_BOOK = re.compile(r"^\s*(?:[A-Za-z][A-Za-z0-9+.-]*:|//|\\\\)")
#: URL-bearing attributes the builder never writes at all.
_NEVER_WRITTEN_URLS = frozenset(("srcset", "poster", "background", "action",
                                 "formaction", "data", "http-equiv"))


def _active_markup(name, root, names):
    """The written bytes of one document, held to what the builder writes.

    The markup boundary (``_refuse_unsafe_pages``) judges a page before it is
    packaged; this judges what was packaged. A document that carries an element
    outside the vocabulary, an event handler, a style the builder never writes,
    or an image or link that leaves the book is refused here whatever road it took
    -- so a gap in an earlier check is a failed job rather than a filed book.
    """
    found = []
    here = posixpath.dirname(name)
    for element in root.iter():
        tag = element.tag if isinstance(element.tag, str) else ""
        local = tag[len(_XHTML_NS):] if tag.startswith(_XHTML_NS) else None
        if local not in _DOCUMENT_TAGS:
            found.append("%s contains a <%s> element, which Reflow never writes"
                         % (name, tag.split("}")[-1] or "?"))
        for attribute, value in element.attrib.items():
            key = attribute.split("}")[-1].lower()
            value = value or ""
            if key.startswith("on") or key in _NEVER_WRITTEN_URLS:
                found.append('%s gives <%s> the attribute "%s"' % (name, local, key))
            elif key == "style" and not _WRITTEN_STYLE.match(value):
                found.append('%s styles <%s> with "%s"' % (name, local, value[:80]))
            elif key in ("href", "src") and _LEAVES_THE_BOOK.match(value):
                found.append('%s points <%s> outside the book: "%s"'
                             % (name, local, value[:80]))
            elif key == "src":
                target = posixpath.normpath(posixpath.join(
                    here, unquote(value.partition("#")[0])))
                if target not in names:
                    found.append('%s shows the image "%s", which is not in the book'
                                 % (name, value[:80]))
    return found


def _too_many(problems, found, kind):
    for item in found[:_REPORT_LIMIT]:
        problems.append(item)
    if len(found) > _REPORT_LIMIT:
        problems.append("and %d more %s" % (len(found) - _REPORT_LIMIT, kind))


def validate(path):
    """A readable zip with the parts a reader needs, and every part of it usable.

    Reading only the zip is what let a document no reader can open ship with a clean
    bill: a page whose markup is not well-formed XML is a well-formed zip entry, and
    the reader loses the chapter. So each document is parsed here as a reader's
    parser would.

    Parsing is not enough either. Two elements under one id, and a footnote link into
    a document that does not hold the note, are both well-formed XML and both a
    footnote that does not open -- OBSERVED with epubcheck on the acceptance book as
    twenty-five RSC-005 and four RSC-012 errors, in a conversion that reported itself
    a success and filed the book. The builder no longer writes either shape, and the
    check that stands between a built book and a reader's library says so anyway:
    a gate that only holds while the generator is right is not a gate.

    ``href="#"`` is deliberately not a fault. It is what ``_bind_links`` writes for a
    marker whose note is nowhere in the book, ``build`` counts them and warns, and
    refusing a whole conversion over one note the scanner lost would be worse for the
    reader than the dead marker is.

    And the markup has to be the markup the builder writes (``_active_markup``):
    no event handler, script, foreign element, unknown style, or image or link out
    of the book, judged on the parsed bytes rather than on what went in.
    """
    problems = []
    try:
        with zipfile.ZipFile(path) as zf:
            broken = zf.testzip()
            if broken:
                problems.append("corrupt entry %s" % broken)
            names = zf.namelist()
            if not names or names[0] != "mimetype":
                problems.append("mimetype is not the first entry")
            for required in ("META-INF/container.xml", "%s/content.opf" % OEBPS):
                if required not in names:
                    problems.append("missing %s" % required)
            declared, followed, active = {}, {}, []
            present = set(names)
            for name in names:
                if not name.endswith((".xhtml", ".opf", ".ncx", ".xml")):
                    continue
                try:
                    root = ElementTree.fromstring(zf.read(name))
                except ElementTree.ParseError as exc:
                    problems.append("%s does not parse: %s" % (name, exc))
                    continue
                if not name.endswith(".xhtml"):
                    continue
                active.extend(_active_markup(name, root, present))
                ids, links = _ids_and_links(root)
                declared[name] = set(ids)
                followed[name] = links
                twice = sorted(set(x for x in ids if ids.count(x) > 1))
                _too_many(problems,
                          ['%s gives two elements the id "%s"' % (name, x)
                           for x in twice], "ids used twice")
            nowhere = []
            for name, links in sorted(followed.items()):
                here = posixpath.dirname(name)
                for href in links:
                    target, _, anchor = href.partition("#")
                    where = (posixpath.normpath(posixpath.join(here, unquote(target)))
                             if target else name)
                    if target and where not in names:
                        nowhere.append('%s links to "%s", which is not in the book'
                                       % (name, href))
                    elif anchor and where in declared and anchor not in declared[where]:
                        nowhere.append('%s links to "%s", and nothing there has '
                                       "that id" % (name, href))
            _too_many(problems, nowhere, "links that land on nothing")
            _too_many(problems, active, "markup the builder never writes")
    except (zipfile.BadZipFile, IOError, OSError) as exc:
        problems.append("not a readable zip: %s" % exc)
    return problems


def run_epubcheck(path, timeout=120):
    """Soft: report what epubcheck says when it is installed, never require it."""
    binary = shutil.which("epubcheck")
    if not binary:
        return None
    try:
        completed = subprocess.run([binary, "--quiet", str(path)],
                                   capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:          # pragma: no cover
        return {"ran": False, "error": str(exc)}
    output = (completed.stdout or b"") + (completed.stderr or b"")
    return {"ran": True, "returncode": completed.returncode,
            "output": output.decode("utf-8", "replace")[:4000]}


def read_sidecar(path):
    """The conversion's own numbers, read back out of a finished EPUB.

    Returns ``None`` rather than raising for a book Reflow did not make, a file that
    is not a zip, or a sidecar that has been corrupted: the caller is a book page
    asking "is there a fidelity report for this?", and every one of those answers is
    "no", not "the request failed".
    """
    try:
        with zipfile.ZipFile(path) as zf:
            with zf.open(SIDECAR_PATH) as handle:
                return json.loads(handle.read().decode("utf-8"))
    except (KeyError, ValueError, zipfile.BadZipFile, IOError, OSError):
        return None
