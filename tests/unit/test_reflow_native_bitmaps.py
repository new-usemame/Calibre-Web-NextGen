"""Original monochrome pixels remain exact through smaller EPUB resources."""
import io
import copy
import zipfile
from PIL import Image,ImageDraw
import pymupdf
import pytest
from cps.services.reflow.source_display import SourceDisplay
from cps.services.reflow import build_epub,assemble,skeleton
pytestmark=pytest.mark.unit


def scan(kind='plain'):
    im=Image.new('1',(600,800),1);draw=ImageDraw.Draw(im);draw.rectangle((80,90,520,130),fill=0);draw.line((90,250,490,300),fill=0,width=3)
    stream=io.BytesIO();im.save(stream,format='PNG');doc=pymupdf.open();page=doc.new_page(width=300,height=400)
    if kind=='hidden':page.insert_text((40,50),'unverified transcript',render_mode=3)
    if kind=='softmask':
        rgba=im.convert('RGBA');rgba.putalpha(128);stream=io.BytesIO();rgba.save(stream,format='PNG')
    page.insert_image(page.rect,stream=stream.getvalue())
    if kind=='text':page.insert_text((50,150),'VISIBLE OVERLAY')
    if kind=='vector':page.draw_rect(pymupdf.Rect(20,20,250,350),color=(1,0,0),width=3)
    if kind=='multiple':page.insert_image(pymupdf.Rect(10,10,70,90),stream=stream.getvalue())
    if kind=='rotation':page.set_rotation(90)
    if kind=='skew':
        # Replace the image matrix with a sheared one; dimensions alone cannot authorize reuse.
        xref=page.get_contents()[-1];raw=doc.xref_stream(xref);doc.update_stream(xref,raw.replace(b'300 0 0 400',b'300 20 0 400'))
    if kind=='mask':
        # A graphics-state opacity changes source pixels without adding an overlay.
        page=doc[0];xref=page.get_contents()[-1];raw=doc.xref_stream(xref)
        state=doc.get_new_xref();doc.update_object(state,'<< /Type /ExtGState /ca 0.5 /CA 0.5 >>');resource=int(doc.xref_get_key(page.xref,'Resources')[1].split()[0]);doc.xref_set_key(resource,'ExtGState',f'<< /Fade {state} 0 R >>');doc.update_stream(xref,raw.replace(b'q',b'q /Fade gs',1))
    return doc,im


def test_original_bitonal_crop_is_lossless_at_native_resolution():
    doc,image=scan('hidden');display=SourceDisplay(doc,0);data=display.source_image(pymupdf.Rect(30,35,270,160),scale=6)
    assert data.startswith(b'\x89PNG\r\n\x1a\n')
    with Image.open(io.BytesIO(data)) as crop:
        assert crop.mode=='1' and crop.size==(480,250)
        assert crop.tobytes()==image.crop((60,70,540,320)).tobytes()
    doc.close()


@pytest.mark.parametrize('kind',['text','vector','multiple','rotation','skew','mask','softmask'])
def test_uncertain_native_bitmap_proof_uses_existing_renderer(kind):
    doc,_=scan(kind);display=SourceDisplay(doc,0);data=display.source_image(scale=1.5)
    assert data.startswith(b'\xff\xd8'),kind
    assert data==display.jpeg(scale=1.5)
    doc.close()


def test_native_png_resource_keeps_routes_and_has_matching_manifest(tmp_path):
    doc,image=scan();element=assemble.Element('p',pno=0,bbox=(40,45,260,65),runs=[['t','Reliable prose remains text.']],punctuation_uncertain=True)
    book=assemble.Book(elements=[element],pages={0:[element]},style=skeleton.BookStyle(body_size=11))
    target=tmp_path/'native.epub';build_epub.build(book,str(target),doc=doc)
    assert build_epub.validate(str(target))==[]
    with zipfile.ZipFile(target) as archive:
        names=archive.namelist();assert 'OEBPS/images/original_p0000.png' in names
        original=archive.read('OEBPS/original-p0000.xhtml').decode();assert 'original_p0000.png' in original and '#pg_0000' in original
        opf=archive.read('OEBPS/content.opf').decode();assert 'image/png' in opf
        assert archive.read('OEBPS/images/original_p0000.png').startswith(b'\x89PNG')
    doc.close()


def test_native_crop_clips_bounds_without_resampling_or_accepting_outside():
    doc,image=scan();display=SourceDisplay(doc,0)
    data=display.source_image(pymupdf.Rect(-10,-10,20,30))
    with Image.open(io.BytesIO(data)) as crop:
        assert crop.size==(40,60) and crop.tobytes()==image.crop((0,0,40,60)).tobytes()
    with pytest.raises(ValueError):display.source_image(pymupdf.Rect(400,400,450,450))
    with pytest.raises(ValueError):display.source_image(scale=float('nan'))
    doc.close()


def test_png_glyph_resource_preserves_descriptor_and_body_semantics(tmp_path):
    from cps.services.reflow.native_text import descriptor
    from cps.services.reflow import enriched_source
    doc,_=scan();run=['glyph','unverified',descriptor(0,(40,45,260,65),11,'scan',reason='transcript')]
    element=assemble.Element('p',pno=0,bbox=(40,45,260,65),runs=[['t','Before '],run,['t',' after.']])
    book=assemble.Book(elements=[element],pages={0:[element]},style=skeleton.BookStyle(body_size=11))
    original=copy.deepcopy(element.runs);source=enriched_source.prepare_source_page(book,0,{'layer':'native'})
    identity=source.identity;target=tmp_path/'glyph.epub'
    build_epub.build(book,str(target),doc=doc,source_pages={0:source})
    assert element.runs==original and source.identity==identity
    assert build_epub.validate(str(target))==[]
    with zipfile.ZipFile(target) as z:
        chapters=[z.read(n).decode() for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')]
        assert any('Before <a class="source-glyph"' in text and '.png" alt=' in text and ' after.' in text for text in chapters)
    doc.close()
