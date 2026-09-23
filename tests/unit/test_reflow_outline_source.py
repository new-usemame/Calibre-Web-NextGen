"""Authored navigation is metadata, never replacement body transcription."""
import re
import zipfile

import pymupdf
import pytest

from cps.services.reflow import assemble, build_epub, extract


def _document(title='CHAPrER', duplicate=False):
    doc=pymupdf.open()
    for label in [title,'SECOND']:
        p=doc.new_page(width=400,height=600)
        p.insert_text(((400-pymupdf.get_text_length(label,fontsize=24))/2,100),label,fontsize=24)
        p.insert_text(((400-pymupdf.get_text_length('An example section',fontsize=18))/2,130),'An example section',fontsize=18)
        for i in range(12):
            body='Ordinary body prose remains readable and unchanged.'
            p.insert_text(((400-pymupdf.get_text_length(body,fontsize=10))/2,210+i*17),body,fontsize=10)
    toc=[[1,'CHAPTER',1],[1,'SECOND',2]]
    if duplicate:toc.insert(1,[2,'Different destination label',1])
    doc.set_toc(toc)
    return doc


def test_conflicting_opening_heading_preserves_pixels_and_literal_outline_navigation(tmp_path):
    doc=_document()
    try:
        book=assemble.deterministic_book(doc)
        assert 'CHAPrER' not in ' '.join(e.text for e in book.elements)
        assert any('CHAPrER' in a['text'] for a in book.artwork)
        assert book.conservation.ok
        assert any('Ordinary body prose' in e.text for e in book.elements)
        figures=[f for f in book.figures if f['found']=='native_outline_conflict']
        assert len(figures)==1 and figures[0]['bbox'][3]-figures[0]['bbox'][1]<180
        path=tmp_path/'outline.epub';build_epub.build(book,str(path),doc=doc)
        assert build_epub.validate(str(path))==[]
        with zipfile.ZipFile(path) as z:
            nav=z.read('OEBPS/nav.xhtml').decode()
            assert re.search(r'href="ch\d+\.xhtml#pg_0000">CHAPTER</a>',nav)
            chapters=''.join(z.read(n).decode() for n in z.namelist() if re.fullmatch(r'OEBPS/ch\d+\.xhtml',n))
            assert 'CHAPrER' not in chapters
            assert 'CHAPTER' not in re.sub(r'<head>.*?</head>','',chapters,flags=re.S)
            assert 'heading text conflicts' in chapters
    finally:doc.close()


@pytest.mark.parametrize('duplicate,title',[(False,'CHAPTER'),(True,'CHAPrER')])
def test_matching_or_ambiguous_outline_does_not_reinterpret_body(duplicate,title,tmp_path):
    doc=_document(title,duplicate)
    try:
        book=assemble.deterministic_book(doc)
        assert title in ' '.join(e.text for e in book.elements)
        assert not any(f['found']=='native_outline_conflict' for f in book.figures)
        assert book.conservation.ok
        if duplicate:
            # Two authored sections may share one page. The ambiguity blocks
            # body-heading inference, not their literal internal navigation.
            path=tmp_path/'same-page-outline.epub'
            build_epub.build(book,str(path),doc=doc)
            assert build_epub.validate(str(path))==[]
            with zipfile.ZipFile(path) as z:
                nav=z.read('OEBPS/nav.xhtml').decode()
                labels=re.findall(r'href="ch\d+\.xhtml#pg_0000">([^<]+)</a>',nav)
                assert labels[:2]==['CHAPTER','Different destination label']
    finally:doc.close()


