"""A printed page and its authored Contents route must have one reading home."""

import re
import zipfile
from xml.etree import ElementTree as ET

import pymupdf
import pytest

from cps.services.reflow import assemble, build_epub

pytestmark = pytest.mark.unit


def test_empty_front_pages_and_page_after_title_keep_destinations():
    pages = build_epub._page_blocks({
        0: '', 1: '', 2: '<h1>Title</h1>', 3: '',
        4: '<h1>Chapter</h1><p>First paragraph.</p>',
    })
    chapters = build_epub._chapters(pages, title_pages={2: 'Title'})
    homes = build_epub._page_homes(chapters)
    assert set(homes) == set(range(5))
    blocks = [block for chapter in chapters for block in chapter.blocks]
    for pno in range(5):
        assert blocks.count(build_epub.page_anchor(pno)) == 1
    assert blocks.index(build_epub.page_anchor(1)) < blocks.index('<h1>Title</h1>')
    assert blocks.index(build_epub.page_anchor(3)) < blocks.index('<h1>Chapter</h1>')


def _source_book(numbered_rows=True, chapter_after_rows=False):
    doc = pymupdf.open()
    dedication = doc.new_page(width=450, height=670)
    dedication.insert_text((100, 230), "Dedicated to the reader")
    contents = doc.new_page(width=450, height=670)
    contents.insert_text((260, 190), "Contents", fontsize=25)
    for index, title in enumerate(("Foreword", "Introduction", "First chapter", "Second chapter")):
        suffix = ("ix", "1", "9", "21")[index] if numbered_rows else "discussed here"
        contents.insert_text((210, 250 + index * 23), "%s  %s" % (title, suffix), fontsize=10)
    if chapter_after_rows:
        contents.insert_text((80, 390), "A real chapter begins here.", fontsize=11)
    following = doc.new_page(width=450, height=670)
    following.insert_text((80, 180), "The next chapter begins here.")
    doc.set_toc([[1, "Contents", 2], [1, "Next chapter", 3]])
    book = assemble.deterministic_book(doc)
    page_html = {
        0: "<p>Dedicated to the reader</p>",
        1: ('<h1>Contents</h1><h2>Foreword</h2><p>ix</p><h2>Introduction</h2><p>1</p>'
            '<h2>First chapter</h2><p>9</p><h2>Second chapter</h2><p>21</p>'),
        2: '<h1>Next chapter</h1><p>The next chapter begins here.</p>',
    }
    if chapter_after_rows:
        page_html[1] += '<h2>Actual section</h2><p>A real chapter begins here.</p>'
    return doc, book, page_html


def _body(archive, name):
    root = ET.fromstring(archive.read("OEBPS/" + name))
    return root.find("{http://www.w3.org/1999/xhtml}body")


def test_authored_contents_page_keeps_heading_rows_marker_and_returns_together(tmp_path):
    """Breaks if a leading notice owns the marker or printed rows split spines."""
    doc, book, page_html = _source_book()
    try:
        built = build_epub.build(book, str(tmp_path / "contents.epub"),
                                page_html=page_html, doc=doc)
    finally:
        doc.close()
    assert build_epub.validate(built.path) == []
    with zipfile.ZipFile(built.path) as z:
        chapters = [name.split("/")[-1] for name in z.namelist()
                    if re.fullmatch(r"OEBPS/ch\d+\.xhtml", name)]
        home = next(name for name in chapters if any(e.get("id") == "pg_0001" for e in _body(z, name)))
        text = " ".join(_body(z, home).itertext())
        assert "Dedicated to the reader" not in text
        assert all(item in text for item in ("Contents", "Foreword", "Introduction",
                                             "First chapter", "Second chapter", "21"))
        assert "The next chapter begins here" not in text
        contents = next(e for e in ET.fromstring(z.read("OEBPS/toc.ncx")).iter()
                        if e.tag.endswith("content") and e.get("src", "").endswith("#pg_0001"))
        assert contents.get("src") == home + "#pg_0001"
        original = build_epub._original_document({
            'page': 1, 'full': 'images/original_p0001.jpg', 'details': []},
            home, 'en')
        assert 'href="%s#pg_0001"' % home in original
        nav = z.read("OEBPS/nav.xhtml").decode()
        assert 'href="%s#pg_0001"' % home in nav


def test_contents_label_without_numbered_source_rows_keeps_real_chapter_splits():
    """An outline title and heading alone do not turn later sections into list rows."""
    doc, _, page_html = _source_book(numbered_rows=False)
    try:
        pages = build_epub._page_blocks(page_html)
        identified = build_epub._source_contents_pages(pages, doc)
        chapters = build_epub._chapters(pages, contents_pages=identified)
    finally:
        doc.close()
    assert identified == set()
    assert sum(any("<h2>" in block for block in chapter.blocks) for chapter in chapters) >= 2


@pytest.mark.parametrize("first", ["<p>Body text.</p>",
                                   '<ol class="source-list"><li>One</li></ol>',
                                   '<figure><img src="images/example.jpg" alt="Source"/></figure>'])
def test_leading_generated_notice_stays_at_start_of_its_page_without_heading(first):
    pages = build_epub._page_blocks({0: "<p>Earlier page.</p>",
        1: '<p class="source-evidence-notice">Check <a href="source-pages.xhtml">source</a>.</p>' + first})
    chapters = build_epub._chapters(pages)
    blocks = [block for chapter in chapters for block in chapter.blocks]
    marker = blocks.index(build_epub.page_anchor(1))
    assert "source-evidence-notice" in blocks[marker + 1]
    assert blocks[marker + 2] == first


