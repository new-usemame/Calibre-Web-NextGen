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

from cps.services.reflow import annotate, assemble, build_epub, gate
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


@pytest.mark.parametrize("control", ["\x00", "\x0b", "\ufffe"])
def test_unprintable_source_characters_do_not_make_a_whole_chapter_unreadable(
        tmp_path, control):
    """Real PDFs 561 and 568 expose NULs through their font mappings.

    Preserve the surrounding words, display an explicit replacement for the
    unrepresentable character, and disclose it in the report and its sidecar.
    """
    book = _book(F.prose_page)
    losses = []

    def report(links, warnings):
        losses.extend(warnings)
        return "<p>Conversion report</p>"

    built = _build(book, tmp_path, page_html={0: "<p>before%safter</p>" % control},
                   report_html=report)
    assert build_epub.validate(built.path) == []
    with zipfile.ZipFile(built.path) as archive:
        text = " ".join("".join(ET.fromstring(archive.read(name)).itertext())
                        for name in archive.namelist() if name.startswith("OEBPS/ch")
                        and name.endswith(".xhtml"))
    assert "before\ufffdafter" in text
    assert built.sidecar["unrepresentable_characters"] == [
        {"page": 0, "codepoint": "U+%04X" % ord(control), "count": 1}]
    assert any("replacement character" in warning for warning in losses)


def test_a_hostile_cached_page_is_refused_at_the_packaging_boundary(tmp_path):
    """The final boundary. A page written to the cache before the adoption gate
    learned this check -- or planted there -- must not ship either: the builder
    refuses the fragment, ships the page's own deterministic text, and says so."""
    book = _book(F.prose_page)
    source = assemble.page_source_text(book, 0)
    hostile = ('<p>%s</p><img src="https://example.invalid/reflow-pixel.gif" '
               'onerror="fetch(\'https://example.invalid/event\')" alt=""/>') % source

    built = _build(book, tmp_path, page_html={0: hostile})

    assert build_epub.validate(built.path) == []
    with zipfile.ZipFile(built.path) as zf:
        chapters = "".join(zf.read(n).decode("utf-8")
                           for n in zf.namelist()
                           if n.startswith("OEBPS/ch") and n.endswith(".xhtml"))
    assert "example.invalid" not in chapters
    assert "onerror" not in chapters
    # The reader still gets the page itself, word for word.
    assert source.split()[0] in chapters
    assert source.split()[-1] in chapters
    assert any("not trusted" in warning for warning in built.warnings), built.warnings
    assert built.sidecar.get("unplaced"), "the refusal is disclosed, not silent"


def test_legitimate_model_markup_survives_the_packaging_boundary_unchanged(tmp_path):
    """The control that matters: a packaging gate that mangles legitimate markup
    would fail the pages the adoption gate exists to let through. Uncertainty
    marks, tables and figure references come out the far side byte for byte."""
    book = _book(F.prose_page)
    source = assemble.page_source_text(book, 0)
    fragment = ('<p>%s</p><table><tbody><tr><td colspan="2">x</td></tr></tbody></table>'
                '<p>a <span class="reflow-uncertain" title="likely: well">wel1</span> b</p>'
                % source)

    built = _build(book, tmp_path, page_html={0: fragment})

    assert build_epub.validate(built.path) == []
    with zipfile.ZipFile(built.path) as zf:
        chapters = "".join(zf.read(n).decode("utf-8")
                           for n in zf.namelist()
                           if n.startswith("OEBPS/ch") and n.endswith(".xhtml"))
    assert '<span class="reflow-uncertain" title="likely: well">wel1</span>' in chapters
    assert '<td colspan="2">x</td>' in chapters
    assert not built.warnings


def test_a_direct_fragment_with_a_disallowed_tag_is_refused_at_the_packaging_boundary(
        tmp_path):
    """The retest finding, closed: the final boundary checked attributes and URLs
    but left tag names to the adoption gate, so a direct (or historical-cache)
    fragment carrying <script> shipped unchanged. The last boundary now refuses
    the whole forbidden markup -- tag included -- and the book still gets the
    page's own text, with the refusal disclosed."""
    book = _book(F.prose_page)
    source = assemble.page_source_text(book, 0)
    hostile = '<p>%s</p><script>window.__reflow_probe__=1</script>' % source

    built = _build(book, tmp_path, page_html={0: hostile})

    assert build_epub.validate(built.path) == []
    with zipfile.ZipFile(built.path) as zf:
        chapters = "".join(zf.read(n).decode("utf-8")
                           for n in zf.namelist()
                           if n.startswith("OEBPS/ch") and n.endswith(".xhtml"))
    assert "<script" not in chapters
    assert "__reflow_probe__" not in chapters
    assert source.split()[0] in chapters
    assert source.split()[-1] in chapters
    assert any("not trusted" in warning for warning in built.warnings), built.warnings
    assert built.sidecar.get("unplaced"), "the refusal is disclosed, not silent"


def _active_markup(archive):
    """Every way a written chapter could act or reach out: event handlers, style,
    and any image or link that is not a relative in-book reference."""
    found = []
    for name in archive.namelist():
        if not (name.startswith("OEBPS/") and name.endswith(".xhtml")):
            continue
        for element in ET.fromstring(archive.read(name)).iter():
            tag = element.tag.split("}")[-1]
            for attribute, value in element.attrib.items():
                local = attribute.split("}")[-1].lower()
                if local.startswith("on"):
                    found.append("%s: %s %s=%r" % (name, tag, local, value))
                if local == "style" and "position" in value:
                    found.append("%s: %s style=%r" % (name, tag, value))
                if local in ("src", "href") and re.match(r"^\s*(?:[a-z][a-z0-9+.-]*:|//)", value, re.I):
                    found.append("%s: %s %s=%r" % (name, tag, local, value))
    return found


