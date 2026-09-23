"""Synthetic character spacing is evidence of uncertain transcription, not spelling."""
import re
import zipfile
import pymupdf
import pytest
from cps.services.reflow import extract, skeleton, assemble, build_epub


def test_tracked_native_line_preserves_pixels_and_neighbor_prose(tmp_path):
    with pymupdf.open() as doc:
        page=doc.new_page(width=400,height=600)
        x=40
        for letter in 'visibility':
            page.insert_text((x,150),letter,fontsize=9)
            x+=pymupdf.get_text_length(letter,fontsize=9)+2.5
        page.insert_text((40,190),'Reliable following prose remains text.',fontsize=11)
        raw=extract.read_page(doc,0)
        assert any(getattr(line,'spacing_uncertain',False) for block in raw.text_blocks for line in block.lines)
        style=skeleton.book_style([raw]);book=assemble.assemble([skeleton.page_skeleton(raw,style)],style,[raw])
        assert book.conservation.ok
        assert 'Reliable following prose remains text.' in ' '.join(e.text for e in book.elements)
        assert any(f['found']=='native_spacing_uncertain' for f in book.figures)
        path=tmp_path/'tracked.epub';build_epub.build(book,str(path),doc=doc)
        assert build_epub.validate(str(path))==[]
        with zipfile.ZipFile(path) as z:
            body=''.join(z.read(n).decode() for n in z.namelist() if re.fullmatch(r'OEBPS/ch\d+\.xhtml',n))
            assert 'Character spacing is uncertain' in body
            assert 'v i s' not in body
            assert 'original-p0000.xhtml#page' in body


def test_encoded_spaces_and_ordinary_inferred_word_spaces_remain_text():
    with pymupdf.open() as doc:
        page=doc.new_page(width=400,height=600)
        page.insert_text((40,150),'a b c d e are deliberate encoded spaces.',fontsize=9)
        x=40
        for word in ['Ordinary','separate','words','remain','readable']:
            page.insert_text((x,190),word,fontsize=9)
            x+=pymupdf.get_text_length(word,fontsize=9)+4
        raw=extract.read_page(doc,0)
        assert not any(getattr(line,'spacing_uncertain',False) for block in raw.text_blocks for line in block.lines)
        book=assemble.assemble([skeleton.page_skeleton(raw,skeleton.book_style([raw]))],skeleton.book_style([raw]),[raw])
        assert not any(f['found']=='native_spacing_uncertain' for f in book.figures)
        assert book.conservation.ok


def _overpainted_document(overpaint=True,invisible=False):
    source=pymupdf.open();p=source.new_page(width=400,height=600)
    p.insert_textbox((40,65,360,120),'Ordinary reliable surrounding prose is a complete sentence with the words that explain how the example is used. The next sentence is normal readable text that stays outside the sparse symbol region.',fontsize=10)
    for x,text in [(40,'Object'),(130,'Speed'),(220,'Direction')]:p.insert_text((x,160),text,fontsize=9)
    for y,label in [(210,'A'),(270,'B'),(330,'C')]:p.insert_text((40,y),label,fontsize=10)
    p.insert_text((130,210),'N/A',fontsize=9)
    png=p.get_pixmap().tobytes('png');source.close()
    doc=pymupdf.open();p=doc.new_page(width=400,height=600)
    if not overpaint:p.insert_image(p.rect,stream=png)
    p.insert_textbox((40,65,360,120),'Ordinary reliable surrounding prose is a complete sentence with the words that explain how the example is used. The next sentence is normal readable text that stays outside the sparse symbol region.',fontsize=10,render_mode=3 if invisible else 0)
    for x,text in [(40,'Object'),(130,'Speed'),(220,'Direction')]:p.insert_text((x,160),text,fontsize=9,render_mode=3 if invisible else 0)
    for y,label in [(210,'O'),(270,'2'),(330,'S')]:p.insert_text((40,y),label,fontsize=10,render_mode=3 if invisible else 0)
    p.insert_text((130,210),'n / a',fontsize=9,render_mode=3 if invisible else 0)
    if overpaint:p.insert_image(p.rect,stream=png)
    return doc


@pytest.mark.parametrize('overpaint',[True,False])
def test_image_paint_order_quarantines_sparse_unverified_layout_only(overpaint,tmp_path):
    with _overpainted_document(overpaint) as doc:
        raw=extract.read_page(doc,0)
        assert getattr(raw,'text_layer_overpainted',False) is overpaint
        style=skeleton.book_style([raw]);book=assemble.assemble([skeleton.page_skeleton(raw,style)],style,[raw])
        assert book.conservation.ok
        assert 'Ordinary reliable surrounding prose' in ' '.join(e.text for e in book.elements)
        regions=[f for f in book.figures if f['found']=='unverified_scan_layout']
        assert bool(regions) is overpaint
        if overpaint:
            assert not any(e.text.strip() in ('O','2','S') for e in book.elements)
            path=tmp_path/'scan-layout.epub';build_epub.build(book,str(path),doc=doc)
            assert build_epub.validate(str(path))==[]
            with zipfile.ZipFile(path) as z:
                body=''.join(z.read(n).decode() for n in z.namelist() if re.fullmatch(r'OEBPS/ch\d+\.xhtml',n))
                assert 'Unverified scan transcription' in body
                assert 'n / a' not in body


def test_invisible_scan_symbols_have_the_same_unverified_provenance_as_overpainted_text():
    with _overpainted_document(False,True) as doc:
        raw=extract.read_page(doc,0)
        assert raw.text_layer_invisible and not raw.text_layer_overpainted
        style=skeleton.book_style([raw]);book=assemble.assemble([skeleton.page_skeleton(raw,style)],style,[raw])
        assert book.conservation.ok
        assert any(f['found']=='unverified_scan_layout' for f in book.figures)
        assert not any(e.text.strip() in ('O','2','S') for e in book.elements)