def test_leading_generated_notice_moves_after_heading_with_same_marker():
    pages = build_epub._page_blocks({0: "<p>Earlier page.</p>",
        1: ('<p class="source-evidence-notice">Check source.</p>'
            '<h1>New chapter</h1><p>Opening prose.</p>')})
    chapters = build_epub._chapters(pages)
    home = next(c for c in chapters if build_epub.page_anchor(1) in c.blocks)
    marker = home.blocks.index(build_epub.page_anchor(1))
    assert home.blocks[marker + 1] == "<h1>New chapter</h1>"
    assert "source-evidence-notice" in home.blocks[marker + 2]
    assert "<p>Earlier page.</p>" not in home.blocks


def test_blank_page_marker_and_multiple_real_headings_remain_distinct():
    pages = build_epub._page_blocks({0: "<p>Before.</p>", 1: "",
        2: "<h1>First section</h1><p>One.</p><h2>Second section</h2><p>Two.</p>"})
    chapters = build_epub._chapters(pages)
    assert sum(build_epub.page_anchor(1) in c.blocks for c in chapters) == 1
    assert any("<h1>First section</h1>" in c.blocks for c in chapters)
    assert any("<h2>Second section</h2>" in c.blocks for c in chapters)
    assert not any("<h1>First section</h1>" in c.blocks and
                   "<h2>Second section</h2>" in c.blocks for c in chapters)


def test_blank_page_after_contents_still_has_a_source_page_home():
    pages = build_epub._page_blocks({0: "<h1>Contents</h1><h2>First row</h2>",
                                     1: "", 2: "<p>Later text.</p>"})
    chapters = build_epub._chapters(pages, contents_pages={0})
    home = next(c for c in chapters if build_epub.page_anchor(1) in c.blocks)
    assert build_epub.page_anchor(0) not in home.blocks
    assert build_epub._page_homes(chapters)[1] == home.href


def test_an_unauthored_numbered_contents_heading_does_not_suppress_sections():
    doc, _, page_html = _source_book()
    doc.set_toc([])
    try:
        pages = build_epub._page_blocks(page_html)
        identified = build_epub._source_contents_pages(pages, doc)
        chapters = build_epub._chapters(pages, contents_pages=identified)
    finally:
        doc.close()
    assert identified == set()
    assert any("<h2>Foreword</h2>" in c.blocks for c in chapters)
    assert any("<h2>Second chapter</h2>" in c.blocks for c in chapters)
    assert not any("<h2>Foreword</h2>" in c.blocks and
                   "<h2>Second chapter</h2>" in c.blocks for c in chapters)


def test_real_chapter_after_numbered_contents_rows_keeps_its_boundary():
    doc, _, page_html = _source_book(chapter_after_rows=True)
    try:
        pages = build_epub._page_blocks(page_html)
        identified = build_epub._source_contents_pages(pages, doc)
        chapters = build_epub._chapters(pages, contents_pages=identified)
    finally:
        doc.close()
    assert identified == set()
    assert any('<h2>Actual section</h2>' in c.blocks for c in chapters)
    assert not any('<h1>Contents</h1>' in c.blocks and
                   '<h2>Actual section</h2>' in c.blocks for c in chapters)


@pytest.mark.parametrize("top", [95, 175, 190])
def test_side_column_beside_contents_keeps_its_real_section_boundary(top):
    """A substantive block outside the list cannot license page-wide heading suppression."""
    doc, _, page_html = _source_book()
    try:
        text = ('Actual section\nThis is a real chapter.\nIt occupies a separate\n'
                'column beside Contents.\nIts text continues down\nthe printed page.')
        assert doc[1].insert_textbox((20, top, 180, top + 175), text, fontsize=11) > 0
        page_html[1] += ('<h2>Actual section</h2><p>This is a real chapter. '
                         'It occupies a separate column beside Contents. '
                         'Its text continues down the printed page.</p>')
        pages = build_epub._page_blocks(page_html)
        identified = build_epub._source_contents_pages(pages, doc)
        chapters = build_epub._chapters(pages, contents_pages=identified)
    finally:
        doc.close()
    assert identified == set()
    assert not any('<h1>Contents</h1>' in c.blocks and
                   '<h2>Actual section</h2>' in c.blocks for c in chapters)
    assert any('<h2>Actual section</h2>' in c.blocks for c in chapters)


def test_side_column_emits_a_separate_chapter_in_the_epub(tmp_path):
    doc, book, page_html = _source_book()
    try:
        text = ('Actual section\nThis is a real chapter.\nIt occupies a separate\n'
                'column beside Contents.\nIts text continues down\nthe printed page.')
        assert doc[1].insert_textbox((20, 175, 180, 350), text, fontsize=11) > 0
        page_html[1] += ('<h2>Actual section</h2><p>This is a real chapter. '
                         'It occupies a separate column beside Contents. '
                         'Its text continues down the printed page.</p>')
        built = build_epub.build(book, str(tmp_path / "mixed.epub"),
                                page_html=page_html, doc=doc)
    finally:
        doc.close()
    assert build_epub.validate(built.path) == []
    with zipfile.ZipFile(built.path) as archive:
        bodies = [_body(archive, name) for name in sorted(
            n.split('/')[-1] for n in archive.namelist()
            if re.fullmatch(r'OEBPS/ch\d+\.xhtml', n))]
        contents_home = next(i for i, body in enumerate(bodies)
                             if any(e.get('id') == 'pg_0001' for e in body))
        section_home = next(i for i, body in enumerate(bodies)
                            if 'Actual section' in ' '.join(body.itertext()))
        assert contents_home != section_home
        assert 'Contents' in ' '.join(bodies[contents_home].itertext())
        assert 'This is a real chapter.' in ' '.join(bodies[section_home].itertext())
