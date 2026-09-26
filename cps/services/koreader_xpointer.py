# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.
"""Exact conversion between epub.js CFIs and KOReader (crengine) XPointers (#324).

A highlight made in the web reader is stored as an epub.js CFI; one made in
KOReader arrives as a pair of crengine XPointers such as
``/body/DocFragment[3]/body/div/p[4]/text().845``.  Each reader can only place
its own form, so this module maps one to the other against the book's EPUB.

The two engines see the same XHTML through different lenses, and every rule
below was established from the engines' source and checked against positions
captured from a real device (``tests/fixtures/koreader_xpointer``):

epub.js (``epubcfi.js``, 0.3.93)
  * spine step ``/6/8`` = the package's spine element, then the itemref's
    index among ALL itemrefs; ``[id]`` only when the itemref has an ``id``.
  * element step ``2(n+1)`` = n-th element child; ``[id]`` when it has one.
  * text step ``2k+1`` = k-th TEXT NODE child (not the spec's "gap between
    elements" numbering), whitespace-only nodes included.
  * offsets are UTF-16 code units in the raw DOM text.

crengine (``lvtinydom.cpp`` / ``lvxml.cpp`` / ``epubfmt.cpp``)
  * ``DocFragment[N]``: one per spine itemref whose idref names a manifest
    item (DOM >= 20240114; older DOMs skipped non-XHTML items, so numbering
    is only trusted while every item up to the target is XHTML).
  * element steps count same-named element siblings; the index is omitted
    when the element is its parent's only one of that name (DOM < 20260812;
    newer DOMs always write ``[1]`` -- both forms are accepted on input).
  * text is condensed at parse time: ``\\t \\r \\n`` become spaces and each run
    of spaces becomes ONE space (leading/trailing kept), unless the element
    is ``white-space: pre*``.  Offsets are code points in that text.
  * whitespace-only text nodes: one arriving while a block element has no
    child yet is dropped at parse time; in a block with block children, one
    survives only between two inline siblings (text counts as inline,
    ``display: none`` elements such as ``[hidden]`` or an EPUB page-break span
    do not).  ``text()[k]`` counts the survivors.
  * a text node longer than 8192 source characters is split into several;
    a comment is not a node, but the text either side of it stays two nodes.

Chapters are parsed as strict XML: that is crengine's tree. An ``.html`` or
``.htm`` member is read by epub.js with the browser's HTML parser, so two
leniencies apply there:

  * void elements left open in ``<head>`` -- ``<meta charset="utf-8">``,
    ``<link ...>`` -- are self-closed and the file parsed again, since both
    engines treat them as void. The body must still parse strictly, and the
    refusals below judge the file as written.
  * a self-closed non-void element (``<a id="x"/>``, ``<div/>``: every Project
    Gutenberg chapter) is an OPEN tag to the browser, which then nests what
    follows inside it, while crengine keeps it empty (checked with the engine
    harness). The browser's tree is built with html5lib, the WHATWG algorithm
    browsers implement, and a position crosses between the two trees through
    its text node: the chapter is modelled only when both trees hold the same
    non-whitespace text nodes in the same order (``_BrowserView``); a chapter
    the browser reorders (a table fostering content out) or swallows (``<script/>``)
    is refused, as is every such chapter when html5lib is not installed or the
    chapter is over ``MAX_HTML5_CHAPTER_BYTES``.

Anything outside the modelled subset (pre-formatted text, CSS that may change
an element's display/float/white-space where it matters, markup the browser's
HTML parser would restructure, split text nodes, ...) returns ``None``: an
unresolvable position is reported as such, never moved.
"""

from __future__ import annotations

import os
import posixpath
from bisect import bisect_right
import re
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional
from urllib.parse import unquote

from lxml import etree

from .. import logger

log = logger.create()

_XHTML_NS = "http://www.w3.org/1999/xhtml"
_OPF_NS = "http://www.idpf.org/2007/opf"
_CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
_EPUB_OPS_NS = "http://www.idpf.org/2007/ops"
_XHTML_MEDIA_TYPE = "application/xhtml+xml"

# Bounds on optional work done for a request. A book beyond them simply gets
# no derived position.
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_CHAPTER_BYTES = 8 * 1024 * 1024
MAX_POSITION_CHARS = 4096
# html5lib is pure Python (~0.3 ms per KiB measured on Gutenberg chapters);
# a chapter it would take long to parse keeps today's refusal instead.
MAX_HTML5_CHAPTER_BYTES = 1024 * 1024

# crengine hands at most this many raw characters to one text node before it
# splits (lvxml.cpp TEXT_SPLIT_SIZE); longer nodes are refused.
_CRENGINE_TEXT_SPLIT = 8192

_XML_SPACE = " \t\n\r"

# fb2def.h: display of HTML elements before any stylesheet. Everything not
# listed is inline (crengine's default for unknown elements).
_BLOCK_ELEMENTS = frozenset("""
    html body head title hr form pre blockquote div h1 h2 h3 h4 h5 h6 p output
    section ol ul li dl dt dd table caption colgroup col thead tbody tfoot tr
    th td address article aside canvas fieldset figcaption figure footer header
    hgroup legend main nav noscript video center dir menu multicol noframes
    search listing textarea plaintext xmp details dialog summary frame frameset
    iframe noembed template select button marquee applet optgroup datalist map
    area track input keygen param audio source
""".split())
_NONE_ELEMENTS = frozenset("style script base basefont bgsound meta link".split())
# Elements whose text is parsed as pre-formatted (white-space: pre*).
_PRE_ELEMENTS = frozenset("pre listing textarea plaintext xmp".split())
# Elements crengine gives no text children at all (allow_text=false).
_NO_TEXT_ELEMENTS = frozenset("""
    html head svg object embed img image table colgroup col thead tbody tfoot
    tr applet area track input keygen param audio source base basefont bgsound
    meta link
""".split())
_VOID_ELEMENTS = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split())
# Start tags that make the browser's HTML parser close an open <p>.
_CLOSES_P = frozenset("""
    address article aside blockquote center details dialog dir div dl fieldset
    figcaption figure footer form h1 h2 h3 h4 h5 h6 header hgroup hr main menu
    nav ol p pre section table ul listing xmp plaintext summary search
""".split())
_HEADINGS = frozenset("h1 h2 h3 h4 h5 h6".split())

