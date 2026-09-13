# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Stage 5: the file a reader actually opens.

Two kinds of failure live here and they fail differently. A malformed EPUB fails
loudly — the reader refuses the file — and is caught by parsing what we wrote. A
*well-formed* EPUB that has quietly dropped a paragraph, moved a footnote away from
its marker or linked a note button to an id in another file opens perfectly and is
wrong, which is why the page the builder emits is measured against the page the PDF
printed with the same gate the model's answers go through.
"""

import collections
import json
import posixpath
import re
import zipfile
from xml.etree import ElementTree as ET

import pytest

from cps.services.reflow import assemble, build_epub, gate
from tests.fixtures import reflow_pdfs as F

pytestmark = pytest.mark.unit

XHTML = "{http://www.w3.org/1999/xhtml}"
OPF = "{http://www.idpf.org/2007/opf}"
CONTAINER = "{urn:oasis:names:tc:opendocument:xmlns:container}"
NCX = "{http://www.daisy.org/z3986/2005/ncx/}"


def _book(*builders):
    doc = F.new_doc()
    for build in builders:
        build(doc)
    try:
        return assemble.deterministic_book(doc)
    finally:
        doc.close()


def _fragments(book):
    return {pno: build_epub.page_fragment(book, pno) for pno in sorted(book.pages)}


def _build(book, tmp_path, **kwargs):
    kwargs.setdefault("page_html", _fragments(book))
    kwargs.setdefault("metadata", {"title": "Hellenistic Astrology",
                                   "authors": ["Chris Brennan"], "language": "en"})
    return build_epub.build(book, str(tmp_path / "out.epub"), **kwargs)


def _xhtml_names(zf):
    """Every XHTML document in the package, navigation included."""
    return [n for n in zf.namelist() if n.endswith(".xhtml")]


def _content_names(zf):
    """The documents that carry the book's text: not the machine navigation."""
    return [n for n in _xhtml_names(zf)
            if posixpath.basename(n) not in ("nav.xhtml", "reflow-about.xhtml")]


_BODY = re.compile(r"<body[^>]*>(.*)</body>", re.S)


def _body(zf, name):
    """The text of a document, without the <head> that repeats its title."""
    text = zf.read(name).decode("utf-8")
    match = _BODY.search(text)
    return match.group(1) if match else text


def _words(text, markup=False):
    return collections.Counter(gate.normalise(text, markup=markup))


# --------------------------------------------------------------- the page as printed

def test_the_page_the_builder_writes_says_what_the_page_printed():
    """The deterministic fragment is measured with the same gate a model answer is.

    If the two disagree, every model answer is being scored against a yardstick the
    deterministic pass itself would fail, and the gate's verdicts mean nothing.
    """
    book = _book(F.defect_c_page)

    verdict = gate.check_word_preservation(assemble.page_source_text(book, 0),
                                           build_epub.page_fragment(book, 0))

    assert verdict.verdict == "PASS", verdict.as_dict()


def test_the_page_the_builder_writes_satisfies_the_structural_contract():
    book = _book(F.defect_c_page)

    result = gate.check_structure(build_epub.page_fragment(book, 0),
                                  ladder=(1, 2, 3, 4))

    assert result.ok, result.reasons
    assert result.counts["noterefs"] >= 1, result.counts
    assert result.counts["asides"] >= result.counts["noterefs"], result.counts


def test_a_marker_whose_note_is_not_on_the_page_is_not_made_into_a_link():
    """The note is overleaf, so there is nothing on this page to link to. A link
    emitted anyway is a footnote button that opens nothing."""
    book = _book(F.orphan_marker_page)

    fragment = build_epub.page_fragment(book, 0)

    assert 'href="#fn_204"' not in fragment
    assert "204" in gate.strip_markup(fragment), fragment


# ------------------------------------------------------------------ the container

def test_the_epub_opens_as_an_epub(tmp_path):
    """`mimetype` first and stored uncompressed is what makes a zip an EPUB rather
    than a zip full of XHTML."""
    book = _book(lambda d: F.chapter_opening_page(d, "Chapter One"), F.prose_page)

    result = _build(book, tmp_path)

    with zipfile.ZipFile(result.path) as zf:
        names = zf.namelist()
        assert names[0] == "mimetype"
        assert zf.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        assert zf.read("mimetype") == b"application/epub+zip"
        root = ET.fromstring(zf.read("META-INF/container.xml"))
        rootfile = root.find(".//%srootfile" % CONTAINER)
        assert rootfile.get("full-path") in names


