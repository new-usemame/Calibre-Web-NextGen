"""Independent source-owned reason protocol using synthetic text and geometry."""
import copy,importlib.util
from pathlib import Path
import pytest,pymupdf
from cps.services.reflow.unit_source import source_unit
from cps.services.reflow.source_display import SourceDisplay
from cps.services.reflow import unit_recovery as ur,vision_pipeline as vp
spec=importlib.util.spec_from_file_location('owned_reason_ink',Path(vp.__file__).parents[3]/'tests/unit/test_round4_defects.py')
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
REASONS=('Amber original crop has no independently proved edge.',
         'Silver original table has no independently proved edge.',
         'Violet original panel has no independently proved edge.')

def controller(tmp_path):
    class Reader:
        key=None
        cache=tmp_path/'cache'
        dispatches=0
        def reread(self,image):
            self.dispatches+=1
            raise AssertionError('No targeted reading route is authorized in this primitive fixture.')
    r=Reader();r.cache.mkdir()
    c=ur.Controller(r,[0,1,2],tmp_path/'staging')
    for n in range(3):
        c.answers[n]={'blocks':[dict(id='owned-'+str(n),role='figure',text='Synthetic source object.')]}
        c.register_image(n,('synthetic-original-page-'+str(n)).encode())
    return c,r

def test_distinct_source_owned_reasons_do_not_become_foreign_visible_prose(tmp_path):
    c,r=controller(tmp_path)
    units=[dict(page=n,block='owned-'+str(n),source_failure_reason=REASONS[n]) for n in range(3)]
    cause='; '.join(REASONS)
    c.recover(ur.UnitFailure(units,'figure_separation',ValueError(cause)))
    assert set(c.degraded)=={(n,'owned-'+str(n)) for n in range(3)}
    for n in range(3):
        case=c.degraded[(n,'owned-'+str(n))]
        assert case['reason']==REASONS[n],'Only the exact SOURCE-bound local unit reason may become its visible disclosure.'
        assert case['cause_message']==cause,'The complete original cause remains unchanged as diagnostic metadata.'
        assert all(other not in case['reason'] for other in REASONS if other!=REASONS[n])
    assert r.dispatches==0 and c.retried==set()
    assert c.events[-1]['transition']=='VISIBLE_SOURCE_DEGRADATION'
    # Exercise the generic renderer that places its selected reason in visible
    # SOURCE disclosure. Existing R5 art contexts use generic labels instead.
    class Package:
        def __init__(self):self.images={}
        def image(self,name,data):self.images[name]=data
    doc=pymupdf.open()
    for n in range(3):
        page=doc.new_page(width=300,height=400)
        page.insert_text((25,45),'Synthetic original artwork '+str(n),fontsize=9)
    try:
        for n in range(3):
            fragment,asides,_=source_unit(SourceDisplay(doc,n),n,'owned-'+str(n),'figure',Package(),{},c.degraded[(n,'owned-'+str(n))]['reason'])
            visible=fragment+''.join(asides)
            assert REASONS[n] in visible and all(other not in visible for other in REASONS if other!=REASONS[n])
    finally:doc.close()

@pytest.mark.parametrize('override',[None,17,False,[],{},'','   '],
                         ids=['null','integer','boolean','list','mapping','empty','whitespace'])
def test_unusable_source_reason_override_preserves_original_cause(tmp_path,override):
    c,r=controller(tmp_path);cause='The original aggregate diagnostic remains authoritative.'
    c.recover(ur.UnitFailure([dict(page=1,block='owned-1',source_failure_reason=copy.deepcopy(override))],
                            'figure_separation',ValueError(cause)))
    case=c.degraded[(1,'owned-1')]
    assert case['reason']==case['cause_message']==cause
    assert r.dispatches==0 and c.retried==set() and set(c.degraded)=={(1,'owned-1')}

def test_ordinary_no_override_recovery_contract_is_unchanged(tmp_path):
    c,r=controller(tmp_path);cause='An ordinary SOURCE-bound unit failed.'
    c.recover(ur.UnitFailure([dict(page=2,block='owned-2')],'figure_separation',ValueError(cause)))
    case=c.degraded[(2,'owned-2')]
    assert case['reason']==case['cause_message']==cause
    assert case['stage']=='figure_separation' and case['status']=='DEGRADED_SOURCE_CONTEXT'
    assert r.dispatches==0 and c.retried==set()

def test_actual_page_disclosures_keep_their_own_reasons_after_multiart_recovery(tmp_path,monkeypatch):
    pages=[]
    body=('The amber body remains a separate healthy sentence.',
          'The silver body remains a separate healthy sentence.')
    for n in range(2):
        p=h.InkPage();p.text(body[n],'body',y=45);p.figure('art',caption=False);pages.append(p)
    def unproved(answer,optical,display,regions):
        return [dict(block=b['id'],degraded=True,seed_bbox=list(vp.block_rect(b,display)),
                     reason=REASONS[display.pno],method='synthetic-owned-reason')
                for b in answer['blocks'] if b['role']=='figure']
    monkeypatch.setattr(vp,'_bind_figures',unproved)
    path,report,docs=h.convert_ink(tmp_path,monkeypatch,pages)
    assert h.body_texts(docs)==list(body)
    assert {(u['page'],u['block']) for u in report['degraded_units']}=={(0,'art'),(1,'art')}
    for u in report['degraded_units']:
        own=REASONS[u['page']];foreign=REASONS[1-u['page']]
        assert u['reason']==own and foreign not in u['reason']
        target=next(e for root in docs.values() for e in root.iter() if e.get('id')==u['source_reference'])
        visible=''.join(target.itertext())
        assert foreign not in visible,'Existing original-page art controls may use a generic label, but must never expose another page failure.'
        assert target.findall('.//x:img',h.NS)
        assert any(h.plain(a)=='Return' for a in target.findall('.//x:a',h.NS))