# epub.js writes ids into CFI assertions unescaped; one containing CFI syntax
# would make the string unparseable, so such paths are not converted.
_CFI_ASSERTION_SAFE = re.compile(r"[^\[\]\^,;()!/:\s]+")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def xpointer_to_cfi(epub_path, xpointer: str) -> Optional[str]:
    """Return the epub.js point CFI for a crengine XPointer, or ``None``."""
    point = _resolve(epub_path, lambda book: _point_from_xpointer(book, xpointer))
    if point is None:
        return None
    book, item, pos = point
    pos = _in_browser(pos)
    steps = _cfi_steps(pos)
    if steps is None:
        return None
    return "epubcfi(" + _cfi_base(book, item) + "!" + "".join(steps) + _cfi_terminal(pos) + ")"


def cfi_to_xpointer(epub_path, cfi: str) -> Optional[str]:
    """Return the crengine XPointer for an epub.js point CFI, or ``None``."""
    def parse(book):
        parsed = _parse_cfi(cfi)
        if parsed is None or parsed.is_range:
            return None
        return _point_from_cfi(book, parsed.base, parsed.common)
    point = _resolve(epub_path, parse)
    if point is None:
        return None
    return _xpointer_string(*point)


def xpointers_to_cfi_range(epub_path, start_xpointer: str,
                           end_xpointer: str) -> Optional[str]:
    """Return an epub.js range CFI for a crengine highlight, or ``None``."""
    def both(book):
        start = _point_from_xpointer(book, start_xpointer)
        end = _point_from_xpointer(book, end_xpointer)
        if start is None or end is None or start[1] is not end[1]:
            return None
        return start, end
    points = _resolve(epub_path, both)
    if points is None:
        return None
    (book, item, start), (_book, _item, end) = points
    if _order(start) > _order(end):
        return None
    start, end = _in_browser(start), _in_browser(end)
    start_steps, end_steps = _cfi_steps(start), _cfi_steps(end)
    if start_steps is None or end_steps is None:
        return None
    common = 0
    for a, b in zip(start_steps[:-1], end_steps[:-1]):
        if a != b:
            break
        common += 1
    base = _cfi_base(book, item)
    shared = "".join(start_steps[:common])
    start_rest = "".join(start_steps[common:]) + _cfi_terminal(start)
    end_rest = "".join(end_steps[common:]) + _cfi_terminal(end)
    return f"epubcfi({base}!{shared},{start_rest},{end_rest})"


def cfi_range_to_xpointers(epub_path, cfi: str) -> Optional[tuple[str, str]]:
    """Return ``(start_xpointer, end_xpointer)`` for an epub.js range CFI."""
    def both(book):
        parsed = _parse_cfi(cfi)
        if parsed is None or not parsed.is_range:
            return None
        start = _point_from_cfi(book, parsed.base, parsed.common + parsed.start)
        end = _point_from_cfi(book, parsed.base, parsed.common + parsed.end)
        if start is None or end is None or start[1] is not end[1]:
            return None
        if _order(start[2]) > _order(end[2]):
            return None
        return start, end
    points = _resolve(epub_path, both)
    if points is None:
        return None
    start, end = points
    return _xpointer_string(*start), _xpointer_string(*end)


def derive_xpointers(epub_path, cfi_range: str,
                     highlighted_text: Optional[str]) -> Optional[tuple[str, str]]:
    """XPointers for a stored web highlight, only if they still frame its text.

    The CFI is resolved against ``epub_path`` (the file the device holds) and
    the passage between the two points must equal ``highlighted_text``
    (ignoring whitespace and soft hyphens, which the engines render
    differently). A CFI from another file version, or a row without text,
    yields ``None`` rather than an XPointer that might frame other words.
    """
    def both(book):
        parsed = _parse_cfi(cfi_range)
        if parsed is None or not parsed.is_range:
            return None
        start = _point_from_cfi(book, parsed.base, parsed.common + parsed.start)
        end = _point_from_cfi(book, parsed.base, parsed.common + parsed.end)
        if not _frames(start, end, highlighted_text):
            return None
        return start, end
    points = _resolve(epub_path, both)
    if points is None:
        return None
    start, end = (_xpointer_string(*point) for point in points)
    if start is None or end is None:
        return None
    return start, end


def derive_cfi_range(epub_path, start_xpointer: str, end_xpointer: str,
                     highlighted_text: Optional[str]) -> Optional[str]:
    """Range CFI for a KOReader highlight, only if it frames the stored text."""
    def check(book):
        start = _point_from_xpointer(book, start_xpointer)
        end = _point_from_xpointer(book, end_xpointer)
        return _frames(start, end, highlighted_text)
    if not _resolve(epub_path, check):
        return None
    return xpointers_to_cfi_range(epub_path, start_xpointer, end_xpointer)


def _normalized_passage(text) -> str:
    return re.sub(r"\s+", "", text).replace("\u00ad", "")


def _frames(start, end, highlighted_text) -> bool:
    if start is None or end is None or start[1] is not end[1]:
        return False
    if not isinstance(highlighted_text, str) or not _normalized_passage(highlighted_text):
        return False
    passage = _text_between(start[2], end[2])
    return passage is not None and (
        _normalized_passage(passage) == _normalized_passage(highlighted_text))


def _text_between(start: "_Pos", end: "_Pos") -> Optional[str]:
    """The DOM text between two text positions of one chapter (Range.toString)."""
    if start.text is None or end.text is None or _order(start) > _order(end):
        return None
    order = start.chapter.text_order()
    i = order.get((start.text.parent, start.text.ordinal))
    j = order.get((end.text.parent, end.text.ordinal))
    if i is None or j is None:
        return None
    nodes = start.chapter.text_nodes
    if i == j:
        return start.text.value[start.offset:end.offset]
    return (start.text.value[start.offset:]
            + "".join(value for _p, _o, value in nodes[i + 1:j])
            + end.text.value[:end.offset])


# --- Text anchors: a place named by the book's text, not its tree ------------
#
# Another file made from this EPUB (a KEPUB: kepubify wraps each sentence in a
# span, and CWNG may split a chapter file into pieces) shares its text but not
# its tree or its file names. A place crosses over as the number of
# non-whitespace characters before it in its chapter's <body>; the caller must
# first check that both books hold exactly the same non-whitespace characters
# (``spine_solid_texts``), or the count means nothing.


def spine_solid_texts(epub_path) -> Optional[tuple]:
    """``(member, text)`` for every spine item in reading order, or None.

    ``text`` is every non-whitespace character of the item's <body>, in
    order. A spine item that cannot be read as an XHTML document makes the
    whole book None: its text is unknown. (Chapters refused for conversion
    still count: their text is known even where positions in them are not.)
    """
    def run(book):
        out = []
        for item in book.items:
            chapter = _parsed_chapter(book, item)
            if chapter is None:
                return None
            out.append((item.member, _solid(chapter).text))
        return tuple(out)
    return _resolve(epub_path, run)


