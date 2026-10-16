"""Measured small spread heads must survive damaged OCR without entering prose."""
from dataclasses import asdict, replace
import pytest
from cps.services.reflow import assemble, extract, skeleton

pytestmark = pytest.mark.unit


def spread(page, *, gutter=50.16, gap=8.88, header_height=8.64):
    # Synthetic geometry: 17.04pt prose rows, 50.16pt gutter on an 841.68pt
    # spread, and 8.88pt separation below an 8.64pt damaged running head.
    blocks=[]
    for side, x in enumerate((436.32-gutter-336, 436.32)):
        hx=x if not side else x+202.56
        box=(hx,6,hx+130.32,6+header_height)
        head=extract.Line([extract.Span('SAMPLE MADE LETTERING iy’',7,'OCR',0,box)],box)
        head.transcription_uncertain=True
        lines=[head]
        for row in range(6):
            y=6+header_height+gap+row*17.04
            box=(x,y,x+336,y+17.04)
            label=('orchid','maple','willow','peony','cedar','birch')[2*page+side]
            text=f'Ordinary {label} source prose continues across this printed spread'+('.' if row==5 else '')
            lines.append(extract.Line([extract.Span(text,12,'OCR',0,box)],box))
        blocks.append(extract.Block(side,(x,6,x+336,140),lines))
    raw=extract.RawPage(page,841.68,595,blocks,images=[extract.Image((0,0,841.68,595),1.0)])
    raw.text_layer_invisible=True
    return raw


@pytest.mark.parametrize('damage',[None,'gutter_too_narrow','attached_head','crossing_line','two_leaves','body_sized_title','unwrapped_rows'])
def test_small_damaged_heads_use_clear_gutter_and_measured_rows(damage):
    raws=[spread(i,gutter=30 if damage=='gutter_too_narrow' else 50.16,
                 gap=8 if damage=='attached_head' else 8.88,
                 header_height=17.04 if damage=='body_sized_title' else 8.64) for i in range(3)]
    if damage=='two_leaves':raws=raws[:2]
    if damage=='crossing_line':
        for raw in raws:
            box=(350,150,480,160)
            raw.blocks.append(extract.Block(9,box,[extract.Line([extract.Span('bridging source ink',10,'OCR',0,box)],box)]))
    if damage=='unwrapped_rows':
        for raw in raws:
            for block in raw.blocks:
                block.lines=[replace(line,spans=[replace(s,text=s.text+'.') for s in line.spans]) if j else line for j,line in enumerate(block.lines)]
    before=[asdict(r) for r in raws]
    style=skeleton.book_style(raws)
    for raw in raws:
        boxes=style.scan_spread_head_boxes.get(raw.pno,[])
        heads=[b.lines[0] for b in raw.blocks[:2]]
        if damage is None:
            assert all(h.bbox in boxes for h in heads)
            skel=skeleton.page_skeleton(raw,style)
            assert all(any(r.kind=='furniture' and h in r.lines for r in skel.regions) for h in heads)
        else:
            assert not any(h.bbox in boxes for h in heads),damage
    if damage is None:
        book=assemble.assemble([skeleton.page_skeleton(r,style) for r in raws],style,raw_pages=raws)
        assert book.conservation.ok
        assert all('SAMPLE MADE LETTERING' not in e.text for e in book.elements)
        assert len(book.furniture)==6
    assert [asdict(r) for r in raws]==before