def test_outline_accepts_only_resolved_internal_in_range_destinations():
    class Doc:
        def __len__(self):return 2
        def get_toc(self,simple=False):
            assert simple is False
            return [[1,'Literal MiXeD title',1,{'kind':1,'page':0}],
                    [1,'external',1,{'kind':2,'uri':'https://example.org'}],
                    [1,'remote',1,{'kind':5,'page':0,'file':'remote.pdf'}],
                    [1,'outside',99,{'kind':1,'page':98}],
                    [1,'mismatched',1,{'kind':1,'page':1}],
                    [1,'unresolved',0,{'kind':0}],['malformed'],
                    [1,'not a title',1,None]]
    assert extract.outline(Doc())==[{'level':1,'title':'Literal MiXeD title','pno':0,'internal':True}]


def test_outline_disagreement_cannot_turn_body_prose_into_a_page_image():
    doc=pymupdf.open()
    try:
        for _ in range(2):
            page=doc.new_page(width=400,height=600)
            for i in range(24):
                page.insert_text((40,70+i*18),'The ordinary paragraph continues without display typography.',fontsize=10)
        doc.set_toc([[1,'Different authored label',1],[1,'Another label',2]])
        book=assemble.deterministic_book(doc)
        assert not any(f['found']=='native_outline_conflict' for f in book.figures)
        assert 'ordinary paragraph' in ' '.join(e.text for e in book.elements)
        assert book.conservation.ok
    finally:doc.close()


def test_authored_duplicate_destinations_share_ncx_order_and_nav_follows_spine(tmp_path):
    from xml.etree import ElementTree as ET
    with _document(duplicate=True) as doc:
        doc.set_toc([[1,'SECOND',2],[1,'CHAPTER',1],[2,'Different destination label',1]])
        book=assemble.deterministic_book(doc);path=tmp_path/'ordered.epub'
        build_epub.build(book,str(path),doc=doc)
        with zipfile.ZipFile(path) as z:
            nav=ET.fromstring(z.read('OEBPS/nav.xhtml'))
            toc=next(n for n in nav.iter('{http://www.w3.org/1999/xhtml}nav') if n.get('id')=='toc')
            links=list(toc.iter('{http://www.w3.org/1999/xhtml}a'))
            assert [n.text for n in links[:3]]==['CHAPTER','Different destination label','SECOND']
            ncx=ET.fromstring(z.read('OEBPS/toc.ncx'));ns='{http://www.daisy.org/z3986/2005/ncx/}'
            seen={}
            for point in ncx.iter(ns+'navPoint'):
                href=point.find(ns+'content').get('src');order=point.get('playOrder')
                assert href not in seen or seen[href]==order
                seen[href]=order
            assert list(map(int,seen.values()))==list(range(1,len(seen)+1))


@pytest.mark.parametrize('fault',['ncx_duplicate_order','backward_toc'])
def test_internal_validator_refuses_semantically_invalid_navigation(tmp_path,fault):
    from xml.etree import ElementTree as ET
    with _document(duplicate=True) as doc:
        book=assemble.deterministic_book(doc);path=tmp_path/'valid.epub';build_epub.build(book,str(path),doc=doc)
    assert build_epub.validate(str(path))==[]
    with zipfile.ZipFile(path) as z:parts={n:z.read(n) for n in z.namelist()}
    if fault=='ncx_duplicate_order':
        root=ET.fromstring(parts['OEBPS/toc.ncx']);ns='{http://www.daisy.org/z3986/2005/ncx/}'
        points=list(root.iter(ns+'navPoint'));points[1].set('playOrder','9');parts['OEBPS/toc.ncx']=ET.tostring(root)
    else:
        root=ET.fromstring(parts['OEBPS/nav.xhtml']);ns='{http://www.w3.org/1999/xhtml}'
        toc=next(n for n in root.iter(ns+'nav') if n.get('id')=='toc');links=list(toc.iter(ns+'a'))
        links[0].set('href',links[2].get('href'));parts['OEBPS/nav.xhtml']=ET.tostring(root)
    bad=tmp_path/'invalid.epub'
    with zipfile.ZipFile(bad,'w') as z:
        for n,b in parts.items():z.writestr(n,b)
    errors=build_epub.validate(str(bad))
    assert any('navigation' in error.lower() for error in errors),errors