def xpointer_at_solid_index(epub_path, member: str, index: int,
                            expected: str) -> Optional[str]:
    """XPointer of the ``index``-th non-whitespace character of ``member``.

    ``expected`` is the chapter's non-whitespace text as the caller aligned it
    (``spine_solid_texts``); a chapter that no longer reads so gives None.
    """
    def run(book):
        found = _item_and_chapter(book, member)
        if found is None or isinstance(index, bool) or not isinstance(index, int):
            return None
        item, chapter = found
        solid = _solid(chapter)
        if solid.text != expected or not 0 <= index < len(solid.text):
            return None
        k = bisect_right(solid.starts, index) - 1
        parent, ordinal, value = solid.nodes[k]
        need = index - solid.starts[k]
        for raw, char in enumerate(value):
            if char not in _XML_SPACE:
                if not need:
                    break
                need -= 1
        return _xpointer_string(book, item, _Pos(parent, _Text(parent, ordinal, value),
                                                 raw, chapter))
    return _resolve(epub_path, run)


def solid_index_of_xpointer(epub_path, xpointer: str) -> Optional[tuple[str, int]]:
    """``(member, index)`` of the character a crengine XPointer points at.

    ``index`` counts the non-whitespace characters before it in the chapter's
    body, so a point on whitespace counts as the character that follows it.
    An element position, or a point after the chapter's last character, is
    None.
    """
    def run(book):
        point = _point_from_xpointer(book, xpointer)
        if point is None:
            return None
        _book, item, pos = point
        if pos.text is None:
            return None
        solid = _solid(pos.chapter)
        k = solid.position.get((pos.text.parent, pos.text.ordinal))
        if k is None:
            return None  # a whitespace-only node
        index = solid.starts[k] + len(pos.text.value[:pos.offset].translate(_NO_SPACE))
        return (item.member, index) if index < len(solid.text) else None
    return _resolve(epub_path, run)


# ---------------------------------------------------------------------------
# Book loading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _SpineItem:
    index: int                    # epub.js spine position (all itemrefs)
    itemref_id: Optional[str]     # epub.js spine-step assertion
    member: Optional[str]         # zip member, None when unresolvable
    fragment: Optional[int]       # crengine DocFragment number (1-based)
    fragment_trusted: bool        # numbering identical across DOM versions


@dataclass
class _Book:
    path: str
    spine_step: int
    items: tuple
    fragment_total: int
    chapters: dict


def _resolve(epub_path, fn):
    """Run ``fn(book)`` against a cached, validated view of the EPUB."""
    try:
        path = os.fspath(epub_path)
        stat = os.stat(path)
    except (OSError, TypeError):
        return None
    if stat.st_size > MAX_ARCHIVE_BYTES:
        return None
    try:
        book = _load_book(path, stat.st_mtime_ns, stat.st_size)
    except Exception:
        log.debug("koreader_xpointer: cannot read %s", path, exc_info=True)
        return None
    if book is None:
        return None
    try:
        return fn(book)
    except Exception:
        log.debug("koreader_xpointer: position conversion failed for %s", path,
                  exc_info=True)
        return None


def _xml(raw: bytes):
    return etree.fromstring(raw, etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False))


@lru_cache(maxsize=16)
def _load_book(path: str, _mtime_ns: int, _size: int) -> Optional[_Book]:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        container = _xml(archive.read("META-INF/container.xml"))
        rootfile = container.find(f".//{{{_CONTAINER_NS}}}rootfile")
        if rootfile is None or not rootfile.get("full-path"):
            return None
        opf_path = rootfile.get("full-path")
        package = _xml(archive.read(opf_path))
    spine = package.find(f"{{{_OPF_NS}}}spine")
    if spine is None:
        return None
    package_children = [c for c in package if isinstance(c.tag, str)]
    spine_step = 2 * (package_children.index(spine) + 1)
    opf_dir = posixpath.dirname(opf_path)
    manifest = {}
    for item in package.iterfind(f"{{{_OPF_NS}}}manifest/{{{_OPF_NS}}}item"):
        item_id, href = item.get("id"), item.get("href")
        # crengine ignores items without both; the first id wins its lookup.
        if item_id and href and item_id not in manifest:
            manifest[item_id] = (href, item.get("media-type"))
    items = []
    fragment = 0
    trusted = True
    for index, ref in enumerate(spine.iterfind(f"{{{_OPF_NS}}}itemref")):
        entry = manifest.get(ref.get("idref"))
        member = None
        number = None
        if entry is not None:
            href, media_type = entry
            candidate = posixpath.normpath(posixpath.join(opf_dir, unquote(href)))
            member = candidate if candidate in names else None
            fragment += 1
            number = fragment
            # Before DOM 20240114 crengine only made fragments for XHTML items
            # it could open; the two numberings agree while every item so far
            # is an XHTML file that exists.
            trusted = trusted and media_type == _XHTML_MEDIA_TYPE and member is not None
        else:
            # An itemref crengine cannot resolve shifts nothing for it but
            # still occupies an epub.js spine slot; later numbering is unsafe.
            trusted = False
        items.append(_SpineItem(index, ref.get("id"), member, number, trusted))
    return _Book(path, spine_step, tuple(items), fragment, {})


@dataclass
class _Chapter:
    body: object
    refusal: Optional[str]
    rules: tuple            # CSS (property, value, compound) triples
    kept: dict              # parent element -> crengine text-node list
    split_texts: frozenset  # decoded text of source runs crengine splits
    text_nodes: Optional[list] = None   # (parent, ordinal, value) in order
    _text_index: Optional[dict] = None
    # The browser's own tree, when its HTML parser builds a different one
    # (see ``_browser_view``); None when the XML tree is the browser's tree.
    browser: Optional["_BrowserView"] = None
    solid: Optional["_Solid"] = None    # see ``_solid``

    def text_order(self) -> dict:
        """(parent, ordinal) -> document-order index of every DOM text node."""
        if self._text_index is None:
            nodes = []

            def walk(el):
                ordinal = 0
                for child in _raw_children(el):
                    if isinstance(child, str):
                        nodes.append((el, ordinal, child))
                        ordinal += 1
                    elif isinstance(child.tag, str):
                        walk(child)
            walk(self.body)
            self.text_nodes = nodes
            self._text_index = {(p, o): i for i, (p, o, _v) in enumerate(nodes)}
        return self._text_index


def _chapter(book: _Book, item: _SpineItem) -> Optional[_Chapter]:
    """The chapter of ``item`` if positions in it can be converted."""
    cached = _parsed_chapter(book, item)
    if cached is None or cached.refusal:
        return None
    return cached


