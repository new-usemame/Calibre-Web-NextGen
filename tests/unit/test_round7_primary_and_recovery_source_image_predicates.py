"""Source image production must be owned before reading and during recovery."""
import pymupdf,zipfile
from xml.etree import ElementTree as ET
from cps.services.reflow import vision_pipeline as vp,source_display,extract,build_epub
from tests.unit.round7_recovery_test_support import fixture,h,TEXTS


def assert_source_page(path,report):
    assert build_epub.validate(path)==[]
    with zipfile.ZipFile(path) as archive:
        docs={n:ET.fromstring(archive.read(n)) for n in archive.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')}
        assert h.body_texts(docs)==[TEXTS[0],TEXTS[2]],'Only the affected source page may leave live prose'
        assert {u['page'] for u in report['degraded_units']}=={1}
        for unit in report['degraded_units']:
            assert 'OEBPS/'+unit['source_image'] in archive.namelist()
            target=next(e for root in docs.values() for e in root.iter() if e.get('id')==unit['source_reference'])
            assert target.findall('.//x:img',h.NS)
            assert any(h.plain(a)=='Return' for a in target.findall('.//x:a',h.NS))


def test_scanned_primary_actual_pixmap_scale_rejection_is_owned_before_reader_dispatch(tmp_path,monkeypatch):
    doc,reader,_=fixture(tmp_path,monkeypatch);original=source_display.SourceDisplay.pixmap;reached=[]
    def rejected(display,rect=None,scale=1,gray=False,max_pixels=None):
        if display.pno==1 and not reached:
            try:return original(display,rect,0,gray,max_pixels)
            except ValueError as error:
                reached.append(str(error));raise
        return original(display,rect,scale,gray,max_pixels)
    monkeypatch.setattr(source_display.SourceDisplay,'pixmap',rejected)
    path=tmp_path/'primary-source.epub'
    try:report=vp.convert(doc,str(path),reader)
    finally:doc.close()
    assert reached==['invalid source scale'],'Actual original source scale predicate must execute'
    assert_source_page(path,report)
    assert reader.calls==[0,2],'Unrendered primary image cannot reach the Reader'


def test_missing_registered_native_image_actual_provider_bound_rejection_is_owned(tmp_path,monkeypatch):
    doc=pymupdf.open()
    for text in TEXTS:
        page=doc.new_page(width=440,height=600);page.insert_text((40,90),text,fontsize=8)
    original_read=extract._read_page;original_jpeg=source_display.SourceDisplay.jpeg;read_reached=[];encoded=[]
    def bad_read(document,pno,**kwargs):
        if pno==1:
            read_reached.append(pno);raise ValueError('synthetic source-owned native extraction failure')
        return original_read(document,pno,**kwargs)
    def rejected(display,rect=None,scale=3,quality=90,max_bytes=2*1024*1024):
        if display.pno==1 and not encoded:
            try:return original_jpeg(display,rect,scale,quality,1)
            except extract.RasterTooLarge as error:
                encoded.append(str(error));raise
        return original_jpeg(display,rect,scale,quality,max_bytes)
    monkeypatch.setattr(extract,'_read_page',bad_read)
    monkeypatch.setattr(source_display.SourceDisplay,'jpeg',rejected)
    class Reader:
        cache=tmp_path/'cache'
        def read(self,*args):raise AssertionError('Native source recovery cannot create a model dispatch')
    Reader.cache.mkdir();path=tmp_path/'native-provider.epub'
    try:report=vp.convert(doc,str(path),Reader())
    finally:doc.close()
    assert read_reached and encoded==['source display exceeds encoded-byte bound']
    assert_source_page(path,report)
