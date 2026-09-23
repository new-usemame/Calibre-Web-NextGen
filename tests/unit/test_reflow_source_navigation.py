# SPDX-License-Identifier: GPL-3.0-or-later
"""A reader can navigate to actual PDF page starts, including sampled gaps."""

import zipfile
from xml.etree import ElementTree as ET

import pymupdf
import pytest

from cps.services.reflow import assemble, build_epub

pytestmark = pytest.mark.unit
XHTML = "{http://www.w3.org/1999/xhtml}"
EPUB_TYPE = "{http://www.idpf.org/2007/ops}type"


class _Images(dict):
    """The builder's image sink (``build_epub._Package``), keeping the bytes it is
    given so a test can compare the pixels it was sent."""

    @property
    def aliases(self):
        return {}  # This byte-comparison sink deliberately keeps requested names.

    @property
    def images(self):
        return {href: len(data) for href, data in self.items()}

    def image(self, href, data):
        self[href] = data


@pytest.mark.parametrize("selected", [(0, 1, 2), (0, 2)])
def test_source_page_navigation_resolves_to_real_starts_without_renumbering(tmp_path, selected):
    with pymupdf.open() as doc:
        for words in ("Arrival beside the river.", "The middle passage continues.",
                      "A final journey into the hills."):
            page = doc.new_page(width=500, height=700)
            page.insert_text((50, 110), words, fontsize=12)
        book = assemble.deterministic_book(doc)
    fragments = {pno: build_epub.page_fragment(book, pno) for pno in selected}
    # The marker must still resolve when the first source page spans chapters.
    fragments[0] += "<h2>A new section</h2><p>Its text follows.</p>"
    target = tmp_path / "navigation.epub"
    build_epub.build(book, str(target), page_html=fragments,
                     metadata={"title": "Source navigation contract", "language": "en"})
    with zipfile.ZipFile(target) as archive:
        nav = ET.fromstring(archive.read("OEBPS/nav.xhtml"))
        page_lists = [node for node in nav.iter(XHTML + "nav")
                      if node.get(EPUB_TYPE) == "page-list"]
        assert len(page_lists) == 1, "PDF page markers are unreachable through the navigation document"
        links = list(page_lists[0].iter(XHTML + "a"))
        assert [node.text for node in links] == ["PDF page %d" % (pno + 1) for pno in selected]
        for pno, link in zip(selected, links):
            filename, ident = link.attrib["href"].split("#")
            assert ident == "pg_%04d" % pno
            chapter = ET.fromstring(archive.read("OEBPS/" + filename))
            markers = [node for node in chapter.iter() if node.get("id") == ident]
            assert len(markers) == 1
            assert markers[0].get(EPUB_TYPE) == "pagebreak"

        # Nickel ignores the standard page-list. An ordinary, visible spine
        # document must offer the same destinations through its normal ToC.
        toc = next(node for node in nav.iter(XHTML + "nav")
                   if node.get(EPUB_TYPE) == "toc")
        index_entry = next((node for node in toc.iter(XHTML + "a")
                            if node.text == "Source PDF pages"), None)
        assert index_entry is not None, "Readers ignoring page-list have no source-page index"
        index_href = index_entry.get("href")
        source_index = ET.fromstring(archive.read("OEBPS/" + index_href))
        assert not any(node.get("hidden") or node.get("aria-hidden") == "true"
                       for node in source_index.iter())
        index_links = list(source_index.iter(XHTML + "a"))
        assert [(node.text, node.get("href")) for node in index_links] == [
            (node.text, node.get("href")) for node in links]
        opf = ET.fromstring(archive.read("OEBPS/content.opf"))
        ns = "{http://www.idpf.org/2007/opf}"
        item = next(node for node in opf.iter(ns + "item")
                    if node.get("href") == index_href)
        spine = list(opf.iter(ns + "itemref"))
        assert spine[-1].get("idref") == item.get("id"), "The index must not interrupt the book"


def test_source_glyph_notice_does_not_split_a_sentence_at_a_page_turn():
    """A page-end source warning remains available after its sentence finishes.

    Breaks if the warning hides the preceding paragraph from page-turn joining,
    or if moving it loses the actual source-page marker or original-page link.
    """
    notice = ('<p class="source-evidence-notice">Some glyphs need checking. '
              '<a href="original-p0000.xhtml#page">Open original page</a>.</p>')
    pages = build_epub._page_blocks({
        0: '<p>The conjunction is not technically an aspect, but is treated</p>' + notice,
        1: '<p>more or less as one, and is usually considered one of the aspects.</p>'
           '<h2>A new section</h2><p>Another paragraph.</p>',
    })
    assert build_epub._join_page_turns(pages) == 1
    chapters = build_epub._chapters(pages)
    root = ET.fromstring('<body xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">%s</body>' %
                         ''.join(block for chapter in chapters for block in chapter.blocks))
    paragraphs = list(root.iter(XHTML + 'p'))
    joined = next(p for p in paragraphs if 'The conjunction' in ''.join(p.itertext()))
    assert 'treated more or less as one' in ''.join(joined.itertext())
    notices = [p for p in paragraphs if 'Some glyphs need checking' in ''.join(p.itertext())]
    assert len(notices) == 1
    assert paragraphs.index(notices[0]) > paragraphs.index(joined)
    assert joined.find('.//*[@id="pg_0001"]') is not None
    assert root.find('.//*[@href="original-p0000.xhtml#page"]') is not None