def _parsed_chapter(book: _Book, item: _SpineItem) -> Optional[_Chapter]:
    """The parsed chapter of ``item``, refused or not; None if unreadable."""
    if item.member is None:
        return None
    # A chapter that cannot be read is remembered as None too, so a book with
    # an unreadable chapter is not re-read and re-parsed on every request.
    if item.member in book.chapters:
        cached = book.chapters[item.member]
    else:
        try:
            cached = _load_chapter(book.path, item.member)
        except (etree.XMLSyntaxError, UnicodeDecodeError, KeyError):
            log.debug("koreader_xpointer: cannot parse %s in %s", item.member,
                      book.path, exc_info=True)
            cached = None
        book.chapters[item.member] = cached
    return cached


def _item_and_chapter(book: _Book, member: str):
    """The one trusted spine item reading ``member``, and its chapter."""
    items = [i for i in book.items if i.member == member]
    if len(items) != 1 or not items[0].fragment_trusted:
        return None
    chapter = _chapter(book, items[0])
    return None if chapter is None else (items[0], chapter)


_NO_SPACE = {ord(c): None for c in _XML_SPACE}


@dataclass(frozen=True)
class _Solid:
    text: str          # every non-whitespace character of the body, in order
    starts: list       # index in ``text`` of each node's first one
    nodes: list        # (parent, ordinal, value) of each such text node
    position: dict     # (parent, ordinal) -> its place in ``nodes``


def _solid(chapter: "_Chapter") -> _Solid:
    if chapter.solid is None:
        parts, starts, nodes, position, count = [], [], [], {}, 0
        for parent, ordinal, value in _solid_texts(chapter.body):
            packed = value.translate(_NO_SPACE)
            position[(parent, ordinal)] = len(nodes)
            starts.append(count)
            nodes.append((parent, ordinal, value))
            parts.append(packed)
            count += len(packed)
        chapter.solid = _Solid("".join(parts), starts, nodes, position)
    return chapter.solid


def _load_chapter(path: str, member: str) -> Optional[_Chapter]:
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo(member)
        if info.file_size > MAX_CHAPTER_BYTES:
            return None
        raw = archive.read(member)
        text = raw.decode("utf-8-sig", errors="strict")
        source = _numeric_named_entities(text)
        try:
            root = _xml(source.encode("utf-8"))
        except etree.XMLSyntaxError:
            repaired = _close_head_voids(source, member)
            if repaired is None:
                raise
            root = _xml(repaired.encode("utf-8"))
        body = root.find(f"{{{_XHTML_NS}}}body")
        if body is None or etree.QName(root).localname != "html":
            return None
        browser = None
        if (posixpath.splitext(member)[1] in (".html", ".htm")
                and info.file_size <= MAX_HTML5_CHAPTER_BYTES
                and _html_self_closing(text)):
            browser = _browser_view(text, body)
        # Refusals judge the file as written, not the repaired copy.
        refusal = _chapter_refusal(text, root, member, browser_tree=browser is not None)
        css = _chapter_css(archive, member, root)
    return _Chapter(body, refusal, tuple(_css_rules(css)), {}, _split_texts(text),
                    browser=browser)


# A start tag of a void element the head may carry, not already self-closed.
# Quoted attribute values may contain '>' or '/'.
_OPEN_HEAD_VOID = re.compile(
    r"<(meta|link|base)(\s(?:[^<>\"']|\"[^\"]*\"|'[^']*')*?)?(?<!/)>")
_HEAD = re.compile(r"(<head(?:\s[^<>]*)?>)(.*?)(</head\s*>)", re.S)


def _close_head_voids(text: str, member: str) -> Optional[str]:
    """``text`` with the void elements of its ``<head>`` self-closed, or None.

    HTML-style ``<meta charset="utf-8">`` / ``<link ...>`` in a chapter's head
    is ordinary in calibre-converted and retail books. The browser parses an
    ``.html`` member with its HTML parser, where these elements are void, and
    crengine is lenient, so both readers build the tree the self-closed file
    would have. Only the head is repaired: positions live in the body, which
    must still parse strictly. An ``.xhtml`` member is not repaired, because
    the browser parses it as XML, fails, and renders no body at all (epub.js
    chooses the parser from the member's extension, case-sensitively).
    """
    if posixpath.splitext(member)[1] not in (".html", ".htm"):
        return None
    match = _HEAD.search(text)
    if match is None:
        return None

    def close(tag):
        return f"<{tag.group(1)}{tag.group(2) or ''}/>"
    head = _OPEN_HEAD_VOID.sub(close, match.group(2))
    if head == match.group(2):
        return None
    return text[:match.start(2)] + head + text[match.end(2):]


def _split_texts(text: str) -> frozenset:
    """Text nodes crengine stores as several nodes.

    Its parser hands at most ``_CRENGINE_TEXT_SPLIT`` SOURCE characters (entity
    references still undecoded) to one text node, so the test is on the raw
    run between two tags, not on the decoded length lxml reports.
    """
    import html
    return frozenset(html.unescape(run) for run in re.findall(
        r">([^<]{%d,})" % _CRENGINE_TEXT_SPLIT, text))


def _numeric_named_entities(text: str) -> str:
    """Let the XML parser read HTML named entities (&nbsp; ...) as a browser does."""
    import html.entities

    def replace(match):
        name = match.group(1)
        if name in ("amp", "lt", "gt", "quot", "apos"):
            return match.group(0)
        codepoints = html.entities.html5.get(name + ";")
        if codepoints is None:
            return match.group(0)
        return "".join(f"&#{ord(c)};" for c in codepoints)
    return re.sub(r"&([A-Za-z][A-Za-z0-9]{0,31});", replace, text)


_WS_CHAR_REF = re.compile(
    r"&#(?:0*(?:9|10|13|32)|[xX]0*(?:9|[aAdD]|20));|&(?:Tab|NewLine);")


def _chapter_refusal(text: str, root, member: str, browser_tree: bool = False) -> Optional[str]:
    """Why the two engines might build different trees for this file, or None.

    ``browser_tree``: the browser's own tree of this file is modelled
    (``_browser_view``), so a self-closed element it reads differently is
    accounted for rather than a reason to refuse.
    """
    if _WS_CHAR_REF.search(text):
        # crengine inserts referenced whitespace without condensing it.
        return "whitespace character reference"
    if "<![CDATA[" in text:
        return "CDATA section"
    if (not browser_tree and member.lower().endswith((".html", ".htm"))
            and _html_self_closing(text)):
        # epub.js parses .html members with the HTML parser, which treats a
        # self-closed non-void element as an open tag.
        return "self-closed non-void element"
    if member.lower().endswith((".html", ".htm")) and _fosters(root):
        # The HTML parser moves it out in front of the table.
        return "content a table fosters out"
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        name = _local(el)
        if name == "p" and _xhtml(el):
            for sub in el.iterdescendants():
                if isinstance(sub.tag, str) and _xhtml(sub) and _local(sub) in _CLOSES_P:
                    return "block inside <p>"
        elif name in _HEADINGS and _xhtml(el):
            for sub in el.iterdescendants():
                if isinstance(sub.tag, str) and _xhtml(sub) and _local(sub) in _HEADINGS:
                    return "heading inside heading"
        elif name == "a" and _xhtml(el):
            for sub in el.iterdescendants():
                if isinstance(sub.tag, str) and _xhtml(sub) and _local(sub) == "a":
                    return "link inside link"
    return None


