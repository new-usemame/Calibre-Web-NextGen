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

from . import assemble, extract, gate, unit_recovery

log = logging.getLogger(__name__)

CONVERTER = "Reflow"
CONVERTER_VERSION = "1.35"
REFLOW_NS = "https://calibre-web-nextgen.org/ns/reflow#"
SIDECAR_PATH = "META-INF/reflow.json"
OEBPS = "OEBPS"
ABOUT_HREF = "reflow-about.xhtml"
SOURCE_INDEX_HREF = "source-pages.xhtml"
SOURCE_CHECKS_HREF = "source-checks.xhtml"
FURNITURE_HREF = "printed-furniture.xhtml"

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
# Only the compiler's canonical bound-note DIV joins the note channel. Other
# native DIVs keep their prior body semantics; source validation happens before
# this publication classification.
_BOUND_NOTE_DIV = re.compile(
    r'^<div\b(?=[^>]*\sid="layout-p[0-9]+-[0-9a-f]{64}-note-a[0-9]+")'
    r'(?=[^>]*\sclass="footnote")(?=[^>]*\sepub:type="footnote")[^>]*>', re.I)
_SOURCE_NOTICE = re.compile(r'^<p\b[^>]*\bclass="[^"]*\bsource-evidence-notice\b', re.I)
_LEADING_NOTICE = re.compile(r'^<(?:p|div)\b[^>]*\bclass="[^"]*\bsource-evidence-notice\b', re.I)
_INLINE_PAGE_ID = re.compile(r'\bid="pg_(\d{4,})"')
_TAG = re.compile(r"<[^>]+>")
_HEADING_TEXT = re.compile(r"<h[1-6][^>]*>(.*?)</h[1-6]>", re.I | re.S)
_TRAILING_HYPHEN = re.compile(
    r"(\w)[" + assemble.HYPHENS + r"](\s*(?:</[A-Za-z0-9]+>\s*)*)$")
# Index entries end in page references after long dot leaders. A page turn can
# start another lowercase entry even though the preceding text has no sentence
# punctuation; that is a new row, not a continuation of prose.
_INDEX_LEADER_REF = re.compile(r"(?:\s*\.\s*){4,}\s*(?:[ivxlcdm]+\s*,\s*)?\d{1,4}\b", re.I)
_INDEX_END_REF = re.compile(r"(?:\d{1,4}|[ivxlcdm]+)\s*$", re.I)
_ID = re.compile(r'\sid="([^"]+)"')
_HREF = re.compile(r'href="#([^"]+)"')
_IMG_SRC = re.compile(r'<img\b[^>]*\bsrc="([^"]+)"[^>]*/?>', re.I)
_IMG_TAG = re.compile(r"<img\b[^>]*/?>", re.I)

