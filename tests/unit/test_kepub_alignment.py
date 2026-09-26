# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""A Kobo span and a KOReader XPointer name the same word, through the text (#324).

Ground truth: ``alice-pg11.kepub.epub`` is exactly what CWNG serves a Kobo for
``alice-pg11.epub`` (kepubify v4.0.4, then the normalizer, which splits the
Gutenberg chapter files into pieces with new names), and each row of
``alice-pg11.kobo-spans.json`` pairs a span with the word KOReader's own
engine reports starting there in the EPUB (``engine/kobo_spans.py``).
"""

from __future__ import annotations

import json
import zipfile

import pytest

from cps.services import kepub_alignment as ka
from tests.unit.test_koreader_xpointer import FIXTURES

pytestmark = pytest.mark.unit

EPUB = FIXTURES / "alice-pg11.epub"
KEPUB = FIXTURES / "alice-pg11.kepub.epub"


def _rows():
    return json.loads((FIXTURES / "alice-pg11.kobo-spans.json").read_text(encoding="utf-8"))


def test_a_kobo_span_lands_on_the_word_koreader_starts_there_and_back():
    rows = _rows()
    wrong = [(row, ka.span_to_xpointer(EPUB, KEPUB, row["source"], row["span"]),
              ka.xpointer_to_span(EPUB, KEPUB, row["xpointer"]))
             for row in rows]
    wrong = [w for w in wrong if w[1] != w[0]["xpointer"]
             or w[2] != (w[0]["source"], w[0]["span"])]
    assert not wrong, wrong[:3]
    assert len(rows) > 400
    # Split pieces (names the EPUB does not have) and whole files both occur.
    assert any("-split-" in row["source"] for row in rows)
    assert any("-split-" not in row["source"] for row in rows)


def test_every_span_of_the_book_crosses_and_comes_back():
    kepub = ka._kepub(KEPUB)
    spans = [(d.member, span) for d in kepub.documents
             for span, (start, end) in d.spans.items() if end > start]
    lost = [s for s in spans
            if ka.xpointer_to_span(EPUB, KEPUB, ka.span_to_xpointer(EPUB, KEPUB, *s) or "") != s]
    assert not lost, lost[:5]
    assert len(spans) > 2000


def _copy_with(tmp_path, source, name, member, old, new):
    """A copy of ``source`` whose ``member`` has ``old`` replaced once in its body."""
    path = tmp_path / name
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(path, "w") as copy:
        for info in original.infolist():
            data = original.read(info)
            if info.filename == member:
                head, body = data.decode("utf-8").split("<body", 1)
                assert old in body
                data = (head + "<body" + body.replace(old, new, 1)).encode("utf-8")
            copy.writestr(info, data)
    return path


def test_a_book_whose_text_changed_anywhere_is_not_aligned(tmp_path):
    row = _rows()[0]
    assert ka.span_to_xpointer(EPUB, KEPUB, row["source"], row["span"]) == row["xpointer"]
    # One letter changed in the LAST chapter of the EPUB: every offset is
    # still right for the first chapter, but the books no longer match, so
    # nothing crosses (the KEPUB is not this EPUB's).
    with zipfile.ZipFile(EPUB) as archive:
        last = [n for n in archive.namelist() if n.endswith("-h-12.htm.html")][0]
    edited = _copy_with(tmp_path, EPUB, "edited.epub", last, "Alice", "Alise")
    assert ka.span_to_xpointer(edited, KEPUB, row["source"], row["span"]) is None
    assert ka.xpointer_to_span(edited, KEPUB, row["xpointer"]) is None


def test_spans_and_sources_the_kepub_does_not_hold_are_not_placed(tmp_path):
    row = _rows()[0]
    source, span = row["source"], row["span"]
    for bad_source, bad_span in [
        (source, "kobo.9999.1"),                 # no such span
        (source, "kobo.1.1'] | //*[@id='x"),     # not a span id at all
        ("OEBPS/missing.html", span),            # no such document
        ("../" + source, span),                  # outside the archive
        ("/" + source, span),
        ("", span),
    ]:
        assert ka.span_to_xpointer(EPUB, KEPUB, bad_source, bad_span) is None, bad_source
    assert ka.span_to_xpointer(EPUB, tmp_path / "missing.kepub.epub", source, span) is None


def _book(path, bodies, spans=False):
    """A one-file-per-body EPUB (or, with ``spans``, its KEPUB twin)."""
    manifest = "".join(f'<item id="c{i}" href="c{i}.xhtml" media-type="application/xhtml+xml"/>'
                       for i in range(len(bodies)))
    spine = "".join(f'<itemref idref="c{i}"/>' for i in range(len(bodies)))
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf", '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">t</dc:identifier><dc:title>t</dc:title><dc:language>en</dc:language></metadata>'
                   f"<manifest>{manifest}</manifest><spine>{spine}</spine></package>")
        for i, body in enumerate(bodies):
            if spans:
                body = f'<div id="book-columns"><div id="book-inner">{body}</div></div>'
            z.writestr(f"OEBPS/c{i}.xhtml", '<?xml version="1.0" encoding="utf-8"?>\n'
                       '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title></head>'
                       f"<body>{body}</body></html>")
    return path


def test_text_outside_every_span_has_no_span(tmp_path):
    epub = _book(tmp_path / "b.epub", ["<p>One. Two.</p>\n<p>Three</p>"])
    kepub = _book(tmp_path / "b.kepub.epub", [
        '<p><span class="koboSpan" id="kobo.1.1">One. </span>'
        '<span class="koboSpan" id="kobo.1.2">Two.</span></p>\n<p>Three</p>'], spans=True)
    assert ka.span_to_xpointer(epub, kepub, "OEBPS/c0.xhtml", "kobo.1.2") == (
        "/body/DocFragment/body/p[1]/text().5")
    assert ka.xpointer_to_span(epub, kepub, "/body/DocFragment/body/p[1]/text().6") == (
        "OEBPS/c0.xhtml", "kobo.1.2")
    assert ka.xpointer_to_span(epub, kepub, "/body/DocFragment/body/p[2]/text().0") is None


def test_a_span_without_text_is_no_place(tmp_path):
    # Its offset is the NEXT text's, which may be in another chapter.
    epub = _book(tmp_path / "b.epub", ["<p>One.</p>", "<p>Two.</p>"])
    kepub = _book(tmp_path / "b.kepub.epub", [
        '<p><span class="koboSpan" id="kobo.1.1">One.</span>'
        '<span class="koboSpan" id="kobo.1.2"></span></p>',
        '<p><span class="koboSpan" id="kobo.1.1">Two.</span></p>'], spans=True)
    assert ka.span_to_xpointer(epub, kepub, "OEBPS/c0.xhtml", "kobo.1.1") == (
        "/body/DocFragment[1]/body/p/text().0")
    assert ka.span_to_xpointer(epub, kepub, "OEBPS/c0.xhtml", "kobo.1.2") is None


def test_a_chapter_split_in_two_maps_into_the_right_piece(tmp_path):
    epub = _book(tmp_path / "b.epub", ["<h1>I</h1><p>Alpha beta.</p><h1>II</h1><p>Gamma.</p>"])
    kepub = _book(tmp_path / "b.kepub.epub", [
        '<h1><span class="koboSpan" id="kobo.1.1">I</span></h1>'
        '<p><span class="koboSpan" id="kobo.2.1">Alpha beta.</span></p>',
        '<h1><span class="koboSpan" id="kobo.1.1">II</span></h1>'
        '<p><span class="koboSpan" id="kobo.2.1">Gamma.</span></p>'], spans=True)
    assert ka.span_to_xpointer(epub, kepub, "OEBPS/c1.xhtml", "kobo.2.1") == (
        "/body/DocFragment/body/p[2]/text().0")
    assert ka.span_to_xpointer(epub, kepub, "OEBPS/c0.xhtml", "kobo.2.1") == (
        "/body/DocFragment/body/p[1]/text().0")
    assert ka.xpointer_to_span(epub, kepub, "/body/DocFragment/body/p[2]/text().2") == (
        "OEBPS/c1.xhtml", "kobo.2.1")