_TAG = re.compile(r"<(/?)([A-Za-z][\w:.-]*)(?:\s[^<>]*?)?(/?)>")


def _html_self_closing(text: str) -> bool:
    """True if an HTML (not SVG/MathML) non-void element is written ``<x/>``."""
    foreign = 0
    for match in _TAG.finditer(text):
        closing, name, self_closed = match.group(1), match.group(2).lower(), match.group(3)
        local = name.split(":")[-1]
        if local in ("svg", "math"):
            if closing:
                foreign = max(0, foreign - 1)
            elif not self_closed:
                foreign += 1
            continue
        if self_closed and not foreign and local not in _VOID_ELEMENTS:
            return True
    return False


def _chapter_css(archive, member: str, root) -> str:
    """Stylesheets that apply to this chapter, concatenated (bounded)."""
    parts = []
    head = root.find(f"{{{_XHTML_NS}}}head")
    base = posixpath.dirname(member)
    budget = 2 * 1024 * 1024

    def read(target, depth):
        nonlocal budget
        if depth > 4 or budget <= 0:
            return
        try:
            data = archive.read(target).decode("utf-8", errors="replace")
        except KeyError:
            return
        budget -= len(data)
        parts.append(data)
        for imp in re.finditer(r"@import\s+(?:url\()?\s*['\"]?([^'\")\s;]+)", data):
            read(posixpath.normpath(posixpath.join(posixpath.dirname(target),
                                                   unquote(imp.group(1)))), depth + 1)

    if head is not None:
        for el in head:
            if not isinstance(el.tag, str):
                continue
            name = _local(el)
            if name == "link" and "stylesheet" in (el.get("rel") or "").lower().split():
                href = el.get("href")
                if href and "://" not in href:
                    read(posixpath.normpath(posixpath.join(base, unquote(href))), 0)
            elif name == "style":
                parts.append(el.text or "")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# CSS: only enough to know when a stylesheet MAY change what we model.
# ---------------------------------------------------------------------------


_CSS_PROPS = ("display", "float", "white-space")


def _css_rules(css: str):
    css = re.sub(r"/\*.*?\*/", " ", css, flags=re.S)
    # Flatten at-rule blocks (@media ...) by treating their inner rules as
    # potentially applying; drop @import/@charset statements.
    css = re.sub(r"@[\w-]+[^{};]*;", " ", css)
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selectors, declarations = match.group(1), match.group(2)
        found = []
        for decl in declarations.split(";"):
            if ":" not in decl:
                continue
            prop, value = decl.split(":", 1)
            prop = prop.strip().lower()
            if prop in _CSS_PROPS:
                found.append((prop, value.replace("!important", "").strip().lower()))
        if not found:
            continue
        for selector in selectors.split(","):
            compound = _subject_compound(selector)
            if compound is None:
                continue
            for prop, value in found:
                yield prop, value, compound


def _subject_compound(selector: str):
    selector = selector.strip()
    if not selector or "::" in selector or re.search(
            r":(before|after|first-letter|first-line|marker)\b", selector):
        return None  # pseudo-elements box content, they don't restyle elements
    last = re.split(r"\s*[>+~]\s*|\s+", selector)[-1]
    last = re.sub(r"\[[^\]]*\]", "", last)
    last = re.sub(r":[\w-]+(\([^)]*\))?", "", last)
    tag = re.match(r"^([A-Za-z][\w-]*|\*)?", last).group(1)
    classes = frozenset(re.findall(r"\.([\w-]+)", last))
    ids = frozenset(re.findall(r"#([\w-]+)", last))
    tag = None if tag in (None, "*") else tag.lower().split("|")[-1]
    return tag, classes, ids


def _may_match(el, compound) -> bool:
    tag, classes, ids = compound
    if tag is not None and tag != _local(el).lower():
        return False
    if classes and not classes <= set((el.get("class") or "").split()):
        return False
    if ids and el.get("id") not in ids:
        return False
    return True


def _style_attribute(el):
    style = el.get("style")
    if not style:
        return ()
    out = []
    for decl in style.split(";"):
        if ":" in decl:
            prop, value = decl.split(":", 1)
            prop = prop.strip().lower()
            if prop in _CSS_PROPS:
                out.append((prop, value.replace("!important", "").strip().lower()))
    return out


def _display_category(value: str) -> str:
    value = value.split()[0] if value.split() else ""
    if value == "none":
        return "none"
    if value.startswith("inline") or value in ("contents",):
        return "inline"
    return "block"


# ---------------------------------------------------------------------------
# The crengine view of an element's children
# ---------------------------------------------------------------------------


def _local(el) -> str:
    return etree.QName(el).localname


def _xhtml(el) -> bool:
    return etree.QName(el).namespace in (None, _XHTML_NS)


def _default_display(el) -> str:
    if not _xhtml(el):
        return "inline"
    name = _local(el)
    if name in _NONE_ELEMENTS or el.get("hidden") is not None:
        return "none"
    if name == "span" and "pagebreak" in (el.get(f"{{{_EPUB_OPS_NS}}}type") or "").split():
        return "none"
    return "block" if name in _BLOCK_ELEMENTS else "inline"


def _default_float(el) -> bool:
    align = (el.get("align") or "").lower()
    return align in ("left", "right") and _local(el) in (
        "img", "table", "embed", "iframe", "input", "object")


def _layout(chapter: _Chapter, el):
    """(display category, certain?) for ``el`` with crengine's defaults.

    ``certain`` is False when the book's CSS or a style attribute could give
    the element a different display or make it float.
    """
    display = _default_display(el)
    certain = not _default_float(el)
    for prop, value in _style_attribute(el):
        if prop == "display":
            if _display_category(value) != display:
                certain = False
        elif prop == "float" and value != "none":
            certain = False
    for prop, value, compound in chapter.rules:
        if prop == "white-space" or not _may_match(el, compound):
            continue
        if prop == "display" and _display_category(value) != display:
            certain = False
        elif prop == "float" and value != "none":
            certain = False
    return display, certain