def test_uppercase_html_entities_cannot_write_markup_the_boundary_never_saw(tmp_path):
    """N1 of the 7daffa5 security retest, its probe verbatim. The boundary parsed
    the fragment with html.parser, which reads ``&QUOT;`` inside a title as a quote
    *character in the value*; afterwards the builder resolved the same entity to a
    literal ``"`` in the markup, which closes the attribute and opens new ones --
    ``onclick``, ``style``, and an ``<img>`` pulling a remote pixel -- in bytes
    the check never parsed. What is written must be what was checked: a resolved
    entity keeps meaning a character, never markup. Breaks if entities are
    resolved to raw markup characters after the boundary check again."""
    book = _book(F.prose_page)
    probe = ('<p><span class="reflow-uncertain" title="x&QUOT; onclick=&QUOT;alert(1)&QUOT; '
             'style=&QUOT;position:fixed">…</span></p><p class="caption"><span '
             'class="reflow-uncertain" title="&QUOT;&GT;&LT;/span&GT;&LT;img src=&QUOT;'
             'https://evil.example/p.gif&QUOT;/&GT;&LT;span title=&QUOT;">x</span></p>')

    built = _build(book, tmp_path, page_html={0: probe})

    assert build_epub.validate(built.path) == []
    with zipfile.ZipFile(built.path) as archive:
        assert _active_markup(archive) == []
        images = [element for name in archive.namelist()
                  if name.startswith("OEBPS/ch") and name.endswith(".xhtml")
                  for element in ET.fromstring(archive.read(name)).iter(XHTML + "img")]
    assert images == [], "the probe's <img> was written as markup"


def test_a_resolved_html_entity_is_always_a_character_and_never_markup():
    """The resolver's own promise, independent of the boundary that also guards
    it: an HTML5 name for an XML-significant character -- upper-case forms and
    ``&nvlt;`` (``<`` plus a combining mark) included -- comes out as that
    character inside the same attribute or text node it was written in. Breaks if
    ``_named_entities`` writes a resolved ``"`` or ``<`` as a raw byte again."""
    written = build_epub._well_formed_text(
        '<p><span class="reflow-uncertain" title="a&QUOT;b&LT;c&nvlt;d">&AMP;x&GT;</span></p>')
    span = ET.fromstring("<r>%s</r>" % written).find("p/span")
    assert span.attrib == {"class": "reflow-uncertain", "title": 'a"b<c<⃒d'}
    assert span.text == "&x>"


def test_validation_refuses_a_book_whose_written_markup_can_act_or_reach_out(tmp_path):
    """The last line, on the bytes themselves: whatever road markup takes into a
    built EPUB, validate() parses every document and refuses an event handler, a
    script, or a remote image -- so a gap in any earlier check is a failed job,
    not a filed book. Breaks if validate() goes back to checking only that the
    documents parse and their internal links land."""
    book = _book(F.prose_page)
    built = _build(book, tmp_path)
    assert build_epub.validate(built.path) == []

    def tampered(original, change, name):
        path = str(tmp_path / name)
        with zipfile.ZipFile(original) as source, zipfile.ZipFile(path, "w") as target:
            for item in source.infolist():
                data = source.read(item.filename)
                if item.filename == "OEBPS/ch001.xhtml":
                    data = change(data.decode("utf-8")).encode("utf-8")
                target.writestr(item, data)
        return path

    handler = tampered(built.path, lambda x: x.replace("<p>", '<p onclick="alert(1)">', 1),
                       "handler.epub")
    remote = tampered(built.path, lambda x: x.replace(
        "</p>", '<img src="https://evil.example/p.gif" alt=""/></p>', 1), "remote.epub")
    script = tampered(built.path, lambda x: x.replace(
        "</p>", "</p><script>alert(1)</script>", 1), "script.epub")
    for path in (handler, remote, script):
        problems = build_epub.validate(path)
        assert problems, "%s validated clean" % path


def test_a_marked_uncertain_reading_reaches_the_reader_looking_marked(tmp_path):
    """R3 ends here. A mark that the builder's block splitting mangles, or that the
    book's stylesheet says nothing about, is an annotation nobody can see -- and an
    invisible annotation is indistinguishable from having replaced the word."""
    book = _book(F.prose_page)
    fragments = _fragments(book)
    marked, placed = annotate.mark_uncertain(
        fragments[0], [{"token": fragments[0].split(">")[1].split()[0],
                        "candidates": ["luminaries"]}])
    assert placed, "the fixture page did not take a mark"

    result = _build(book, tmp_path, page_html={0: marked})

    with zipfile.ZipFile(result.path) as zf:
        chapters = "".join(zf.read(n).decode("utf-8") for n in _content_names(zf))
        style = zf.read("OEBPS/style.css").decode("utf-8")

    assert 'class="%s"' % annotate.CLASS in chapters
    assert 'title="likely: luminaries"' in chapters
    assert annotate.CLASS in style, "the mark is in the book and styled by nothing"


def _xhtml_names(zf):
    """Every XHTML document in the package, navigation included."""
    return [n for n in zf.namelist() if n.endswith(".xhtml")]


def _content_names(zf):
    """The documents that carry the book's text: not the machine navigation."""
    return [n for n in _xhtml_names(zf)
            if posixpath.basename(n) not in ("nav.xhtml", "reflow-about.xhtml",
                                            "source-pages.xhtml")]


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
    book = _book(lambda d: F.chapter_opening_page(d, "Chapter Four"),
                 lambda d: F.section_heading_page(d, "Antiochus of Athens"),
                 F.mid_page_heading_page, F.defect_c_page)

    result = _build(book, tmp_path)

    crossing = 0
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
                if where != name:
                    crossing += 1
                links += 1

    assert links >= 2, "no internal link was checked"
    # Without this the test passes on a book whose links never leave their own
    # document, which is the case it exists to cover.
    assert crossing >= 1, "no footnote link crossed a document boundary"


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

    # The navigation document also contains a source page list; compare the
    # chapter table of contents, not unrelated navigation destinations.
    toc = next(node for node in ET.fromstring(nav).iter(XHTML + "nav")
               if node.get("{http://www.idpf.org/2007/ops}type") == "toc")
    nav_targets = [node.get("href").split("#")[0]
                   for node in toc.iter(XHTML + "a")]
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