@pytest.mark.parametrize('first,second', [
    ('A complete sentence.', 'Another paragraph starts here.'),
    ('A heading follows', '<h2>New chapter</h2><p>new opening.</p>'),
    ('An unfinished thought', '<figure><img src="images/diagram.jpg" alt="diagram"/></figure><p>new prose.</p>'),
])
def test_source_notice_cannot_force_unrelated_page_blocks_to_merge(first, second):
    """Warnings are movable only when the two adjacent prose blocks continue."""
    notice = '<p class="source-evidence-notice">Check source.</p>'
    pages = build_epub._page_blocks({0: '<p>%s</p>%s' % (first, notice),
                               1: second if second.startswith('<') else '<p>%s</p>' % second})
    assert build_epub._join_page_turns(pages) == 0
    assert pages[0]['body'][-1] == notice


def test_page_turn_with_a_prior_note_keeps_its_marker_after_the_note():
    """Joining prose cannot put the next PDF page ahead of an earlier note."""
    pages = build_epub._page_blocks({
        0: '<p>An unfinished thought</p><aside class="footnote" id="fn_1">1 Earlier note.</aside>',
        1: '<p>continues here.</p>',
    })
    assert build_epub._join_page_turns(pages) == 1
    assert 'continues here' in pages[0]['body'][0]
    assert 'pg_0001' not in pages[0]['body'][0]
    assert 'pg_0001' in pages[1]['anchor']


@pytest.mark.parametrize('details', [
    [],
    [{'id': 'notes', 'label': 'Printed note context',
      'src': 'images/original_p0015_notes.jpg', 'inspection_ids': ['inspection_0']},
     {'id': 'inspection_0', 'label': 'Original detail 1 (row order)',
      'src': 'images/original_p0015_inspection_0.jpg'}],
])
def test_original_page_fragment_has_return_at_entry_and_after_final_image(details):
    """A fragment jump and the last image both expose an explicit return route.

    Breaks when #page lands below the sole return link, or when a reader finishing
    a full-page image or inspection tile has no later link back to reflowed text.
    """
    record = {'page': 15, 'full': 'images/original_p0015.jpg', 'details': details}
    root = ET.fromstring(build_epub._original_document(record, 'ch015.xhtml', 'en'))
    body = root.find(XHTML + 'body')
    children = list(body)
    entry = next(el for el in body.iter() if el.get('id') == 'page')
    entry_index = next(i for i, el in enumerate(children) if entry in el.iter())
    assert entry_index == 0, 'fragment target misses the original-page entry'
    nodes = list(body.iter())
    images = [el for el in nodes if el.tag == XHTML + 'img']
    returns = [el for el in nodes if el.tag == XHTML + 'a'
               and el.get('href') == 'ch015.xhtml#pg_0015']
    assert len(images) == 1 + len(details)
    assert all(img.get('alt') for img in images)
    assert returns
    first_return_owner = next(i for i, el in enumerate(children) if returns[0] in el.iter())
    assert first_return_owner <= entry_index + 1, 'the return is not at fragment entry'
    assert nodes.index(returns[0]) < nodes.index(images[0])
    assert nodes.index(returns[-1]) > nodes.index(images[-1]), 'image end has no return'
    assert {el.get('id') for el in nodes if el.get('id')} >= {'page'} | {d['id'] for d in details}
    assert {img.get('src') for img in images} == {record['full']} | {d['src'] for d in details}