def _preformatted(chapter: _Chapter, el) -> bool:
    """True when ``el`` or an ancestor is (or may be) white-space: pre*."""
    pre_values = ("pre", "pre-wrap", "pre-line", "break-spaces")
    node = el
    while node is not None and isinstance(node.tag, str):
        if _xhtml(node) and _local(node) in _PRE_ELEMENTS:
            return True
        for prop, value in _style_attribute(node):
            if prop == "white-space" and value.split()[:1] and value.split()[0] in pre_values:
                return True
        for prop, value, compound in chapter.rules:
            if (prop == "white-space" and value.split()[:1]
                    and value.split()[0] in pre_values and _may_match(node, compound)):
                return True
        node = node.getparent()
    return False


@dataclass(frozen=True)
class _Text:
    """One raw DOM text node: the ``ordinal``-th text child of ``parent``."""
    parent: object
    ordinal: int
    value: str


def _raw_children(el):
    """The DOM child sequence of ``el``: strings for text nodes, else nodes."""
    out = []
    if el.text:
        out.append(el.text)
    for child in el:
        out.append(child)
        if child.tail:
            out.append(child.tail)
    return out


def _is_ws(text: str) -> bool:
    return not text.strip(_XML_SPACE)


def _crengine_texts(chapter: _Chapter, parent) -> Optional[list]:
    """crengine's text nodes of ``parent`` as raw ``_Text``s, or None if unsure."""
    # Keyed by the element itself (not id()): holding the proxy keeps lxml
    # from recycling it, so identity comparisons stay valid.
    if parent in chapter.kept:
        return chapter.kept[parent]
    result = _compute_crengine_texts(chapter, parent)
    chapter.kept[parent] = result
    return result


def _compute_crengine_texts(chapter: _Chapter, parent):
    if not _xhtml(parent) or _local(parent) in _NO_TEXT_ELEMENTS:
        return None
    if _preformatted(chapter, parent):
        return None
    ordinal = 0
    raw = []
    for child in _raw_children(parent):
        if isinstance(child, str):
            if len(child) >= _CRENGINE_TEXT_SPLIT or child in chapter.split_texts:
                return None  # crengine splits it into several text nodes
            raw.append(_Text(parent, ordinal, child))
            ordinal += 1
        elif isinstance(child.tag, str):
            raw.append(child)
        # Comments/PIs are not nodes in crengine and are skipped by epub.js's
        # text numbering; the text on either side stays two nodes in both.
    for a, b in zip(raw, raw[1:]):
        if (isinstance(a, _Text) and isinstance(b, _Text)
                and _is_ws(a.value) and _is_ws(b.value)):
            return None  # adjacent blank nodes: crengine's two trimming passes differ
    has_ws = any(isinstance(c, _Text) and _is_ws(c.value) for c in raw)
    display, certain = _layout(chapter, parent)
    layouts = {}
    for child in raw:
        if not isinstance(child, _Text):
            layouts[id(child)] = _layout(chapter, child)
    if has_ws and (not certain or not all(c for _d, c in layouts.values())):
        return None
    block = display == "block"
    # Parse time: whitespace-only text arriving while a block has no child yet
    # is never inserted (lvtinydom.cpp ldomElementWriter::onText).
    while block and raw and isinstance(raw[0], _Text) and _is_ws(raw[0].value):
        raw = raw[1:]
    if display == "inline":
        if has_ws and any(layouts[id(c)][0] == "block"
                          for c in raw if not isinstance(c, _Text)):
            return None  # crengine re-boxes blocks inside inlines, trimming spaces
        return [c for c in raw if isinstance(c, _Text)]
    mixed = any(layouts[id(c)][0] == "block" for c in raw if not isinstance(c, _Text))
    if not block or not mixed:
        return [c for c in raw if isinstance(c, _Text)]

    def inline(node):
        if node is None:
            return False
        if isinstance(node, _Text):
            return True
        return layouts[id(node)][0] == "inline"

    kept = []
    for i, node in enumerate(raw):
        if not isinstance(node, _Text):
            continue
        if _is_ws(node.value):
            prev = raw[i - 1] if i > 0 else None
            nxt = raw[i + 1] if i + 1 < len(raw) else None
            if not (inline(prev) and inline(nxt)):
                continue
        kept.append(node)
    return kept


def _condense(raw: str):
    """crengine's non-pre text: ``(text, raw index of each kept char)``."""
    out = []
    starts = []
    in_space = False
    for index, ch in enumerate(raw):
        if ch in _XML_SPACE:
            if in_space:
                continue
            in_space = True
            out.append(" ")
        else:
            in_space = False
            out.append(ch)
        starts.append(index)
    return "".join(out), starts


def _raw_to_condensed(raw: str, offset: int) -> int:
    _text, starts = _condense(raw)
    count = 0
    for start in starts:
        if start < offset:
            count += 1
        else:
            break
    return count


def _condensed_to_raw(raw: str, offset: int) -> Optional[int]:
    text, starts = _condense(raw)
    if offset < 0 or offset > len(text):
        return None
    return len(raw) if offset == len(text) else starts[offset]


def _utf16(text: str, index: int) -> int:
    return index + sum(1 for ch in text[:index] if ord(ch) > 0xFFFF)


def _from_utf16(text: str, units: int) -> Optional[int]:
    count = 0
    for index, ch in enumerate(text):
        if count == units:
            return index
        count += 2 if ord(ch) > 0xFFFF else 1
        if count > units:
            return None  # inside a surrogate pair
    return len(text) if count == units else None


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------


def _browser_keeps_path(node) -> bool:
    """False when the browser's HTML parser would insert a wrapper on the path
    (``<table><tr>`` gains an implied ``<tbody>``), shifting its CFI steps."""
    while node is not None and node.getparent() is not None:
        parent = node.getparent()
        if _xhtml(node) and isinstance(parent.tag, str):
            name, parent_name = _local(node), _local(parent)
            if name == "tr" and parent_name not in ("tbody", "thead", "tfoot"):
                return False
            if name in ("td", "th") and parent_name != "tr":
                return False
        node = parent
    return True


@dataclass(frozen=True)
class _Pos:
    """A position in a chapter's raw DOM: a text node + raw code-point offset,
    or an element (``text`` None)."""
    element: object      # the text node's parent, or the addressed element
    text: Optional[_Text]
    offset: int          # code points into text.value (0 for an element)
    chapter: _Chapter


