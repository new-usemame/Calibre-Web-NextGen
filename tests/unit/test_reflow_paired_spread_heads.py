"""Unique spread heads need opposing recurrence and progressing paired folios."""
from dataclasses import asdict,replace
import pytest
from cps.services.reflow import extract,skeleton,assemble
from tests.unit.test_reflow_rules_first_seams import _scan_spread
pytestmark=pytest.mark.unit


@pytest.mark.parametrize('damage',[None,'list_boxes','wrong_pair','no_opposing_template','two_leaves','body_title','plausible_title','misaligned_row'])
def test_unique_opposing_head_uses_physical_spread_template_without_repair(damage):
    labels=['FOG DAISY','PAPER OCEAN','NORTH CHAPTER','HORIZON BIRDS','QUARTZ MUSIC']
    raws=[_scan_spread(i,'TRADITIONAL ASTROLOGY',height=10,gap=18) for i in range(5)]
    for raw in raws:
        for index,ln in enumerate(raw.blocks[0].lines):
            x,y,x1,y1=ln.bbox;box=(x,y+32,x1,y1+32);text=ln.stripped
            if y==20:
                side=int(x>400);folio=16+raw.pno*2+side
                if damage=='wrong_pair' and raw.pno==2 and side:folio=99
                label=labels[raw.pno] if side or damage=='no_opposing_template' else 'TRADITIONAL ASTROLOGY'
                text=(str(folio)+' '+label) if side==0 else (label+' '+str(folio))
                if raw.pno==2 and side and damage in ('body_title','misaligned_row'):
                    dy=28 if damage=='body_title' else -14;box=tuple(v+dy if j%2 else v for j,v in enumerate(box))
            elif x>400:
                text=text[:1].lower()+text[1:]
                if damage=='plausible_title' and raw.pno==2:
                    text=text[:1].upper()+text[1:]
            raw.blocks[0].lines[index]=replace(ln,bbox=box,spans=[extract.Span(text,18 if y==20 else 11,'OCR',0,box)])
    if damage=='two_leaves':raws=raws[:2]
    if damage=='list_boxes':
        for raw in raws:
            for b in raw.text_blocks:
                for ln in b.lines:ln.bbox=list(ln.bbox)
    before=[asdict(raw) for raw in raws];style=skeleton.book_style(raws)
    target=raws[min(2,len(raws)-1)]
    head=next(ln for b in target.text_blocks for ln in b.lines if ln.stripped.startswith(labels[target.pno]))
    boxes=style.scan_spread_head_boxes.get(target.pno,[])
    if damage in (None,'list_boxes'):
        assert head.bbox in boxes,'paired progressing folios plus the proved opposing head must retain a unique marginal title as furniture'
        book=assemble.assemble([skeleton.page_skeleton(raw,style) for raw in raws],style,raws)
        assert book.conservation.ok
        assert head.stripped in book.furniture
        assert all(head.stripped not in e.text for e in book.elements)
    else:assert head.bbox not in boxes
    assert [asdict(raw) for raw in raws]==before
