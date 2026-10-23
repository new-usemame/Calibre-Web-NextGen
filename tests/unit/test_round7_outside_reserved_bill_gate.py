"""A reconciled bill outside its reserved ceiling still closes future paid dispatch."""
import hashlib,json,requests,zipfile
from xml.etree import ElementTree as ET
from cps.services.reflow import vision_pipeline as vp,vision_reading as vr,build_epub
from cps.services.reflow.source_display import SourceDisplay
from cps.services.reflow.ledger import Ledger
from tests.unit.round7_recovery_test_support import fixture,h,TEXTS
from tests.unit.round7_public_note_contract_support import local_ocr_visible

def test_outside_reserved_ceiling_cannot_trigger_reread_or_next_primary_request(tmp_path,monkeypatch):
    doc,_,prepared=fixture(tmp_path,monkeypatch);ledger=Ledger(tmp_path/'ledger.jsonl',cap_usd=2.)
    class Reader(vr.Reader):
        reread_calls=0
        def reread(self,image):self.reread_calls+=1;return super().reread(image)
    reader=Reader('synthetic-no-provider-key',tmp_path/'real-readings',ledger)
    config=hashlib.sha256((vr.VERSION+vr.MODEL+reader.prompt+json.dumps(reader.api_schema,sort_keys=True)).encode()).hexdigest()
    image=SourceDisplay(doc,0).pixmap(scale=2,max_pixels=2_500_000).tobytes('jpg',jpg_quality=90);digest=hashlib.sha256(image).hexdigest()
    (reader.cache/(digest+'-'+config+'.json')).write_text(json.dumps(dict(image_sha256=digest,config_sha256=config,answer=prepared[0][2],cost_usd=.001)))
    dispatched=[]
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'usage':{'cost':.2},'id':'synthetic-outside-reserved-ceiling'}
    def transport(*args,**kwargs):
        entries=ledger.entries('reservation');entry=next(e for e in reversed(entries) if e['event']=='pending')
        assert entry['bound_usd']<.2,'actual reported fake bill is outside admitted request ceiling'
        dispatched.append(entry['attempt']);return Response()
    monkeypatch.setattr(requests,'post',transport)
    out=tmp_path/'held-finance.epub'
    try:report=vp.convert(doc,str(out),reader)
    finally:doc.close()
    assert build_epub.validate(out)==[]
    assert len(dispatched)==1 and reader.reread_calls==0,'UncertainBilling closes both targeted reread and later new primary dispatch even when reconciled pending balance is zero'
    assert ledger.spent()==.2 and ledger.pending_usd()==0,'the known debit is retained; no fabricated unresolved liability or zero-cost claim'
    assert report['new_cost_usd']==.2 and report['reread_pages']==[]
    with zipfile.ZipFile(out) as z:docs={n:ET.fromstring(z.read(n)) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')}
    local_ocr_visible(out,report,{1:[TEXTS[1]],2:[TEXTS[2]]},non_local_pages={0})
    assert h.body_texts(docs)==list(TEXTS)