def test_a_note_that_ran_over_a_page_is_not_counted_as_a_second_footnote(tmp_path):
    """The sidecar's count is what the report page tells a reader the book holds.
    A note printed across a page turn is one footnote set in two pieces, and the
    unnumbered piece is not a footnote of its own."""
    book = _book(F.runover_footnote_pages)

    result = _build(book, tmp_path)

    with zipfile.ZipFile(result.path) as zf:
        data = json.loads(zf.read("META-INF/reflow.json").decode("utf-8"))

    assert [n.num for n in book.notes] == [257, 258, None, 259]
    assert data["notes"] == 3


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

    def write_about(where, losses=()):
        seen.update(where)
        return ('<h1>About this conversion</h1>'
                '<p><a href="%s#pg_0001">the second page</a></p>' % where[1])

    result = _build(book, tmp_path, report_html=write_about)

    assert set(seen) == {0, 1}, seen
    with zipfile.ZipFile(result.path) as zf:
        about = zf.read("OEBPS/reflow-about.xhtml").decode("utf-8")
    assert 'href="%s#pg_0001"' % seen[1] in about, about


def test_a_note_number_that_repeats_on_another_page_does_not_collide(tmp_path):
    """Note numbering restarts per chapter in most books, so the same number is set
    on more than one page. Two elements with one id is a document no reader can
    resolve: the note button opens whichever the parser saw first."""
    book = _book(F.defect_c_page, F.defect_c_page)

    result = _build(book, tmp_path)

    with zipfile.ZipFile(result.path) as zf:
        for name in _xhtml_names(zf):
            found = re.findall(r'id="([^"]+)"', zf.read(name).decode("utf-8"))
            duplicates = [i for i, n in collections.Counter(found).items() if n > 1]
            assert not duplicates, "%s repeats %s" % (name, duplicates)
        notes = sum(len(re.findall(r'epub:type="footnote"', zf.read(n).decode("utf-8")))
                    for n in _content_names(zf))

    assert notes >= 6, "the two pages' notes were not both written"


def test_two_sentences_either_side_of_a_page_turn_stay_two_paragraphs(tmp_path):
    """The control for the page-turn join. A rule that joins every page turn reads
    as confidently as one that joins none, and silently welds the last sentence of
    every page onto the first of the next."""
    book = _book(F.prose_page, F.prose_page)

    result = _build(book, tmp_path, page_html={
        0: "<p>The argument of the chapter is complete on this page.</p>",
        1: "<p>Another argument begins on the page after it.</p>"})

    with zipfile.ZipFile(result.path) as zf:
        body = "".join(_body(zf, name) for name in _content_names(zf))

    paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", body, re.S)

    assert len(paragraphs) == 2, paragraphs
    assert paragraphs[0].strip().endswith("complete on this page."), paragraphs


def test_a_sentence_left_outside_a_paragraph_still_reaches_the_reader(tmp_path):
    """A model that answers with a bare sentence between two paragraphs has made a
    mistake the gate judges. Losing the sentence while writing the file is a
    different thing: the gate has already passed the page by then."""
    book = _book(F.prose_page)

    result = _build(book, tmp_path, page_html={
        0: "<p>Before the interruption.</p>\n"
           "Ptolemy is named here and nowhere else.\n"
           "<p>After the interruption.</p>\n"
           "Valens is named after the last paragraph closes."})

    with zipfile.ZipFile(result.path) as zf:
        body = "".join(_body(zf, name) for name in _content_names(zf))

    written = build_epub.block_text(body)

    assert "Ptolemy is named here and nowhere else." in written
    # The tail is its own case: text after the last closing tag is where a splitter
    # that only notices what is between elements stops looking.
    assert "Valens is named after the last paragraph closes." in written


def test_a_picture_that_could_not_be_taken_out_of_the_pdf_is_told_to_the_reader(tmp_path):
    """The builder is the only thing that knows what it had to drop.

    A figure whose crop fails and a note link whose note is not in the book are both
    losses a reader can see in their book and cannot explain: the plate is simply
    not there, the number simply does not open. Both were counted into a warnings
    list on the return value that the task never read and the report page never
    printed, so the one page whose whole job is to say what the conversion could not
    do said nothing about either.
    """
    doc = F.new_doc()
    F.illustrated_page(doc, F.solid_png())
    told = {}

    def write_about(where, losses):
        told.setdefault("losses", []).append(list(losses))
        return "<h1>About this conversion</h1><p>ok</p>"

    try:
        book = assemble.deterministic_book(doc)
        kept = _build(book, tmp_path / "kept", report_html=write_about, doc=doc)
        # The same book built by a builder with no PDF to crop from: every figure
        # the fragments refer to is one it cannot produce.
        lost = _build(book, tmp_path / "lost", report_html=write_about, doc=None)
    finally:
        doc.close()

    assert kept.images == 1 and lost.images == 0, (kept.images, lost.images)
    assert told["losses"][0] == [], "nothing was dropped and something was reported"
    assert told["losses"][1], "the picture went missing and the report page was not told"


def test_a_figure_the_page_printed_no_caption_for_says_so(tmp_path):
    """SPEC §6: every figure carries a caption or an explicit "caption not found".
    An illustration contributes no words, so nothing that counts words can tell
    whether it was placed, mis-placed or dropped."""
    book = _book(lambda d: F.illustrated_page(d, F.solid_png()))

    fragment = build_epub.page_fragment(book, 0)
    verdict = gate.check_structure(fragment, ladder=(1,), require_figure_caption=True)

    assert verdict.ok, verdict.reasons
    assert verdict.counts["figures"] == 1, verdict.counts
    assert "figure_without_caption" in book.page_reasons(0)


