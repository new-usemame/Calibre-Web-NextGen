"""Original durable settlements supersede stale memory; transport is always local."""
import base64,copy,hashlib,json,threading
from concurrent.futures import ThreadPoolExecutor
import pytest,requests
from cps.services.reflow import vision_reading as vr,vision_pipeline as vp,build_epub
from cps.services.reflow.ledger import Ledger
from cps.services.reflow.source_display import SourceDisplay
from tests.unit.round7_recovery_test_support import fixture,TEXTS

def setup(tmp_path,monkeypatch,*,hold=True,cap=.2):
    doc,_,prepared=fixture(tmp_path,monkeypatch)
    writer=Ledger(tmp_path/'ledger.jsonl',cap_usd=cap)
    attempt=writer.reserve_attempt('other-worker',.01) if hold else None
    ledger=Ledger(writer.path,cap_usd=cap)
    reader=vr.Reader('synthetic-local-transport-only',tmp_path/'reader',ledger)
    images=[SourceDisplay(doc,n).pixmap(scale=2,max_pixels=2_500_000).tobytes('jpg',jpg_quality=90) for n in range(3)]
    return doc,prepared,writer,attempt,ledger,reader,images

def cache(reader,image,answer):
    identity=hashlib.sha256(image).hexdigest();config=reader._configuration()
    (reader.cache/(identity+'-'+config+'.json')).write_text(json.dumps(dict(image_sha256=identity,config_sha256=config,answer=copy.deepcopy(answer),cost_usd=0.)))

def local_transport(monkeypatch,answer,dispatch):
    class Response:
        def raise_for_status(self):pass
        def json(self):return dict(id='synthetic-settled-'+str(len(dispatch)),model=vr.MODEL,usage={'cost':.0001},choices=[{'finish_reason':'stop','message':{'content':json.dumps(answer)}}])
    def post(url,**kwargs):
        dispatch.append(hashlib.sha256(base64.b64decode(kwargs['json']['messages'][0]['content'][1]['image_url']['url'].split(',',1)[1])).hexdigest())
        return Response()
    monkeypatch.setattr(requests,'post',post)

def test_external_settlement_before_primary_read_unit_does_not_disable_key(tmp_path,monkeypatch):
    doc,prepared,writer,attempt,ledger,reader,images=setup(tmp_path,monkeypatch)
    try:
        writer.reconcile_attempt(attempt,.0001)
        assert ledger.pending_usd()==.01 and Ledger(writer.path,.2).pending_usd()==0
        dispatch=[];local_transport(monkeypatch,prepared[1][2],dispatch)
        result=reader.read_unit(images[1],page=1)
        assert result.get('answer')==prepared[1][2] and not result.get('degraded_source')
        assert len(dispatch)==1 and ledger.pending_usd()==0
        assert Ledger(writer.path,.2).spent()==.0002
    finally:doc.close()

def test_external_settlement_refreshes_targeted_ceiling_preflight(tmp_path,monkeypatch):
    doc,prepared,writer,attempt,ledger,reader,images=setup(tmp_path,monkeypatch)
    try:
        writer.reconcile_attempt(attempt,.0001)
        assert ledger.pending_usd()==.01
        assert reader.reread_dispatch_allowed(images[1]) is True
        assert ledger.pending_usd()==0 and ledger.spent()==.0001
    finally:doc.close()

def test_stage_recovery_after_external_settlement_performs_one_targeted_read(tmp_path,monkeypatch):
    doc,prepared,writer,attempt,ledger,reader,images=setup(tmp_path,monkeypatch)
    for image,row in zip(images,prepared):cache(reader,image,row[2])
    dispatch=[];local_transport(monkeypatch,prepared[1][2],dispatch)
    old=vp.bind_geometry;hit=[]
    def fail_owned_once(answer,*args,**kwargs):
        if any(b.get('text')==TEXTS[1] for b in answer['blocks']) and not hit:
            hit.append(True);writer.reconcile_attempt(attempt,.0001)
            assert ledger.pending_usd()==.01 and Ledger(writer.path,.2).pending_usd()==0
            raise ValueError('synthetic owned stage requires its single targeted reading')
        return old(answer,*args,**kwargs)
    monkeypatch.setattr(vp,'bind_geometry',fail_owned_once)
    path=tmp_path/'stage.epub'
    try:report=vp.convert(doc,str(path),reader)
    finally:doc.close()
    assert hit and len(dispatch)==1 and report['reread_pages']==[1]
    assert report['degraded_units']==[] and build_epub.validate(path)==[]
    assert Ledger(writer.path,.2).spent()==.0002 and Ledger(writer.path,.2).pending_usd()==0

@pytest.mark.parametrize('cached',[False,True])
def test_actual_unresolved_liability_blocks_network_but_allows_exact_cache(tmp_path,monkeypatch,cached):
    doc,prepared,writer,attempt,ledger,reader,images=setup(tmp_path,monkeypatch)
    dispatch=[];local_transport(monkeypatch,prepared[1][2],dispatch)
    if cached:cache(reader,images[1],prepared[1][2])
    try:
        result=reader.read_unit(images[1],page=1)
        assert dispatch==[] and Ledger(writer.path,.2).pending_usd()==.01
        if cached:assert result['answer']==prepared[1][2] and result['cached'] is True
        else:assert result.get('degraded_source') is True
    finally:doc.close()

def test_new_external_settled_cost_at_cap_blocks_targeted_preflight(tmp_path,monkeypatch):
    doc,prepared,writer,attempt,ledger,reader,images=setup(tmp_path,monkeypatch,hold=False,cap=.01)
    try:
        external=writer.reserve_attempt('external-new',.01);writer.reconcile_attempt(external,.01)
        assert ledger.spent()==0 and Ledger(writer.path,.01).spent()==.01
        assert reader.reread_dispatch_allowed(images[1]) is False
        assert ledger.spent()==.01 and ledger.pending_usd()==0
    finally:doc.close()

def test_two_readers_cannot_dispatch_past_shared_pending_ceiling(tmp_path,monkeypatch):
    doc,prepared,writer,attempt,ledger,reader,images=setup(tmp_path,monkeypatch,hold=False,cap=.012)
    second=vr.Reader('synthetic-local-transport-only',tmp_path/'second',Ledger(writer.path,.012))
    entered=threading.Event();release=threading.Event();dispatch=[]
    class Response:
        def raise_for_status(self):pass
        def json(self):return dict(id='synthetic-atomic',model=vr.MODEL,usage={'cost':.0001},choices=[{'finish_reason':'stop','message':{'content':json.dumps(prepared[1][2])}}])
    def post(url,**kwargs):
        dispatch.append(True);entered.set();assert release.wait(5)
        return Response()
    monkeypatch.setattr(requests,'post',post)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(reader.read_unit,images[1],page=1)
            assert entered.wait(5)
            other=pool.submit(second.read_unit,images[2],page=2)
            try:result=other.result(timeout=5)
            finally:release.set()
            assert result.get('degraded_source') is True
            assert first.result(timeout=5)['answer']==prepared[1][2]
        assert len(dispatch)==1 and Ledger(writer.path,.012).spent()==.0001 and Ledger(writer.path,.012).pending_usd()==0
    finally:doc.close()
