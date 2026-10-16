"""Failed SOURCE-owned furniture remains excluded from healthy live BODY."""
import pytest
from cps.services.reflow import vision_pipeline as vp,unit_recovery
from tests.unit.round7_recovery_test_support import h
from tests.unit.round7_public_note_contract_support import source_visible
FIRST='The amber source paragraph remains complete before its furniture.'
LAST='The violet source paragraph remains complete after its furniture.'

@pytest.mark.parametrize('role',['running_head','folio'])
def test_owned_furniture_geometry_failure_keeps_body_and_discloses_source(tmp_path,monkeypatch,role):
    page=h.InkPage();page.text(FIRST,'first',y=65);page.text(LAST,'last',y=165)
    furniture='Notebook heading' if role=='running_head' else '37'
    page.text(furniture,'furniture',role,y=25 if role=='running_head' else 365,size=7)
    actual=vp.bind_geometry;reached=[]
    def bind(answer,*args,**kwargs):
        for block in answer['blocks']:
            if block['id']=='furniture' and 'furniture' not in unit_recovery.current().blocked(0):
                reached.append((block['id'],block['role']));raise ValueError('Synthetic source-owned furniture geometry rejection')
        return actual(answer,*args,**kwargs)
    monkeypatch.setattr(vp,'bind_geometry',bind)
    path,report,docs=h.convert_ink(tmp_path,monkeypatch,[page])
    assert reached==[('furniture',role)]
    assert h.body_texts(docs)==[FIRST,LAST]
    assert not any(furniture in text for text in h.body_texts(docs))
    assert {(u['page'],u.get('block')) for u in report['degraded_units']}=={(0,'furniture')}
    source_visible(path,report,{0})
