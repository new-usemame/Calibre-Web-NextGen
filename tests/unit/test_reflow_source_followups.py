"""Hidden damaged layers and retained source structure must not silently flatten."""
import io
import re
import zipfile
import pymupdf
import pytest
from cps.services.reflow import extract,source,skeleton,assemble,build_epub,ocr


def _hidden_scan(hidden=True):
    doc=pymupdf.open();page=doc.new_page(width=400,height=600)
    paper=pymupdf.Pixmap(pymupdf.csRGB,pymupdf.IRect(0,0,400,600));paper.clear_with(255)
    page.insert_image(page.rect,stream=paper.tobytes('png'))
    page.insert_text((40,100),'zq$ if lt ; 9 : 6 7',fontsize=12,render_mode=3 if hidden else 0)
    return doc


@pytest.mark.parametrize('hidden',[True,False])
def test_short_hidden_scan_noise_requests_recovery_but_visible_source_is_preserved(hidden):
    with _hidden_scan(hidden) as doc:
        raw=extract.read_page(doc,0)
        assert source.needs_recovery(raw)==('damaged_layer' if hidden else '')


def test_failed_hidden_scan_recovery_preserves_pixels_and_exact_source_link(monkeypatch,tmp_path):
    monkeypatch.setattr(ocr,'_engine',lambda *a:None)
    def failed(*a,**k):raise ocr.OCRFailed('fixture recognition unavailable')
    monkeypatch.setattr(ocr,'recognize_page',failed)
    with _hidden_scan() as doc:
        raw=extract.read_page(doc,0)
        recovery=source.recover(doc,[raw],extract.document_fingerprint(doc),mode='auto')
        assert recovery.failed==1
        style=skeleton.book_style(recovery.pages)
        book=assemble.assemble([skeleton.page_skeleton(raw,style)],style,[raw])
        assert book.conservation.ok
        path=tmp_path/'fallback.epub';build_epub.build(book,str(path),doc=doc)
        assert build_epub.validate(str(path))==[]
        with zipfile.ZipFile(path) as z:
            body=''.join(z.read(n).decode() for n in z.namelist() if re.fullmatch(r'OEBPS/ch\d+\.xhtml',n))
            assert 'zq$' not in body
            assert 'original-p0000.xhtml#page' in body
            assert 'Original page image' in body


def test_numbered_source_lines_are_distinct_items_not_inline_numbers(tmp_path):
    from xml.etree import ElementTree as ET
    with pymupdf.open() as doc:
        p=doc.new_page(width=400,height=600)
        for i,text in enumerate(['First source item','Second source item continues','Third source item']):
            p.insert_text((40,120+i*13),str(i+1)+'.',fontsize=11)
            p.insert_text((60,120+i*13),text,fontsize=11)
        p.insert_text((40,230),'Prose refers to 1. one point and 2. another in one sentence.',fontsize=10)
        artwork=pymupdf.Pixmap(pymupdf.csRGB,pymupdf.IRect(0,0,120,100));artwork.clear_with(80)
        p.insert_image(pymupdf.Rect(220,330,340,430),stream=artwork.tobytes('png'))
        book=assemble.deterministic_book(doc);path=tmp_path/'list.epub';build_epub.build(book,str(path),doc=doc)
        assert book.conservation.ok
        assert build_epub.validate(str(path))==[]
        with zipfile.ZipFile(path) as z:
            roots=[ET.fromstring(z.read(n)) for n in z.namelist() if re.fullmatch(r'OEBPS/ch\d+\.xhtml',n)]
            ns='{http://www.w3.org/1999/xhtml}'
            lists=[n for root in roots for n in root.iter(ns+'ol')]
            assert len(lists)==1
            assert [re.sub(r'\s+',' ',''.join(n.itertext())).strip() for n in lists[0].findall(ns+'li')]==['1. First source item','2. Second source item continues','3. Third source item']
            assert any('Prose refers to 1.' in ''.join(n.itertext()) for root in roots for n in root.iter(ns+'p'))