def test_a_marker_whose_note_is_elsewhere_is_not_glued_to_the_word_before_it():
    """A marker that resolves to nothing is still a marker. Left as text it closes
    up against the word in front of it and the page reads "set overleaf204."."""
    book = _book(F.orphan_marker_page)

    printed = assemble.page_source_text(book, 0)
    fragment = build_epub.page_fragment(book, 0)

    assert "overleaf204" not in printed, printed[-120:]
    assert "overleaf204" not in build_epub.block_text(fragment), fragment[-160:]
    assert "204" in build_epub.block_text(fragment)
    assert "unresolved_marker" in book.page_reasons(0)


# ------------------------------------------ the notes on a page the model rewrote

#: The shape ``prompts/structure.txt`` asks the model for, and therefore the shape a
#: page reaches the builder in once the gate has accepted it: the class, the href,
#: the id on the note, and nothing else. The prompt asks small on purpose -- every
#: attribute in the ask is one more thing an answer can get wrong and lose the page
#: for -- so what the reader needs on top of it has to be added here.
MODEL_ANSWER = (
    '<p>Pingree notes that he wrote sometime prior to the second century '
    'CE<a class="noteref" href="#fn_56">56</a> and that the text should be used '
    'with caution<a class="noteref" href="#fn_16">16</a>.</p>\n'
    '<aside class="footnote" id="fn_56">56 Cumont first made this argument in '
    'CCAG 8, 4, p. 225.</aside>\n'
    '<aside class="footnote" id="fn_16">16 Hephaestio, Apotelesmatika, 2, 2: 11-18.'
    '</aside>'
)

MODEL_ANSWER_NOTE_CARRIED_OVER = (
    '<p>The argument runs on from the page before and finishes here.</p>\n'
    '<aside class="footnote" id="fn_9">9 Continued from the foot of the last page.'
    '</aside>'
)

MODEL_ANSWER_MARKED_TWICE = (
    '<p>He says so once<a class="noteref" href="#fn_56">56</a> and again lower down '
    'the page<a class="noteref" href="#fn_56">56</a>.</p>\n'
    '<aside class="footnote" id="fn_56">56 Cumont, CCAG 8, 4, p. 225.</aside>'
)


def _model_page(tmp_path, html=MODEL_ANSWER):
    """One page whose markup came back from the model rather than from the PDF."""
    return _build(_book(F.defect_c_page), tmp_path, page_html={0: html})


def _noterefs(root):
    return [a for a in root.iter(XHTML + "a")
            if "noteref" in (a.get("class") or "").split()]


def test_a_note_the_model_marked_up_itself_still_opens_as_a_popup(tmp_path):
    """A device shows a note in a popup because ``epub:type`` says it is a note, and
    lays it out as running text when it does not. The prompt never asks the model for
    ``epub:type``, so the pages Reflow *succeeded* on are the ones whose notes would
    read as stray paragraphs at the end of every page -- the pages it refused keep
    the deterministic markup and come out right."""
    result = _model_page(tmp_path)

    with zipfile.ZipFile(result.path) as zf:
        body = "".join(_body(zf, n) for n in _content_names(zf))

    assert body.count('epub:type="noteref"') == 2, body
    assert body.count('epub:type="footnote"') == 2, body


def test_a_reader_who_followed_a_model_pages_note_can_get_back(tmp_path):
    """The number printed at the head of a note is the way back to the sentence that
    called it. The model is not asked to write that link, and there is nothing for it
    to point at either: the marker it writes carries no id."""
    result = _model_page(tmp_path)

    with zipfile.ZipFile(result.path) as zf:
        root = ET.fromstring(zf.read(_content_names(zf)[0]))

    ids = {el.get("id"): el for el in root.iter() if el.get("id")}
    checked = 0
    for aside in root.iter(XHTML + "aside"):
        back = [a for a in aside.iter(XHTML + "a")
                if (a.get("href") or "").startswith("#")]
        assert back, "note %s gives the reader no way back" % aside.get("id")
        for link in back:
            target = link.get("href")[1:]
            assert target in ids, "the way back points at %s, which is not here" % target
            assert ids[target].get("href") == "#" + aside.get("id"), \
                "the way back from %s lands on something else" % aside.get("id")
        checked += 1

    assert checked == 2, "the two notes on the page were not both checked"


def test_a_note_whose_marker_was_printed_on_the_last_page_invents_no_way_back(tmp_path):
    """A note that runs over from the previous page is set here and called there. A
    link back written anyway points at an id that is nowhere in the book, and the
    builder has to disarm it -- so the reader gets a note with a dead link in it
    instead of a note."""
    result = _model_page(tmp_path, html=MODEL_ANSWER_NOTE_CARRIED_OVER)

    assert result.warnings == [], result.warnings
    with zipfile.ZipFile(result.path) as zf:
        name = _content_names(zf)[0]
        body = _body(zf, name)
        root = ET.fromstring(zf.read(name))

    assert 'epub:type="footnote"' in body, "it stopped being a note"
    assert 'href="#"' not in body, "a link was written and then disarmed"
    asides = list(root.iter(XHTML + "aside"))
    assert len(asides) == 1
    assert not list(asides[0].iter(XHTML + "a")), ET.tostring(asides[0])


def test_a_marker_the_model_wrote_at_body_size_is_raised_like_every_other(tmp_path):
    """Half the markers in a book set as superscripts and half set inline is the same
    book telling the reader two different things about what a bare number means."""
    result = _model_page(tmp_path)

    with zipfile.ZipFile(result.path) as zf:
        root = ET.fromstring(zf.read(_content_names(zf)[0]))

    markers = _noterefs(root)
    assert len(markers) == 2
    for marker in markers:
        raised = list(marker.iter(XHTML + "sup"))
        assert len(raised) == 1, ET.tostring(marker)