def test_every_file_the_package_promises_is_in_the_book(tmp_path):
    """A manifest entry with no file, or a spine idref with no manifest entry, is
    the most common way a hand-built EPUB fails on a device and not on a desk."""
    book = _book(lambda d: F.chapter_opening_page(d, "Chapter One"), F.prose_page, F.defect_c_page)

    result = _build(book, tmp_path)

    with zipfile.ZipFile(result.path) as zf:
        names = set(zf.namelist())
        opf_path = ET.fromstring(zf.read("META-INF/container.xml")) \
            .find(".//%srootfile" % CONTAINER).get("full-path")
        opf = ET.fromstring(zf.read(opf_path))
        base = posixpath.dirname(opf_path)
        ids = {}
        for item in opf.iter("%sitem" % OPF):
            href = posixpath.normpath(posixpath.join(base, item.get("href")))
            assert href in names, href
            ids[item.get("id")] = href
        spine = [ref.get("idref") for ref in opf.iter("%sitemref" % OPF)]
        assert spine
        for idref in spine:
            assert idref in ids, idref


def test_every_document_in_the_book_is_well_formed_xml(tmp_path):
    """An unescaped ampersand in a publisher's name is enough for a reader to refuse
    the whole file, and the text that carries one looks perfectly ordinary."""
    book = _book(F.typographers_page, F.prose_page)

    result = _build(book, tmp_path)

    with zipfile.ZipFile(result.path) as zf:
        documents = _xhtml_names(zf)
        assert documents
        for name in documents:
            ET.fromstring(zf.read(name))
        body = " ".join(gate.strip_markup(_body(zf, n))
                        for n in _content_names(zf))
    assert "Hall & Fisher" in body, body[:400]


# --------------------------------------------------------------- the reading order

def test_a_chapter_heading_starts_a_new_document(tmp_path):
    """Split on the ladder's top levels, or a 700-page book is one XHTML file that
    every e-ink reader takes seconds to repaginate."""
    book = _book(lambda d: F.chapter_opening_page(d, "Chapter One", folio="31"),
                 F.prose_page,
                 lambda d: F.chapter_opening_page(d, "Chapter Two", folio="45"),
                 F.prose_page)

    result = _build(book, tmp_path)

    with zipfile.ZipFile(result.path) as zf:
        bodies = [_body(zf, n) for n in _content_names(zf)]
    with_h1 = [b for b in bodies if "Chapter One" in b or "Chapter Two" in b]

    assert len(with_h1) == 2, [b[:80] for b in bodies]
    assert not any("Chapter One" in b and "Chapter Two" in b for b in bodies)


def test_the_whole_book_survives_being_split_into_documents(tmp_path):
    """Splitting is where text goes missing: a chunk dropped on a boundary, a page's
    footnotes left in the file the split moved away from."""
    book = _book(lambda d: F.chapter_opening_page(d, "Chapter One", folio="31"),
                 F.defect_c_page, F.prose_page,
                 lambda d: F.chapter_opening_page(d, "Chapter Two", folio="45"),
                 F.prose_page)
    fragments = _fragments(book)

    result = _build(book, tmp_path, page_html=fragments)

    with zipfile.ZipFile(result.path) as zf:
        written = _words("\n".join(_body(zf, n) for n in _content_names(zf)),
                         markup=True)
    printed = _words("\n".join(fragments[p] for p in sorted(fragments)), markup=True)

    assert written - printed == collections.Counter(), (written - printed).most_common(10)
    assert printed - written == collections.Counter(), (printed - written).most_common(10)


def test_a_sentence_that_runs_over_a_page_turn_is_one_paragraph_in_the_file(tmp_path):
    """DIAGNOSIS B, at the level the reader sees. The pages are cleaned one at a
    time — by the deterministic pass or by a model — and the join is made afterwards
    on whatever came back, so it has to happen on the markup, not only on the runs."""
    book = _book(F.defect_b_pages)

    result = _build(book, tmp_path)

    with zipfile.ZipFile(result.path) as zf:
        body = "".join(_body(zf, n) for n in _content_names(zf))
    paragraphs = [gate.strip_markup(p) for p in re.findall(r"<p\b.*?</p>", body, re.S)]

    assert any("oftentimes Firmicus is more expansive" in p for p in paragraphs), \
        paragraphs
    assert not any(p.strip().startswith("is more expansive") for p in paragraphs)