def test_overpainted_paired_columns_preserve_complete_source_associations(tmp_path):
    def put(p):
        p.insert_text((40,110),'Authors',fontsize=10);p.insert_text((180,110),'Meaning',fontsize=10)
        for i in range(3):
            y=140+i*75
            p.insert_text((40,y),'Author '+str(i+1),fontsize=10)
            p.insert_text((40,y+13),'Work title '+str(i+1),fontsize=10)
            p.insert_text((180,y),'This definition belongs to this author.',fontsize=9)
            p.insert_text((180,y+12),'Its continuation remains associated.',fontsize=9)
    with pymupdf.open() as raster:
        p=raster.new_page(width=400,height=500);put(p);png=p.get_pixmap().tobytes('png')
    with pymupdf.open() as doc:
        p=doc.new_page(width=400,height=500);put(p);p.insert_image(p.rect,stream=png)
        book=assemble.deterministic_book(doc)
        assert book.conservation.ok
        assert any(f['found']=='unverified_paired_columns' for f in book.figures)
        assert not any('Work title' in e.text for e in book.elements)
        path=tmp_path/'paired.epub';build_epub.build(book,str(path),doc=doc)
        assert build_epub.validate(str(path))==[]
        with zipfile.ZipFile(path) as z:
            body=''.join(z.read(n).decode() for n in z.namelist() if re.fullmatch(r'OEBPS/ch\d+\.xhtml',n))
            assert 'Original column relationships' in body
            assert 'original-p0000.xhtml#page' in body


def test_plausible_invisible_prose_is_not_automatically_replaced_by_second_ocr():
    with _hidden_scan() as doc:
        doc[0].insert_textbox((40,150,360,300),
            'This is a plausible paragraph of ordinary prose with enough words to pass a language test. '
            'The source is a normal passage with complete sentences and the same readable words in each line. '
            'It does not require another recognition pass merely because the embedded transcript is invisible.',
            fontsize=10,render_mode=3)
        raw=extract.read_page(doc,0)
        assert source.needs_recovery(raw)==''


@pytest.mark.parametrize('numeric',[True,False])
def test_unverified_three_column_layout_owns_pixels_once_and_preserves_surrounding_prose(numeric,tmp_path):
    def put(p,hidden):
        p.insert_textbox((30,25,420,95),'A surrounding source paragraph remains outside the numeric table. '
            'This is ordinary prose with enough words to establish that the page has a readable passage. '
            'It remains in the source and should still reflow around the table.',fontsize=9,render_mode=3 if hidden else 0)
        for col in range(3):
            for row in range(12):
                text=f'{row+1} {col+1}.{row:02d}' if numeric else f'Column {col+1} line {row+1}'
                p.insert_text((40+col*130,110+row*13),text,fontsize=9,render_mode=3 if hidden else 0)
    with pymupdf.open() as pixels:
        p=pixels.new_page(width=440,height=500);put(p,False);png=p.get_pixmap().tobytes('png')
    with pymupdf.open() as doc:
        p=doc.new_page(width=440,height=500);p.insert_image(p.rect,stream=png);put(p,True)
        raw=extract.read_page(doc,0)
        assert raw.is_page_scan
        assert raw.text_layer_invisible
        book=assemble.deterministic_book(doc)
        assert book.conservation.ok
        regions=[f for f in book.figures if f['found']=='unverified_scan_layout']
        assert len(regions)==1
        assert not any('Column 1 line' in e.text or '1 1.00' in e.text for e in book.elements)
        if numeric:
            assert regions[0]['bbox'][1]>80
            assert any('surrounding source paragraph' in e.text for e in book.elements)
        else:
            assert tuple(regions[0]['bbox'])==(0,0,440,500)
        path=tmp_path/'layout.epub';build_epub.build(book,str(path),doc=doc)
        assert build_epub.validate(str(path))==[]