def test_two_markers_pointing_at_one_note_do_not_take_the_same_id(tmp_path):
    """Two elements with one id is a document a reader resolves by whichever the
    parser saw first: the same failure the per-page scoping exists to prevent, one
    page further in."""
    result = _model_page(tmp_path, html=MODEL_ANSWER_MARKED_TWICE)

    with zipfile.ZipFile(result.path) as zf:
        name = _content_names(zf)[0]
        raw = zf.read(name).decode("utf-8")
        root = ET.fromstring(raw)

    found = re.findall(r'id="([^"]+)"', raw)
    duplicates = [i for i, n in collections.Counter(found).items() if n > 1]
    assert not duplicates, "%s repeats %s" % (name, duplicates)

    aside = list(root.iter(XHTML + "aside"))[0]
    markers = _noterefs(root)
    assert len(markers) == 2
    assert {m.get("href") for m in markers} == {"#" + aside.get("id")}
    back = [a.get("href")[1:] for a in aside.iter(XHTML + "a")]
    # The way back goes to the first of the two, the way a printed book sends you to
    # where the note was first called.
    assert back == [markers[0].get("id")], (back, [m.get("id") for m in markers])


def test_a_page_the_gate_refused_is_not_marked_up_a_second_time(tmp_path):
    """The builder finishes the model's short form into the form a reader needs. The
    pages the gate refused are already in that form, and finishing them again gives a
    note whose number is a link inside a link and an attribute the parser sees
    twice."""
    result = _build(_book(F.defect_c_page), tmp_path)

    with zipfile.ZipFile(result.path) as zf:
        name = _content_names(zf)[0]
        raw = zf.read(name).decode("utf-8")
        root = ET.fromstring(raw)

    for tag in re.findall(r"<(?:a|aside)\b[^>]*>", raw):
        assert tag.count("epub:type") <= 1, tag
        assert tag.count(' id="') <= 1, tag
    for anchor in root.iter(XHTML + "a"):
        assert not list(anchor.iter(XHTML + "a"))[1:], ET.tostring(anchor)
    for aside in root.iter(XHTML + "aside"):
        assert len([a for a in aside.iter(XHTML + "a")
                    if (a.get("href") or "").startswith("#")]) <= 1, ET.tostring(aside)


MODEL_ANSWER_NUMBER_RAISED = (
    '<p>Pingree notes that he wrote sometime prior to the second century '
    'CE<a class="noteref" href="#fn_56"><sup>56</sup></a>.</p>\n'
    '<aside class="footnote" id="fn_56"><sup>56</sup> Cumont first made this '
    'argument in CCAG 8, 4, p. 225.</aside>'
)


def test_a_note_whose_number_the_model_raised_itself_still_goes_back(tmp_path):
    """The prompt shows the number at the head of a note as plain text and the model
    is free to set it as a superscript instead, the way the page prints it. That is
    the same note and the reader still has to be able to get out of it."""
    result = _model_page(tmp_path, html=MODEL_ANSWER_NUMBER_RAISED)

    assert result.warnings == [], result.warnings
    with zipfile.ZipFile(result.path) as zf:
        root = ET.fromstring(zf.read(_content_names(zf)[0]))

    aside = list(root.iter(XHTML + "aside"))[0]
    back = [a for a in aside.iter(XHTML + "a") if (a.get("href") or "").startswith("#")]
    assert len(back) == 1, ET.tostring(aside)
    assert back[0].get("href")[1:] == _noterefs(root)[0].get("id")
    assert len(list(_noterefs(root)[0].iter(XHTML + "sup"))) == 1


MODEL_ANSWER_NUMBER_NOT_PRINTED = (
    '<p>Cumont said as much in his last paper'
    '<a class="noteref" href="#fn_9">9</a>.</p>\n'
    '<aside class="footnote" id="fn_9">1929 was the year he wrote it.</aside>'
)


def test_a_note_that_does_not_print_its_number_is_not_linked_through_whatever_does(tmp_path):
    """The way back out of a note is the number the note prints at its head. When the
    scanner lost that number the note starts with an ordinary word -- here a year --
    and turning that into the link back tells the reader the date is a control."""
    result = _model_page(tmp_path, html=MODEL_ANSWER_NUMBER_NOT_PRINTED)

    assert result.warnings == [], result.warnings
    with zipfile.ZipFile(result.path) as zf:
        name = _content_names(zf)[0]
        root = ET.fromstring(zf.read(name))
        body = _body(zf, name)

    aside = list(root.iter(XHTML + "aside"))[0]
    assert not list(aside.iter(XHTML + "a")), ET.tostring(aside)
    assert "1929 was the year" in body
    assert 'epub:type="footnote"' in body


#: OBSERVED on page 162 of book 567, and on twelve other pages of it: the page
#: prints notes 43, 44 and 45, the scanner reads the third number as 43, and the
#: book ships two notes with one id. epubcheck 5.2.1 calls that 25 errors; a reader
#: calls it a note they cannot open. The same shape occurs honestly wherever a
#: chapter's notes restart under the tail of the chapter before.
MODEL_ANSWER_NUMBER_PRINTED_TWICE = (
    '<p>Thorndike put it plainly<a class="noteref" href="#fn_43">43</a> and '
    'returned to it later<a class="noteref" href="#fn_44">44</a>.</p>\n'
    '<aside class="footnote" id="fn_43">43 Thorndike, "A Roman Astrologer as a '
    'Historical Source," p. 416.</aside>\n'
    '<aside class="footnote" id="fn_44">44 The approach is not always clear.'
    '</aside>\n'
    '<aside class="footnote" id="fn_43">43 See Cumont, L\'Egypte des astrologues.'
    '</aside>'
)

