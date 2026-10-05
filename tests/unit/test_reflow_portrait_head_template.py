"""Detached native header/folio rows need three independent physical-page peers."""
from dataclasses import asdict
import pytest
from cps.services.reflow import extract,skeleton,assemble
from tests.unit.test_reflow_ruled_notes import line,block
pytestmark=pytest.mark.unit


@pytest.mark.parametrize('damage',[None,'two_pages','wrong_offset','missing_slot',
    'body_band','different_label','misaligned_head','misaligned_folio','no_body',
    'one_readable','title_row','table_row','table_title'])
def test_native_portrait_row_template_preserves_literal_words_and_body(damage):
    labels=['Technical Basis and che Inherent Difficulties of House Division',
            'Technical Basis and the Inherent Diffirnlties of House Division',
            'Technical Basis and the Inherent Difficulties of House Division']
    boxes=[(118.08,58.26,354.51,71.99),(118.56,53.80,355.05,72.55),(113.28,58.25,350.21,68.41)]
    pages=[]
    for i,pno in enumerate((120,122,124)):
        head=line(labels[i],boxes[i][0],boxes[i][1],8.6);head.bbox=boxes[i];head.spans[0].bbox=head.bbox
        folio=line(('97','99','I 0 I')[i],365,61.7,8.6)
        body=line('small and causes no real problems; but in high latitudes it becomes',88,91.39,8.6)
        pages.append(extract.RawPage(pno,450,669,blocks=[block(0,[head]),block(1,[folio]),block(2,[body])]))
    middle=pages[1]
    if damage=='two_pages':pages.pop()
    if damage=='wrong_offset':middle.blocks[1].lines[0].spans[0].text='100'
    if damage=='missing_slot':pages[2].blocks.pop(1)
    if damage=='body_band':
        for raw in pages:
            for blk in raw.blocks:
                for ln in blk.lines:
                    a=ln.bbox;ln.bbox=(a[0],a[1]+150,a[2],a[3]+150);ln.spans[0].bbox=ln.bbox
    if damage=='different_label':middle.blocks[0].lines[0].spans[0].text='A wholly separate chapter title has different lettering'
    if damage=='misaligned_head':
        a=middle.blocks[0].lines[0].bbox;middle.blocks[0].lines[0].bbox=(a[0]+30,a[1],a[2]+30,a[3])
    if damage=='misaligned_folio':middle.blocks[1].lines[0].bbox=(260,61.7,275,70.3)
    if damage=='no_body':pages[2].blocks.pop()
    if damage=='one_readable':pages[0].blocks[1].lines[0].spans[0].text='I O I'
    if damage=='title_row':middle.blocks[2].lines[0].bbox=(88,75,400,83.6)
    if damage=='table_row':
        middle.blocks[2].lines=[line('A B C',88,91.39,8.6)]
    if damage=='table_title':
        for raw in pages:
            raw.blocks[0].lines[0].spans[0].text='Table of Observed Orbital Classifications and Their Annual Frequencies'
    before=[asdict(raw) for raw in pages]
    proved=skeleton._repeated_detached_heads(pages,8.6)
    target=middle.blocks[0].lines[0]
    if damage is None:
        assert all(len(proved.get(raw.pno,[]))==2 for raw in pages)
        style=skeleton.book_style(pages)
        book=assemble.assemble([skeleton.page_skeleton(raw,style) for raw in pages],style,pages)
        assert book.conservation.ok
        assert not any('Technical Basis' in e.text or 'I 0 I' in e.text for e in book.elements)
        assert any('small and causes no real problems' in e.text for e in book.elements)
        assert any('Diffirnlties' in t for t in book.furniture)
        assert 'I 0 I' in book.furniture
    else:
        assert target.bbox not in proved.get(middle.pno,[]),'unproved peers/slot/folio progression cannot grant furniture role'
    assert before==[asdict(raw) for raw in pages]