# ------------------------------------------------------------------- the footnotes

def test_no_link_in_the_book_points_at_an_id_that_is_not_there(tmp_path):
    """A footnote whose marker and text are split into different documents by a
    chapter break still has to work: the link names the file, not just the id."""
    book = _book(F.mid_page_heading_page, F.defect_c_page)

    result = _build(book, tmp_path)

    with zipfile.ZipFile(result.path) as zf:
        documents = _xhtml_names(zf)
        ids = {name: set(re.findall(r'id="([^"]+)"', zf.read(name).decode("utf-8")))
               for name in documents}
        links = 0
        for name in documents:
            text = zf.read(name).decode("utf-8")
            for href in re.findall(r'<a\b[^>]+href="([^"]+)"', text):
                if href.startswith(("http:", "https:", "mailto:")):
                    continue
                target, _, anchor = href.partition("#")
                where = posixpath.normpath(
                    posixpath.join(posixpath.dirname(name), target)) if target else name
                assert where in ids, "%s -> %s" % (name, href)
                if anchor:
                    assert anchor in ids[where], "%s -> %s" % (name, href)
                links += 1

    assert links >= 2, "no internal link was checked"


def test_a_footnote_is_an_epub_footnote_and_not_a_paragraph_at_the_end(tmp_path):
    """`epub:type` is what turns a note into a popup on a device instead of a jump
    to the back of the file and a jump back."""
    book = _book(F.defect_c_page)

    result = _build(book, tmp_path)

    with zipfile.ZipFile(result.path) as zf:
        body = "".join(_body(zf, n) for n in _content_names(zf))

    assert 'epub:type="noteref"' in body
    assert 'epub:type="footnote"' in body
    assert body.count('epub:type="footnote"') >= 3, body.count('epub:type="footnote"')


# -------------------------------------------------------------- navigation + sidecar

def test_the_navigation_documents_agree_with_the_spine(tmp_path):
    """Old readers use the NCX, new ones use nav.xhtml, and a book whose two tables
    of contents disagree sends the same reader to two different places."""
    book = _book(lambda d: F.chapter_opening_page(d, "Chapter One", folio="31"),
                 F.prose_page,
                 lambda d: F.chapter_opening_page(d, "Chapter Two", folio="45"))

    result = _build(book, tmp_path)

    with zipfile.ZipFile(result.path) as zf:
        opf_path = ET.fromstring(zf.read("META-INF/container.xml")) \
            .find(".//%srootfile" % CONTAINER).get("full-path")
        base = posixpath.dirname(opf_path)
        nav = zf.read(posixpath.join(base, "nav.xhtml")).decode("utf-8")
        ncx = ET.fromstring(zf.read(posixpath.join(base, "toc.ncx")))

    nav_targets = re.findall(r'<a[^>]+href="([^"#]+)', nav)
    ncx_targets = [c.get("src").split("#")[0]
                   for c in ncx.iter("%scontent" % NCX)]

    assert nav_targets == ncx_targets, (nav_targets, ncx_targets)
    assert "Chapter One" in nav and "Chapter Two" in nav


def test_the_sidecar_is_where_the_api_looks_for_it(tmp_path):
    """`/reflow/report` reads the numbers back out of the file itself, so a book
    handed to a user months later can still say how it was made."""
    book = _book(F.defect_c_page, F.prose_page)

    result = _build(book, tmp_path, sidecar={"spend_usd": 0.0132, "pages_routed": 1})

    with zipfile.ZipFile(result.path) as zf:
        data = json.loads(zf.read("META-INF/reflow.json").decode("utf-8"))
        opf_path = ET.fromstring(zf.read("META-INF/container.xml")) \
            .find(".//%srootfile" % CONTAINER).get("full-path")
        opf = zf.read(opf_path).decode("utf-8")

    assert data["spend_usd"] == 0.0132
    assert data["pages"] == 2
    assert data["notes"] >= 3
    assert "cwng:reflow" in opf