MODEL_ANSWER_TWO_MARKERS_TWO_NOTES = (
    '<p>The last note of the chapter<a class="noteref" href="#fn_43">43</a> and '
    'then the first of the next<a class="noteref" href="#fn_43">43</a>.</p>\n'
    '<aside class="footnote" id="fn_43">43 Thorndike, p. 416.</aside>\n'
    '<aside class="footnote" id="fn_43">43 See Cumont, L\'Egypte des astrologues.'
    '</aside>'
)


def _ids(root):
    return [el.get("id") for el in root.iter() if el.get("id")]


def test_two_notes_that_print_the_same_number_do_not_ship_under_one_id(tmp_path):
    """A duplicate id is an invalid EPUB, which is reason enough, but the reader's
    version is worse: both notes answer to the same anchor, so whichever the parser
    reaches first is the one every marker opens and the other note is in the book
    with no way to reach it."""
    result = _model_page(tmp_path, html=MODEL_ANSWER_NUMBER_PRINTED_TWICE)

    with zipfile.ZipFile(result.path) as zf:
        root = ET.fromstring(zf.read(_content_names(zf)[0]))

    ids = _ids(root)
    assert len(ids) == len(set(ids)), sorted(ids)
    assert len(list(root.iter(XHTML + "aside"))) == 3


def test_the_note_a_page_repeats_is_not_opened_by_the_marker_for_the_first_one(tmp_path):
    """Only one marker 43 is printed, so only one note 43 has a way in. The second
    keeps its place on the page and is left unmarked -- which the report already
    counts and says -- rather than being wired to a marker that does not cite it."""
    result = _model_page(tmp_path, html=MODEL_ANSWER_NUMBER_PRINTED_TWICE)

    with zipfile.ZipFile(result.path) as zf:
        root = ET.fromstring(zf.read(_content_names(zf)[0]))

    ids = set(_ids(root))
    opened = [a.get("href")[1:] for a in _noterefs(root)]
    assert len(opened) == len(set(opened)), opened
    for target in opened:
        assert target in ids, "a marker opens %s, which is not in the book" % target
    asides = list(root.iter(XHTML + "aside"))
    assert asides[0].get("id") in opened and asides[2].get("id") not in opened, \
        [a.get("id") for a in asides]


def test_two_markers_of_one_number_open_the_two_notes_in_turn(tmp_path):
    """The honest version of the same page: a chapter's notes restart under the last
    of the chapter before, so the page really does print 43 twice and cite it twice.
    The second marker has to reach the second note, or a reader following it lands on
    the wrong chapter's citation and has no way to tell."""
    result = _model_page(tmp_path, html=MODEL_ANSWER_TWO_MARKERS_TWO_NOTES)

    with zipfile.ZipFile(result.path) as zf:
        root = ET.fromstring(zf.read(_content_names(zf)[0]))

    asides = list(root.iter(XHTML + "aside"))
    opened = [a.get("href")[1:] for a in _noterefs(root)]
    assert opened == [asides[0].get("id"), asides[1].get("id")], opened
    for aside, marker in zip(asides, _noterefs(root)):
        back = [a.get("href")[1:] for a in aside.iter(XHTML + "a")]
        assert back == [marker.get("id")], (back, marker.get("id"))


def test_a_page_whose_notes_were_already_separated_is_not_separated_again(tmp_path):
    """The deterministic pages arrive here in the long form already, and every page
    is written twice in a sample-then-whole-book conversion. A second pass that
    renumbered what the first pass numbered would move every anchor in the book."""
    once = build_epub._reader_ready_notes(MODEL_ANSWER_NUMBER_PRINTED_TWICE)

    assert build_epub._reader_ready_notes(once) == once


# ------------------------------------------------- a book a reader can actually open

def _links_and_ids(zf):
    """Every internal link in the book, and every id there is to land on."""
    ids, links = set(), []
    names = [n for n in zf.namelist() if n.endswith(".xhtml")]
    for name in names:
        text = zf.read(name).decode("utf-8")
        for ident in re.findall(r'\sid="([^"]+)"', text):
            ids.add("%s#%s" % (name.split("/")[-1], ident))
        for href in re.findall(r'href="([^"]*#[^"]+)"', text):
            doc, frag = href.split("#", 1)
            links.append("%s#%s" % (doc or name.split("/")[-1], frag))
    return links, ids


def test_every_link_in_the_finished_book_lands_on_something_that_is_in_it(tmp_path):
    """A page whose blocks straddle a chapter break belongs to two documents, and its
    page marker is written into the first of them. The report is told which document
    to send a reader to for a given page, and being told the second one is a link
    that goes nowhere -- OBSERVED with epubcheck as RSC-012 on the acceptance book,
    where the about page sent a reader to ch008 for a marker that is in ch007."""
    book = _book(F.prose_page, F.mid_page_heading_page, F.prose_page)

    def write_about(where, losses=()):
        return ('<h1>About this conversion</h1><p>%s</p>'
                % "".join('<a href="%s#pg_%04d">page %d</a> ' % (href, pno, pno)
                          for pno, href in sorted(where.items())))

    result = _build(book, tmp_path, report_html=write_about)

    with zipfile.ZipFile(result.path) as zf:
        links, ids = _links_and_ids(zf)
    assert links, "a book with no links proves nothing"
    assert [link for link in links if link not in ids] == [], sorted(ids)


MODEL_ANSWER_WITH_AN_AMPERSAND = (
    '<p>He cites both places at once'
    '<a class="noteref" href="#fn_120">120</a>.</p>\n'
    '<aside class="footnote" id="fn_120">120 Hephaestio, Apotelesmatika, 2, 10: '
    '9 & 29. Pingree, "From Alexandria," p. 7, fn. 35.</aside>'
)


