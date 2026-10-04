"""One printed word cannot be split by OCR confidence and disclosure blocks."""
from dataclasses import asdict
import pytest
from cps.services.reflow import extract,skeleton,assemble,build_epub
from tests.unit.test_reflow_layout_artwork import _line,_block
pytestmark=pytest.mark.unit

@pytest.mark.parametrize('barrier',[None,'size_noise','gap','column','indent'])
def test_split_ocr_paragraph_keeps_complete_printed_word_in_one_crop(barrier):
    first=_line('The original paragraph ends this printed line with basic knowl-',30,40,270,51,11)
    x=330 if barrier=='column' else (45 if barrier=='indent' else 30)
    y=85 if barrier=='gap' else 52
    second=_line('edge is preserved with every word in its source order.',x,y,x+240,y+11,11)
    third=_line('This is the end of the original source paragraph.',x,y+12,x+220,y+23,11)
    for ln in [first,second,third]:
        for span in ln.spans:span.font='ocr'
    second.spans[0].uncertain=True
    if barrier=='size_noise':first.spans[0].size=14.7
    blocks=[_block(0,[first]),_block(1,[second,third])]
    raw=extract.RawPage(0,600,400,blocks);before=asdict(raw)
    style=skeleton.BookStyle(body_size=11)
    book=assemble.assemble([skeleton.page_skeleton(raw,style)],style,[raw])
    assert book.conservation.ok and asdict(raw)==before
    owned=[a for a in book.artwork if 'edge is preserved' in a['text']]
    assert len(owned)==1
    if barrier in ('gap','column','indent'):
        assert 'knowl-' not in owned[0]['text']
        assert any('knowl-' in e.text for e in book.elements)
    else:
        assert 'basic knowl- edge is preserved' in owned[0]['text']
        assert len(book.figures)==1
        html=build_epub.page_fragment(book,0)
        assert html.count('source-raster')==1 and 'knowl-' not in html
        assert owned[0]['bbox'][1]==40
