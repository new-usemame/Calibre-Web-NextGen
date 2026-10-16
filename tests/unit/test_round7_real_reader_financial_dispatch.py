"""Actual Reader billing/reservation paths use a local transport stub, never a provider."""
import base64,copy,hashlib,json,requests,zipfile
from xml.etree import ElementTree as ET
import pytest
from cps.services.reflow import vision_pipeline as vp,vision_reading as vr,build_epub
from cps.services.reflow.source_display import SourceDisplay
from cps.services.reflow.ledger import Ledger
from tests.unit.round7_recovery_test_support import fixture,h,TEXTS
from tests.unit.round7_public_note_contract_support import source_visible

@pytest.mark.parametrize('mode',['unknown_transport','unknown_bill','cap','known_billed_bad_content'])
def test_actual_reader_dispatch_hold_cap_and_one_known_billed_reread(tmp_path,monkeypatch,mode):
    doc,_,prepared=fixture(tmp_path,monkeypatch);ledger=Ledger(tmp_path/'ledger.jsonl',cap_usd=0 if mode=='cap' else .2)
    class Reader(vr.Reader):
        reread_calls=0
        def reread(self,image):self.reread_calls+=1;return super().reread(image)
    reader=Reader('synthetic-no-provider-key',tmp_path/'real-reader',ledger)
    config=hashlib.sha256((vr.VERSION+vr.MODEL+reader.prompt+json.dumps(reader.api_schema,sort_keys=True)).encode()).hexdigest()
    for n in ([0] if mode.startswith('unknown') else [0,2]):
        image=SourceDisplay(doc,n).pixmap(scale=2,max_pixels=2_500_000).tobytes('jpg',jpg_quality=90);digest=hashlib.sha256(image).hexdigest()
        (reader.cache/(digest+'-'+config+'.json')).write_text(json.dumps(dict(image_sha256=digest,config_sha256=config,answer=prepared[n][2],cost_usd=.001)))
    dispatch=[]
    class Response:
        def __init__(self,data):self.data=data
        def raise_for_status(self):pass
        def json(self):return self.data
    def transport(url,**kwargs):
        image=base64.b64decode(kwargs['json']['messages'][0]['content'][1]['image_url']['url'].split(',',1)[1]);dispatch.append(hashlib.sha256(image).hexdigest())
        assert ledger.pending_usd()>0,'actual persistent reservation precedes local transport'
        if mode=='unknown_transport':raise requests.ConnectionError('synthetic post-dispatch response lost')
        if mode=='unknown_bill':return Response({'usage':{},'id':'synthetic-unpriced'})
        assert mode=='known_billed_bad_content','cap must refuse before local transport'
        answer={'blocks':'invalid known-billed content'} if len(dispatch)==1 else copy.deepcopy(prepared[1][2])
        return Response(dict(id='synthetic-paid-'+str(len(dispatch)),model=vr.MODEL,provider='synthetic',usage={'cost':.0001},choices=[{'finish_reason':'stop','message':{'content':json.dumps(answer)}}]))
    monkeypatch.setattr(requests,'post',transport)
    path=tmp_path/'financial.epub'
    try:report=vp.convert(doc,str(path),reader)
    finally:doc.close()
    assert build_epub.validate(path)==[] and set(map(int,report['page_homes']))=={0,1,2}
    with zipfile.ZipFile(path) as z:docs={n:ET.fromstring(z.read(n)) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')}
    if mode=='known_billed_bad_content':
        assert len(dispatch)==2 and reader.reread_calls==1
        assert ledger.spent()==.0002 and ledger.pending_usd()==0
        assert report['degraded_units']==[] and h.body_texts(docs)==list(TEXTS)
        assert report['new_cost_usd']==.0002 and report['reread_pages']==[1]
    elif mode.startswith('unknown'):
        assert len(dispatch)==4,'Each of two proved Source units permits primary plus one separately bounded targeted attempt'
        assert reader.reread_calls==2 and report['reread_pages']==[1,2]
        held=ledger._financial_state()[1]
        assert len(held)==4 and ledger.spent()==0,'Settled spend is zero; four unknown bills retain their full bounds'
        reservations=ledger.entries('reservation')
        pending=[e for e in reservations if e.get('event')=='pending']
        assert len(pending)==4 and set(held)=={e['attempt']for e in pending}
        assert sum(held.values())==pytest.approx(ledger.pending_usd()) and 0<ledger.pending_usd()<=ledger.cap_usd
        assert not any(e.get('event')in('released','reconciled')for e in reservations),'Unknown liability is neither released nor reported as an actual zero bill'
        from collections import Counter
        assert Counter(dispatch)==Counter({e['page_label']:2 for e in pending}) and len(set(dispatch))==2,'No repeated primary or third physical-page request'
        proofs=ledger.entries('billing_quarantine')
        assert {e['attempt']for e in proofs}==set(held)
        for e in proofs:
            assert e['bill_status']=='UNKNOWN_FULL_BOUND_RETAINED' and e['bound_usd']==held[e['attempt']]
            witness=e['request_witness'];image=(reader.cache/(e['image_sha256']+'-'+e['config_sha256']+'.liability-source.jpg')).read_bytes()
            assert hashlib.sha256(image).hexdigest()==e['image_sha256']
            assert reader._bounded_image_request(image,witness['prompt'],witness['api_schema'],witness['confidence_protocol'])==held[e['attempt']]
        assert {(u['page'],u.get('block'))for u in report['degraded_units']}=={(1,None),(2,None)}
        assert h.body_texts(docs)==[TEXTS[0]]
        source_visible(path,report,{0,1,2})
    else:
        assert len(dispatch)==(0 if mode=='cap' else 1),'no later paid dispatch after cap or unresolved bill'
        assert reader.reread_calls==0 and report['reread_pages']==[],'financially blocked page must not consume or invoke targeted reread'
        assert ledger.spent()==0 and (ledger.pending_usd()==0 if mode=='cap' else ledger.pending_usd()>0)
        assert {(u['page'],u.get('block')) for u in report['degraded_units']}==({(1,None)} if mode=='cap' else {(1,None),(2,None)})
        assert h.body_texts(docs)==([TEXTS[0],TEXTS[2]] if mode=='cap' else [TEXTS[0]])
        source_visible(path,report,{0,1,2})