def test_an_ampersand_the_model_left_raw_does_not_stop_the_page_opening(tmp_path):
    """OBSERVED on the acceptance book: one note on one model page carried a bare
    "&", epubcheck called it FATAL RSC-016, and the whole document stops parsing --
    so a reader loses the chapter, not the ampersand. The deterministic reader
    escapes it; the model's answer is markup the model wrote, and it reaches the
    builder exactly as sent."""
    result = _model_page(tmp_path, html=MODEL_ANSWER_WITH_AN_AMPERSAND)

    with zipfile.ZipFile(result.path) as zf:
        name = _content_names(zf)[0]
        root = ET.fromstring(zf.read(name))          # the whole point: it parses

    text = ET.tostring(root, method="text", encoding="unicode")
    assert "9 & 29" in text, text


#: HTML a model writes looks like HTML a person writes, and a person writing HTML
#: types "&mdash;". None of these are XML entities; ``&fnord;`` is not an entity at
#: all, and the model that typed it meant those seven characters.
MODEL_ANSWER_WITH_NAMED_ENTITIES = (
    '<p>Ptolemy&nbsp;II is cited at &sect;12 &mdash; and again at &fnord;7'
    '<a class="noteref" href="#fn_44">44</a>.</p>\n'
    '<aside class="footnote" id="fn_44">44 Neugebauer &amp; Van Hoesen, p. 12.</aside>'
)


def test_an_entity_the_model_named_is_a_character_and_not_a_lost_chapter(tmp_path):
    """``&nbsp;`` is a well-formed zip entry, a well-formed ampersand, and an
    undefined entity in XML: the reader loses the document, the same way the raw
    ampersand lost one. Only the five XML entities are defined in a book, so an HTML
    name has to become the character it names, and a name that is not an entity at
    all has to stay the text the model typed."""
    result = _model_page(tmp_path, html=MODEL_ANSWER_WITH_NAMED_ENTITIES)

    assert build_epub.validate(result.path) == []

    with zipfile.ZipFile(result.path) as zf:
        root = ET.fromstring(zf.read(_content_names(zf)[0]))
    text = ET.tostring(root, method="text", encoding="unicode")

    assert "\u00a7" + "12" in text, text            # &sect; is a section sign
    assert "\u2014" in text, text                   # &mdash; is an em dash
    assert "Ptolemy\u00a0II" in text, text          # &nbsp; is a space that holds
    assert "&fnord;7" in text, text                 # and this one is just words
    assert "Neugebauer & Van Hoesen" in text, text  # the XML five still mean it


def test_a_document_that_does_not_parse_is_not_called_a_valid_book(tmp_path):
    """The builder's own check read the zip and not the documents inside it, so a
    page no reader can open shipped with a clean bill. Crafted here the way the
    model produced it: markup that is a well-formed zip entry and not well-formed
    XML."""
    result = _build(_book(F.prose_page), tmp_path)
    assert build_epub.validate(result.path) == []

    broken = str(tmp_path / "broken.epub")
    with zipfile.ZipFile(result.path) as good, zipfile.ZipFile(broken, "w") as bad:
        for item in good.infolist():
            data = good.read(item.filename)
            if item.filename.endswith("ch001.xhtml"):
                data = data.replace(b"<p>", b"<p>Hephaestio 9 & 29 ", 1)
            bad.writestr(item, data)

    problems = build_epub.validate(broken)

    assert problems, "a document that does not parse is a book that does not open"
    assert any("ch001.xhtml" in p for p in problems), problems


def _rewritten(source, target, name_ends, old, new):
    """The same book with one document altered -- a violation no builder writes."""
    with zipfile.ZipFile(source) as good, zipfile.ZipFile(target, "w") as bad:
        for item in good.infolist():
            data = good.read(item.filename)
            if item.filename.endswith(name_ends):
                assert old in data, "the fixture never contained %r" % old
                data = data.replace(old, new, 1)
            bad.writestr(item, data)
    return target


def test_a_book_that_uses_one_id_twice_is_not_called_a_valid_book(tmp_path):
    """Two elements under one id is a document that parses and a footnote that does
    not open: a reader following the second marker lands on the first note, and the
    second note is unreachable from anywhere in the book.

    OBSERVED with epubcheck on the acceptance book before the builder learned to tell
    two notes of one number apart -- twelve pages, twenty-five RSC-005 errors, and the
    conversion reported itself a success. The builder no longer writes them, and the
    check that stands between a built book and the library has to be able to say so
    anyway: the whole point of the check is that it holds when the generator is
    wrong."""
    result = _build(_book(F.defect_c_page), tmp_path)
    assert build_epub.validate(result.path) == []

    with zipfile.ZipFile(result.path) as zf:
        name = _content_names(zf)[0]
        ident = re.search(rb'\sid="(fn_[^"]+)"', zf.read(name)).group(1)
    # The page marker is made to answer to the note's id as well: two elements, one
    # id, and a document that still parses -- which is exactly why parsing missed it.
    broken = _rewritten(result.path, str(tmp_path / "twice.epub"), "ch001.xhtml",
                        b'id="pg_', b'id="%s" data-page="pg_' % ident)

    problems = build_epub.validate(broken)

    assert problems, "a book with one id on two elements is not a book that opens"
    assert any(ident.decode("ascii") in p for p in problems), problems
    assert any("ch001.xhtml" in p for p in problems), problems


