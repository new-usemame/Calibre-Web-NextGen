"""Evidence-only PNG selection preserves rendered pixels and source routes."""
import io
import random
import zipfile

import pymupdf
from PIL import Image

from cps.services.reflow import assemble, build_epub, skeleton
from cps.services.reflow.source_display import SourceDisplay


def _decoded_rgb(data):
    with Image.open(io.BytesIO(data)) as image:
        return image.size, image.convert('RGB').tobytes()


def test_gray_evidence_png_is_exact_while_default_jpeg_input_is_unchanged(tmp_path):
    doc=pymupdf.open();page=doc.new_page(width=300,height=400)
    for y in range(30,350,16):
        page.insert_text((24,y),'A source line and fine antialiasing.',fontsize=10)
    display=SourceDisplay(doc,0)
    baseline=display.jpeg(scale=1.5,quality=85)
    assert display.source_image(scale=1.5,quality=85)==baseline
    candidate=display.source_image(scale=1.5,quality=85,lossless_candidate=True)
    assert candidate.startswith(b'\x89PNG')
    pix=display.pixmap(scale=1.5)
    assert _decoded_rgb(candidate)==((pix.width,pix.height),pix.samples)
    element=assemble.Element('p',pno=0,bbox=(24,30,270,46),
                             runs=[['t','Source words remain once.']],punctuation_uncertain=True)
    book=assemble.Book(elements=[element],pages={0:[element]},
                       style=skeleton.BookStyle(body_size=11))
    target=tmp_path/'evidence.epub'
    result=build_epub.build(book,str(target),doc=doc)
    assert build_epub.validate(str(target))==[]
    with zipfile.ZipFile(target) as archive:
        original=archive.read('OEBPS/original-p0000.xhtml').decode()
        opf=archive.read('OEBPS/content.opf').decode()
        chapter=archive.read('OEBPS/'+result.chapters[0]['href']).decode()
        assert 'images/original_p0000.png' in original
        assert 'image/png' in opf and 'original_p0000.png' in opf
        assert 'original-p0000.xhtml#text_0' in chapter
        assert 'Return to reflowed PDF page' in original
        assert archive.read('OEBPS/images/original_p0000.png')==candidate
    doc.close()


def test_colored_overlay_and_rotated_crop_keep_exact_rendered_samples():
    doc=pymupdf.open();page=doc.new_page(width=140,height=200)
    page.draw_rect((4,10,90,145),fill=(.82,.91,.74),color=None)
    page.draw_circle((75,80),25,fill=(.1,.2,.9),color=(.7,.1,.2))
    page.insert_text((22,170),'Visible overlay',fontsize=13,color=(1,0,0))
    page.set_rotation(90)
    display=SourceDisplay(doc,0)
    for rect in (None,pymupdf.Rect(13.25,21.75,110.4,155.2)):
        data=display.source_image(rect,lossless_candidate=True)
        if data.startswith(b'\x89PNG'):
            pix=display.pixmap(rect,3)
            assert _decoded_rgb(data)==((pix.width,pix.height),pix.samples)
        else:
            assert data==display.jpeg(rect)
    doc.close()


def test_photographic_png_larger_keeps_exact_existing_jpeg():
    rng=random.Random(7)
    rgb=rng.randbytes(180*180*3)
    image=Image.frombytes('RGB',(180,180),rgb)
    stream=io.BytesIO();image.save(stream,format='PNG')
    doc=pymupdf.open();page=doc.new_page(width=180,height=180)
    page.insert_image(page.rect,stream=stream.getvalue())
    display=SourceDisplay(doc,0)
    assert display.source_image(scale=1,lossless_candidate=True)==display.jpeg(scale=1)
    doc.close()


def test_inset_multiple_images_and_visible_outside_text_remain_in_render():
    raster=Image.new('RGB',(40,55),'white')
    stream=io.BytesIO();raster.save(stream,format='PNG')
    doc=pymupdf.open();page=doc.new_page(width=160,height=220)
    page.insert_image((10,12,50,67),stream=stream.getvalue())
    page.insert_image((85,100,125,155),stream=stream.getvalue())
    page.insert_text((20,200),'Outside the inset images',fontsize=11)
    display=SourceDisplay(doc,0)
    assert display._native_bitonal() is None
    pix=display.pixmap(scale=3)
    data=display.source_image(scale=3,lossless_candidate=True)
    assert data.startswith(b'\x89PNG')
    assert _decoded_rgb(data)==((pix.width,pix.height),pix.samples)
    doc.close()


def test_proven_native_bitonal_png_is_identical_with_candidate_enabled():
    raster=Image.new('1',(300,400),1)
    stream=io.BytesIO();raster.save(stream,format='PNG')
    doc=pymupdf.open();page=doc.new_page(width=300,height=400)
    page.insert_image(page.rect,stream=stream.getvalue())
    display=SourceDisplay(doc,0)
    normal=display.source_image(scale=1.5)
    candidate=display.source_image(scale=1.5,lossless_candidate=True)
    assert normal.startswith(b'\x89PNG') and candidate==normal
    doc.close()


def test_mixed_png_and_jpeg_package_preserves_each_original_route(tmp_path):
    rng=random.Random(19)
    photo=Image.frombytes('RGB',(180,180),rng.randbytes(180*180*3))
    stream=io.BytesIO();photo.save(stream,format='PNG')
    doc=pymupdf.open()
    for _ in range(2):doc.new_page(width=180,height=180)
    for y in range(16,170,12):
        doc[0].insert_text((10,y),'Sparse gray source text',fontsize=8)
    doc[1].insert_image(doc[1].rect,stream=stream.getvalue())
    elements=[assemble.Element('p',pno=pno,bbox=(10,10,170,28),
                               runs=[['t','Source text']],punctuation_uncertain=True)
              for pno in (0,1)]
    book=assemble.Book(elements=elements,pages={0:[elements[0]],1:[elements[1]]},
                       style=skeleton.BookStyle(body_size=11))
    target=tmp_path/'mixed.epub'
    build_epub.build(book,str(target),doc=doc)
    assert build_epub.validate(str(target))==[]
    with zipfile.ZipFile(target) as archive:
        images={name for name in archive.namelist() if name.startswith('OEBPS/images/original_p')}
        assert 'OEBPS/images/original_p0000.png' in images
        assert 'OEBPS/images/original_p0001.jpg' in images
        opf=archive.read('OEBPS/content.opf').decode()
        assert 'image/png' in opf and 'image/jpeg' in opf
        for pno,extension in ((0,'png'),(1,'jpg')):
            original=archive.read('OEBPS/original-p%04d.xhtml' % pno).decode()
            assert 'original_p%04d.%s' % (pno,extension) in original
            assert 'Return to reflowed PDF page' in original
    doc.close()
