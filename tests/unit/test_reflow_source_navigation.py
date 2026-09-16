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
        evidence,images=build_epub._original_evidence(book,fragments,doc)
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
                                          figure_transform=lambda p,b:(0,0,0,0))


def test_unmatched_notes_without_damaged_source_group_remain_unqualified():
    book=assemble.Book(notes=[assemble.Note(num=3,text='Unmatched but undamaged.',pno=0)],
                       pages={0:[]})
    assert book.ambiguous_note_numbers(0)==set()
    assert '(?)' not in build_epub.page_fragment(book,0)
