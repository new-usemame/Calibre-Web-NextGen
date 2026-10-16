"""One local original-page encode repair differs from genuinely unrenderable SOURCE."""
import pytest,zipfile,json
from xml.etree import ElementTree as ET
from cps.services.reflow import vision_pipeline as vp,source_display,extract,build_epub,unit_recovery
from tests.unit.round7_recovery_test_support import fixture,h,TEXTS


@pytest.mark.parametrize('persistent',[False,True])
def test_whole_page_source_encode_failure_has_one_local_repair_and_explicit_fatal_limit(tmp_path,monkeypatch,persistent):
    doc,reader,_=fixture(tmp_path,monkeypatch);original_read=reader.read;original_jpeg=source_display.SourceDisplay.jpeg;encoded=[]
    def rejected_read(image):
        record=original_read(image)
        if reader.calls[-1]==1:raise ValueError('The source page reading is rejected before publication')
        return record
    def rejected_encode(display,rect=None,scale=3,quality=90,max_bytes=2*1024*1024):
        if display.pno==1 and (persistent or not encoded):
            try:return original_jpeg(display,rect,scale,quality,1)
            except extract.RasterTooLarge as error:
                encoded.append(str(error));raise
        return original_jpeg(display,rect,scale,quality,max_bytes)
    monkeypatch.setattr(reader,'read',rejected_read)
    monkeypatch.setattr(source_display.SourceDisplay,'jpeg',rejected_encode)
    path=tmp_path/'whole-source.epub'
    try:
        if persistent:
            with pytest.raises(unit_recovery.SourceIntegrityError,match='[Ss][Oo][Uu][Rr][Cc][Ee]'):
                vp.convert(doc,str(path),reader)
            assert len(encoded)==2,'Genuinely unrenderable SOURCE must stop after one bounded local encode repair'
            diagnostic=json.loads((tmp_path/'whole-source.epub.source-failure.json').read_text())
            assert diagnostic['status']=='original_source_unrenderable' and diagnostic['page']==1
            assert diagnostic.get('stage') and diagnostic.get('cause_type')
            assert not path.exists()
        else:
            report=vp.convert(doc,str(path),reader)
            assert len(encoded)==1 and build_epub.validate(path)==[]
            with zipfile.ZipFile(path) as archive:
                docs={n:ET.fromstring(archive.read(n)) for n in archive.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')}
                assert h.body_texts(docs)==[TEXTS[0],TEXTS[2]]
                assert {(u['page'],u.get('block')) for u in report['degraded_units']}=={(1,None)}
                unit=report['degraded_units'][0]
                assert 'OEBPS/'+unit['source_image'] in archive.namelist()
                target=next(e for root in docs.values() for e in root.iter() if e.get('id')==unit['source_reference'])
                assert target.findall('.//x:img',h.NS)
                assert any(h.plain(a)=='Return' for a in target.findall('.//x:a',h.NS))
        assert reader.calls==[0,1,2],'Local SOURCE encoding repair must not redispatch any primary reading'
    finally:doc.close()
