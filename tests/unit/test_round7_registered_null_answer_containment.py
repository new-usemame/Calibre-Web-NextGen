"""A returned unusable page answer cannot escape ownership refinement."""
from cps.services.reflow import vision_pipeline as vp
from tests.unit.round7_recovery_test_support import fixture,publish,h,TEXTS


def test_registered_null_answer_preserves_owned_source_page_and_neighbors(tmp_path,monkeypatch):
    doc,reader,_=fixture(tmp_path,monkeypatch);original=reader.read
    def null_answer(image):
        record=original(image)
        return dict(record,answer=None) if reader.calls[-1]==1 else record
    monkeypatch.setattr(reader,'read',null_answer)
    path,report,docs=publish(doc,reader,tmp_path)
    assert h.body_texts(docs)==[TEXTS[0],TEXTS[2]]
    assert {(u['page'],u.get('block')) for u in report['degraded_units']}=={(1,None)}
    unit=report['degraded_units'][0]
    target=next(e for root in docs.values() for e in root.iter() if e.get('id')==unit['source_reference'])
    assert target.findall('.//x:img',h.NS)
    assert any(h.plain(a)=='Return' for a in target.findall('.//x:a',h.NS))
    assert reader.calls==[0,1,2]