STYLESHEET = """\
body { margin: 0 5%; line-height: 1.45; text-align: justify; }
h1, h2, h3, h4, h5, h6 { text-align: left; page-break-after: avoid; }
.source-heading-centered { text-align: center; }
p { margin: 0; text-indent: 1.2em; }
p.first, h1 + p, h2 + p, h3 + p, blockquote + p { text-indent: 0; }
blockquote { margin: 1em 2em; font-size: 0.95em; }
aside.footnote, div.footnote, aside.source-note-unbound { font-size: 0.85em; margin: 0.4em 0; }
.reflow-retained-furniture { margin: 1em 0; padding: 0.5em 0; font-size: 0.85em; text-align: left; border-top: 1px solid currentColor; border-bottom: 1px solid currentColor; }
.reflow-retained-furniture p { text-indent: 0; }
a.noteref { text-decoration: none; }
sup.noteref-unresolved { color: inherit; }
/* A word the scan damaged. The page's own reading is what is printed here; the
   likelier one is in the title, and every one of them is listed on the about
   page, because an e-reader has no hover. */
span.reflow-uncertain { border-bottom: 1px dotted currentColor; }
p.source-evidence-compact { text-indent: 0; margin: 0.35em 0; border-left: 0.12em solid currentColor; padding-left: 0.45em; }
figure { margin: 1em 0; text-align: center; page-break-inside: avoid; }
figcaption { font-size: 0.85em; text-align: center; }
img { max-width: 100%; }
a.source-word-image img { height:1em; width:auto; vertical-align:baseline; }
.source-list { list-style: none; padding-left: 1.5em; }
.source-list li { margin: 0.25em 0; }
.source-pages { list-style: none; padding: 0; text-align: left; }
.source-pages li { display: inline-block; width: 9em; }
.source-pages a { display: block; padding: 0.35em 0.25em; }
.source-evidence img { width: 100%; height: auto; }
"""
from .source_reading_display import glyph_stylesheet
STYLESHEET += glyph_stylesheet()+"\n"
from .glyph_presentation import stylesheet as glyph_presentation_stylesheet
STYLESHEET += glyph_presentation_stylesheet()
from .reading_tiers import stylesheet as reading_tiers_stylesheet
STYLESHEET += reading_tiers_stylesheet()
STYLESHEET += ".source-reading-annotation { font-size:1em; font-style:normal; border-bottom:1px dotted currentColor; }\n"
STYLESHEET += """\
.source-control, .source-reading-annotation a { display:inline-block; min-height:1.75em; padding:0.5em 0.7em; line-height:1.5; text-indent:0; text-align:left; border:1px solid currentColor; margin:0.25em 0; }
.source-inspection-controls { text-align:left; margin:0.5em 0; }
.source-inspection-controls ol { padding-left:1.5em; }
.source-inspection-controls a { display:block; }
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

def _caption_keys(elements):
    """Stable source-crop identities shared by canonical markup and packaging."""
    keys, counts, figure = {}, {}, -1
    for index, element in enumerate(elements):
        if element.kind == "fig":
            figure += 1
        elif element.kind == "caption":
            ordinal = counts.get(figure, 0)
            counts[figure] = ordinal + 1
            if element.caption_uncertain:
                base = "caption_%d" % figure if figure >= 0 else "caption_orphan"
                keys[index] = base + ("_%d" % ordinal if ordinal else "")
    return keys


def _source_caption(pno, key, *, within_figure=False):
    image = ('' if within_figure else
             '<img src="images/original_p%04d_%s.jpg" alt="Original printed caption"/><br/>' % (pno, key))
    placement = 'in the figure above' if within_figure else 'as an image'
    return ('<span class="reflow-uncertain">%s<span class="source-reading-annotation">Caption transcription uncertain; '
            'the original printed caption is shown %s, without searchable text. '
            '<a href="original-p%04d.xhtml#%s">Inspect original printed caption</a>.</span></span>'
            % (image, placement, pno, key))


def page_fragment(book, pno, style=None, wrappers=None, element_blocks=None):
    """One page as it was printed: the unit the model edits and the gate measures."""
    with unit_recovery.guard(pno, 'native_page_fragment'):
        return _page_fragment(book, pno, style, wrappers, element_blocks)


def _page_fragment(book, pno, style=None, wrappers=None, element_blocks=None, source_units=None):
    """One page as it was printed: the unit the model edits and the gate measures."""
    elements = list(book.pages.get(pno) or [])
    notes = [n for n in book.notes if n.pno == pno]
    ambiguous = book.ambiguous_note_numbers(pno)
    available = {str(n.num) for n in notes if n.num is not None} - ambiguous
    ref_ids = {}
    blocks = []
    body_returns = []
    figure_index = 0
    caption_keys = _caption_keys(elements)
    note_context_figures = assemble.source_note_context_figures(book, pno)
    navigation = [link for link in book.source_navigation
                  if link['status'] == 'resolved' and
                  (link['pno'] == pno or link['dest_page'] == pno)]

    index = 0
    while index < len(elements):
        element_index = index
        element = elements[index]
        index += 1
        with unit_recovery.guard(pno, "native_element_markup", block="native-element-" + str(element_index)):
            replacement = (source_units or {}).get("native-element-" + str(element_index))
            if replacement is not None:
                fragment, asides, _ = replacement
                if fragment:blocks.append(fragment)
                blocks.extend(asides)
                if element.kind == "fig":figure_index += 1
                continue
            if element.kind == "fig":
                if element_blocks is not None:
                    element_blocks[element_index] = len(blocks)
                caption = ""
                source_caption = ""
                caption_index = None
                if (index < len(elements) and elements[index].kind == "caption" and
                        "native-element-" + str(index) not in (source_units or {})):
                    with unit_recovery.guard(pno, "native_caption_markup", block="native-element-" + str(index)):
                        caption_index = index
                        caption = _nav_runs_html(elements[index].runs,
                            _source_nav_marks(navigation,pno,index,None),
                            available, ref_ids, ambiguous)
                        source_caption = caption
                        if elements[index].caption_uncertain:
                            caption_box = elements[index].bbox
                            figure_box = element.bbox
                            within_figure = bool(figure_box and caption_box and
                                figure_box[0] <= caption_box[0] and figure_box[1] <= caption_box[1]
                                and figure_box[2] >= caption_box[2] and figure_box[3] >= caption_box[3])
                            caption = _source_caption(pno, caption_keys[index], within_figure=within_figure)
                            source_caption = caption
                        index += 1
                figures = [f for f in book.figures if f["pno"] == pno]
                reason = figures[figure_index].get("found") if figure_index < len(figures) else ""
                if caption and figure_index < len(figures):
                    from .visual_coverage import VERSION as coverage_version, REGION_VERSION
                    if figures[figure_index].get('visual_evidence', {}).get('version') in (coverage_version, REGION_VERSION):
                        # Coverage preserves adjacent source captions as well as pixels.
                        source_caption = caption
                if source_caption and reason in ('sparse_scan_spread_panel',
                        'unrecovered_scan_layer', 'unverified_paired_columns'):
                    caption_box = elements[index-1].bbox
                    figure_box = element.bbox
                    if (figure_box and caption_box and
                            figure_box[0] <= caption_box[0] and figure_box[1] <= caption_box[1]
                            and figure_box[2] >= caption_box[2] and figure_box[3] >= caption_box[3]):
                        source_caption = ''
                source_region = reason in ("source_furniture_context", "ocr_uncertain_note", "source_scan_grid", "source_visual_table", "ocr_uncertain_region", "native_outline_conflict", "native_spacing_uncertain", "unverified_scan_layout", "unrecovered_scan_layer", "unverified_paired_columns", "embedded_source_mark", "source_figure_title", "uncertain_aligned_scan_list", "sparse_scan_spread_panel", "uncertain_scan_key_panel")
                if reason in ("source_visual_table", "source_scan_grid"):
                    caption = ('Original table · Cell text and associations are retained together '
                               'as source pixels; no searchable table transcription is claimed. '
                               '<a href="original-p%04d.xhtml#figure_%d">View larger</a>' % (pno, figure_index))
                elif reason == "source_furniture_context":
                    caption = ('Original printed page furniture. '
                               '<a href="original-p%04d.xhtml#page">View original page</a>' % pno)
                elif reason == "ocr_uncertain_note":
                    caption = ('Original printed note. Its damaged OCR reading is retained as pixels, '
                               'without inferred note digits or searchable text. '
                               '<a href="original-p%04d.xhtml#page">Open original page and enlarged details</a>.' % pno)
                elif reason == "native_outline_conflict":
                    caption = ('Native heading text conflicts with PDF navigation metadata. '
                               'The original printed heading is shown as an image; no replacement '
                               'transcription was inferred. '
                               '<a href="original-p%04d.xhtml#page">Open original page and enlarged details</a>.' % pno)
                elif reason == "unverified_paired_columns":
                    caption = ('Original column relationships are preserved as a source image. '
                               'The unverified scan transcript cannot establish a linear reading order. '
                               '<a href="original-p%04d.xhtml#page">Open original page and enlarged details</a>.' % pno)
                elif reason == "unrecovered_scan_layer":
                    caption = ('Original page image. The hidden text layer could not establish a '
                               'reliable transcription; no searchable text is claimed. '
                               '<a href="original-p%04d.xhtml#page">Open original page and enlarged details</a>.' % pno)
                elif reason == "unverified_scan_layout":
                    caption = ('Unverified scan transcription cannot establish these symbols or their layout. '
                               'The original region is shown as an image, without inferred or searchable text. '
                               '<a href="original-p%04d.xhtml#page">Open original page and enlarged details</a>.' % pno)
                elif reason == "native_spacing_uncertain":
                    caption = ('Character spacing is uncertain. The original printed text region is '
                               'shown as an image, without inferred or searchable text. '
                               '<a href="original-p%04d.xhtml#page">Open original page and enlarged details</a>.' % pno)
                elif reason == "source_figure_title":
                    caption = ('Original figure and printed title. '
                               '<a href="original-p%04d.xhtml#figure_%d">View larger</a>' % (pno, figure_index))
                elif reason == "embedded_source_mark":
                    caption = ('Printed source lettering is preserved as an image, '
                               'without inferred or searchable text. '
                               '<a href="original-p%04d.xhtml#page">Open original page and enlarged details</a>.' % pno)
                elif reason == "uncertain_aligned_scan_list":
                    caption = ('Complete printed list. The OCR labels and row boundaries are unverified; '
                               'the label and value rows are preserved together as pixels. '
                               '<a href="original-p%04d.xhtml#page">Open original page and enlarged details</a>.' % pno)
                elif reason == "sparse_scan_spread_panel":
                    caption = ('Complete printed panel. OCR cannot establish every printed cell '
                               'or its reading order; use these source pixels. '
                               '<a href="original-p%04d.xhtml#page">Open original spread and enlarged details</a>.' % pno)
                elif reason == "uncertain_scan_key_panel":
                    caption = ('Printed symbol key retained from the original page. OCR cannot verify '
                               'the symbols or their associations with adjacent labels; read the '
                               'aligned source rows in this image. It is not searchable text. '
                               '<a href="original-p%04d.xhtml#page">Open original page and enlarged details</a>.' % pno)
                elif source_region:
                    caption = ('Source image · <a href="source-checks.xhtml">'
                               'OCR uncertain</a> · <a href="original-p%04d.xhtml#page">'
                               'View larger</a>' % pno)
                if source_region:
                    # Generated figure/table disclosures obey the same paragraph
                    # boundary rule as prose warnings; printed captions stay here.
                    caption = '<span class="source-reading-annotation">'+caption+'</span>'
                if source_caption and source_region:
                    caption = source_caption + ' ' + caption
                figure = figures[figure_index] if figure_index < len(figures) else {}
                if (reason in ('ocr_uncertain_region', 'native_spacing_uncertain') and not figure.get('full_page')
                        and not figure.get('visual_evidence') and not source_caption
                        and not (index > element_index + 1)):
                    # This is a display projection of an existing source crop, not
                    # a paragraph decision or a transcription. The model keeps the
                    # complete pixel atom and chooses its role against the raster.
                    image = _figure_html(pno, figure_index, '', source_region=True)
                    image = re.search(r'<img\b[^>]*>', image).group(0)
                    image = '<p><span class="source-raster">' + image + '</span></p>'
                    if element_index in note_context_figures:
                        image = '<aside class="source-note-context">' + image + '</aside>'
                    blocks.append(image)
                    blocks.append('<p class="source-evidence-notice">' + caption + '</p>')
                else:
                    image = _figure_html(pno, figure_index, caption, source_region=source_region)
                    if element_index in note_context_figures:
                        image = '<aside class="source-note-context">' + image + '</aside>'
                    blocks.append(image)
                if caption_index is not None:
                    controls = _native_return_controls(navigation,pno,element_index=caption_index)
                    if controls:body_returns.append(controls)
                figure_index += 1
                continue
            if wrappers and element_index in wrappers:
                from .structural_ops import render_element
                blocks.append(render_element(element, wrappers[element_index],
                    lambda runs: _runs_html(runs, available, ref_ids, ambiguous)))
                if element.punctuation_uncertain:
                    blocks.append(_punctuation_notice(pno, element_index))
                continue
            marks = _source_nav_marks(navigation, pno, element_index, None)
            # Structured branches below emit their own original semantic runs.
            # Do not allocate note-reference occurrences in a discarded rendering:
            # a note's Return must identify an origin that is actually published.
            structured = element.kind == "list" or (element.kind == "h" and
                getattr(element, 'display_lines', []))
            inner = (element.text if structured else
                _nav_runs_html(element.runs, marks, available, ref_ids, ambiguous))
            if not inner.strip():
                continue
            if element_blocks is not None:
                element_blocks[element_index] = len(blocks)
            if element.kind == "list":
                from .assemble import plain_text
                items=getattr(element,'list_items',[])
                if not items or ' '.join(plain_text(r).strip() for r in items)!=element.text.strip():
                    raise ValueError('Source list items differ from source inventory')
                blocks.append('<ol class="source-list">%s</ol>' % ''.join(
                    '<li>%s</li>' % _nav_runs_html(r,
                        _source_nav_marks(navigation,pno,element_index,ii),
                        available,ref_ids,ambiguous) for ii,r in enumerate(items)))
            elif element.kind == "h":
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
                    inner = _source_caption(pno, caption_keys[element_index])
                blocks.append('<p class="caption">%s</p>' % inner)
            else:
                blocks.append("<p>%s</p>" % inner)
            controls = _native_return_controls(navigation,pno,element_index=element_index)
            if controls:body_returns.append(controls)
            if element.punctuation_uncertain:
                blocks.append(_punctuation_notice(pno, element_index))

    if body_returns:
        blocks.append('<aside class="source-native-returns">'+''.join(body_returns)+'</aside>')

    for note_index, note in enumerate(notes):
        with unit_recovery.guard(pno, "native_note_markup", block="native-note-" + str(note_index)):
            replacement = (source_units or {}).get("native-note-" + str(note_index))
            if replacement is not None:
                _, asides, _ = replacement
                # Preserve only the native target and backlink already proved by
                # this actual note and its emitted reference. No owner is guessed.
                for aside in asides:
                    number = str(note.num)
                    if note.num is not None and number in available:
                        aside = aside.replace('<p>', '<p><span id="fn_' + escape(number) + '"></span>', 1)
                        if number in ref_ids:
                            aside = aside.replace('</aside>', '<a href="#' + ref_ids[number] + '">Return</a></aside>')
                    blocks.append(aside)
                continue
            aside = _aside_html(note, ref_ids, available, str(note.num) in ambiguous,
                [n for n in book.notes if getattr(n, "continued_from", None) == (pno, note.num)])
            arrivals = ''.join('<span id="%s"></span>' % link['id']
                               for link in navigation if link['dest_page'] == pno
                               and link.get('dest_note') == note_index)
            if arrivals:
                aside = aside.replace('<p>', '<p>' + arrivals, 1)
                controls = _native_return_controls(navigation,pno,note_index=note_index)
                aside = aside.replace('</aside>',controls+'</aside>')
            blocks.append(aside)
    if any(r[0]=="glyph" for el in elements for r in el.runs) or any(getattr(n,"glyph_fallback",False) for n in notes):
        blocks.append(_source_check_notice('Words/glyphs uncertain',
            'original-p%04d.xhtml#page' % pno, 'View original page'))
    if any(link['pno'] == pno and link['kind'] == 1 and link['status'] != 'resolved'
           for link in book.source_navigation):
        blocks.append(_source_check_notice('PDF link unresolved',
            'original-p%04d.xhtml#page' % pno, 'View original page'))
    return "\n".join(blocks)


def native_unit_registry(book, pno):
    """Name the actual native source objects in their preserved page order."""
    roles = {"p": "body", "h": "heading", "fig": "figure", "caption": "caption"}
    registry = {"native-element-" + str(index): dict(page=pno, index=index,
                    role=roles.get(element.kind, "other"), bbox=element.bbox)
                for index, element in enumerate(book.pages.get(pno) or ())}
    registry.update({"native-note-" + str(index): dict(page=pno, note_index=index,
                        role="footnote", bbox=note.bbox)
                     for index, note in enumerate(n for n in book.notes if n.pno == pno)})
    return registry


def recovery_page_fragment(book, pno, display, package, image_records, style=None):
    """Render healthy native objects and replace only named failed objects.

    Returns the complete page fragment and the finite degraded-unit reports.
    Registered native-N seams are handled after fresh fragment alignment by
    :func:`degrade_native_units`, rather than by parsing their numeric suffixes.
    """
    controller = unit_recovery.current()
    if controller is None:return page_fragment(book, pno, style), []
    controller.check_units([dict(page=pno)])
    from .unit_source import source_unit
    registry = native_unit_registry(book, pno)
    replacements = {}
    for bid, case in controller.blocked(pno).items():
        owned = registry.get(bid)
        if owned is None:continue  # A registered native-N fragment is applied later.
        fragment, asides, report = source_unit(display, pno, bid, owned['role'],
                                               package, image_records, case['reason'])
        report['stage'] = case['stage'];case.update(report)
        replacements[bid] = fragment, asides, report
    with unit_recovery.guard(pno, "native_page_fragment"):
        fragment = _page_fragment(book, pno, style, source_units=replacements)
    return fragment, [value[2] for value in replacements.values()]


def degrade_native_units(page, report, blocked, display, package, image_records):
    """Replace exact freshly registered native fragments; keep others unchanged.

    The caller supplies fresh _native_seam_blocks alignment for this page. Each
    record corresponds to one body fragment in order; no guessed ID suffix or
    substring is used to choose a replacement. Source fallback is a seam barrier.
    """
    alignment = report['blocks']
    if len(alignment) != len(page['body']):
        raise ValueError('Native fragment/source alignment differs')
    ids = [record['id'] for record in alignment]
    if len(set(ids)) != len(ids):
        raise ValueError('Native fragment identities are ambiguous')
    from .unit_source import source_unit
    report.setdefault('degraded_units', [])
    for index, record in enumerate(list(alignment)):
        case = blocked.get(record['id'])
        if case is None:continue
        # native-N is a body ordinal, so earlier unit replacements may shift it.
        # A retained exact-fragment proof prevents deleting a healthy successor.
        import hashlib
        signature = hashlib.sha256(page['body'][index].encode('utf-8')).hexdigest()
        if (not case.get('fragment_sha256') or
                case['fragment_sha256'] != record.get('fragment_sha256') or
                case['fragment_sha256'] != signature):
            raise ValueError('Native source fragment changed after unit failure')
        if not record.get('native_source') or record.get('degraded_source_context'):
            raise ValueError('Unregistered native source fragment')
        role = record['role'] if record['role'] in ('body','other','heading','caption','figure','table') else 'other'
        fragment, asides, unit = source_unit(display, page['pno'], record['id'], role,
                                            package, image_records, case['reason'])
        unit['stage'] = case['stage'];case.update(unit)
        # Transcription rejection does not revoke a separately admitted printed
        # note action. Keep its exact number, origin and uniquely present note
        # target beside the retained paragraph's inspection control. Never infer
        # a note association from a number or manufacture a missing destination.
        actions = []
        current = page['body'][index]
        publication = '\n'.join(page['body'] + page['asides'])
        for action in re.findall(r'<a\b[^>]*>.*?</a>', current, re.S):
            node = ElementTree.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">' + action + '</root>')[0]
            origin, target = node.get('id'), node.get('href', '')
            if ('noteref' in node.get('class', '').split() and
                    node.get('{http://www.idpf.org/2007/ops}type') == 'noteref' and
                    origin and target.startswith('#') and len(target) > 1 and
                    _ID.findall(publication).count(origin) == 1 and
                    _ID.findall(publication).count(target[1:]) == 1):
                actions.append(action)
        if actions:
            fragment = fragment.replace('</p>', ' ' + ' '.join(actions) + '</p>', 1)
        page['body'][index] = fragment
        page['asides'].extend(asides)
        alignment[index] = dict(unit['blocks'][0], native_source=True)
        report['degraded_units'].append(unit)
    return page, report


def _source_nav_marks(navigation, pno, element_index, item_index):
    marks=[]
    for link in navigation:
        if link['pno'] == pno and link['source_element'] == element_index \
                and link['source_item'] == item_index:
            marks.append((link['source_offset'], 'anchor', _pdf_reference_id(link['id'])))
            marks.append((link['source_offset'], 'open', link['id']))
            marks.append((link['source_offset']+link['source_extent'], 'close', link['id']))
        if link['dest_page'] == pno and link['dest_element'] == element_index \
                and link['dest_item'] == item_index:
            marks.append((link['dest_offset'], 'anchor', link['id']))
            marks.append((link['dest_offset'], 'return', link['id']))
    return marks


def _native_return_controls(navigation,pno,*,element_index=None,note_index=None):
    """Visible generated actions stay outside source prose and note text."""
    ids=[]
    for link in navigation:
        if link['dest_page']!=pno:continue
        owned=(link.get('dest_note')==note_index if note_index is not None else
               link.get('dest_note') is None and link['dest_element']==element_index)
        if owned and link['id'] not in ids:ids.append(link['id'])
    return ('<p class="source-evidence-notice">'+ ' '.join(_pdf_return(ident) for ident in ids)+'</p>'
            if ids else '')


def _pdf_reference_id(ident):
    return 'pdfref_' + ident.removeprefix('pdfgoto_')


def _pdf_return(ident):
    return ('<a class="pdf-return" href="#%s" aria-label="Return to PDF reference" '
            'title="Return to PDF reference">Return</a>' % _pdf_reference_id(ident))


def _nav_runs_html(runs, marks, available, ref_ids, ambiguous):
    if not marks:
        return _runs_html(runs, available, ref_ids, ambiguous)
    # Navigation is bound against Element.text (plain_text), while the renderer
    # retains the original run whitespace. Translate those coordinates instead
    # of applying normalized offsets to a different character stream.
    values = [run[1] if run[0] in ('t', 'raised', 'glyph') else '[%s]' % run[1]
              for run in runs]
    raw = ''.join(values)
    characters = [((' ' if len(m.group()) > 1 else m.group()), m.start(), m.end())
                  for m in re.finditer(r'[^\S\n]{2,}|[\s\S]', raw)]
    while characters and characters[0][0].isspace():characters.pop(0)
    while characters and characters[-1][0].isspace():characters.pop()
    atomic = []
    position = 0
    for run, value in zip(runs, values):
        if run[0] != 't':atomic.append((position, position + len(value)))
        position += len(value)

    events={}
    for offset, kind, ident in marks:
        if not 0 <= offset <= len(characters):
            raise ValueError('PDF navigation offset beyond source text')
        if kind == 'close' and offset:
            mapped = characters[offset - 1][2]
        else:
            mapped = characters[offset][1] if offset < len(characters) else (
                characters[-1][2] if characters else 0)
        # Markers and source glyphs are indivisible. A target inside one lands
        # immediately before it; an authored link covers the whole atom.
        for start, end in atomic:
            if start < mapped < end:
                mapped = end if kind == 'close' else start
                break
        events.setdefault(mapped, []).append((kind, ident))
    output=[];position=0;active=None;opened=None

    def linked(ident):
        nonlocal opened
        if opened == ident:return
        if opened is not None:output.append('</a>')
        if ident is not None:output.append('<a href="#%s">' % ident)
        opened = ident

    def emit(offset):
        nonlocal active
        for kind, ident in sorted(events.get(offset, []),
                                  key=lambda row: {'close':0,'anchor':1,'return':2,'open':3}[row[0]]):
            if kind == 'close':
                if active != ident:raise ValueError('Unbalanced PDF navigation range')
                active = None
            elif kind == 'return':
                # The precise target anchor stays here; the generated action
                # follows the complete source block in page_fragment.
                pass
            elif kind == 'anchor':
                linked(None)
                output.append('<span id="%s"></span>' % ident)
            else:
                if active is not None:raise ValueError('Overlapping PDF navigation ranges')
                active = ident
        if opened != active:linked(None)
    emit(0)
    for run in runs:
        size=(len(run[1]) if run[0] in ('t','raised','glyph')
              else len('[%s]' % run[1]))
        if not size:continue
        if run[0] != 't':
            note_link = run[0] not in ('t', 'raised', 'glyph') and str(run[1]) in available
            owns_link = note_link or run[0] == 'glyph'
            linked(None if owns_link else active)
            output.append(_runs_html([run],available,ref_ids,ambiguous))
            if owns_link and active is not None:
                # The note/source image and PDF viewport are distinct actions.
                # Keep the source atom once, with an adjacent named control for
                # its PDF destination instead of nesting two anchors.
                label = ('PDF destination for note %s' % str(run[1]) if note_link
                         else 'PDF destination for source text')
                output.append('<a class="source-navigation" href="#%s" '
                              'aria-label="%s">↗</a>' % (active, escape(label)))
            position+=size;emit(position)
            continue
        run_start=position
        starts=[run_start]+sorted(p for p in events if run_start<p<run_start+size)+[run_start+size]
        for a,b in zip(starts,starts[1:]):
            part=list(run);part[1]=run[1][a-run_start:b-run_start]
            linked(active)
            output.append(_runs_html([part],available,ref_ids,ambiguous))
            position=b;emit(position)
    if any(offset>position for offset in events):
        raise ValueError('PDF navigation offset beyond source text: %r, length %d, %r' %
                         (marks,position,assemble.plain_text(runs)[:100]))
    if active is not None:raise ValueError('Unclosed PDF navigation range')
    linked(None)
    return ''.join(output)


def _punctuation_notice(pno, index):
    return _source_check_notice('Punctuation uncertain',
        'original-p%04d.xhtml#text_%d' % (pno, index),
        'View original passage', uncertain=True)


def _source_check_notice(label, source_href, source_label, uncertain=False):
    classes = 'source-evidence-notice' + (' reflow-uncertain' if uncertain else '')
    return ('<p class="%s source-evidence-compact">'
            '<a href="%s">%s</a> · '
            '<a href="%s">%s</a></p>' %
            (classes, SOURCE_CHECKS_HREF, label, source_href, source_label))


def _runs_html(runs, available, ref_ids, ambiguous=()):
    parts = []
    for run in runs:
        if run[0] == "glyph":
            from .native_text import glyph_html
            parts.append(glyph_html(run[2]))
            continue
        if run[0] == "raised":
            parts.append("<sup>%s</sup>" % escape(run[1]))
            continue
        if run[0] == "t":
            text = escape(run[1])
            style = run[2] if len(run) > 2 else None
            if style == "italic":
                text = "<em>%s</em>" % text
            elif style == "bold":
                text = "<strong>%s</strong>" % text
            elif style == "bolditalic":
                text = "<strong><em>%s</em></strong>" % text
            parts.append(text)
            continue
        number = str(run[1])
        # A marker established by a repair over a scan is an uncertain reading:
        # the number the layer gave, kept and marked, never an authoritative one.
        uncertain = number in ambiguous or (len(run) > 3 and run[3] == "uncertain")
        if number in available:
            ref = "fnref_%s" % number
            count_key = ('occurrences', number)
            count = ref_ids.get(count_key, 0) + 1
            ref_ids[count_key] = count
            if count > 1:
                ref = "%s_%d" % (ref, count)
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
                # reading_flow.finish defers converter copy while preserving
                # the source mark and its uncertainty metadata in place.
                parts.append('<sup class="noteref-unresolved"><span class="reflow-uncertain" '
                             'title="number or association read from a damaged text layer">%s'
                             '<span class="source-reading-annotation">Uncertain source mark %s (?); '
                             'number or association read from a damaged text layer.</span>'
                             '</span></sup>' % (escape(number), escape(number)))
                continue
            parts.append('<sup class="noteref-unresolved">%s</sup>' % escape(number))
    return "".join(parts)


def _aside_html(note, ref_ids, available, ambiguous=False, continuations=()):
    body = escape(note.text)
    if note.uses_source_image:
        from .native_text import glyph_html, note_descriptor
        body = (_runs_html(note.glyph_runs, set(), {}) if getattr(note,"glyph_runs",None)
                else glyph_html(note_descriptor(note),block=True))
    if note.num is None:
        origin = getattr(note, "continued_from", None)
        if origin is not None:
            return ('<aside class="footnote" epub:type="footnote" id="note_tail_p%d">'
                    '<p><a href="#fn_p%04d_%d">Note %d, continued</a>: %s</p></aside>'
                    % (note.pno, origin[0], origin[1], origin[1], body))
        return '<aside class="footnote" epub:type="footnote"><p>%s</p></aside>' % body
    number = str(note.num)
    label = escape(number)
    if ambiguous or getattr(note, "uncertain", False):
        label = ('<span class="reflow-uncertain" title="number read from a '
                 'damaged text layer">%s (?)</span>' % label)
    if number in ref_ids:
        label = '<a class="note-return" href="#%s" aria-label="Return to note reference">%s</a>' % (ref_ids[number], label)
    links = ''.join(' <a href="#note_tail_p%d">Continued on PDF page %d</a>.'
                    % (n.pno, n.pno+1) for n in continuations)
    return ('<aside class="footnote" epub:type="footnote" id="fn_%s">'
            '<p>%s %s%s</p></aside>'
            % (number, label, body, links))


def _figure_html(pno, index, caption, source_region=False):
    src = "images/fig_p%04d_%d.jpg" % (pno, index)
    # SPEC §3: a figure always carries a figcaption, empty when the page printed no
    # caption, so "no caption found" is stated rather than left to be inferred.
    alt = ("Original source text region; see the source-image disclosure" if source_region else
           "" if caption else "Original figure from PDF page %d" % (pno + 1))
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


# These exact factory shapes are generated controls, never source words. They
# must not decide source paragraph continuation, headings or chapter titles.
_READING_COPY = re.compile(
    r'<span class="source-reading-annotation" id="reading-return-reading-[a-f0-9]{64}">.*?</span>'
    r'|<a class="pdf-return" href="#pdfref_p\d{4}_x\d+" aria-label="Return to PDF reference" '
    r'title="Return to PDF reference">Return</a>', re.S)


def _reading_copy_parts(fragment):
    parts=[];clean=[];cursor=0;length=0
    for match in _READING_COPY.finditer(fragment):
        clean.append(fragment[cursor:match.start()]);length+=match.start()-cursor
        parts.append((length,match.group(0)));cursor=match.end()
    clean.append(fragment[cursor:])
    return ''.join(clean),parts


def _merge_without_reading_copy(merger,left,right,marker,*args):
    """Existing source-only merge, then restore generated copy by byte offsets.

    The merger only concatenates inner trees and optionally consumes the known
    trailing hyphen. Whole prefix/suffix equality proves both offset domains;
    repeated strings never choose a location.
    """
    from .structural_ops import ContractError
    clean_left,left_marks=_reading_copy_parts(left)
    clean_right,right_marks=_reading_copy_parts(right)
    result=merger(clean_left,clean_right,marker,*args)
    opening=_open_tag(clean_left)
    raw_tail=_inner(clean_left);tail=raw_tail.rstrip()
    raw_head=_inner(clean_right);head=raw_head.lstrip()
    head_removed=()
    closing='</'+re.match(r'<([A-Za-z0-9]+)',opening).group(1)+'>'
    if not result.endswith(head+closing):
        transformed,positions=_word_head_without_space(head)
        if positions and result.endswith(transformed+closing):
            head,head_removed=transformed,positions
    if not result.startswith(opening) or not result.endswith(head+closing):
        raise ContractError('source merge frame differs while placing reading copy')
    tail_start=len(opening);head_start=len(result)-len(closing)-len(head)
    removed=None
    if not result[tail_start:].startswith(tail):
        match=_TRAILING_HYPHEN.search(tail)
        if match is None:raise ContractError('source merge changed reading-copy coordinates')
        removed=match.end(1)
        transformed=_TRAILING_HYPHEN.sub(r"\1\2",tail)
        if not result[tail_start:].startswith(transformed):
            raise ContractError('source merge changed more than the admitted hyphen')
    placed=[]
    for offset,copy in left_marks:
        local=min(max(0,offset-len(opening)),len(tail))
        if removed is not None and local>removed:local-=1
        placed.append((tail_start+local,copy))
    trim=len(raw_head)-len(raw_head.lstrip())
    for offset,copy in right_marks:
        local=max(0,offset-len(_open_tag(clean_right))-trim)
        # Inner prefix removals have coordinates in the outer-trimmed clean
        # head, not in its transformed markup or the generated annotation.
        local-=sum(position<local for position in head_removed)
        placed.append((head_start+local,copy))
    for _,(offset,copy) in sorted(enumerate(placed),key=lambda x:(x[1][0],x[0]),reverse=True):
        result=result[:offset]+copy+result[offset:]
    return result


def block_text(block):
    return re.sub(r"\s+", " ", _TAG.sub(" ", _READING_COPY.sub("", block or ""))).strip()


def _is_paragraph(block):
    return bool(_PARAGRAPH.match(block.strip()))


def _is_aside(block):
    return bool(_ASIDE.match(block.strip()) or _BOUND_NOTE_DIV.match(block.strip()))


def _inner(block):
    opening = block.find(">")
    closing = block.rfind("</")
    if opening < 0 or closing < opening:
        return block
    return block[opening + 1:closing]


def _open_tag(block):
    return block[:block.find(">") + 1]


def _word_head_without_space(head):
    """Trim a word seam through transparent markup, preserving its exact tree.

    Only source text whitespace before the first letter is removable. A visible
    prefix or an unknown tag leaves the whole head intact; entity whitespace is
    conservatively retained. The original indices also locate generated copies.
    """
    cursor=0;removed=[]
    while cursor<len(head):
        if head[cursor] in ' \t\r\n':
            removed.append(cursor);cursor+=1;continue
        if head[cursor]=='<':
            tag=_TAG.match(head,cursor)
            name=re.match(r'</?([A-Za-z0-9]+)(?:\s|/?>)',tag[0]) if tag else None
            if not name or name[1].lower() not in ('span','em','strong','b','i','u','a'):
                return head,()
            cursor=tag.end();continue
        if not head[cursor].isalpha():return head,()
        indices=set(removed)
        return ''.join(char for n,char in enumerate(head) if n not in indices),tuple(removed)
    return head,()


def merge_paragraphs(left, right, page_marker="", words=None, compounds=()):
    """Join two paragraphs the way the typesetter's page turn joined them."""
    if _READING_COPY.search(left+right):
        return _merge_without_reading_copy(merge_paragraphs,left,right,page_marker,words,compounds)
    head = _inner(right).lstrip()
    tail = _inner(left).rstrip()
    plain = block_text(tail)
    if _TRAILING_HYPHEN.search(tail) and block_text(head)[:1].islower():
        head,_ = _word_head_without_space(head)
        prefix = re.search(r"([\w'’]+)[" + assemble.HYPHENS + r"]$", plain)
        following = re.match(r"[A-Za-z'’]+", block_text(head))
        earned = words is None or (prefix and following and
            (prefix[1]+following[0]).casefold() in words and
            (prefix[1]+'-'+following[0]).casefold() not in compounds)
        if earned:
            tail = _TRAILING_HYPHEN.sub(r"\1\2", tail)
        glue = ""
    elif _TRAILING_HYPHEN.search(tail) or assemble.ENDDASH.search(plain):
        if _TRAILING_HYPHEN.search(tail):head,_ = _word_head_without_space(head)
        glue = ""
    else:
        glue = " "
    return "%s%s%s%s%s</p>" % (_open_tag(left), tail, glue, page_marker, head)


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
    """Normalize deterministic and model note markup to the reader contract.

    Every note receives one stable target and references use the same identity.
    Repeated printed numbers on different pages remain distinct notes."""
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
    """Escape raw markup text without changing its visible words.

    Bare ampersands in a model fragment must not make the resulting XHTML
    unreadable. Preserve already valid entity spellings."""
    return _LOOSE_ANGLE.sub("&lt;",
                            _LOOSE_AMPERSAND.sub("&amp;", _named_entities(html)))


