"""Verified scan prose stays separate from raised marks and interior artwork."""
from dataclasses import asdict
import pytest
from cps.services.reflow import assemble, extract, skeleton
pytestmark=pytest.mark.unit

def line(text,box,size=10,uncertain=False):
    return extract.Line([extract.Span(text,size,'Native',0,box)],box,transcription_uncertain=uncertain)

def test_detached_uncertain_raised_marker_remains_inline_source_pixels():
    body=line('independent of its sex.',(30,32,113,42))
    marker=line('1',(113,30,116,36),5,True)
    marker.spans.append(extract.Span(' ',6.5,'Native',0,(116,29,118,36)))
    first=line('The source defines the sect of a planet and explains the meaning of the words in detail. This explanation provides the reader with the definitions that are needed for understanding the following discussion. The sect of a planet is',(30,19,290,31))
    raw=extract.RawPage(0,330,510,[extract.Block(i,l.bbox,[l]) for i,l in enumerate([first,marker,body])],
        images=[extract.Image((0,0,330,510),1)],text_layer_invisible=True)
    before=asdict(raw);style=skeleton.BookStyle(body_size=10)
    skel=skeleton.page_skeleton(raw,style)
    book=assemble.assemble([skel],style,[raw])
    assert book.conservation.ok,book.conservation.to_dict()
    assert len([e for e in book.pages[0] if e.kind=='p'])==1
    assert not any(f['found']=='ocr_uncertain_region' for f in book.figures)
    assert 'The sect of a planet is independent of its sex.' in book.pages[0][0].text
    glyph=next(r for r in book.pages[0][0].runs if r[0]=='glyph')
    assert glyph[1]=='1' and glyph[2]['bbox']==[113,30,116,36]
    assert asdict(raw)==before

@pytest.mark.parametrize('move', ['far','not_raised','ambiguous'])
def test_detached_mark_requires_unique_raised_terminal_geometry(move):
    body=line('independent of its sex.',(30,32,113,42))
    box=(125,30,128,36) if move=='far' else ((113,36,116,42) if move=='not_raised' else (113,30,116,36))
    marker=line('1',box,5,True);lines=[line('The source defines the sect of a planet and explains the meaning of the words in detail. This explanation provides the reader with the definitions that are needed for understanding the following discussion.',(30,10,290,21)),body,marker]
    if move=='ambiguous':lines.append(line('Another terminal line.',(30,31.8,113,41.8)))
    raw=extract.RawPage(0,330,510,[extract.Block(i,l.bbox,[l]) for i,l in enumerate(lines)],
        images=[extract.Image((0,0,330,510),1)],text_layer_invisible=True)
    book=assemble.assemble([skeleton.page_skeleton(raw,skeleton.BookStyle(body_size=10))],skeleton.BookStyle(body_size=10),[raw])
    assert any(f['found']=='ocr_uncertain_region' for f in book.figures)


def test_deep_numeric_footer_sequence_ignores_blank_span_size_and_scan_leading():
    raws=[]
    for p in range(5):
        body=line('An ordinary source paragraph continues',(30,422,290,455),9.5)
        folio=line(str(57+p),(275,467+(p%2)*2,284,476+(p%2)*2),9.3)
        folio.spans.append(extract.Span(' ',10,'Native',0,(284,folio.bbox[1]-1,286,folio.bbox[3]+1)))
        folio.bbox=(275,folio.bbox[1]-1,286,folio.bbox[3]+1)
        raws.append(extract.RawPage(p,330,510,[extract.Block(0,body.bbox,[body]),extract.Block(1,folio.bbox,[folio])]))
    style=skeleton.book_style(raws)
    assert all(p in style.folio_boxes for p in range(5))
    book=assemble.assemble([skeleton.page_skeleton(r,style) for r in raws],style,raws)
    assert book.conservation.ok
    assert len(book.furniture)==5


def test_interior_scan_diagram_does_not_turn_verified_surrounding_prose_into_plate(tmp_path):
    import io,pymupdf
    from PIL import Image,ImageDraw
    image=Image.new('L',(600,1000),255);draw=ImageDraw.Draw(image)
    for y in range(210,730,25):draw.line((65,y,535,y),fill=0,width=8)
    for x in range(65,536,25):draw.line((x,210,x,730),fill=0,width=8)
    stream=io.BytesIO();image.save(stream,format='PNG')
    path=tmp_path/'interior.pdf'
    with pymupdf.open() as doc:
        p=doc.new_page(width=300,height=500);p.insert_image(p.rect,stream=stream.getvalue())
        p.insert_textbox((30,20,270,95),'The source paragraph continues in the order in which it was written. '*4,fontsize=9,render_mode=3)
        p.insert_text((30,400),'The next paragraph continues below the diagram.',fontsize=9,render_mode=3)
        doc.save(path)
    with pymupdf.open(path) as doc:
        raw=extract.read_page(doc,0);probe=extract.ScanPixelProbe(doc,0,mask=[l.bbox for b in raw.text_blocks for l in b.lines])
        assert probe.coverage((0,0,300,500))>=skeleton.PLATE_INK_COVER
        assert skeleton._full_bleed_plate(raw,[(b,b.lines) for b in raw.text_blocks],probe) is None
        skel=skeleton.page_skeleton(raw,skeleton.BookStyle(body_size=9),pixel_probe=probe)
        assert any(r.kind=='body' and 'The source paragraph' in r.text for r in skel.regions)
        assert any(r.kind=='figure' and 0<r.bbox[1]<=105 and 365<=r.bbox[3]<450 for r in skel.regions)
