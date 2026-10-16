"""Actual bounded SOURCE encoder rejection cannot erase other source pages."""
import zipfile
from xml.etree import ElementTree as ET
from cps.services.reflow import vision_pipeline as vp,vision_reading as vr,build_epub,source_display,extract
from tests.unit.round7_recovery_test_support import fixture,h,TEXTS


def test_actual_source_encoder_limit_during_unit_recovery_escalates_only_that_page(tmp_path,monkeypatch):
    doc,reader,_=fixture(tmp_path,monkeypatch);original_align=vr.align;original_jpeg=source_display.SourceDisplay.jpeg;reached=[]
    def bad_alignment(text,*args,**kwargs):
        if text==TEXTS[1]:raise ValueError('synthetic owned body alignment rejection')
        return original_align(text,*args,**kwargs)
    def bounded_rejection(display,rect=None,scale=3,quality=90,max_bytes=2*1024*1024):
        if display.pno==1 and not reached:
            try:return original_jpeg(display,rect,scale,quality,1)
            except extract.RasterTooLarge as error:
                reached.append(str(error));raise
        return original_jpeg(display,rect,scale,quality,max_bytes)
    monkeypatch.setattr(vr,'align',bad_alignment)
    monkeypatch.setattr(source_display.SourceDisplay,'jpeg',bounded_rejection)
    path=tmp_path/'source-encoder.epub'
    try:report=vp.convert(doc,str(path),reader)
    finally:doc.close()
    assert reached==['source display exceeds encoded-byte bound'],'Actual original source JPEG ladder must execute its own rejection'
    assert build_epub.validate(path)==[]
    with zipfile.ZipFile(path) as archive:
        docs={n:ET.fromstring(archive.read(n)) for n in archive.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')}
        assert h.body_texts(docs)==[TEXTS[0],TEXTS[2]],'Unaffected pages remain live exactly once'
        units=report['degraded_units'];assert {u['page'] for u in units}=={1}
        for unit in units:
            assert 'OEBPS/'+unit['source_image'] in archive.namelist()
            target=next(e for root in docs.values() for e in root.iter() if e.get('id')==unit['source_reference'])
            assert target.findall('.//x:img',h.NS)
            assert any(h.plain(a)=='Return' for a in target.findall('.//x:a',h.NS))
    assert reader.calls==[0,1,2],'Native rendering/encoding faults cannot silently start more primary readings'