def _scope_ids(html, pno):
    """Note numbers repeat from page to page; ids in one book may not."""
    prefix = "p%04d_" % pno
    html = re.sub(r'(\sid=")(fn_|fnref_)', r"\1\2%s" % prefix, html)
    html = re.sub(r'(href="#)(fn_|fnref_)(?!p\d+_)', r"\1\2%s" % prefix, html)
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
        with unit_recovery.guard(pno, "page_block_assembly"):
            blocks = split_blocks(
                _scope_ids(_reader_ready_notes(page_html[pno] or ""), pno))
            pages.append({"pno": pno, "anchor": page_anchor(pno),
                          "body": [b for b in blocks if not _is_aside(b)],
                          "asides": [b for b in blocks if _is_aside(b)]})
    return pages


def _completed_index_entry_boundary(tail, head):
    """A completed leader/reference row followed by a new indexed term."""
    left, right = block_text(tail), block_text(head)
    if not _INDEX_END_REF.search(left) or not _INDEX_LEADER_REF.search(left):
        return False
    first = _INDEX_LEADER_REF.search(right)
    return bool(first and first.start() <= 100
                and re.search(r"[A-Za-z]", right[:first.start()]))


def _layout_flow_index(body, last):
    from xml.etree import ElementTree as ET
    from . import _layout_flow
    def notice(fragment):
        return _layout_flow.is_notice(ET.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">'+fragment+'</root>')[0])
    indices = range(len(body)-1,-1,-1) if last else range(len(body))
    return next((i for i in indices if not notice(body[i]) and
        not body[i].startswith('<section class="reflow-retained-furniture"')), None)


def _raster_wraps(book):
    """Source-evidenced wraps between two opaque tracked-text paragraph crops.

    A known wrap authorizes paragraph flow, not transcription or pixel edits.
    Restrict this to exact factory figures and uniquely contained source text.
    """
    words=set(getattr(book,'page_wrap_words',()))
    compounds=set(getattr(book,'page_wrap_compounds',()))
    def endpoint(pno,last):
        elements=book.pages[pno]
        if not elements:return None
        element=elements[-1 if last else 0]
        if element.kind!='fig':return None
        figures=[f for f in book.figures if f['pno']==pno]
        matches=[(i,f) for i,f in enumerate(figures) if tuple(f['bbox'])==tuple(element.bbox)
                 and f.get('found')=='native_spacing_uncertain' and not f.get('full_page')
                 and not f.get('visual_evidence')]
        if len(matches)!=1:return None
        index,figure=matches[0];box=figure['bbox']
        texts=[a['text'] for a in book.artwork if a['pno']==pno
               and box[0]<=a['bbox'][0] and box[1]<=a['bbox'][1]
               and a['bbox'][2]<=box[2] and a['bbox'][3]<=box[3]]
        if len(texts)!=1:return None
        return 'images/fig_p%04d_%d.jpg'%(pno,index),texts[0].strip()
    wraps={}
    for pno in sorted(book.pages):
        if pno-1 not in book.pages:continue
        left,right=endpoint(pno-1,True),endpoint(pno,False)
        if not left or not right:continue
        tail=re.search(r'([A-Za-z]+)['+assemble.HYPHENS+r']$',left[1])
        head=re.match(r'([a-z]+)',right[1])
        if tail and head:
            a,b=tail[1].casefold(),head[1].casefold()
            if a+b in words or a+'-'+b in compounds:
                wraps[pno]=(left[0],right[0])
    return wraps


def _raster_pair_matches(left,right,pair):
    from . import _layout_flow
    return bool(pair and not block_text(left) and not block_text(right)
        and _IMG_SRC.findall(left)==[pair[0]] and _IMG_SRC.findall(right)==[pair[1]]
        and _layout_flow.raster_endpoint(left,True) and _layout_flow.raster_endpoint(right,False))


def _join_page_turns(pages, title_pages=(), layout_pages=(), layout_boundaries=None, executed=None,
                     words=None, compounds=(), raster_wraps=None, parenthetical_seams=(), native_boundaries=None):
    """Use checked model seams for model pages, legacy seams for fallback pairs."""
    joined = 0
    carriers = {}
    for index in range(1, len(pages)):
        previous, current = pages[index - 1], pages[index]
        controller = unit_recovery.current()
        with unit_recovery.guard([previous["pno"], current["pno"]], "assembly_seam",
                units=controller.seam_units(previous, current) if controller else None):
            if current['pno'] != previous['pno'] + 1:
                continue
            model_owned = previous["pno"] in layout_pages or current["pno"] in layout_pages
            decision = (layout_boundaries or {}).get(current["pno"]) if model_owned else None
            if model_owned and (decision is None or current["pno"] != previous["pno"]+1):
                continue
            if not model_owned and (previous['pno'] in title_pages or current['pno'] in title_pages):
                continue
            if not model_owned and native_boundaries is not None and current['pno'] not in native_boundaries:
                continue
            owner = carriers.get(previous['pno'], previous)
            checked_flow = model_owned or native_boundaries is not None
            tail_index = _layout_flow_index(owner['body'], True) if checked_flow else len(owner['body'])-1
            head_index = _layout_flow_index(current['body'], False) if checked_flow else 0
            if not model_owned:
                while tail_index >= 0 and _SOURCE_NOTICE.match(owner['body'][tail_index]):
                    tail_index -= 1
                # Publication adds source warnings before the actual opening prose.
                # Keep them visible, but do not mistake them for its first words.
                while head_index < len(current['body']) and _LEADING_NOTICE.match(current['body'][head_index]):
                    head_index += 1
            valid = (tail_index is not None and tail_index >= 0 and head_index is not None and
                     head_index < len(current['body']))
            if valid:
                tail, head = owner['body'][tail_index], current['body'][head_index]
                if model_owned:
                    from . import _layout_flow
                    tag = _layout_flow.CONTINUABLE.get(decision.get('role', 'paragraph'))
                    valid = tag is not None and all(re.match(r'^<' + tag + r'(?:\s|>)', b) for b in (tail, head))
                else:
                    valid = (_is_paragraph(tail) or decision is None and tail.startswith('<ol class="source-list"') and (native_boundaries or {}).get(current['pno'],{}).get('status')=='joined') and _is_paragraph(head)
            if not valid:
                if model_owned:
                    from .structural_ops import ContractError
                    raise ContractError('emitted layout boundary is not the admitted paragraph seam')
                continue
            raster_wrap=not model_owned and _raster_pair_matches(tail,head,(raster_wraps or {}).get(current['pno']))
            if not model_owned and native_boundaries is None and not raster_wrap and (_completed_index_entry_boundary(tail, head) or
                    not (assemble.continues(block_text(tail), block_text(head)) or current["pno"] in parenthetical_seams)):
                continue
            move_page_marker = not any(not block.startswith('<aside class="source-native-returns">')
                                       for block in previous['asides'])
            # Earlier-page notes keep their print-page scope. Source evidence still
            # needs a target at the passage moved into the preceding paragraph.
            marker = current['anchor'] if move_page_marker else ''
            if model_owned:
                owner['body'][tail_index] = _merge_layout_paragraphs(tail, current['body'].pop(head_index), marker, decision['hyphen'], decision.get('role', 'paragraph'), decision.get('pixel_boundary', False))
                if _layout_flow_index(current['body'], False) is None:
                    carriers[current['pno']] = owner
                if executed is not None: executed.add(current['pno'])
            else:
                head=current['body'].pop(head_index)
                if tail.startswith('<ol class="source-list"'):
                    items=list(re.finditer(r'<li(?:\s[^>]*)?>(.*?)</li>',tail,re.S))
                    if not items:raise ValueError('Source list has no owned tail item')
                    item=items[-1];paragraph='<p>'+item[1]+'</p>'
                    merged=merge_paragraphs(paragraph,head,marker,words,compounds)
                    owner['body'][tail_index]=tail[:item.start(1)]+_inner(merged)+tail[item.end(1):]
                else:
                    owner['body'][tail_index] = (_merge_layout_paragraphs(tail,head,marker,None,pixel_boundary=True)
                        if raster_wrap else merge_paragraphs(tail,head,marker,words,compounds))
                if _layout_flow_index(current['body'], False) is None:
                    carriers[current['pno']] = owner
            if not move_page_marker:
                # Return to the joined paragraph, without inserting markup into a
                # word that straddles the source boundary.
                merged = owner['body'][tail_index]
                opening = _open_tag(merged)
                owner['body'][tail_index] = (opening + '<span id="source_return_%04d"></span>' % current['pno']
                                            + merged[len(opening):])
            if move_page_marker: current['anchor'] = ''
            joined += 1
    return joined


def _merge_layout_paragraphs(left, right, marker, hyphen, role="paragraph", pixel_boundary=False):
    """Apply only the recompiled source-bound decision, without spelling rules."""
    from .structural_ops import ContractError
    if _READING_COPY.search(left+right):
        return _merge_without_reading_copy(_merge_layout_paragraphs,left,right,marker,hyphen,role,pixel_boundary)
    tail, head = _inner(left).rstrip(), _inner(right).lstrip()
    from . import _layout_flow
    if pixel_boundary and (hyphen is not None or not (_layout_flow.raster_endpoint(left, True) or _layout_flow.raster_endpoint(right, False))):
        raise ContractError('emitted protected raster endpoint differs')
    if hyphen is not None:
        if hyphen not in ('keep','drop') or not _TRAILING_HYPHEN.search(tail):
            raise ContractError('emitted layout boundary differs from source hyphen')
        if hyphen == 'drop': tail = _TRAILING_HYPHEN.sub(r"\1\2", tail)
        head,_ = _word_head_without_space(head)
        glue = ''
    else:
        if block_text(tail).endswith('-') and not pixel_boundary:raise ContractError('unapproved boundary hyphen')
        glue = ' '
    from . import _layout_flow
    tag = _layout_flow.CONTINUABLE.get(role)
    if tag is None: raise ContractError('unsupported continuation role')
    return "%s%s%s%s%s</%s>" % (_open_tag(left), tail, glue, marker, head, tag)


def _opening_folio_before_heading(blocks):
    """A detached numbered running line can precede a page's first heading.

    Preserve the printed line, including its spacing, but put it and the page
    marker in the heading's chapter. A plain numbered paragraph or table value
    does not supply the large source-layout gap and short text-only label.
    """
    if len(blocks) < 2 or not _is_paragraph(blocks[0]) or not _SPLIT_HEADING.match(blocks[1]):
        return False
    text = _TAG.sub('', _inner(blocks[0]))
    return bool(re.fullmatch(r'\s*\d{1,4}[ \t]{8,}[A-Za-z][^.!?]{0,60}\s*', text))


_CONTENTS_TITLE = re.compile(r"^(?:table of )?contents$", re.I)
_CONTENTS_REF = re.compile(r"\b(?:\d{1,4}|[ivxlcdm]{1,8})\s*$", re.I)


def _source_contents_pages(pages, doc):
    """Find a printed contents *list*, not prose that happens to say Contents.

    The PDF must itself navigate to this page. Its native text must have the
    matching heading followed by several numbered rows at one measured right
    edge; the emitted page must open with that heading too. This only changes
    chapter boundaries, never source words or the page's evidence images.
    """
    if doc is None:
        return set()
    from .skeleton import outline_is_useful
    outline = extract.outline(doc)
    if not outline_is_useful(outline):
        return set()
    candidates = {entry['pno']: entry['title'] for entry in outline
                  if _CONTENTS_TITLE.fullmatch(entry['title'].strip())}
    found = set()
    for page in pages:
        pno = page['pno']
        title = candidates.get(pno)
        if title is None:
            continue
        first = next((b for b in page['body'] if not _LEADING_NOTICE.match(b)), '')
        if not _SPLIT_HEADING.match(first) or block_text(first).strip().casefold() != title.casefold():
            continue
        try:
            source = doc[pno]
            blocks = [(b[:4], ' '.join(b[4].split())) for b in source.get_text('blocks')
                      if len(b) > 6 and b[6] == 0 and b[4].strip()]
        except (IndexError, ValueError, TypeError):
            continue
        if not blocks or blocks[0][1].casefold() != title.casefold():
            continue
        # The qualification suppresses *all* later emitted headings on this
        # page. Therefore every other substantive native block must belong to
        # the numbered list. A block beside or overlapping the title may be a
        # separate section; filtering it out here would silently absorb that
        # section into Contents.
        rows = blocks[1:]
        if any(box[1] <= blocks[0][0][3] for box, _ in rows):
            continue
        numbered = [box for box, text in rows if _CONTENTS_REF.search(text)
                    and re.search(r'[A-Za-z]', text)]
        # A real chapter can begin below a printed contents list on the same
        # page. If any following text block is not a numbered row, leave the
        # normal heading boundaries intact rather than absorbing that chapter.
        if (len(numbered) < 3 or len(numbered) != len(rows)
                or max(box[2] for box in numbered) - min(box[2] for box in numbered)
                   > source.rect.width * .08):
            continue
        found.add(pno)
    return found


def _chapters(pages, title_pages=None, contents_pages=()):
    title_pages = title_pages or {}
    contents_pages = set(contents_pages)
    chapters = []
    current = None
    after_contents = False

    def start(title, continued=False):
        chapter = Chapter(index=len(chapters) + 1, title=title, continued=continued)
        chapters.append(chapter)
        return chapter

    for page in pages:
        if after_contents:
            # The following page is no longer part of the printed list. Even a
            # blank page needs a real home for its source-page marker.
            current = start("") if not page['body'] and not page['asides'] else None
        after_contents = page['pno'] in contents_pages
        title_page = page['pno'] in title_pages
        if title_page:
            current = start(title_pages[page['pno']])
        body = list(page['body'])
        leading = []
        while body and _LEADING_NOTICE.match(body[0]):
            leading.append(body.pop(0))
        opening_heading = None
        if not title_page and _opening_folio_before_heading(body):
            opening_heading = body[1]
            current = start(block_text(opening_heading))
        # The page marker waits for the block it belongs to, so a page that opens a
        # chapter puts its marker in the new document and not the previous one.
        pending = page["anchor"]
        first_heading = body[0] if body and _SPLIT_HEADING.match(body[0]) else None
        if first_heading is not None and not title_page and first_heading is not opening_heading:
            current = start(block_text(first_heading))
        if first_heading is not None:
            # Generated warnings can precede a source heading. Put the page
            # marker and heading together, then the warnings, in that chapter.
            body = [body.pop(0)] + leading + body
            leading = []
        first_block = first_heading
        for block in leading + body + page["asides"]:
            heading = _SPLIT_HEADING.match(block)
            if (heading and not title_page and block is not opening_heading
                    and block is not first_block and page['pno'] not in contents_pages):
                current = start(block_text(block))
            elif current is None:
                current = start("")
            elif len(current.blocks) >= MAX_BLOCKS_PER_DOC and not title_page:
                current = start("", continued=True)
            if pending:
                current.blocks.append(pending)
                pending = None
            current.blocks.append(block)
            if page["pno"] not in current.pages:
                current.pages.append(page["pno"])
            for pno in (int(found) for found in _INLINE_PAGE_ID.findall(block)):
                if pno not in current.pages:
                    current.pages.append(pno)
        if pending:
            # Empty opening pages (or a blank after a title) still need a
            # destination for the source-page map and explicit return links.
            if current is None:
                current = start("")
            current.blocks.append(pending)
            if page["pno"] not in current.pages:
                current.pages.append(page["pno"])
        if title_page:
            current = None
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
    """Map page markers to the document where each marker was actually emitted.

    A page can straddle a chapter split. Recompute homes from final chapters
    so report links target the right document."""
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


def _source_return_targets(chapters):
    """Resolve moved source passages in their actual emitted chapter."""
    targets = {}
    for chapter in chapters:
        for block in chapter.blocks:
            for ident in _ID.findall(block):
                if re.fullmatch(r'source_return_\d+', ident):
                    targets[int(ident.removeprefix('source_return_'))] = chapter.href + '#' + ident
    return targets


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


def _printed_furniture(book, page_homes, language, evidence=None):
    """Retain exact marginal text in an accessible, page-bound inspection channel.

    This is published after the source gate: furniture is a separate source
    channel, never additional words in the body page being checked by that gate.
    """
    sections = []
    for pno, inventory in sorted(getattr(book, 'source_inventory', {}).items()):
        lines = {row['id']: row['source'] for row in inventory.get('lines', [])}
        furniture = []
        source = (evidence or {}).get(pno, {})
        details = {detail['id']: detail for detail in source.get('details', [])}
        for index, region in enumerate(inventory.get('regions', [])):
            if region['suggested_kind'] != 'furniture':continue
            if region['reason'] in ('repeated_scan_spread_head','repeated_portrait_head'):
                key = 'furniture_%d' % index
                detail = details.get(key)
                if not detail or not source.get('href'):
                    raise ValueError('Original running-head pixels are required')
                furniture.append('<figure><a href="%s#%s"><img src="%s" '
                    'alt="Original printed running head"/></a><figcaption>'
                    '<a href="%s#%s">View larger</a></figcaption></figure>' %
                    (source['href'],key,detail['src'],source['href'],key))
            else:
                furniture.extend('<p>%s</p>' % escape(lines[key].text) for key in region['line_ids'])
        if not furniture or pno not in page_homes:
            continue
        sections.append('<section class="reflow-retained-furniture" id="furniture_p%04d"><h2>PDF page %d</h2>%s'
                        '<p><a href="%s#pg_%04d">Return to reading</a></p></section>' %
                        (pno, pno+1, ''.join(furniture), page_homes[pno], pno))
    if not sections:
        return None
    return _document('Printed running heads and folios',
        '<h1>Printed running heads and folios</h1>' + ''.join(sections), language)


def _source_checks(language, evidence=None):
    """A short, file-level help target for reader links at every font size."""
    images = (('<section><h2>Reading source text images</h2>'
               '<p>Some text is shown as original printed pixels because OCR '
               'cannot verify its wording or layout. Text inside these images '
               'is not searchable and does not grow with reader font settings. '
               'Use View larger beneath an image to open the original page and '
               'enlarged details, then use its Return link to resume reading.</p></section>')
              if evidence else '')
    return _document("Source checks", (
        '<h1>Source checks in the reading text</h1>'
        '<p>Punctuation uncertain means the printed punctuation may differ '
        'from the transcription; the linked original passage shows it.</p>'
        '<p>Words/glyphs uncertain means source lettering is retained as '
        'original images because its encoding or transcription is unclear.</p>'
        '<p>OCR uncertain means a recognized reading may be wrong; source '
        'images retain the affected pixels.</p>'
        '<p>PDF link unresolved means an authored internal destination could '
        'not be placed unambiguously.</p>'
        '<p>Each local link opens the original page or passage for checking.</p>'
    ) + images, language)


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
    target_order = {}
    for index, (href, label) in enumerate(entries, start=1):
        order = target_order.setdefault(href, len(target_order) + 1)
        points.append(
            '  <navPoint id="nav%d" playOrder="%d">\n'
            "    <navLabel><text>%s</text></navLabel>\n"
            '    <content src="%s"/>\n'
            "  </navPoint>" % (index, order, escape(label), href))
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


def _scan_key_white_margin_jpeg(doc, pno, bbox):
    """Remove only side columns proved pure white in the emitted source raster.

    The source-owned region remains intact. A symbol OCR did not bound
    can be anywhere on it; the same pixmap supplies the proof and the JPEG, so
    cached OCR geometry and a separate approximate ink query have no authority
    to cut it. Unknown colors or a failed render keep the complete region.
    """
    from PIL import Image, ImageChops
    clip = extract.pymupdf.Rect(*bbox)
    scale = extract._bounded_scale(clip, 2.0)
    pix = doc[pno].get_pixmap(matrix=extract.pymupdf.Matrix(scale, scale),
                              clip=clip, alpha=False, colorspace=extract.pymupdf.csRGB)
    if pix.n != 3 or pix.width < 2 or pix.height < 2:
        return pix.tobytes('jpg', jpg_quality=85)
    image = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
    extent = ImageChops.difference(image, Image.new('RGB', image.size, 'white')).getbbox()
    if extent is None:
        return pix.tobytes('jpg', jpg_quality=85)
    # Eight source pixels of the independently proved white columns protect the
    # printed edge from JPEG boundary ringing. Never trim rows or nonwhite ink.
    left = max(0, extent[0]-8)
    right = min(pix.width, extent[2]+8)
    if pix.width-(right-left) < pix.width*.10:
        return pix.tobytes('jpg', jpg_quality=85)
    cropped = extract.pymupdf.Pixmap(pix, pix.width, pix.height,
        extract.pymupdf.IRect(pix.x+left, pix.y, pix.x+right, pix.y+pix.height))
    if cropped.samples != image.crop((left, 0, right, pix.height)).tobytes():
        raise ValueError('source side crop changed rendered pixels')
    return cropped.tobytes('jpg', jpg_quality=85)



def _figure_crop_margin(data, audit=None):
    """Keep edge ink away from a reader's image boundary, without resampling.

    The original decoded crop remains byte-for-byte inside the white frame.
    Lossless PNG prevents a second JPEG pass changing table rules or glyph ink.
    Crops already surrounded by white paper retain their original encoding.
    """
    import io
    from PIL import Image, ImageOps, ImageChops
    with Image.open(io.BytesIO(data)) as image:
        if audit is not None:
            audit['reading_image_geometry'] = dict(source_pixel_size=list(image.size),
                packaged_pixel_size=list(image.size), padding=[0,0,0,0])
        ink = image.convert('L').point(lambda value: 255 if value < 240 else 0).getbbox()
        margin = 8
        if ink is None or (ink[0] >= margin and ink[1] >= margin and
                           image.width-ink[2] >= margin and image.height-ink[3] >= margin):
            return data
        padded = ImageOps.expand(image.convert('RGB'), border=margin, fill='white')
        if audit is not None:
            audit['reading_image_geometry'].update(packaged_pixel_size=list(padded.size),
                padding=[margin]*4)
        # Grayscale storage is lossless when all three channels are equal.
        # Framing a gray source as RGB needlessly triples the filtered samples.
        r,g,b=padded.split()
        if ImageChops.difference(r,g).getbbox() is None and ImageChops.difference(r,b).getbbox() is None:
            padded=r
        output = io.BytesIO()
        padded.save(output, format='PNG', optimize=True)
        return output.getvalue()


def _source_edge_boxes(book, pno, transform=None):
    """Literal source glyph extents, including pixels absent from live prose."""
    boxes = []
    for row in getattr(book, 'source_inventory', {}).get(pno, {}).get('lines', []):
        line = row['source']
        for span in getattr(line, 'spans', ()):
            if span.text.strip():
                boxes.append(transform(pno, span.bbox) if transform else span.bbox)
    return boxes


def _source_scan_edges(doc, pno):
    from .crop_edges import source_scan_edges
    return source_scan_edges(extract.render_page_jpeg(doc, pno, scale=2, quality=90),
                             _page_rect(doc, pno))


def _retained_source_rect(rect, audits):
    for audit in audits:
        if audit.get('action') not in (None, 'trim') or 'retained_pixel_bbox' not in audit:
            continue
        x0,y0,x1,y1 = audit['retained_pixel_bbox']
        width,height = audit['size']
        return (rect[0]+(rect[2]-rect[0])*x0/width,
                rect[1]+(rect[3]-rect[1])*y0/height,
                rect[0]+(rect[2]-rect[0])*x1/width,
                rect[1]+(rect[3]-rect[1])*y1/height)
    return tuple(rect)


def _exclude_source_furniture_pixels(data, reading_bbox, furniture_boxes=(),
                                     protected_boxes=(), *, protected_pixel_boxes=()):
    """Exclude complete owned header components, retaining every other pixel.

    Source ownership authorizes glyph components, not a rectangular page band.
    A component connected to chart ink, leaving the bounded glyph context, or
    touching another source glyph remains intact. The original evidence image
    is published separately and never passes through this presentation step.
    """
    import io
    from PIL import Image, ImageDraw

    def valid(box):
        return (len(box) == 4 and all(math.isfinite(float(v)) for v in box)
                and box[2] > box[0] and box[3] > box[1])

    if not furniture_boxes or not valid(reading_bbox):
        return data, []
    owned = [tuple(box) for box in furniture_boxes if valid(box)]
    if not owned:
        return data, []
    try:
        image = Image.open(io.BytesIO(data)).convert('RGB')
    except (OSError, ValueError):
        return data, []
    width, height = image.size
    x0, y0, x1, y1 = reading_bbox
    sx, sy = width/(x1-x0), height/(y1-y0)

    def mask(boxes, padding=0, pixel_centers=False, pixel_intersections=()):
        result = Image.new('L', image.size)
        draw = ImageDraw.Draw(result)
        for a, b, c, d in boxes:
            # A rendered pixel belongs to the exact owned row when its center
            # is inside. Inward rounding omitted border centers on fractional
            # native coordinates. Context and protected masks retain their
            # established conservative component-containment contract.
            offset = .5 if pixel_centers else 0
            left, top = math.ceil((a-x0-padding)*sx-offset), math.ceil((b-y0-padding)*sy-offset)
            right, bottom = math.floor((c-x0+padding)*sx-offset), math.floor((d-y0+padding)*sy-offset)
            if left <= right and top <= bottom:
                draw.rectangle((left, top, right, bottom), fill=255)
        # Recognition obligations can be smaller than a rendered pixel. Protect
        # every intersecting pixel cell, including cells whose centers lie
        # outside the box. These boxes never authorize a furniture seed.
        for a, b, c, d in pixel_intersections:
            left, top = math.floor((a-x0)*sx), math.floor((b-y0)*sy)
            right, bottom = math.ceil((c-x0)*sx)-1, math.ceil((d-y0)*sy)-1
            if left <= right and top <= bottom:
                draw.rectangle((left, top, right, bottom), fill=255)
        return result.tobytes()

    core, context = mask(owned, pixel_centers=True), mask(owned, 3)
    protected = mask([tuple(box) for box in protected_boxes if valid(box)],
        pixel_intersections=[tuple(box) for box in protected_pixel_boxes if valid(box)])
    gray = image.convert('L').tobytes()
    visited = bytearray(width*height)
    pixels = image.load()
    audit, changed = [], False
    for seed, inside in enumerate(core):
        if not inside or visited[seed] or gray[seed] >= 240:
            continue
        pending, component, safe = [seed], [], True
        visited[seed] = 1
        while pending:
            at = pending.pop()
            component.append(at)
            safe = safe and bool(context[at]) and not protected[at]
            x, y = at % width, at // width
            for yy in range(max(0, y-1), min(height, y+2)):
                for xx in range(max(0, x-1), min(width, x+2)):
                    neighbour = yy*width+xx
                    if not visited[neighbour] and gray[neighbour] < 240:
                        visited[neighbour] = 1
                        pending.append(neighbour)
        if not safe:
            continue
        xs, ys = [at % width for at in component], [at // width for at in component]
        bounds = [min(xs), min(ys), max(xs)+1, max(ys)+1]
        for at in component:
            pixels[at % width, at // width] = (255, 255, 255)
        changed = True
        audit.append(dict(action='exclude_owned_furniture_component',
            pixel_bbox=bounds, size=[width, height], reading_bbox=list(reading_bbox),
            source_bbox=[x0+bounds[0]/sx, y0+bounds[1]/sy,
                         x0+bounds[2]/sx, y0+bounds[3]/sy], pixels=len(component)))
    if not changed:
        return data, []
    output = io.BytesIO()
    image.save(output, format='PNG', optimize=True)
    return output.getvalue(), audit


def _source_furniture_pixel_boxes(book, pno, figure):
    """Select only source-proved running heads in scan figure carriers."""
    inventory = getattr(book, 'source_inventory', {}).get(pno, {})
    geometry = inventory.get('source_geometry', {})
    rect = geometry.get('page_rect', ())
    eligible = figure.get('found') in ('scan_figure_band', 'scan_figure_side')
    if figure.get('found') == 'ocr_uncertain_region':
        # An uncertain crop may still be an independently established chart.
        # Match its canonical region and require both its owned caption and
        # contained source lettering; uncertain prose alone proves no artwork.
        rows = {row['id'] for row in inventory.get('lines', ())}
        carriers = [region for region in inventory.get('regions', ())
                    if region.get('suggested_kind') == 'figure'
                    and region.get('reason') == figure.get('found')
                    and region.get('geometry_status') == 'valid'
                    and tuple(region.get('bbox', ())) == tuple(figure.get('bbox', ()))]
        if len(carriers) == 1:
            carrier = carriers[0]
            captions = carrier.get('caption_line_ids', ())
            owners = {owner['line_id']: owner for owner in inventory.get('ownership', ())}
            box = carrier['bbox']
            lettering = [region for region in inventory.get('regions', ())
                         if region.get('suggested_kind') == 'artwork'
                         and region.get('reason') == 'figure_lettering'
                         and region.get('geometry_status') == 'valid'
                         and region.get('line_ids')
                         and all(line_id in rows for line_id in region['line_ids'])
                         and region.get('bbox')
                         and box[0] <= region['bbox'][0] < region['bbox'][2] <= box[2]
                         and box[1] <= region['bbox'][1] < region['bbox'][3] <= box[3]]
            eligible = bool(lettering and captions and all(
                line_id in rows and owners.get(line_id, {}).get('representation') == 'text'
                and owners.get(line_id, {}).get('owner_id') == carrier['id']
                for line_id in captions))
    if (not eligible
            or figure.get('full_page')
            or figure.get('source_geometry', {}).get('space') != 'reading'
            or geometry.get('space') != 'reading' or len(rect) != 4
            or rect[3] <= rect[1]
            or not any(image.get('page_background') if isinstance(image, dict)
                       else getattr(image, 'page_background', False)
                       for image in inventory.get('raw_images', ()))):
        return [], []
    rows = {row['id']: row['source'] for row in inventory.get('lines', ())}
    carrier_box = figure.get('bbox', ())
    proved = set()
    for region in inventory.get('regions', ()):
        box, ids = region.get('bbox'), region.get('line_ids', ())
        if (region.get('suggested_kind') != 'furniture'
                or region.get('reason') not in ('repeated_scan_spread_head', 'repeated_portrait_head')
                or region.get('geometry_status') != 'valid' or not box or not ids
                or box[1] < rect[1] or box[3] > rect[1]+(rect[3]-rect[1])*.12
                or any(line_id not in rows for line_id in ids)):
            continue
        if (len(carrier_box) != 4 or box[0] >= carrier_box[2]
                or box[2] <= carrier_box[0] or box[1] >= carrier_box[3]
                or box[3] <= carrier_box[1]):
            continue
        if any(sum(char.isalpha() for char in rows[line_id].text) < 6 for line_id in ids):
            continue
        if any(other.get('suggested_kind') != 'furniture'
               and any(line_id in other.get('line_ids', ()) for line_id in ids)
               for other in inventory.get('regions', ())):
            continue
        proved.update(ids)
    furniture, protected = [], []
    for line_id, line in rows.items():
        destination = furniture if line_id in proved else protected
        destination.extend(tuple(span.bbox) for span in line.spans if span.text.strip())
    for region in inventory.get('regions', ()):
        if region.get('suggested_kind') in ('caption', 'table') and region.get('bbox'):
            protected.append(tuple(region['bbox']))
    return furniture, protected

def _native_margin_furniture_pixel_boxes(doc, book, pno, figure):
    """Bind margin furniture to its original native scan and source peers.

    An empty native geometry record is explicit source evidence, not a guessed
    reading frame. Native vector pages, rotated page frames and uncertain
    role ownership do not authorize erasure. The caller still protects required
    source resources; the component eraser protects all surrounding source ink.
    """
    inventory = getattr(book, 'source_inventory', {}).get(pno, {})
    if (inventory.get('source_geometry') != {}
            or figure.get('source_geometry', {}) != {}
            or figure.get('pno') != pno or figure.get('full_page')
            or figure.get('found') not in ('ocr_uncertain_region',
                                         'scan_figure_band', 'scan_figure_side',
                                         'unverified_paired_columns')):
        return [], []

    def valid(box):
        return (len(box) == 4 and all(math.isfinite(float(v)) for v in box)
                and box[0] < box[2] and box[1] < box[3])

    def same(a, b):
        return len(a) == len(b) and all(abs(x-y) < .0001 for x, y in zip(a, b))

    def overlap(a, b):
        return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]

    crop = figure.get('bbox', ())
    if not valid(crop) or not any(
            region.get('suggested_kind') == 'furniture'
            and region.get('reason') in ('contents_footer_folio', 'sequence_folio',
                                         'running_caps', 'repeated_detached_head', 'folio')
            and valid(region.get('bbox', ())) and overlap(region['bbox'], crop)
            for region in inventory.get('regions', ())):
        return [], []

    try:
        page = doc[pno]
        rect = tuple(page.rect)
        native_media = tuple(page.mediabox * page.transformation_matrix)
        if page.rotation or not same(native_media, rect):
            return [], []
        crop = figure.get('bbox', ())
        if (not valid(crop) or crop[0] < rect[0] or crop[1] < rect[1]
                or crop[2] > rect[2] or crop[3] > rect[3]):
            return [], []
        # Raw extraction and image operators expose different rectangles for
        # clipped bitmaps. Compare each representation only to its own source:
        # retain the complete raw-image multiset, and independently require
        # original bitmap operators in native page space. Duplicate instances are real.
        from collections import Counter
        raw = page.get_text('rawdict')
        original_images = []
        for block in raw.get('blocks', ()):
            if block.get('type') == 1:
                box = tuple(block.get('bbox', ()))
                if not valid(box):
                    return [], []
                original_images.append((box, (box[2]-box[0])*(box[3]-box[1]) /
                                        page.rect.get_area()))
        captured_images = []
        for image in inventory.get('raw_images', ()):
            box = image.get('bbox', ()) if isinstance(image, dict) else image.bbox
            area = image.get('area_ratio', 0) if isinstance(image, dict) else image.area_ratio
            captured_images.append((tuple(box), area))
        if not original_images or Counter(captured_images) != Counter(original_images):
            return [], []
        native_bitmaps = []
        for image in page.get_image_info():
            box = tuple(image.get('bbox', ()))
            transform = tuple(image.get('transform', ()))
            if (not valid(box) or image.get('width', 0) <= 0
                    or image.get('height', 0) <= 0 or len(transform) != 6
                    or not all(math.isfinite(v) for v in transform)
                    or transform[0] <= 0 or transform[3] <= 0
                    or transform[0]*transform[3]-transform[1]*transform[2] == 0):
                return [], []
            # Reproduce the original operator with MuPDF's own arithmetic.
            # Python subtraction of rounded endpoints is a different precision
            # representation; it cannot prove or disprove the original frame.
            unit = extract.pymupdf.Rect(0, 0, 1, 1)
            if tuple(unit * extract.pymupdf.Matrix(*transform)) != box:
                return [], []
            native_bitmaps.append(box)
        if len(native_bitmaps) != len(original_images):
            return [], []
        native_lines = [line for block in page.get_text('dict')['blocks']
                        for line in block.get('lines', ())]
    except (AttributeError, TypeError, ValueError, IndexError):
        return [], []

    from . import source_inventory
    source_rows = inventory.get('lines', ())
    rows = {row['id']: row for row in source_rows}
    owners = inventory.get('ownership', ())
    if len(rows) != len(source_rows):
        return [], []
    regions = inventory.get('regions', ())
    # Rebind every captured row, not only a plausible head: equal-sized
    # adjacent scans can share a title but cannot exchange their body text.
    normal = lambda text: ''.join(text.split())
    native_rows = [(tuple(line.get('bbox', ())), normal(''.join(
        span.get('text', '') for span in line.get('spans', ())))) for line in native_lines]
    if any(row.get('sha256') != source_inventory.digest(row['source'])
           or native_rows.count((tuple(row['source'].bbox),
                                 normal(row['source'].text))) != 1
           for row in source_rows):
        return [], []

    def source_peer(pn, text, source_box, numeric=False):
        try:
            peer = doc[pn]
            if peer.rotation:
                return False
            height = peer.rect.height
            source_x = ((source_box[0]+source_box[2])/2-rect[0])/(rect[2]-rect[0])
            source_y = ((source_box[1]+source_box[3])/2-rect[1])/(rect[3]-rect[1])
            for block in peer.get_text('dict')['blocks']:
                for line in block.get('lines', ()):
                    box = line.get('bbox', ())
                    if not valid(box) or box[3] > peer.rect.y0+height*.20:
                        continue
                    if normal(''.join(span.get('text', '') for span in line['spans'])) != text:
                        continue
                    peer_x = ((box[0]+box[2])/2-peer.rect.x0)/peer.rect.width
                    peer_y = ((box[1]+box[3])/2-peer.rect.y0)/height
                    if numeric and (pn-pno) % 2:
                        peer_x = 1-peer_x
                    # Compare margin families, not coordinate provenance. The
                    # latter is exact above. Paired folios alternate sides;
                    # matching text in an inner lane or another top band is
                    # authored content, not evidence for erasing this row.
                    if abs(source_x-peer_x) <= .025 and abs(source_y-peer_y) <= .03:
                        return True
            return False
        except (AttributeError, TypeError, ValueError, IndexError, KeyError):
            return False

    def repeated_top_row(source):
        text = normal(source.text)
        if re.fullmatch(r'[0-9]+', text):
            value = int(text)
            # A contents destination can occupy the same lane. Require both
            # sides of a printed sequence at the original physical page gaps.
            return (any(pno-gap >= 0 and source_peer(pno-gap, str(value-gap), source.bbox, numeric=True)
                        for gap in range(1, 5))
                    and any(source_peer(pno+gap, str(value+gap), source.bbox, numeric=True)
                            for gap in range(1, 5)))
        if not any(char.isalpha() for char in text):
            return False
        return any(pno+gap >= 0 and source_peer(pno+gap, text, source.bbox)
                   for gap in (-4, -2, 2, 4))

    proved = set()
    for region in regions:
        box, ids = region.get('bbox', ()), region.get('line_ids', ())
        if (region.get('suggested_kind') != 'furniture'
                or region.get('reason') not in ('contents_footer_folio', 'sequence_folio',
                                              'running_caps', 'repeated_detached_head', 'folio')
                or region.get('geometry_status') != 'valid' or not ids
                or not valid(box) or box[3] > rect[3] or not overlap(box, crop)
                or any(lid not in rows for lid in ids)):
            continue
        if any(rows[lid].get('sha256') != source_inventory.digest(rows[lid]['source'])
               or not valid(rows[lid]['source'].bbox)
               or not (box[0] <= rows[lid]['source'].bbox[0]
                       and box[1] <= rows[lid]['source'].bbox[1]
                       and rows[lid]['source'].bbox[2] <= box[2]
                       and rows[lid]['source'].bbox[3] <= box[3]) for lid in ids):
            continue
        bottom_folio = (region.get('reason') in
                        ('contents_footer_folio', 'sequence_folio')
                        and box[1] >= rect[3]-(rect[3]-rect[1])*.12)
        top_head = (region.get('reason') in
                    ('running_caps', 'repeated_detached_head', 'folio')
                    and box[1] >= rect[1] and box[3] <= rect[1]+(rect[3]-rect[1])*.20
                    and all(repeated_top_row(rows[lid]['source']) for lid in ids))
        if not (bottom_folio or top_head):
            continue
        if any(not any(bitmap[0] <= rows[lid]['source'].bbox[0]
                       and bitmap[1] <= rows[lid]['source'].bbox[1]
                       and rows[lid]['source'].bbox[2] <= bitmap[2]
                       and rows[lid]['source'].bbox[3] <= bitmap[3]
                       for bitmap in native_bitmaps) for lid in ids):
            continue
        if any([owner for owner in owners if owner.get('line_id') == lid] !=
               [dict(line_id=lid, representation='text', owner_id=region['id'])]
               for lid in ids):
            return [], []
        if any(other is not region and any(lid in other.get('line_ids', ())
                   or lid in other.get('caption_line_ids', ()) for lid in ids)
               for other in regions):
            return [], []
        protected_territory = [other['bbox'] for other in regions
                              if other.get('suggested_kind') in ('caption', 'table', 'note')
                              and other.get('bbox') and valid(other['bbox'])]
        for element in (getattr(book, 'pages', {}) or {}).get(pno, ()):
            if element.kind in ('p', 'h', 'caption'):
                protected_territory.extend(element.line_boxes or
                                          ([element.bbox] if element.bbox else []))
        protected_territory.extend(note.bbox for note in getattr(book, 'notes', ())
                                   if note.pno == pno and note.bbox)
        if any(valid(other) and overlap(box, other) for other in protected_territory):
            continue
        proved.update(ids)
    if not proved:
        return [], []
    furniture, protected = [], []
    for lid, row in rows.items():
        if lid in proved:
            # Physical qualification and native text binding own the complete
            # row. Seed its glyph components across whitespace between spans;
            # isolated JPEG fringes in those gaps also belong to this header.
            furniture.append(tuple(row['source'].bbox))
        else:
            protected.extend(tuple(span.bbox) for span in row['source'].spans
                             if span.text.strip() and valid(span.bbox))
    protected.extend(protected_territory)
    return furniture, protected


def _required_transcript_pixel_protection(doc, book, pno, figure, src, required):
    """Rebind a required native image and all its recognition obligations.

    Required image membership alone cannot authorize changing its pixels. A
    complete original-source transcript proof permits removal of separately
    qualified furniture while protecting every unmatched recognition box.
    Visual/table obligations and unsupported source frames remain untouched.
    """
    from .visual_coverage import REGION_VERSION
    if (figure.get('visual_evidence', {}).get('version') == REGION_VERSION
            or not isinstance(required, dict) or required.get(src) != figure
            or figure.get('source_geometry') or getattr(doc, 'is_dirty', True)
            or not getattr(doc, 'name', '') or not os.path.isfile(doc.name)):
        return None
    proofs = getattr(book, 'source_region_protection', {})
    if not isinstance(proofs, dict):
        return None
    proof = proofs.get(pno)
    if not isinstance(proof, dict):
        return None
    from . import transcript_regions
    from .structural_ops import ContractError
    try:
        if (doc[pno].rotation or list(doc[pno].rect) != proof.get('frame')
                or extract.document_fingerprint(doc) != proof.get('source_pdf_sha256')
                or transcript_regions._required(book, pno, proof).get(src) != figure):
            return None
    except (AttributeError, TypeError, ValueError, IndexError, KeyError, ContractError):
        return None
    return [tuple(box) for box in proof['unmatched_pdf_boxes']]


def _figure_images(chapters, doc, book, package, figure_transform=None, owned_images=(),
                   runtime_progress=None, required_source_regions=(),
                   figure_presentation=None):
    """Crop each figure the fragments referred to; drop the ones we cannot make.

    Each crop goes into ``package`` as it is cut (:class:`_Package`); what comes
    back is the two lists of figures the book does not get.

    Two drops are not the same event. A crop that *fails* is a loss: the page
    printed a figure and the book does not have it, so it is reported. A candidate
    region that proves to hold no ink is blank paper. Omitting an empty
    candidate is the correct outcome and is disclosed in the sidecar rather
    than counted as a lost illustration on the report page.

    ``figure_transform`` maps a figure's box from the page's reading space into
    unrotated PDF space before cropping: on a rotated spread the skeleton measured
    in the upright reading space, and the ink is stored sideways.

    ``figure_presentation`` receives crop/probe metadata separately from source
    ownership. Book.figures participates in canonical source seals and is never
    mutated while publishing images, including on repeated builds or audits.
    """
    wanted = []
    for chapter in chapters:
        wanted.extend(_IMG_SRC.findall("\n".join(chapter.blocks)))
    wanted = [src for src in dict.fromkeys(wanted) if src.startswith("images/") and src not in owned_images]
    if not wanted:
        return [], []
    if doc is None and unit_recovery.current() is not None:
        raise unit_recovery.SourceIntegrityError("Original PDF required for source-owned figures")

    boxes = {}
    for index, figure in enumerate(book.figures):
        seen = boxes.setdefault(figure["pno"], [])
        seen.append(figure)

    missing, blanks = [], []
    scan_edges = {}
    if figure_presentation is None:
        figure_presentation = {}
    for ordinal, src in enumerate(wanted, 1):
        match = re.match(r"images/fig_p(\d+)_(\d+)\.jpg$", src)
        if not match or doc is None:
            missing.append(src)
            continue
        pno, index = int(match.group(1)), int(match.group(2))
        if runtime_progress is not None:
            runtime_progress({"kind": "figure_crop", "page": pno,
                              "ordinal": ordinal, "total": len(wanted)})
        page_figures = boxes.get(pno) or []
        if index >= len(page_figures):
            missing.append(src)
            continue
        source_elements = ["native-element-" + str(i)
                           for i, element in enumerate(book.pages.get(pno) or ()) if element.kind == "fig"]
        bid = source_elements[index] if index < len(source_elements) else None
        with unit_recovery.guard(page_figures[index]["pno"], "native_figure_image", block=bid):
            figure = dict(page_figures[index])
            figure_presentation[src] = figure
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
            from .visual_coverage import REGION_VERSION
            table_source = figure.get('visual_evidence', {}).get('version') == REGION_VERSION
            # The table gate above separately rebinds its supported reading frame.
            if src in required_source_regions and not table_source and (geometry or tuple(bbox) != tuple(figure['bbox'])):
                from .structural_ops import ContractError
                raise ContractError('source region crop frame changed')
            ink_doc=render_doc
            if figure.get('needs_ink') and geometry.get('space')=='reading':
                ink_doc=SourceDisplay(doc,pno,dict(geometry,layer='ocr')).query_document(isolate=True)
            data = None
            whole_page_fallback = False
            try:
                # A source-bound unmatched-glyph obligation outranks the optional
                # artwork threshold: sparse ink still needs its original pixels.
                if src not in required_source_regions and figure.get("needs_ink") and not extract.region_has_ink(
                        ink_doc, pno, bbox, mask=mask):
                    blanks.append(src)
                    continue
                if figure.get('found') == 'uncertain_scan_key_panel':
                    try:
                        data = _scan_key_white_margin_jpeg(render_doc, pno, bbox)
                    except Exception as exc:
                        if unit_recovery.current() is not None and (
                                isinstance(exc, unit_recovery.UnitFailure) or unit_recovery.fatal(exc)):
                            raise
                        # A failed trim proof cannot suppress the key's primary image.
                        data = extract.crop_jpeg(render_doc, pno, bbox)
                else:
                    data = extract.crop_jpeg(render_doc, pno, bbox)
            except Exception as exc:                                  # pragma: no cover
                if unit_recovery.current() is not None and (
                        isinstance(exc, unit_recovery.UnitFailure) or unit_recovery.fatal(exc)):
                    raise
                if src in required_source_regions:
                    from .structural_ops import ContractError
                    raise ContractError('source region crop failed') from exc
                log.warning("reflow: figure %s could not be cropped: %s", src, exc)
                if figure.get("needs_ink"):
                    # Fail closed on the visible source, not on silence: when the
                    # measured territory cannot be cut, the whole printed page is the
                    # honest remainder -- the reader loses nothing that was there.
                    try:
                        data = extract.crop_jpeg(render_doc, pno, _page_rect(render_doc, pno))
                        whole_page_fallback = True
                    except Exception as exc:                          # pragma: no cover
                        if unit_recovery.current() is not None and (
                                isinstance(exc, unit_recovery.UnitFailure) or unit_recovery.fatal(exc)):
                            raise
                if data is None:
                    if unit_recovery.current() is not None:
                        raise ValueError("Source-owned figure could not be rendered") from exc
                    missing.append(src)
            finally:
                if ink_doc is not render_doc:ink_doc.close()
            if data is not None:
                required_pixels = (_required_transcript_pixel_protection(
                    doc, book, pno, figure, src, required_source_regions)
                    if src in required_source_regions else ())
                if required_pixels is not None and not whole_page_fallback:
                    furniture, protected = _source_furniture_pixel_boxes(book, pno, figure)
                    if not furniture and tuple(bbox) == tuple(figure['bbox']):
                        furniture, protected = _native_margin_furniture_pixel_boxes(
                            doc, book, pno, figure)
                    if furniture:
                        if src in required_source_regions:
                            data, exclusions = _exclude_source_furniture_pixels(
                                data, bbox, furniture, protected,
                                protected_pixel_boxes=required_pixels)
                        else:
                            data, exclusions = _exclude_source_furniture_pixels(
                                data, bbox, furniture, protected)
                        if exclusions:
                            figure['source_furniture_exclusions'] = exclusions
                if not figure.get('full_page') and (geometry.get('space') == 'reading' or
                        figure.get('found', '').startswith(('scan_', 'ocr_', 'uncertain_scan_'))):
                    from .crop_edges import trim_source_edges
                    if pno not in scan_edges:
                        scan_edges[pno] = _source_scan_edges(render_doc, pno)
                    data, edge_audit, dropped = trim_source_edges(data, bbox, scan_edges[pno],
                        _source_edge_boxes(book, pno, transform))
                    if edge_audit:
                        figure['source_scan_edge'] = edge_audit
                        figure['reading_bbox'] = list(_retained_source_rect(bbox, edge_audit))
                    if dropped and src not in required_source_regions:
                        figure['scan_edge_dropped'] = True
                        blanks.append(src)
                        continue
                from .crop_edges import trim_scan_borders
                data, trims = trim_scan_borders(data)
                if trims:
                    figure['scan_border_trim'] = trims
                    figure['reading_bbox'] = list(_retained_source_rect(
                        figure.get('reading_bbox', bbox), trims))
                # Outside the crop's failure handling: a crop the archive cannot take
                # is a failed build, never a figure reported lost from the source.
                writer = getattr(package, 'figure_image', package.image)
                writer(src, _figure_crop_margin(data, figure))
    return missing, blanks


def _page_rect(doc, pno):
    rect = doc[pno].rect
    return (rect.x0, rect.y0, rect.x1, rect.y1)


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
        chapter.blocks = [re.sub(
            r'<figure(?:\s[^>]*)?>\s*(?:<a\b[^>]*>\s*</a>\s*)?'
            r'<figcaption class="reflow-no-caption"></figcaption>\s*</figure>',
            '', _IMG_TAG.sub(strip, block)) for block in chapter.blocks]


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
                       should_stop=None, progress=None, source_pages=None,
                       runtime_progress=None, reading_contexts=None, raw_pages=None):
    """Package original pixels, never a re-render of the extracted reading.

    Detail crops retain adjacent printed context; the full original page lets a
    reader resolve associations outside a crop. All rendering uses extract's
    existing pre-allocation and encoded-byte bounds. These links are generated
    after the model markup boundary, not accepted from model output. Every image
    goes into ``package`` as it is rendered (:class:`_Package`), so no more than
    one of them is in memory at a time however long the book is.
    """
    evidence = {}
    recovered, readings, scanned = set(), {}, set()
    for pno, source in (source_pages or {}).items():
        with unit_recovery.guard(pno, "original_evidence_admission"):
            report = source.report()
            if report['uncertain']:recovered.add(pno)
            if report.get('source_readings'):readings[pno] = report['source_readings']
            if json.loads(source.provenance_json).get('layer') == 'ocr':scanned.add(pno)
    wanted = []
    for pno in page_html:
        with unit_recovery.guard(pno, "original_evidence_admission"):
            if book.needs_source_evidence(pno) or pno in recovered or pno in scanned or pno in readings:
                wanted.append(pno)
    for pno, inventory in getattr(book, 'source_inventory', {}).items():
        if pno not in page_html or pno in wanted:continue
        with unit_recovery.guard(pno, "original_evidence_admission"):
            if any(region['suggested_kind']=='furniture' and region['reason'] in (
                    'repeated_scan_spread_head','repeated_portrait_head')
                    for region in inventory.get('regions', [])):
                wanted.append(pno)
    # Native figures need an inspection route even when their extraction has no
    # uncertainty flag. Add navigation after layout admission; the model-owned
    # figure, caption and source words remain unchanged.
    native_figures = {}
    inspectable_figures = {}
    if doc is not None:
        for pno, html in page_html.items():
            with unit_recovery.guard(pno, "original_figure_evidence_admission"):
                figures = [(i, f) for i, f in enumerate(f for f in book.figures if f['pno'] == pno)
                           if f.get('found') != 'embedded_source_mark'
                           and 'images/fig_p%04d_%d.jpg' % (pno, i) in _IMG_SRC.findall(html)]
                if figures:
                    inspectable_figures[pno] = figures
                    if pno not in wanted:
                        native_figures[pno] = figures
                        wanted.append(pno)
    for index, pno in enumerate(wanted):
        _check_cancelled(should_stop)
        with unit_recovery.guard(pno, "original_source_evidence"):
            if progress is not None:
                progress(index, len(wanted))
            if runtime_progress is not None:
                runtime_progress({"kind": "evidence_page", "page": pno,
                                  "ordinal": index + 1, "total": len(wanted)})
            specs = []
            for region_index, region in enumerate(getattr(book, 'source_inventory', {}).get(pno, {}).get('regions', [])):
                if region['suggested_kind']=='furniture' and region['reason'] in ('repeated_scan_spread_head','repeated_portrait_head'):
                    specs.append(('furniture_%d' % region_index, 'Original printed running head', region['bbox']))
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
            elements = book.pages.get(pno, [])
            for element_index, key in _caption_keys(elements).items():
                specs.append((key, "Original printed caption", elements[element_index].bbox))
            for figure_index, figure in enumerate(f for f in book.figures if f['pno'] == pno):
                if figure.get('found') == 'embedded_source_mark':
                    specs.append(('source_mark_%d' % figure_index,
                                  'Original printed lettering and neighboring layout',
                                  figure['bbox']))
            for figure_index, figure in inspectable_figures.get(pno, []):
                reason=figure.get('found')
                label=('Original printed table and neighboring context' if reason in ('source_visual_table','source_scan_grid') else
                       'Original printed region and neighboring context' if reason in (
                           'ocr_uncertain_note','ocr_uncertain_region','native_spacing_uncertain','native_outline_conflict',
                           'unverified_scan_layout','unrecovered_scan_layer','unverified_paired_columns',
                           'uncertain_aligned_scan_list','sparse_scan_spread_panel','uncertain_scan_key_panel') else
                       'Original figure and neighboring context')
                specs.append(('figure_%d' % figure_index,
                              label, figure['bbox']))
            if not specs and pno not in recovered and pno not in scanned and pno not in readings and not book.needs_source_evidence(pno):
                continue
            if doc is None:
                if unit_recovery.current() is not None:
                    raise unit_recovery.SourceIntegrityError("Original PDF required for source evidence")
                raise ValueError("Original PDF required for uncertain source evidence on page %d" % pno)
            started = time.monotonic()
            from .source_display import SourceDisplay, inspection_tiles, MAX_INSPECTION_TILES
            provenance = json.loads(source_pages[pno].provenance_json) if source_pages and pno in source_pages else {}
            display = SourceDisplay(doc, pno, provenance)
            def package_source_image(src, data):
                # Inspection identities remain distinct; identical evidence bytes
                # can share one archive member and retain every inspection link.
                package._deduplicate_evidence = True
                try:
                    if pno in native_figures:
                        package.image(src, data, store=True)
                    else:
                        package.image(src, data)
                finally:
                    package._deduplicate_evidence = False
            def display_evidence(rect):
                # Lossless native pixels and their source predicates are an
                # existing evidence contract. Preserve them exactly; only lossy
                # display copies use the bounded shared inspection encoder.
                data=display.source_image(rect,scale=2 if pno in native_figures else 3,
                    lossless_candidate=pno not in native_figures)
                if pno in native_figures or data.startswith(b'\x89PNG\r\n\x1a\n') or not hasattr(package,'evidence_jpeg'):
                    return data
                return package.evidence_jpeg(display,rect,detail=True)
            source_edges = None
            def reading_crop(rect, data):
                # Primary artwork and its enlarged inspection must agree. The full
                # original remains pristine; only detached bars in scan crops are
                # trimmed. Alternative-reading evidence keeps its sealed bytes.
                if provenance.get('layer') != 'ocr' and display._native_bitonal() is None:
                    return rect, data, []
                nonlocal source_edges
                from .crop_edges import trim_scan_borders, trim_source_edges
                if source_edges is None:
                    source_edges = _source_scan_edges(display.query_document(), pno)
                protected = _source_edge_boxes(book, pno)
                if provenance.get('layer') != 'ocr':
                    protected = [tuple(display.reading_rect(extract.pymupdf.Rect(box)))
                                 for box in protected]
                data, edge_audit, dropped = trim_source_edges(data, tuple(rect), source_edges, protected)
                if dropped:
                    return rect, data, edge_audit
                rect = extract.pymupdf.Rect(_retained_source_rect(tuple(rect), edge_audit))
                data, trims = trim_scan_borders(data)
                if not trims:
                    return rect, data, edge_audit
                x0,y0,x1,y1 = trims[0]['retained_pixel_bbox']
                width,height = trims[0]['size']
                retained = extract.pymupdf.Rect(rect.x0+rect.width*x0/width,
                    rect.y0+rect.height*y0/height, rect.x0+rect.width*x1/width,
                    rect.y0+rect.height*y1/height)
                return retained, data, edge_audit + trims
            full = "images/original_p%04d.jpg" % pno
            package_source_image(full, display.source_image(scale=1.5, quality=85,
                                                            lossless_candidate=pno not in native_figures))
            details = []
            for entry in readings.get(pno,{}).get('entries',[]):
                src='images/original_p%04d_%s.jpg' % (pno,entry['id'])
                pixels=display.jpeg(entry['occurrence']['crop_bbox'],scale=3,quality=90)
                from .source_readings import sha
                if sha(pixels)!=entry['occurrence']['crop_sha256']:raise ValueError('reading crop changed')
                package_source_image(src,pixels)
                details.append(dict(id=entry['id'],label='Source reading: '+entry['original'],src=src,reading=entry,reading_bbox=entry['occurrence']['crop_bbox']))
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
                edge_proof = None
                if key.startswith(('text_', 'caption_', 'furniture_')):
                    reading_rect, edge_proof = display.complete_detail_rect(reading_rect)
                    rect = display.source_rect(reading_rect) * doc[pno].derotation_matrix
                original_reading_rect = reading_rect
                reading_rect, data, trims = reading_crop(reading_rect, display_evidence(reading_rect))
                if any(t.get('action') == 'drop_scan_carrier' for t in trims):
                    continue
                package_source_image(src, data)
                if trims:
                    rect = display.source_rect(reading_rect) * doc[pno].derotation_matrix
                details.append({"id": key, "label": label, "src": src, "bbox": list(rect),
                                "reading_bbox": list(reading_rect)})
                if trims:
                    details[-1].update(scan_border_trim=trims,
                        original_reading_bbox=list(original_reading_rect))
                if edge_proof is not None:details[-1]['source_edge_completion'] = edge_proof
            from .source_display import grid_regions
            # These new native routes inspect an already-owned figure region; they
            # need no raster grid inference. Preserve that analysis for the existing
            # uncertain/source-evidence path, without scanning native photographs.
            grids = ({} if pno in native_figures else
                     grid_regions(book, doc, pno, provenance, lambda: _check_cancelled(should_stop)))
            for element_index, proof in grids.items():
                if element_index != proof['element_indices'][0]:continue
                key = "layout_%d" % element_index
                src = "images/original_p%04d_%s.jpg" % (pno, key)
                rect, data, trims = reading_crop(extract.pymupdf.Rect(proof['reading_bbox']),
                    display_evidence(proof['reading_bbox']))
                if any(t.get('action') == 'drop_scan_carrier' for t in trims):
                    continue
                package.image(src, data)
                details.append({"id": key, "label": "Original layout and labels", "src": src, **proof})
                if trims:
                    details[-1].update(scan_border_trim=trims, reading_bbox=list(rect),
                        original_reading_bbox=proof['reading_bbox'])
            if pno in native_figures or provenance.get('layer') == 'ocr' or any(
                    f.get('found') in ('unrecovered_scan_layer','unverified_scan_layout','unverified_paired_columns') and f.get('pno') == pno
                    for f in book.figures):
                regions = ([d['reading_bbox'] for d in details if d['id'].startswith('figure_')]
                           if pno in native_figures else [display.rect])
                tiles = [tile for region in regions for tile in inspection_tiles(region)]
                if len(tiles) > MAX_INSPECTION_TILES:
                    raise ValueError('source inspection exceeds tile count bound')
                for tile_index, tile in enumerate(tiles):
                    _check_cancelled(should_stop)
                    key = "inspection_%d" % tile_index
                    src = "images/original_p%04d_%s.jpg" % (pno, key)
                    trimmed_tile, data, trims = reading_crop(tile, display_evidence(tile))
                    if any(t.get('action') == 'drop_scan_carrier' for t in trims):
                        continue
                    package_source_image(src, data)
                    details.append({"id": key, "label": "Original detail %d (row order)" % (tile_index + 1),
                        "src": src, "reading_bbox": list(trimmed_tile), "displayed_pdf_bbox": list(display.source_rect(trimmed_tile))})
                    if trims:
                        details[-1].update(scan_border_trim=trims, original_reading_bbox=list(tile))
            inspection = [d for d in details if d['id'].startswith('inspection_')]
            for detail in details:
                if detail not in inspection and detail.get('reading_bbox'):
                    region = extract.pymupdf.Rect(detail['reading_bbox'])
                    detail['inspection_ids'] = [d['id'] for d in inspection
                        if not (region & extract.pymupdf.Rect(d['reading_bbox'])).is_empty]
            evidence[pno] = {"page": pno, "href": "original-p%04d.xhtml" % pno,
                             "presentation_version": "source-inspection-access-1",
                             "full": full, "details": details,
                             "orientation": display.angle, "source_rotation": doc[pno].rotation,
                             "reading_rect": list(display.rect),
                             "ambiguous_notes": sorted(ambiguous),
                             "bytes": package.images[package.aliases.get(full,full)] + sum(package.images[package.aliases.get(d["src"],d["src"])] for d in details),
                             "render_seconds": round(time.monotonic() - started, 4)}
            if pno in readings:evidence[pno]['source_readings']=readings[pno]
            if pno in recovered:
                evidence[pno]['source_uncertainty'] = source_pages[pno].report()
            html = page_html[pno]
            href = evidence[pno]["href"]
            for figure_index, _ in inspectable_figures.get(pno, []):
                src = 'images/fig_p%04d_%d.jpg' % (pno, figure_index)
                pattern = r'<img\b[^>]*\bsrc="' + re.escape(src) + r'"[^>]*/>'
                html = re.sub(pattern, lambda m: '<a href="%s#figure_%d" '
                              'aria-label="Inspect original page and enlarged details">%s</a>' %
                              (href, figure_index, m.group(0)), html)
            if ambiguous:
                link = '<a href="%s#notes">Original printed notes and context</a>' % href
                html = ('<div class="source-evidence-notice"><p>Some note labels or associations '
                        'are uncertain. Ambiguous links are not used. %s.</p></div>\n' % link) + html

                def note_link(match):
                    ident = _ID.search(match.group("attrs"))
                    if ident and ident.group(1).removeprefix("fn_") in ambiguous:
                        return '<aside%s>%s<p class="source-evidence-notice">%s</p></aside>' % (
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
                if pno in readings:
                    target=readings[pno]['entries'][0]['id']
                    notice=_source_check_notice('Alternative source readings; original text retained', '%s#%s' % (href,target), 'Inspect all proposed readings', uncertain=True)
                    notices.append(notice);html=notice+'\n'+html
                if pno in recovered:
                    notice = _source_check_notice('OCR uncertain',
                        '%s#page' % href, 'View original page', uncertain=True)
                    notices.append(notice)
                    html = notice + '\n' + html
                if notices:
                    html = re.sub(r'(</h[1-6]>)', lambda m: m.group(0) + '\n' + '\n'.join(notices), html)
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
            if pno in readings:
                from . import source_reading_display
                context = (reading_contexts or {}).get(pno, {})
                html, placement = source_reading_display.render(book, doc,
                    source_pages[pno], context['raw'], html,
                    plan=context.get('plan'), previous_source=context.get('previous_source'),
                    previous_raw=context.get('previous_raw'))
                evidence[pno]['reading_display'] = placement
                states = {r['id']:r for r in placement['entries']}
                for entry in readings[pno]['entries']:
                    entry.update(states[entry['id']])
            if source_pages and pno in source_pages:
                from . import paragraph_presentation
                html, inset_audit = paragraph_presentation.render(book, doc, source_pages[pno],
                    (raw_pages or {}).get(pno), html,
                    excluded={i for proof in grids.values() for i in proof['element_indices']})
                if inset_audit['entries']:evidence[pno]['paragraph_presentation'] = inset_audit
                from . import glyph_presentation
                html, glyph_audit = glyph_presentation.render(book, doc, source_pages[pno],
                    (raw_pages or {}).get(pno), html, package)
                if glyph_audit['entries']:evidence[pno]['glyph_presentation'] = glyph_audit
            page_html[pno] = html
            if progress is not None:
                progress(index + 1, len(wanted))
    _check_cancelled(should_stop)
    return evidence


def _inspection_controls(record, ids):
    """Only existing, bound source details supply these navigation choices."""
    details = {d['id']:d for d in record['details']}
    if not ids:
        return ''
    if len(ids) != len(set(ids)) or any(key not in details or not key.startswith('inspection_') for key in ids):
        raise ValueError('source inspection route is not a bound detail')
    return ('<nav class="source-inspection-controls" aria-label="Larger original details">'
            '<p>Read larger original details in row order. These are overlapping source crops; no note identity is inferred.</p><ol>'
            + ''.join('<li><a class="source-control" href="#%s">%s</a></li>' %
                (key, escape(details[key]['label'])) for key in ids) + '</ol></nav>')


def _original_document(record, home, language, return_target=None, reading_returns=None):
    pno = record["page"]
    target = return_target or '%s#pg_%04d' % (home, pno)
    back = '<p><a class="source-control" href=%s>Return to reflowed PDF page %d</a></p>' % (quoteattr(target), pno + 1)
    # A fragment jump must land before the return link. On paginated readers a
    # target on the image section skips all earlier siblings, including the only
    # return on an ordinary original page. Keep a route at entry and after the
    # final source image for readers that continue through the full-page raster.
    body = ('<h1 id="page">Original PDF page %d</h1>%s<p>These are original printed pixels. '
            'Extracted labels and glyphs may be wrong; no note identity is inferred '
            'from the transcription. The details below preserve printed context.</p>'
            % (pno + 1, back))
    if record["details"]:
        body += '<nav class="source-inspection-controls" aria-label="Original source details"><h2>Inspect original details</h2><ol>'
        body += ''.join('<li><a class="source-control" href="#%s">%s</a></li>' % (d['id'], escape(d['label'])) for d in record['details'])
        body += '</ol></nav>'
    body += ('<section class="source-evidence"><h2>Complete original page</h2>'
             '<img src="%s" alt="Complete original PDF page %d"/></section>'
             % (record["full"], pno + 1))
    if record.get('source_uncertainty'):
        report = record['source_uncertainty']
        body += '<section class="source-evidence"><h2>OCR readings to check</h2><p>These tokens are retained as extracted. Some could not be highlighted in the reflowed text.</p><ul>'
        for index, item in enumerate(report['uncertain']):
            state = 'highlighted' if index in report['placed_record_indices'] else 'not highlighted'
            if index in report.get('artwork_record_indices',[]):state='in original figure; not highlighted in reflow text'
            elif index in report.get('qualified_caption_record_indices',[]):state='covered by the caption uncertainty notice'
            body += '<li>%s (%s)</li>' % (escape(item['token']), state)
        body += '</ul></section>'
    inspection = [d['id'] for d in record['details'] if d['id'].startswith('inspection_')]
    for detail in record["details"]:
        links = _inspection_controls(record, detail.get('inspection_ids', []))
        if detail['id'] in inspection:
            index = inspection.index(detail['id'])
            neighbors = [(inspection[index-1], 'Previous original detail')] if index else []
            if index+1 < len(inspection):
                neighbors.append((inspection[index+1], 'Next original detail'))
            links += '<nav class="source-inspection-controls" aria-label="Original detail sequence">' + ''.join(
                '<a class="source-control" href="#%s">%s</a>' % (key, label) for key,label in neighbors) + '</nav>'
        if detail.get('reading'):
            entry=detail['reading'];occurrence=entry['occurrence']
            label='Synthetic recognition fixture; not a qualified reading. ' if entry['synthetic'] else 'Independently reviewed proposed reading; original retained. '
            disclosure='<p>'+escape(label)+'Original: '+escape(entry['original'])+'.</p>'
            for alternative in entry['alternatives']:
                disclosure+='<p>Proposed reading: '+escape(alternative['text'])+' (confidence %.1f%%).</p>' % (100*alternative['confidence'])
            if not entry['alternatives']:disclosure+='<p>Uncertain reading; no alternative proposed.</p>'
            disclosure+='<p>This proposal applies to the pictured source occurrence. The reading text stays unchanged; no note association is inferred.</p>'
            if not entry.get('inline_placed'):
                disclosure += '<p>Inline reading unavailable: %s. Retained in this source detail only.</p>' % escape(entry.get('reason', 'placement_not_proved'))
            elif entry.get('return_id'):
                target = (reading_returns or {}).get(entry['return_id'])
                if not target:raise ValueError('reading annotation return target missing')
                disclosure += '<p><a class="source-control" href=%s>Return to this reading annotation</a></p>' % quoteattr(target)
            links=disclosure+links
        # A visible heading gives paginated readers a stable detail landing point.
        body += ('<section class="source-evidence"><h2 id="%s">%s</h2>%s%s<img src="%s" alt="%s"/>%s</section>'
                 % (detail['id'], escape(detail["label"]), back, links, detail["src"],
                    escape(detail["label"]), links + back))
    body += back
    return _document("Original PDF page %d" % (pno + 1), body, language)


def build(book, out_path, page_html=None, metadata=None, doc=None,
          report_html=None, sidecar=None, identifier=None, figure_transform=None,
          should_stop=None, evidence_progress=None, operation_plans=(), source_pages=None,
          runtime_progress=None, layout_plans=(), raw_pages=None):
    """Write one EPUB 3 and say what went into it.

    ``report_html`` is called last, with the document each page marker landed in and
    the list of things the build itself could not place -- neither is known until
    the split and the crops are done.
    """
    from .native_ipc import NativeDocument
    if isinstance(doc, NativeDocument):
        layout_arguments = dict(layout_plans=layout_plans, raw_pages=raw_pages) if layout_plans else {}
        return doc.build(book, out_path, page_html=page_html, metadata=metadata,
            report_html=report_html, sidecar=sidecar, identifier=identifier,
            figure_transform=figure_transform, should_stop=should_stop,
            evidence_progress=evidence_progress, operation_plans=operation_plans,
            source_pages=source_pages, runtime_progress=runtime_progress,
            **layout_arguments)
    metadata = dict(metadata or {})
    language = metadata.get("language") or "en"
    source_pages = dict(source_pages or {})
    for pno, source in source_pages.items():
        from .enriched_source import SourcePage
        if not isinstance(source, SourcePage) or source.page != pno:
            raise ValueError('canonical source pages required')
        source.validate(book)
        if source.report().get('source_readings'):
            from . import source_readings,enriched_source
            if doc is None:raise ValueError('reading evidence requires original PDF')
            base=enriched_source.prepare_source_page(book,pno,json.loads(source.provenance_json),json.loads(source.records_json))
            source_readings.replay(book,doc,base,(raw_pages or {}).get(pno) or extract.read_page(doc,pno),source)
    canonical = lambda pno: source_pages[pno].html if pno in source_pages else page_fragment(book, pno)
    if page_html is None:
        page_html = {pno: canonical(pno) for pno in sorted(book.pages)}
    if layout_plans and not set(page_html).issubset(source_pages):
        from .structural_ops import ContractError
        raise ContractError('layout publication requires factory source for every output and fallback page')
    current_page_html = dict(page_html) if operation_plans else {}
    # Source evidence also governs direct builder callers. Do this before XML
    # character filtering, so no raw source character is reintroduced afterward.
    page_html = {pno: canonical(pno) if book.needs_source_evidence(pno) or
                 book.has_source_navigation(pno) or pno in source_pages else html
                 for pno, html in page_html.items()}
    generated_pages={pno:html for pno,html in page_html.items()
                     if book.needs_source_evidence(pno) or book.has_source_navigation(pno)
                     or pno in source_pages}
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
    from . import layout_build
    compiled_layouts = layout_build.compile_pages(book, doc, layout_plans,
        source_pages, dict(raw_pages or {}), page_html, seen_pages)
    for pno, compiled in compiled_layouts.items():
        page_html[pno] = compiled.page_html
        generated_pages[pno] = compiled.page_html
    layout_boundaries = layout_build.boundaries(book, doc, layout_plans, source_pages, dict(raw_pages or {}))
    from . import transcript_regions
    required_source_regions = transcript_regions.required_images(book, page_html, doc)
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
    from . import image_budget
    package = _Package(out_path, source_bytes=image_budget.source_size(doc))
    try:
        from .native_text import package_glyphs
        if runtime_progress is not None:
            runtime_progress({"kind": "phase", "phase": "native_glyphs"})
        package_glyphs(book,page_html,doc,package,should_stop)
        if runtime_progress is not None:
            runtime_progress({"kind": "phase", "phase": "source_evidence"})
        reading_contexts = {}
        plans_by_page = {plan.prepared.page:plan for plan in layout_plans}
        for pno, source in source_pages.items():
            if source.report().get('source_readings'):
                plan = plans_by_page.get(pno)
                reading_contexts[pno] = dict(raw=(raw_pages or {}).get(pno) or extract.read_page(doc,pno), plan=plan)
                if plan and json.loads(plan.prepared.contract_json).get('previous'):
                    reading_contexts[pno].update(previous_source=source_pages[pno-1], previous_raw=raw_pages[pno-1])
        evidence = _original_evidence(book, page_html, doc, package, figure_transform,
                                      should_stop, evidence_progress, source_pages,
                                      runtime_progress, reading_contexts, raw_pages)
        # Ordinary native pages need the same measured insets without paying
        # the cost of original-page raster evidence they do not otherwise need.
        from . import paragraph_presentation
        paragraph_evidence = {pno:dict(page=pno,paragraph_presentation=record['paragraph_presentation'])
                              for pno,record in evidence.items() if 'paragraph_presentation' in record}
        for pno, source in source_pages.items():
            if pno in evidence or pno in compiled_layouts:continue
            _check_cancelled(should_stop)
            page_html[pno], inset_audit = paragraph_presentation.render(book, doc, source,
                (raw_pages or {}).get(pno), page_html[pno])
            if inset_audit['entries']:
                paragraph_evidence[pno] = dict(page=pno,paragraph_presentation=inset_audit)

        if runtime_progress is not None:
            runtime_progress({"kind": "phase", "phase": "figure_crops"})
        figure_fragments = [Chapter(index=0, title="", blocks=list(page_html.values()))]
        figure_presentation = {}
        missing, blanks = _figure_images(figure_fragments, doc, book, package,
                                         figure_transform=figure_transform,
                                         owned_images=set(package.images)|set(package.aliases),
                                         runtime_progress=runtime_progress,
                                         required_source_regions=required_source_regions,
                                         figure_presentation=figure_presentation)
        if set(required_source_regions).intersection(missing + blanks) or not all(
                package.aliases.get(src, src) in package.images for src in required_source_regions):
            from .structural_ops import ContractError
            raise ContractError('source region image could not be packaged')
        protected_layout_images = {src for c in compiled_layouts.values() for src in _IMG_SRC.findall(c.page_html)}
        if protected_layout_images.intersection(missing + blanks):
            from .structural_ops import ContractError
            raise ContractError('protected layout resource could not be packaged')
        _drop_images(figure_fragments, missing + blanks)
        page_html = dict(zip(page_html, figure_fragments[0].blocks))

        pages = _page_blocks(page_html)
        executed_layout_boundaries = set()
        parenthetical_seams = (assemble.parenthetical_page_seams(book, raw_pages or {})
            if doc is not None and book.source_fingerprint==extract.document_fingerprint(doc) and not doc.is_dirty else set())
        joins = _join_page_turns(pages, book.title_pages, compiled_layouts, layout_boundaries, executed_layout_boundaries,
            words=getattr(book, 'page_wrap_words', None) or None,
            compounds=getattr(book, 'page_wrap_compounds', ()), raster_wraps=_raster_wraps(book),
            parenthetical_seams=parenthetical_seams)
        contents_pages = _source_contents_pages(pages, doc)
        chapters = _chapters(pages, book.title_pages, contents_pages)
        from . import reading_flow
        reading_flow_audit = reading_flow.finish(chapters, book)
        chapter_images = {src for chapter in chapters for src in _IMG_SRC.findall('\n'.join(chapter.blocks))}
        if not set(required_source_regions) <= chapter_images:
            from .structural_ops import ContractError
            raise ContractError('source region image absent from publication')
        protected_layout_targets = {target for pno,c in compiled_layouts.items() for target in _HREF.findall(_scope_ids(c.page_html,pno))}
        dropped = _bind_links(chapters)
        if protected_layout_targets.intersection(dropped):
            from .structural_ops import ContractError
            raise ContractError('protected layout link target is absent from publication')
        images = package.images
        for chapter in chapters:
            chapter.blocks = [_image_aliases(block,package.aliases) for block in chapter.blocks]
        evidence = _resource_aliases(evidence,package.aliases)

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
        source_returns = _source_return_targets(chapters)

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

        # Use only literal, resolved internal PDF navigation. Its labels never
        # replace body text, and destinations are our generated source-page anchors.
        authored = extract.outline(doc) if doc is not None else []
        from .skeleton import outline_is_useful
        authored = [entry for entry in authored if entry['pno'] in page_homes] if outline_is_useful(authored) else []
        for chapter in chapters:
            documents[chapter.href] = _document(chapter.title,
                                                "\n".join(chapter.blocks), language)
            manifest.append({"id": chapter.item_id, "href": chapter.href,
                             "type": "application/xhtml+xml"})
            spine.append(chapter.item_id)
            if not authored:
                entries.append((chapter.href, chapter.title))
        if authored:
            # Navigation follows emitted reading order. Preserve every literal
            # label and stable ties, including sections sharing one source page.
            authored.sort(key=lambda entry: entry['pno'])
            first = min(page_homes)
            if first != authored[0]['pno']:
                entries.append((page_homes[first] + '#pg_%04d' % first, 'Beginning'))
            entries.extend((page_homes[entry['pno']] + '#pg_%04d' % entry['pno'], entry['title'])
                           for entry in authored)

        # An ordinary spine item also works in readers which ignore EPUB page-list.
        # Keep this generated reference after the book, never inside its prose.
        if page_homes:
            furniture = _printed_furniture(book, page_homes, language, evidence)
            if furniture:
                documents[FURNITURE_HREF] = furniture
                manifest.append({'id': 'printed-furniture', 'href': FURNITURE_HREF,
                                 'type': 'application/xhtml+xml'})
                spine.append('printed-furniture')
                entries.append((FURNITURE_HREF, 'Printed running heads and folios'))
            documents[SOURCE_CHECKS_HREF] = _source_checks(language, evidence)
            manifest.append({"id": "source-checks", "href": SOURCE_CHECKS_HREF,
                             "type": "application/xhtml+xml"})
            spine.append("source-checks")
            entries.append((SOURCE_CHECKS_HREF, "Source checks"))
            documents[SOURCE_INDEX_HREF] = _source_index(page_homes, language, evidence)
            manifest.append({"id": "source-pages", "href": SOURCE_INDEX_HREF,
                             "type": "application/xhtml+xml"})
            spine.append("source-pages")
            entries.append((SOURCE_INDEX_HREF, "Source PDF pages"))

        reading_returns = {}
        for chapter in chapters:
            for block in chapter.blocks:
                for ident in _ID.findall(block):
                    if ident.startswith('reading-return-reading-'):
                        if ident in reading_returns:raise ValueError('duplicate reading annotation return target')
                        reading_returns[ident] = chapter.href+'#'+ident
        for pno, record in sorted(evidence.items()):
            ident = "original-p%04d" % pno
            documents[record["href"]] = _original_document(record, page_homes[pno], language,
                                                          source_returns.get(pno), reading_returns)
            manifest.append({"id": ident, "href": record["href"], "type": "application/xhtml+xml"})
            spine.append(ident)

        for index, src in enumerate(sorted(images)):
            manifest.append({"id": "img%03d" % index, "href": src, "type": package.media_types[src]})

        payload = _sidecar(book, pages, chapters, images, joins, sidecar, blanks)
        payload['reading_flow'] = reading_flow_audit
        # Geometry of delivered resources is presentation evidence, not a
        # change to the immutable source figure records. Resolve image aliases
        # in keys explicitly: the generic sidecar alias walker changes values.
        payload['figure_presentation'] = {package.aliases.get(src, src):record
            for src, record in figure_presentation.items()}
        payload['scan_border_trims'] = [dict(page=f['pno'], bbox=list(f['bbox']),
            trims=f['scan_border_trim']) for f in figure_presentation.values()
            if f.get('scan_border_trim')]
        if compiled_layouts:
            payload['layout_operations'] = layout_build.report(compiled_layouts)
            bound_notes = payload['layout_operations'].get('note_bindings_applied', 0)
            if bound_notes:
                # Checked note targets are ordinary source atoms, never the
                # opaque numbered Book.notes containers. Equal printed labels
                # on distinct source notes do not identify the same note.
                payload['notes_source'] = sum(1 for n in book.notes if n.num is not None)
                payload['notes_bound'] = bound_notes
                payload['notes'] = payload['notes_source'] + bound_notes
            payload['layout_operations']['boundaries'] = [dict(right_page=pno, **layout_boundaries[pno]) for pno in sorted(executed_layout_boundaries)]
        if source_pages:
            payload['source_enrichment'] = {str(pno): dict(source.report(), identity=source.identity,
                provenance=json.loads(source.provenance_json), raw_records=json.loads(source.records_json))
                for pno, source in source_pages.items() if pno in page_html}
        if parenthetical_seams:payload['source_parenthetical_page_seams_eligible'] = sorted(parenthetical_seams)
        if paragraph_evidence:
            payload['paragraph_presentation'] = list(paragraph_evidence.values())
        if evidence:
            payload["source_evidence"] = list(evidence.values())
            if reading_contexts:
                payload['generated_evidence_semantics'] = ('source-reading-annotation is generated original/alternative disclosure, '
                    'excluded from source conservation; canonical source, original glyph resources and note associations are unchanged')
        payload = _resource_aliases(payload,package.aliases)
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
        if runtime_progress is not None:
            runtime_progress({"kind": "phase", "phase": "archive_finalize"})
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
        "source_document_sha256": book.source_fingerprint,
        "source_navigation": book.source_navigation,
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


def _image_aliases(html,aliases):
    def image(match):
        before=match.group(1);after=aliases.get(before,before)
        return match.group(0).replace('src="'+before+'"','src="'+after+'"',1)
    return _IMG_SRC.sub(image,html)


def _resource_aliases(value,aliases):
    if isinstance(value,dict):return {key:_resource_aliases(item,aliases) for key,item in value.items()}
    if isinstance(value,list):return [_resource_aliases(item,aliases) for item in value]
    if isinstance(value,str):return aliases.get(value,value)
    return value


def _encoded_image_href(href, data):
    """Name image resources for their encoded bytes, preserving caller aliases."""
    extension = ('.png' if data.startswith(b'\x89PNG\r\n\x1a\n') else
                 '.jpg' if data.startswith(b'\xff\xd8') else None)
    return posixpath.splitext(href)[0] + extension if extension else href


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

    def __init__(self, out_path, *, source_bytes=0):
        from .image_budget import limit
        limit(source_bytes)
        self.source_bytes = source_bytes
        self._image_digests = {}
        self._resource_hashes = {}
        self.path = str(out_path)
        self.tmp = "%s.tmp" % self.path
        directory = os.path.dirname(self.path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        #: href -> encoded bytes, for the manifest, the sidecar and each evidence
        #: record. The pixels themselves are only ever in the archive.
        self.images = {}
        self.aliases = {}
        self.media_types = {}
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

    def evidence_jpeg(self, display, rect, *, detail=False):
        from .image_budget import EvidenceRaster
        if not hasattr(self, '_evidence_raster'):
            self._evidence_raster = EvidenceRaster()
        return self._evidence_raster.jpeg(display, rect, detail=detail)

    def figure_image(self, href, data):
        # The PNG/JPEG encoder already compressed these pixels. Avoid a second
        # compressor and its in-flight workspace while streaming each plate.
        self.image(href, data, store=True)

    def disclosure_image(self, href, display, rect):
        # Whole-page context has one physical owner. Separate cropped regions
        # keep their identities even when their encoded samples are identical.
        self._deduplicate_evidence = tuple(rect) == tuple(display.rect)
        try:
            self.image(href, self.evidence_jpeg(display, rect))
        finally:
            self._deduplicate_evidence = False

    def image(self, href, data, *, store=False, deduplicate=False):
        requested=href
        png=data.startswith(b'\x89PNG\r\n\x1a\n')
        href=_encoded_image_href(href,data)
        if requested in self.aliases or href in self.images:
            # A dictionary kept the last of two renders under one name; an archive
            # would keep both, and a reader could open either.
            raise ValueError("the image %s was rendered twice" % href)
        import hashlib
        digest = hashlib.sha256(data).hexdigest()
        existing = self._image_digests.get(digest) if (deduplicate or getattr(self, '_deduplicate_evidence', False)) else None
        if existing is not None:
            self.aliases[requested] = existing
            return
        if store:
            # These native-figure source assets are already JPEG/PNG encoded.
            # DEFLATE reserves a multi-megabyte workspace for each tile even
            # when it saves almost no archive bytes, obscuring the renderer's
            # one-image-at-a-time memory bound.
            info = zipfile.ZipInfo(posixpath.join(OEBPS, href))
            info.compress_type = zipfile.ZIP_STORED
            self._zf.writestr(info, data)
        else:
            self._zf.writestr(posixpath.join(OEBPS, href), data)
        self._image_digests[digest] = href
        self._resource_hashes[href] = digest
        self.images[href] = len(data)
        self.media_types[href] = 'image/png' if png else 'image/jpeg'
        if requested!=href:self.aliases[requested]=href

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
        from .glyph_presentation import metric_stylesheet
        from .paragraph_presentation import stylesheet as inset_stylesheet
        zf.writestr("%s/style.css" % OEBPS, STYLESHEET +
                    metric_stylesheet(parts['sidecar'].get('source_evidence', [])) +
                    inset_stylesheet(parts['sidecar'].get('paragraph_presentation', [])))
        for href, text in parts["documents"].items():
            zf.writestr(posixpath.join(OEBPS, href), text)
        zf.close()
        from .image_budget import check
        try:
            check(self.source_bytes, os.path.getsize(self.tmp))
        except BaseException:
            self.abandon()
            raise
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
#: URL-built attributes the builder never writes at all.
_NEVER_WRITTEN_URLS = frozenset(("srcset", "poster", "background", "action",
                                 "formaction", "data", "http-equiv"))


def _written_glyph_style(element, parent):
    """Only the exact bounded image/link shape emitted by native_text.glyph_html.

    This does not extend the external/model markup gate or authorize these CSS
    values on arbitrary nodes. The normal URL/resource checks still apply.
    """
    if element.tag != _XHTML_NS+'img' or parent is None or parent.tag != _XHTML_NS+'a':
        return False
    if element.get('style') not in ('height:1em;width:auto;vertical-align:baseline',
                                    'max-width:100%;height:auto'):
        return False
    if set(element.attrib) != {'src','alt','style'} or set(parent.attrib) != {'class','href','title'}:
        return False
    if parent.get('class') != 'source-glyph' or len(parent) != 1 or parent.text or len(element) or element.text or element.tail:
        return False
    labels = ('Original source text; Unicode encoding unavailable. Open original page.',
              'Original source text; transcription uncertain. Open original page.')
    if element.get('alt') == 'Original printed note layout. Open original page.':
        if element.get('style') != 'max-width:100%;height:auto':
            return False
        labels += ('Original printed note layout. Open original page.',)
    if element.get('alt') not in labels or parent.get('title') != element.get('alt'):
        return False
    page = re.fullmatch(r'original-p(\d{4,})\.xhtml#page', parent.get('href',''))
    return bool(page and re.fullmatch(r'images/glyph_p'+page.group(1)+r'_[a-f0-9]{20}\.(?:jpg|png)', element.get('src','')))


def _written_word_preview_style(element, parent):
    """Bounded factory metrics apply only to a local source-word image link."""
    if (element.tag != _XHTML_NS+'img' or parent is None
            or parent.tag != _XHTML_NS+'a' or parent.get('class') != 'source-word-image'
            or len(parent) != 1 or parent.text or len(element)
            or set(element.attrib) != {'src','alt','style'}
            or set(parent.attrib) not in ({'class','href'}, {'class','href','title'})
            or ('title' in parent.attrib and parent.get('title') != 'Inspect original print')):
        return False
    if not re.fullmatch(r'(?:[^/#]+\.xhtml)?#v4-(?:d|native)-[A-Za-z0-9_-]+',parent.get('href','')):
        return False
    if not re.fullmatch(r'images/v4-[A-Za-z0-9_-]+\.(?:png|jpg)',element.get('src','')):
        return False
    metric=re.fullmatch(r'height:([0-9]+(?:\.[0-9]+)?)em;vertical-align:(-[0-9]+(?:\.[0-9]+)?)em;width:auto',element.get('style',''))
    return bool(metric and 0<float(metric[1])<=8 and -4<=float(metric[2])<0)


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
    parents = {child: parent for parent in root.iter() for child in parent}
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
            elif key == "style" and not (_WRITTEN_STYLE.match(value) or _written_glyph_style(element, parents.get(element)) or _written_word_preview_style(element, parents.get(element))):
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


def _navigation_problems(documents):
    """Bounded checks for the navigation contracts our builder actually uses."""
    errors=[];opf=documents.get(OEBPS+'/content.opf')
    if opf is None:return errors
    ns='{http://www.idpf.org/2007/opf}'
    items={node.get('id'):posixpath.normpath(posixpath.join(OEBPS,node.get('href','')))
           for node in opf.iter(ns+'item')}
    spine={items.get(node.get('idref')):i for i,node in enumerate(opf.iter(ns+'itemref'))}
    anchors={name:{node.get('id'):i for i,node in enumerate(root.iter()) if node.get('id')}
             for name,root in documents.items() if name.endswith('.xhtml')}
    def target(name,href):
        file,_,anchor=unquote(href).partition('#')
        path=posixpath.normpath(posixpath.join(posixpath.dirname(name),file)) if file else name
        return path,anchor
    for name,root in documents.items():
        if name.endswith('.ncx'):
            seen={};namespace='{http://www.daisy.org/z3986/2005/ncx/}'
            for point in root.iter(namespace+'navPoint'):
                content=point.find(namespace+'content')
                if content is None:continue
                key=target(name,content.get('src',''));order=point.get('playOrder','')
                if not order.isdecimal() or int(order)<1:
                    errors.append('navigation %s has an invalid NCX playOrder' % name)
                elif key in seen and seen[key]!=order:
                    errors.append('navigation %s gives one target different NCX playOrder values' % name)
                seen[key]=order
        if not name.endswith('.xhtml'):continue
        for nav in root.iter(_XHTML_NS+'nav'):
            if 'toc' not in nav.get('{http://www.idpf.org/2007/ops}type','').split():continue
            previous=None
            for link in nav.iter(_XHTML_NS+'a'):
                path,anchor=target(name,link.get('href',''))
                if path not in spine or path not in anchors or (anchor and anchor not in anchors[path]):
                    errors.append('navigation %s has an unresolved reading-order target' % name);continue
                position=(spine[path],anchors[path].get(anchor,0))
                if previous is not None and position<previous:
                    errors.append('navigation %s goes backward in emitted reading order' % name)
                previous=position
    return errors


def validate(path):
    """Validate the EPUB package, documents and internal link targets.

    Well-formed ZIP entries alone do not prove readable XHTML. Duplicate IDs
    and missing targets are rejected before publication."""
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
            declared, followed, active, documents = {}, {}, [], {}
            present = set(names)
            for name in names:
                if not name.endswith((".xhtml", ".opf", ".ncx", ".xml")):
                    continue
                try:
                    root = ElementTree.fromstring(zf.read(name))
                except ElementTree.ParseError as exc:
                    problems.append("%s does not parse: %s" % (name, exc))
                    continue
                documents[name]=root
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
            _too_many(problems, _navigation_problems(documents), "invalid navigation contracts")
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