def test_uncertain_notes_expose_original_pixels_and_only_disarm_ambiguous_links(tmp_path):
    from cps.services.reflow import extract
    with pymupdf.open() as doc:
        page = doc.new_page(width=500,height=700)
        page.insert_text((50,90),'Original body marker 188 and reliable note 2.',fontsize=12)
        page.insert_text((50,580),'188 Original printed reference.',fontsize=10)
        page.insert_text((50,610),'2 Reliable printed reference.',fontsize=10)
        elements = [assemble.Element(kind='p',pno=0,runs=[
            ['t','A damaged association '],['sup','1',0,'uncertain'],
            ['t',' and a reliable association '],['sup','2',0],['t','.']])]
        notes = [assemble.Note(num=1,text='Misread extracted reference.',pno=0,marked=True,uncertain=True),
                 assemble.Note(num=2,text='Reliable printed reference.',pno=0,marked=True),
                 assemble.Note(num=3,text='Ordinary-looking unmatched neighbor.',pno=0),
                 assemble.Note(num=4,text='Another unmatched neighbor.',pno=0)]
        for note in notes: note.bbox=(50,565,400,620)
        book = assemble.Book(elements=elements,pages={0:elements},notes=notes)
        target=tmp_path/'evidence.epub'
        result=build_epub.build(book,str(target),doc=doc,
                               page_html={0:'<p>Unqualified replacement rendering.</p>'})
        original=extract.render_page_jpeg(doc,0,scale=1.5,quality=85)
    assert build_epub.validate(str(target)) == []
    with zipfile.ZipFile(target) as z:
        chapter=ET.fromstring(z.read('OEBPS/'+result.chapters[0]['href']))
        links=list(chapter.iter(XHTML+'a'))
        assert not any('#fn_p0000_1' in a.get('href','') for a in links)
        assert any(a.get('href','').endswith('#fn_p0000_2') for a in links)
        evidence=next((a for a in links if 'original-p0000.xhtml' in a.get('href','')),None)
        assert evidence is not None, 'a touch reader cannot reach original source pixels'
        assert 'uncertain' in ''.join(chapter.itertext()).lower()
        original_doc=ET.fromstring(z.read('OEBPS/original-p0000.xhtml'))
        images=list(original_doc.iter(XHTML+'img'))
        assert len(images)>=2, 'full page plus readable note-context detail are required'
        assert z.read('OEBPS/'+images[0].get('src'))==original
        assert any('notes' in img.get('alt','').lower() for img in images)
        assert any(a.get('href','').endswith('#pg_0000') for a in original_doc.iter(XHTML+'a'))
        index=ET.fromstring(z.read('OEBPS/source-pages.xhtml'))
        assert any('original-p0000.xhtml' in a.get('href','') for a in index.iter(XHTML+'a'))
        sidecar=build_epub.read_sidecar(str(target))
        opf=ET.fromstring(z.read('OEBPS/content.opf'))
        refs=list(opf.iter('{http://www.idpf.org/2007/opf}itemref'))
        assert next(r for r in refs if r.get('idref')=='original-p0000').get('linear')=='no'
        assert sidecar['notes_ambiguous']==3
        for number in (1,3,4):
            note=next(n for n in chapter.iter(XHTML+'aside')
                      if n.get('id')=='fn_p0000_%d'%number)
            assert '%d (?)'%number in ''.join(note.itertext())
            assert any(a.get('href')=='original-p0000.xhtml#notes'
                       for a in note.iter(XHTML+'a')), 'each detached note needs its own evidence link'
        reliable=next(n for n in chapter.iter(XHTML+'aside') if n.get('id')=='fn_p0000_2')
        assert '(?)' not in ''.join(reliable.itertext())
        assert sidecar['source_evidence'][0]['page']==0
        assert sidecar['source_evidence'][0]['bytes']>0


def test_original_caption_details_are_unique_and_linked_per_caption(tmp_path):
    with pymupdf.open() as doc:
        page=doc.new_page(width=400,height=600)
        page.insert_text((50,300),'First printed caption')
        page.insert_text((50,400),'Second printed caption')
        elements=[assemble.Element(kind='fig',pno=0),
                  assemble.Element(kind='caption',pno=0,runs=[['t','First extracted caption']],
                                   bbox=(40,280,250,310),caption_uncertain=True),
                  assemble.Element(kind='caption',pno=0,runs=[['t','Second extracted caption']],
                                   bbox=(40,380,250,410),caption_uncertain=True)]
        book=assemble.Book(elements=elements,pages={0:elements})
        fragments={0:build_epub.page_fragment(book,0)}
        images=_Images()
        evidence=build_epub._original_evidence(book,fragments,doc,images)
        details=evidence[0]['details']
        assert len({d['id'] for d in details})==2
        assert len({d['src'] for d in details})==2
        assert images[details[0]['src']] != images[details[1]['src']]
        for d in details:
            assert 'original-p0000.xhtml#'+d['id'] in fragments[0]
        assert fragments[0].count('transcription uncertain') >= 2


def test_original_evidence_rejects_empty_transformed_geometry_before_padding():
    with pymupdf.open() as doc:
        doc.new_page(width=400,height=600)
        caption=assemble.Element(kind='caption',pno=0,runs=[['t','Caption']],
                                 bbox=(40,280,250,310),caption_uncertain=True)
        book=assemble.Book(elements=[caption],pages={0:[caption]})
        with pytest.raises(ValueError,match='geometry'):
            build_epub._original_evidence(book,{0:build_epub.page_fragment(book,0)},doc,
                                          _Images(),figure_transform=lambda p,b:(0,0,0,0))


def test_unmatched_notes_without_damaged_source_group_remain_unqualified():
    book=assemble.Book(notes=[assemble.Note(num=3,text='Unmatched but undamaged.',pno=0)],
                       pages={0:[]})
    assert book.ambiguous_note_numbers(0)==set()
    assert '(?)' not in build_epub.page_fragment(book,0)