def test_the_about_page_leads_the_book_when_it_is_asked_for(tmp_path):
    book = _book(F.prose_page, F.defect_c_page)
    about = "<h1>About this conversion</h1><p>No wording was changed.</p>"

    result = _build(book, tmp_path, report_html=about)

    with zipfile.ZipFile(result.path) as zf:
        opf_path = ET.fromstring(zf.read("META-INF/container.xml")) \
            .find(".//%srootfile" % CONTAINER).get("full-path")
        opf = ET.fromstring(zf.read(opf_path))
        base = posixpath.dirname(opf_path)
        hrefs = {i.get("id"): i.get("href") for i in opf.iter("%sitem" % OPF)}
        spine = [hrefs[r.get("idref")] for r in opf.iter("%sitemref" % OPF)]
        nav = zf.read(posixpath.join(base, "nav.xhtml")).decode("utf-8")

    assert spine[0] == "reflow-about.xhtml", spine
    assert "About this conversion" in nav


def test_the_about_page_can_be_declined(tmp_path):
    book = _book(F.prose_page)

    result = _build(book, tmp_path, report_html=None)

    with zipfile.ZipFile(result.path) as zf:
        assert not [n for n in zf.namelist() if "reflow-about" in n], zf.namelist()


@pytest.mark.parametrize("builder", [
    F.defect_c_page, F.broken_note_number_page, F.ambiguous_note_number_page,
    F.merged_note_number_page, F.hyphenated_note_page, F.typographers_page,
    F.orphan_marker_page, F.numbered_bibliography_page, F.defect_a_page,
])
def test_every_kind_of_page_is_written_as_the_page_printed_it(builder):
    """The parity above, held across the awkward pages rather than one easy one.

    A note whose number the OCR destroyed, a page of numbered bibliography entries,
    an ampersand set as type: each of them is a place where the builder could put a
    word on the page that the PDF did not print, and each would be scored against
    the model as if the model had done it."""
    book = _book(builder)

    for pno in sorted(book.pages):
        verdict = gate.check_word_preservation(assemble.page_source_text(book, pno),
                                               build_epub.page_fragment(book, pno))
        assert verdict.verdict in ("PASS", "NOT_APPLICABLE"), \
            (pno, verdict.as_dict())


# ---------------------------------------------------------------- page markers

def test_every_page_of_the_pdf_is_marked_once_in_the_book(tmp_path):
    """EPUB 3 page markers let a reader show "page 97 of 698" against the print, and
    give the about page somewhere to send a reader who wants to see an uncertain
    reading in its context. A duplicate marker makes both ambiguous."""
    book = _book(lambda d: F.chapter_opening_page(d, "Chapter One"),
                 F.defect_c_page, F.prose_page)

    result = _build(book, tmp_path)

    with zipfile.ZipFile(result.path) as zf:
        body = "".join(_body(zf, n) for n in _content_names(zf))
    markers = re.findall(r'id="(pg_\d+)"', body)

    assert sorted(markers) == ["pg_0000", "pg_0001", "pg_0002"], markers


def test_the_marker_for_a_page_that_opens_a_chapter_is_in_that_chapter(tmp_path):
    """Left in the previous document, every link for that page lands at the end of
    the chapter before it."""
    book = _book(F.prose_page,
                 lambda d: F.chapter_opening_page(d, "Chapter Two", folio="45"))

    result = _build(book, tmp_path)

    with zipfile.ZipFile(result.path) as zf:
        holder = [n for n in _content_names(zf) if 'id="pg_0001"' in _body(zf, n)]
        assert len(holder) == 1, holder
        assert "Chapter Two" in _body(zf, holder[0])


def test_the_about_page_is_written_once_the_pages_have_somewhere_to_live(tmp_path):
    """The report wants to link to a page, and which document a page ends up in is
    only known after the split. So the report is asked for last, with the answer."""
    book = _book(F.prose_page,
                 lambda d: F.chapter_opening_page(d, "Chapter Two", folio="45"))
    seen = {}

    def write_about(where):
        seen.update(where)
        return ('<h1>About this conversion</h1>'
                '<p><a href="%s#pg_0001">the second page</a></p>' % where[1])

    result = _build(book, tmp_path, report_html=write_about)

    assert set(seen) == {0, 1}, seen
    with zipfile.ZipFile(result.path) as zf:
        about = zf.read("OEBPS/reflow-about.xhtml").decode("utf-8")
    assert 'href="%s#pg_0001"' % seen[1] in about, about