def test_a_link_that_lands_on_nothing_is_not_called_a_valid_book(tmp_path):
    """A noteref whose aside is not in the same document is the shape a chapter break
    makes of a page: the marker goes in one file and the note in the next, and an
    ``href="#fn_x"`` means "in this file". OBSERVED as epubcheck RSC-012 on the
    acceptance book. ``href="#"`` is not this: the builder writes that on purpose for
    a marker whose note the book does not have, and reports it as a warning."""
    result = _build(_book(F.defect_c_page), tmp_path)
    assert build_epub.validate(result.path) == []

    broken = _rewritten(result.path, str(tmp_path / "nowhere.epub"), "ch001.xhtml",
                        b'href="#fn_', b'href="#fn_gone_')

    problems = build_epub.validate(broken)

    assert problems, "a link into nothing is a footnote the reader cannot open"
    assert any("fn_gone_" in p for p in problems), problems

    away = _rewritten(result.path, str(tmp_path / "away.epub"), "ch001.xhtml",
                      b'href="#fn_', b'href="ch404.xhtml#fn_')
    assert any("ch404.xhtml" in p for p in build_epub.validate(away)), \
        build_epub.validate(away)


def test_the_link_a_note_never_had_is_still_a_book_that_opens(tmp_path):
    """The control for the test above. ``href="#"`` is the builder's declared answer
    for a marker whose note is nowhere in the book -- ``build`` counts those and
    warns -- and turning that warning into a refusal would throw a whole conversion
    away over one note the scanner lost."""
    result = _build(_book(F.defect_c_page), tmp_path)
    disarmed = _rewritten(result.path, str(tmp_path / "disarmed.epub"), "ch001.xhtml",
                          b'href="#fn_', b'href="#" data-was="fn_')

    assert build_epub.validate(disarmed) == []


def _nav_labels(path, hrefs=None):
    """The table of contents as a reader reads it, in order."""
    with zipfile.ZipFile(path) as zf:
        nav = zf.read("OEBPS/nav.xhtml").decode("utf-8")
    toc = next(node for node in ET.fromstring(nav).iter(XHTML + "nav")
               if node.get("{http://www.idpf.org/2007/ops}type") == "toc")
    return ["".join(node.itertext()).strip() for node in toc.iter(XHTML + "a")
            if hrefs is None or node.get("href") in hrefs]


class TestADocumentWithNoHeadingOfItsOwn(object):
    """What to call a document the book never gave a title to.

    MEASURED on the acceptance book: 4 of its 221 documents carry no heading of any
    level, and naming a document after the first words that happen to stand on it
    made ``, ill .ts ?`` -- the specks a scanner read off the frontispiece plate --
    the first entry in the book's own table of contents. The other three are the
    middle of the bibliography and the middle of the index, split for length and
    named after whichever list line the split landed on, e.g.
    ``507, 510 and whole sign houses,…``.

    A first line is a title only when the page happens to open with one. A document
    is a place in the book whether or not it announces itself, so it is named for
    where it is.
    """

    def test_a_document_of_scan_noise_is_not_named_after_the_noise(self, tmp_path):
        book = _book(F.garbage_text_page,
                     lambda doc: F.chapter_opening_page(doc, "The Hellenistic Astrologers"))

        labels = _nav_labels(_build(book, tmp_path).path)

        assert labels, "a book with no table of contents has no way in"
        assert "aenlm" not in " ".join(labels), labels

    def test_it_is_named_for_the_page_it_stands_on(self, tmp_path):
        """Not naming it after the noise is half the job: a nav entry still has to
        tell the reader where it goes."""
        book = _book(F.garbage_text_page,
                     lambda doc: F.chapter_opening_page(doc, "The Hellenistic Astrologers"))

        labels = _nav_labels(_build(book, tmp_path).path)

        assert labels[0] == "Page 1", labels

    def test_a_document_split_for_length_says_which_chapter_it_continues(
            self, tmp_path, monkeypatch):
        """The three of the four that are not scan noise. A chapter too long for one
        document is cut into several, and only the first carries the heading; the
        rest are the same chapter and should say so."""
        monkeypatch.setattr(build_epub, "MAX_BLOCKS_PER_DOC", 4)
        book = _book(lambda doc: F.chapter_opening_page(doc, "The Hellenistic Astrologers"),
                     F.prose_page, F.prose_page, F.prose_page)

        result = _build(book, tmp_path)
        labels = _nav_labels(result.path, {chapter["href"] for chapter in result.chapters})

        assert len(labels) > 1, labels
        assert all(label.startswith("The Hellenistic Astrologers") for label in labels[1:]), \
            labels
        assert labels[1] != labels[0], labels

    def test_every_document_is_still_reachable_from_the_table_of_contents(
            self, tmp_path, monkeypatch):
        """The property the old fallback existed to keep: a document with no entry is
        a part of the book a reader can only reach by turning pages to it."""
        monkeypatch.setattr(build_epub, "MAX_BLOCKS_PER_DOC", 4)
        book = _book(F.garbage_text_page,
                     lambda doc: F.chapter_opening_page(doc, "The Hellenistic Astrologers"),
                     F.prose_page, F.prose_page)
        result = _build(book, tmp_path)

        with zipfile.ZipFile(result.path) as zf:
            documents = set(posixpath.basename(n) for n in _content_names(zf))
        targets = set()
        with zipfile.ZipFile(result.path) as zf:
            nav = zf.read("OEBPS/nav.xhtml").decode("utf-8")
        for href in re.findall(r'<a [^>]*href="([^"#]+)', nav):
            targets.add(posixpath.basename(href))

        assert documents <= targets, documents - targets

    def test_a_heading_anywhere_in_the_document_is_still_its_title(
            self, tmp_path, monkeypatch):
        """The control. A document that was split for length but happens to carry a
        lower-level heading is titled by that heading, as it always was."""
        monkeypatch.setattr(build_epub, "MAX_BLOCKS_PER_DOC", 4)
        book = _book(lambda doc: F.chapter_opening_page(doc, "The Hellenistic Astrologers"),
                     F.prose_page,
                     lambda doc: F.section_heading_page(doc, "Serapio of Alexandria"))

        labels = _nav_labels(_build(book, tmp_path).path)

        assert any("Serapio of Alexandria" in label for label in labels), labels