def _order(pos: _Pos):
    """Document-order key for positions within one chapter."""
    chain = []
    node = pos.element
    while node is not None and node.getparent() is not None:
        parent = node.getparent()
        chain.append(parent.index(node))
        node = parent
    chain.reverse()
    if pos.text is None:
        return tuple(chain) + (-1, -1)
    # Place the text node among its siblings: its ordinal-th text child.
    slot = 0
    seen = 0
    for i, child in enumerate(_raw_children(pos.element)):
        if isinstance(child, str):
            if seen == pos.text.ordinal:
                slot = i
                break
            seen += 1
    return tuple(chain) + (slot, pos.offset)


_XP_STEP = re.compile(r"/(text\(\)|[A-Za-z_][\w-]*)(?:\[(\d+)\])?")


def _parse_xpointer(xpointer):
    if not isinstance(xpointer, str) or not xpointer or len(xpointer) > MAX_POSITION_CHARS:
        return None
    match = re.fullmatch(r"((?:/(?:text\(\)|[A-Za-z_][\w-]*)(?:\[\d+\])?)+)(?:\.(\d+))?",
                         xpointer)
    if not match:
        return None
    steps = [(m.group(1), int(m.group(2)) if m.group(2) else None)
             for m in _XP_STEP.finditer(match.group(1))]
    offset = int(match.group(2)) if match.group(2) is not None else None
    return steps, offset


def _point_from_xpointer(book: _Book, xpointer):
    parsed = _parse_xpointer(xpointer)
    if parsed is None:
        return None
    steps, offset = parsed
    if len(steps) < 3 or steps[0][0] != "body" or steps[0][1] not in (None, 1):
        return None
    if steps[1][0] != "DocFragment" or steps[2][0] != "body" or steps[2][1] not in (None, 1):
        return None
    number = steps[1][1]
    if number is None:
        if book.fragment_total != 1:
            return None
        number = 1
    item = next((i for i in book.items if i.fragment == number), None)
    if item is None or not item.fragment_trusted:
        return None
    chapter = _chapter(book, item)
    if chapter is None:
        return None
    node = chapter.body
    rest = steps[3:]
    for position, (name, index) in enumerate(rest):
        if name == "text()":
            if position != len(rest) - 1 or offset is None:
                return None
            texts = _crengine_texts(chapter, node)
            if not texts:
                return None
            pick = (index or 1) - 1
            if pick >= len(texts) or index == 0:
                return None
            text = texts[pick]
            raw_offset = _condensed_to_raw(text.value, offset)
            if raw_offset is None:
                return None
            if chapter.browser is None and not _browser_keeps_path(node):
                return None
            return book, item, _Pos(node, text, raw_offset, chapter)
        same = [c for c in node if isinstance(c.tag, str) and _local(c) == name]
        if not same or index == 0 or (index or 1) > len(same):
            return None
        node = same[(index or 1) - 1]
        if not _xhtml(node) and not (_local(node) == "svg" and position == len(rest) - 1):
            return None
    if offset not in (None, 0) or node is chapter.body or not _browser_keeps_path(node):
        return None
    return book, item, _Pos(node, None, 0, chapter)


def _xpointer_string(book: _Book, item: _SpineItem, pos: _Pos) -> Optional[str]:
    chapter = pos.chapter
    parts = []
    node = pos.element
    while node is not chapter.body:
        parent = node.getparent()
        if parent is None:
            return None
        name = _local(node)
        if not _xhtml(node) and name != "svg":
            return None
        same = [c for c in parent if isinstance(c.tag, str) and _local(c) == name]
        step = "/" + name
        if len(same) > 1:
            step += f"[{same.index(node) + 1}]"
        parts.append(step)
        node = parent
    parts.reverse()
    fragment = f"/DocFragment[{item.fragment}]" if book.fragment_total > 1 else "/DocFragment"
    head = "/body" + fragment + "/body" + "".join(parts)
    if pos.text is None:
        return head + ".0"
    texts = _crengine_texts(chapter, pos.element)
    if not texts or pos.text not in texts:
        return None
    step = "/text()"
    if len(texts) > 1:
        step += f"[{texts.index(pos.text) + 1}]"
    return f"{head}{step}.{_raw_to_condensed(pos.text.value, pos.offset)}"


# --- The browser's tree -------------------------------------------------------


@dataclass
class _BrowserView:
    """The tree the browser's HTML parser builds for an ``.html`` chapter.

    epub.js reads ``.html`` members with the browser's HTML parser, and a
    self-closed non-void element (``<div/>``, ``<a id="x"/>``) is an OPEN tag
    there: it swallows what follows (``<a>`` is even re-opened around later
    text), while crengine reads the XML tree, where the element is empty. CFIs
    are therefore written in this tree and XPointers in the XML one, and a
    position crosses between them through its text node: the two trees' text
    nodes that are not whitespace-only must be the same strings in the same
    order, node for node, or the chapter is not modelled at all.
    """
    body: object
    to_xml: dict        # (browser parent, ordinal) -> (xml parent, ordinal)
    to_browser: dict    # (xml parent, ordinal) -> (browser parent, ordinal)


_TABLE_PARTS = frozenset("table thead tbody tfoot tr".split())
_TABLE_CONTENT = frozenset("caption colgroup col thead tbody tfoot tr td th script template style".split())


def _fosters(xml_body) -> bool:
    """True if the browser would move content out of a table (foster parenting).

    That reorders the tree -- and can reorder text so that equal strings trade
    places, which aligning text nodes by order cannot see -- so such an
    ``.html`` chapter is refused outright (``_chapter_refusal``)."""
    for el in xml_body.iter():
        if not isinstance(el.tag, str) or not _xhtml(el) or _local(el) not in _TABLE_PARTS:
            continue
        if not _is_ws(el.text or ""):
            return True
        for child in el:
            if not _is_ws(child.tail or ""):
                return True
            if isinstance(child.tag, str) and (not _xhtml(child)
                                               or _local(child) not in _TABLE_CONTENT):
                return True
    return False


def _solid_texts(body) -> list:
    """``(parent, ordinal, value)`` of every text node that is not whitespace-only."""
    out = []

    def walk(el):
        ordinal = 0
        for child in _raw_children(el):
            if isinstance(child, str):
                if not _is_ws(child):
                    out.append((el, ordinal, child))
                ordinal += 1
            elif isinstance(child.tag, str):
                walk(child)
    walk(body)
    return out


