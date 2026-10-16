"""A native note with a detached N. opening needs a real isolated divider."""
import copy
import io
from dataclasses import asdict
from PIL import Image,ImageDraw
import pytest
from cps.services.reflow import assemble,extract,skeleton,build_epub
from tests.unit.test_reflow_ruled_notes import line,block
pytestmark=pytest.mark.unit


@pytest.mark.parametrize('damage',[None,'no_rule','two_rules','thick','medium_band','vector_rule','no_callout','two_callouts','ordinary_digit','uncertain','ocr','mixed_below','lower_digit','wide_note'])
def test_source_pixel_separator_and_unique_native_callout_bind_literal_note(tmp_path,damage):
    doc=extract.pymupdf.open();page=doc.new_page(width=500,height=700)
    body=line('Complete body with a uniquely raised reference.',40,140,11)
    marker=line('6',body.bbox[2]+1,141,5)
    body.spans += marker.spans;body.bbox=(40,140,marker.bbox[2],151)
    if damage=='no_callout':body.spans.pop()
    if damage=='ordinary_digit':body.spans[-1].size=11;body.spans[-1].bbox=(marker.bbox[0],140,marker.bbox[2],151)
    if damage=='uncertain':body.spans[-1].uncertain=True
    if damage=='two_callouts':body.spans.append(copy.deepcopy(body.spans[-1]))
    main=block(0,[body,line('Separate source body above the note divider.',40,565,11)])
    opening=line('6.',42,597,10);sentence=line('This role holds true in the source.',65,602,8)
    note=block(1,[opening,sentence]);raw=extract.RawPage(0,500,700,blocks=[main,note])
    if damage=='ocr':
        for b in raw.text_blocks:
            for ln in b.lines:
                for sp in ln.spans:sp.font='ocr'
    if damage=='mixed_below':raw.blocks.append(block(2,[line('Unrelated ordinary body below the candidate.',40,625,11)]))
    if damage=='lower_digit':raw.blocks.append(block(2,[line('7',42,625,8)]))
    if damage=='wide_note':sentence.bbox=(25,602,490,610);sentence.spans[0].bbox=sentence.bbox;note.bbox=(25,597,490,610)
    if damage!='no_rule':
        im=Image.new('RGB',(640,32),'white');draw=ImageDraw.Draw(im)
        draw.line((0,15,639,15),fill='black',width=20 if damage=='thick' else 10 if damage=='medium_band' else 2)
        if damage=='two_rules':draw.line((0,3,639,3),fill='black',width=2)
        stream=io.BytesIO();im.save(stream,format='PNG')
        page.insert_image((40,580,360,596),stream=stream.getvalue())
    if damage=='vector_rule':
        page.draw_line((40,588),(360,588),width=.5)
        raw.drawings,raw.drawing_rects=extract.drawing_rects(page)
    before=asdict(raw);style=skeleton.BookStyle(body_size=11)
    probe=extract.ScanPixelProbe(doc,0,mask=[ln.bbox for b in raw.text_blocks for ln in b.lines])
    skel=skeleton.page_skeleton(raw,style,pixel_probe=probe)
    book=assemble.assemble([skel],style,[raw])
    assert asdict(raw)==before and book.conservation.ok
    if damage is None:
        assert len(book.notes)==1 and book.notes[0].num==6 and book.notes[0].marked
        assert book.notes[0].text=='This role holds true in the source.'
        html=build_epub.page_fragment(book,0)
        assert 'href="#fn_6"' in html and 'href="#fnref_6"' in html
        assert not any('This role' in e.text for e in book.pages[0])
    else:
        assert not book.notes,'an unproved divider/reference/territory cannot authorize a numeric note'
    doc.close()
