"""Opaque tracked source text can retain a proved wrap without intervening notices."""
import zipfile
from xml.etree import ElementTree as ET
import pymupdf
import pytest
from cps.services.reflow import assemble,build_epub,extract,skeleton
pytestmark=pytest.mark.unit


def raster_book(tail='A planet inhabits the fem-',head='inine sign Taurus.',known='The feminine sign is discussed again.'):
    doc=pymupdf.open();raws=[]
    for p,text in enumerate((tail,head,known)):
        page=doc.new_page(width=350,height=500);page.insert_text((40,100),text,fontsize=10)
        box=(40,85,310,105)
        line=extract.Line([extract.Span(text,10,'Serif',0,box)],box)
        line.spacing_uncertain=p<2
        raws.append(extract.RawPage(p,350,500,[extract.Block(0,box,[line])]))
    style=skeleton.BookStyle(body_size=10)
    book=assemble.assemble([skeleton.page_skeleton(r,style) for r in raws],style,raws)
    assert book.conservation.ok
    return doc,book


def test_source_raster_wrap_keeps_both_pixel_atoms_in_one_paragraph(tmp_path):
    doc,book=raster_book()
    try:
        built=build_epub.build(book,tmp_path/'raster.epub',doc=doc)
        assert built.page_joins==1
        with zipfile.ZipFile(built.path) as z:
            paragraphs=[n for name in z.namelist() if name.startswith('OEBPS/ch') and name.endswith('.xhtml')
                        for n in ET.fromstring(z.read(name)).iter('{http://www.w3.org/1999/xhtml}p')]
            joined=[p for p in paragraphs if len(list(p.iter('{http://www.w3.org/1999/xhtml}img')))==2]
            assert len(joined)==1
            assert [n.get('src').rsplit('/',1)[-1].split('.')[0] for n in joined[0].iter('{http://www.w3.org/1999/xhtml}img')]==['fig_p0000_0','fig_p0001_0']
            assert 'Character spacing is uncertain' not in ''.join(joined[0].itertext())
    finally:doc.close()


@pytest.mark.parametrize('tail,head,known',[
    ('A complete source sentence.','another printed paragraph.','An unrelated sentence.'),
    ('An unknown source xyz-','abc combination.','An unrelated sentence.'),
])
def test_complete_or_unproved_raster_boundary_stays_separate(tmp_path,tail,head,known):
    doc,book=raster_book(tail,head,known)
    try:
        assert build_epub.build(book,tmp_path/'separate.epub',doc=doc).page_joins==0
    finally:doc.close()
