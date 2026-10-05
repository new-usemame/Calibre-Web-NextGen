"""Two captioned raster keys retain rows and an independent opposite leaf."""
from dataclasses import asdict,replace
import pytest
import pymupdf
from cps.services.reflow import assemble,extract,skeleton

pytestmark=pytest.mark.unit

def _fixture(change=None):
    raw=extract.RawPage(0,600,400,images=[extract.Image((0,0,600,400),1)])
    lines=[]
    def line(text,box,uncertain=False):
        ln=extract.Line([extract.Span(text,9,'ocr',0,box,uncertain=uncertain)],box)
        lines.append(ln);return ln
    for i in range(12):line('Figure reference %s with printed page'%i,(40,65+12*i,250,74+12*i))
    line('CONTENTS',(405,25,455,34))
    for panel,(top,count,caption_y) in enumerate(((80,10,204),(255,8,354))):
        for i in range(count):
            x=440 if change=='misaligned' and panel==1 and i%2 else 420
            line('Label '+str(i),(x,top+12*i,x+55,top+12*i+9))
        if not (change=='missing_caption' and panel==1):
            line('Figure %s: Printed associations'%(panel+1),(350,caption_y,560,caption_y+9))
        if panel==1:
            line('unreadable glyph strip',(392,top,401,top+12*(count-1)+9),True)
    if change=='prose':line('An independent long ordinary paragraph crosses the labels.',(330,315,580,329))
    if change=='gutter':lines[12]=replace(lines[12],bbox=(285,25,455,34))
    raw.blocks=[extract.Block(i,ln.bbox,[ln]) for i,ln in enumerate(lines)]
    painting=pymupdf.open();page=painting.new_page(width=600,height=400)
    for ln in lines:
        if ln.stripped=='unreadable glyph strip':continue
        page.insert_text((ln.bbox[0],ln.bbox[3]-1),ln.stripped,fontsize=7)
    for panel,(top,count) in enumerate(((80,10),(255,8))):
        for i in range(count):
            if change=='no_symbols' and panel==1:continue
            x=366 if change=='wide_symbol' and panel==1 and i==3 else 394
            page.draw_rect((x,top+12*i+1,401,top+12*i+8),color=(0,0,0),width=.7)
    if change=='marginal_ink':page.draw_rect((590,120,599,130),color=None,fill=(0,0,0))
    if change in ('unowned_ink','faint_ink'):
        page.draw_rect((475,235,490,245),color=None,fill=(.99,.99,.98) if change=='faint_ink' else (0,0,0))
    pixels=page.get_pixmap(matrix=pymupdf.Matrix(2,2)).tobytes('png');painting.close()
    doc=pymupdf.open();p=doc.new_page(width=600,height=400);p.insert_image(p.rect,stream=pixels)
    return raw,doc

@pytest.mark.parametrize('change',[None,'no_probe','missing_caption','misaligned','no_symbols','wide_symbol','prose','gutter','unowned_ink','faint_ink','marginal_ink'])
def test_captioned_keys_have_separate_complete_source_owners(change):
    raw,doc=_fixture(change);before=asdict(raw)
    try:
        probe=None if change=='no_probe' else extract.ScanPixelProbe(doc,0)
        style=skeleton.book_style([raw]);skel=skeleton.page_skeleton(raw,style,pixel_probe=probe)
        keys=[r for r in skel.regions if r.kind=='figure' and r.reason=='uncertain_scan_key_panel']
        if change is None:
            assert len(keys)==2,'two independently captioned keys must keep their literal glyph/label associations'
            assert keys[0].bbox[2]<=600 and keys[0].bbox[3]<keys[1].bbox[1]
            assert all(r.bbox[0]>300 for r in keys)
            for r in skel.regions:
                if r.reason=='uncertain_scan_key_panel' and r.kind=='artwork':
                    assert all(not ln.stripped.startswith('Figure reference') for ln in r.lines)
            book=assemble.assemble([skel],style,[raw]);assert book.conservation.ok
            assert [f['found'] for f in book.figures].count('uncertain_scan_key_panel')==2
            assert book.figures[0]['bbox'][2]<=300
            assert all(f['bbox'][0]>=300 for f in book.figures[1:])
        else:assert not keys
        assert asdict(raw)==before
    finally:doc.close()


def test_packaged_local_keys_keep_complete_source_rows_and_caption(tmp_path):
    import io,zipfile
    from PIL import Image,ImageChops
    from cps.services.reflow import build_epub
    from tests.unit.reflow_image_assertions import figure_name
    raw,doc=_fixture();style=skeleton.book_style([raw])
    try:
        skel=skeleton.page_skeleton(raw,style,pixel_probe=extract.ScanPixelProbe(doc,0))
        book=assemble.assemble([skel],style,[raw]);assert book.conservation.ok
        target=tmp_path/'keys.epub';build_epub.build(book,target,doc=doc)
        assert build_epub.validate(target)==[]
        with zipfile.ZipFile(target) as package:
            html=package.read('OEBPS/ch001.xhtml').decode()
            assert html.count('Printed symbol key retained from the original page.')==2
            assert html.index('fig_p0000_0')<html.index('fig_p0000_2')<html.index('fig_p0000_3')
            assert 'original-p0000.xhtml#page' in html
            for index,figure in enumerate(book.figures):
                if figure['found']!='uncertain_scan_key_panel':continue
                pix=doc[0].get_pixmap(matrix=pymupdf.Matrix(2,2),clip=pymupdf.Rect(figure['bbox']),
                                     colorspace=pymupdf.csRGB,alpha=False)
                source=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
                extent=ImageChops.difference(source,Image.new('RGB',source.size,'white')).getbbox()
                assert extent
                left=max(0,extent[0]-8);right=min(source.width,extent[2]+8)
                if source.width-(right-left)<source.width*.10:left,right=0,source.width
                with Image.open(io.BytesIO(package.read(figure_name(package,'fig_p0000_%s'%index)))) as image:
                    pad=(image.width-(right-left))//2
                    assert pad in (0,8)
                    assert image.height==source.height+2*pad
                    gray=image.convert('L')
                    top,count=(80,10) if index==2 else (255,8)
                    for row in range(count):
                        x=round(396*2)-pix.x-left+pad
                        y=round((top+12*row+2)*2)-pix.y+pad
                        assert min(gray.crop((x-3,y-3,x+4,y+4)).getdata())<100
    finally:doc.close()