def _browser_view(text: str, xml_body) -> Optional[_BrowserView]:
    """The browser's tree of this chapter aligned to the XML one, or None."""
    try:
        import html5lib  # the WHATWG parsing algorithm, as browsers implement it
    except ImportError:
        return None
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # lxml's infoset coercion notices
            document = html5lib.parse(text, treebuilder="lxml")
    except Exception:
        log.debug("koreader_xpointer: html5lib could not parse a chapter", exc_info=True)
        return None
    body = document.getroot().find(f"{{{_XHTML_NS}}}body")
    if body is None:
        return None
    xml_texts, browser_texts = _solid_texts(xml_body), _solid_texts(body)
    if len(xml_texts) != len(browser_texts) or any(
            x[2] != b[2] for x, b in zip(xml_texts, browser_texts)):
        return None  # the browser dropped, merged or split text
    return _BrowserView(
        body,
        {(b[0], b[1]): (x[0], x[1]) for x, b in zip(xml_texts, browser_texts)},
        {(x[0], x[1]): (b[0], b[1]) for x, b in zip(xml_texts, browser_texts)},
    )


def _in_browser(pos: Optional[_Pos]) -> Optional[_Pos]:
    """``pos`` (in the XML tree) as a position in the tree the browser builds."""
    if pos is None:
        return None
    browser = pos.chapter.browser
    if browser is None:
        return pos
    if pos.text is None:
        return None
    mapped = browser.to_browser.get((pos.text.parent, pos.text.ordinal))
    if mapped is None:
        return None
    parent, ordinal = mapped
    return _Pos(parent, _Text(parent, ordinal, pos.text.value), pos.offset, pos.chapter)


# --- CFI --------------------------------------------------------------------


@dataclass(frozen=True)
class _ParsedCfi:
    base: list
    common: list
    start: list
    end: list
    is_range: bool


_CFI_STEP = re.compile(r"/(\d+)(?:\[([^\]]*)\])?(?::(\d+)(?:\[[^\]]*\])?)?")


def _parse_steps(component: str):
    steps = []
    position = 0
    while position < len(component):
        match = _CFI_STEP.match(component, position)
        if not match:
            return None
        steps.append((int(match.group(1)), match.group(2),
                      int(match.group(3)) if match.group(3) is not None else None))
        position = match.end()
    for number, _assertion, offset in steps[:-1]:
        if offset is not None:
            return None
    return steps


def _parse_cfi(cfi) -> Optional[_ParsedCfi]:
    if not isinstance(cfi, str) or len(cfi) > MAX_POSITION_CHARS:
        return None
    match = re.fullmatch(r"epubcfi\((.*)\)", cfi.strip())
    if not match:
        return None
    body = match.group(1)
    if body.count("!") != 1:
        return None
    base, path = body.split("!")
    parts = path.split(",")
    if len(parts) not in (1, 3):
        return None
    base_steps = _parse_steps(base)
    common = _parse_steps(parts[0]) if parts[0] not in ("", "/") else []
    if base_steps is None or common is None or len(base_steps) != 2:
        return None
    if len(parts) == 3:
        start, end = _parse_steps(parts[1]), _parse_steps(parts[2])
        if not start or not end:
            return None
        if common and common[-1][2] is not None:
            return None
        return _ParsedCfi(base_steps, common, start, end, True)
    if not common:
        return None
    return _ParsedCfi(base_steps, common, [], [], False)


def _point_from_cfi(book: _Book, base, steps):
    (spine_number, _a, spine_offset), (item_number, item_id, item_offset) = base
    if spine_number != book.spine_step or spine_offset is not None or item_offset is not None:
        return None
    if item_number % 2 or item_number < 2:
        return None
    index = item_number // 2 - 1
    if index >= len(book.items):
        return None
    item = book.items[index]
    if item_id is not None and item_id != (item.itemref_id or ""):
        return None
    if item.fragment is None or not item.fragment_trusted:
        return None
    chapter = _chapter(book, item)
    if chapter is None or not steps:
        return None
    first = steps[0]
    browser = chapter.browser
    body = browser.body if browser is not None else chapter.body
    body_id = body.get("id")
    if first[0] != 4 or first[2] is not None or (first[1] is not None and first[1] != body_id):
        return None
    node = body
    rest = steps[1:]
    for position, (number, assertion, offset) in enumerate(rest):
        last = position == len(rest) - 1
        if number % 2:
            if not last or offset is None:
                return None
            texts = [c for c in _raw_children(node) if isinstance(c, str)]
            k = (number - 1) // 2
            if k >= len(texts):
                return None
            value = texts[k]
            raw_offset = _from_utf16(value, offset)
            if raw_offset is None:
                return None
            if browser is not None:
                # The same text node in the tree crengine reads.
                mapped = browser.to_xml.get((node, k))
                if mapped is None:
                    return None
                node, k = mapped
            elif not _browser_keeps_path(node):
                return None
            text = _Text(node, k, value)
            kept = _crengine_texts(chapter, node)
            if kept is None:
                return None
            if text not in kept:
                # (A whitespace-only node crengine dropped is collapsed space
                # between blocks: there is no crengine position to give.)
                return None
            return book, item, _Pos(node, text, raw_offset, chapter)
        elements = [c for c in node if isinstance(c.tag, str)]
        k = number // 2 - 1
        if k >= len(elements):
            return None
        node = elements[k]
        if assertion is not None and assertion != (node.get("id") or ""):
            return None
        if offset is not None:
            return None
        if not _xhtml(node) and not (_local(node) == "svg" and last):
            return None
    if browser is not None:
        return None  # elements are not mapped between the two trees
    if node is chapter.body or not _browser_keeps_path(node):
        return None
    return book, item, _Pos(node, None, 0, chapter)


def _cfi_base(book: _Book, item: _SpineItem) -> str:
    step = f"/{book.spine_step}/{2 * (item.index + 1)}"
    if item.itemref_id:
        step += f"[{item.itemref_id}]"
    return step


def _cfi_steps(pos: Optional[_Pos]) -> Optional[list]:
    """epub.js steps from <body> (inclusive) down to the position's node."""
    if pos is None:
        return None
    steps = []
    node = pos.element
    chain = []
    while node is not None and node.getparent() is not None:
        chain.append(node)
        node = node.getparent()
    chain.reverse()  # body ... element
    for el in chain:
        parent = el.getparent()
        if el is pos.chapter.body or (pos.chapter.browser is not None
                                      and el is pos.chapter.browser.body):
            # The browser's HTML parser always gives <html> a <head> first.
            step = "/4"
        else:
            elements = [c for c in parent if isinstance(c.tag, str)]
            step = f"/{2 * (elements.index(el) + 1)}"
        el_id = el.get("id")
        if el_id:
            if not _CFI_ASSERTION_SAFE.fullmatch(el_id):
                return None
            step += f"[{el_id}]"
        steps.append(step)
    if pos.text is not None:
        steps.append(f"/{2 * pos.text.ordinal + 1}")
    return steps


def _cfi_terminal(pos: _Pos) -> str:
    if pos.text is None:
        return ""
    return f":{_utf16(pos.text.value, pos.offset)}"


