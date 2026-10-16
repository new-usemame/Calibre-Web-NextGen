"""Real local ledger/Reader; synthetic transport only. Unknown debit is never zero."""
import base64,copy,hashlib,io,json,math,threading,time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest,requests
from PIL import Image
from cps.services.reflow import vision_reading as vr
from cps.services.reflow.ledger import Ledger
from tests.unit.round7_recovery_test_support import h

def setup(tmp_path):
 images=[]
 for n in range(4):
  out=io.BytesIO();Image.new('RGB',(64,64),(35+n*35,90,140)).save(out,format='JPEG');images.append(out.getvalue())
 page=h.InkPage();page.text('The synthetic quartz specimen remains complete.','b1',y=60);answer=page.finish()[2]
 reader=vr.Reader('synthetic-local-transport-only',tmp_path/'cache',Ledger(tmp_path/'ledger.jsonl',19.5))
 return reader,images,answer

def fresh(r):return Ledger(r.ledger.path,r.ledger.cap_usd)
def ceiling(r,image):
 w,hh=Image.open(io.BytesIO(image)).size
 tokens=len((r.prompt+json.dumps(r.api_schema)).encode())+1024+max(1024,2*math.ceil(w*hh/750))
 return (tokens*.1+(20000 if r.confidence_protocol else 10000)*.5)/1e6

def hold(r,image):
 identity=hashlib.sha256(image).hexdigest();config=r._configuration();bound=ceiling(r,image)
 with r._budget():aid=r.ledger.reserve_attempt(identity,bound,vr.MODEL,vr.VERSION)
 path=r.cache/(identity+'-'+config+'.pending');path.write_text(json.dumps({'reservation':aid,'bound_usd':bound}))
 return aid,path,bound

def admit(r,image):
 # Absent API retains old global block; positives meaningfully RED before implementation.
 return getattr(r,'quarantine_pending',lambda value:False)(image)
def unchanged_hold(r,aid,bound):
 ledger=fresh(r);assert ledger._financial_state()[1][aid]==bound
 assert not any(e.get('attempt')==aid and e.get('event')in('reconciled','released')for e in ledger.entries('reservation'))

def local_transport(monkeypatch,r,answer,*,failure=None,release=None):
 calls=[];entered=threading.Event();lock=threading.Lock()
 class Response:
  def raise_for_status(self):pass
  def json(self):return dict(id='synthetic-local-generation',model=vr.MODEL,usage={'cost':.0001},choices=[{'finish_reason':'stop','message':{'content':json.dumps(answer)}}])
 def post(*args,**kw):
  image=base64.b64decode(kw['json']['messages'][0]['content'][1]['image_url']['url'].split(',',1)[1]);prompt=kw['json']['messages'][0]['content'][0]['text']
  state=fresh(r);assert state.spent()+state.pending_usd()<=state.cap_usd+1e-9
  with lock:calls.append({'image':hashlib.sha256(image).hexdigest(),'targeted':prompt!=r.prompt});entered.set()
  if release is not None:assert release.wait(5)
  if failure=='unknown':raise requests.HTTPError('synthetic lost primary bill')
  if failure=='known_rejected':
   class Rejected(Response):
    def json(self):
     d=super().json();d['choices'][0]['message']['content']='{}';return d
   return Rejected()
  return Response()
 monkeypatch.setattr(requests,'post',post);return calls,entered

def no_dispatch(monkeypatch):
 calls=[]
 def post(*a,**k):calls.append(True);raise AssertionError('Synthetic invalid financial proof must stop dispatch')
 monkeypatch.setattr(requests,'post',post);return calls

def test_actual_unknown_transport_can_be_explicitly_quarantined_without_freeing_hold(tmp_path,monkeypatch):
 r,images,answer=setup(tmp_path);calls,_=local_transport(monkeypatch,r,answer,failure='unknown')
 with pytest.raises(requests.HTTPError):r.read(images[0])
 assert len(calls)==1;aid=list(fresh(r)._financial_state()[1])[0];bound=ceiling(r,images[0]);path=r.cache/(hashlib.sha256(images[0]).hexdigest()+'-'+r._configuration()+'.pending');original=path.read_bytes()
 assert admit(r,images[0])is True,'A unique current-configuration original-image liability is isolatable, never free'
 assert path.read_bytes()==original;unchanged_hold(r,aid,bound)
 identity=hashlib.sha256(images[0]).hexdigest()
 proof=[e for e in fresh(r)._entries if e.get('attempt')==aid and e.get('image_sha256')==identity and e.get('config_sha256')==r._configuration() and e.get('bound_usd')==bound]
 assert proof,'Admission requires durable exact attempt/image/config/bound proof'
 calls,_=local_transport(monkeypatch,r,answer);result=r.read_unit(images[1],page=1)
 assert result['answer']==answer and len(calls)==1 and not calls[0]['targeted'];unchanged_hold(r,aid,bound)
 assert fresh(r).spent()==pytest.approx(.0001)

def test_admitted_quarantine_survives_restart_and_other_source_can_dispatch(tmp_path,monkeypatch):
 r,images,answer=setup(tmp_path);aid,path,bound=hold(r,images[0]);assert admit(r,images[0])is True
 restarted=vr.Reader(r.key,r.cache,Ledger(r.ledger.path,19.5));calls,_=local_transport(monkeypatch,restarted,answer)
 result=restarted.read_unit(images[1],page=1)
 assert result['answer']==answer and len(calls)==1;unchanged_hold(restarted,aid,bound)

def test_quarantined_primary_is_not_reissued_but_separate_targeted_read_is_bounded(tmp_path,monkeypatch):
 r,images,answer=setup(tmp_path);aid,path,bound=hold(r,images[0]);original=path.read_bytes();assert admit(r,images[0])is True
 calls,_=local_transport(monkeypatch,r,answer)
 with pytest.raises(Exception):r.read(images[0])
 assert calls==[] and path.read_bytes()==original
 assert r.reread_dispatch_allowed(images[0])is True
 result=r.read_unit(images[0],page=0)
 assert result['answer']==answer and len(calls)==1 and calls[0]['targeted']
 assert path.read_bytes()==original;unchanged_hold(r,aid,bound)


def test_known_billed_bad_reread_consumes_only_one_physical_allowance_and_keeps_unknown(tmp_path,monkeypatch):
 r,images,answer=setup(tmp_path);aid,path,bound=hold(r,images[0]);assert admit(r,images[0])is True
 calls,_=local_transport(monkeypatch,r,answer,failure='known_rejected')
 first=r.read_unit(images[0],page=0);second=r.read_unit(images[0],page=0)
 assert first['degraded_source']and second['degraded_source'] and first['answer']is None and second['answer']is None
 assert len(calls)==1 and calls[0]['targeted'];unchanged_hold(r,aid,bound)
 assert fresh(r).spent()==pytest.approx(.0001)and fresh(r).pending_usd()==bound
 assert first['rejected_receipts']and any(x['transition']=='VISIBLE_SOURCE_DEGRADATION'for x in first['recovery_events'])


def test_unadmitted_strict_reader_still_blocks_other_primary(tmp_path,monkeypatch):
 r,images,answer=setup(tmp_path);aid,path,bound=hold(r,images[0]);calls=no_dispatch(monkeypatch)
 with pytest.raises(Exception):r.read(images[1])
 assert calls==[];unchanged_hold(r,aid,bound)

@pytest.mark.parametrize('bad',['missing_marker','wrong_attempt','wrong_bound','wrong_model','wrong_prompt','wrong_configuration','unbound_source'])
def test_unproved_liability_never_admitted_and_global_block_is_retained(tmp_path,monkeypatch,bad):
 r,images,answer=setup(tmp_path);aid,path,bound=hold(r,images[0])
 if bad=='missing_marker':path.unlink()
 elif bad=='wrong_attempt':path.write_text(json.dumps({'reservation':'not-this-attempt','bound_usd':bound}))
 elif bad=='wrong_bound':path.write_text(json.dumps({'reservation':aid,'bound_usd':bound/2}))
 elif bad in('wrong_model','wrong_prompt'):
  rows=fresh(r)._entries;rows[0]['model_id' if 'model_id'in rows[0]and bad=='wrong_model'else('model'if bad=='wrong_model'else'prompt_version')]='unproved-mismatch'
  Path(r.ledger.path).write_text(''.join(json.dumps(e)+'\n'for e in rows))
 elif bad=='wrong_configuration':path.rename(path.with_name(hashlib.sha256(images[0]).hexdigest()+'-'+'0'*64+'.pending'))
 else:images[0]=images[2]
 assert admit(r,images[0])is False
 calls=no_dispatch(monkeypatch);result=r.read_unit(images[1],page=1)
 assert result['degraded_source']and calls==[];unchanged_hold(r,aid,bound)

@pytest.mark.parametrize('change',['deleted','wrong_bound','wrong_attempt'])
def test_previously_admitted_proof_is_revalidated_after_pending_marker_changes(tmp_path,monkeypatch,change):
 r,images,answer=setup(tmp_path);aid,path,bound=hold(r,images[0]);assert admit(r,images[0])is True
 if change=='deleted':path.unlink()
 else:path.write_text(json.dumps({'reservation':aid if change=='wrong_bound'else'changed','bound_usd':bound/2 if change=='wrong_bound'else bound}))
 calls=no_dispatch(monkeypatch);result=r.read_unit(images[1],page=1)
 assert result['degraded_source']and calls==[];unchanged_hold(r,aid,bound)


def test_mixed_proved_and_unbound_hold_does_not_admit_later_source(tmp_path,monkeypatch):
 r,images,answer=setup(tmp_path);aid,path,bound=hold(r,images[0]);assert admit(r,images[0])is True
 writer=fresh(r);other=writer.reserve_attempt('no-source-image-proof',.001,vr.MODEL,vr.VERSION)
 calls=no_dispatch(monkeypatch);result=r.read_unit(images[1],page=1)
 assert result['degraded_source']and calls==[];unchanged_hold(r,aid,bound)
 assert fresh(r)._financial_state()[1][other]==.001


def test_multiple_exact_quarantines_stay_counted_and_allow_other_source(tmp_path,monkeypatch):
 r,images,answer=setup(tmp_path);a,p,b=hold(r,images[0]);c,q,d=hold(r,images[1]);assert admit(r,images[0])is True and admit(r,images[1])is True
 calls,_=local_transport(monkeypatch,r,answer);result=r.read_unit(images[2],page=2)
 assert result['answer']==answer and len(calls)==1;unchanged_hold(r,a,b);unchanged_hold(r,c,d);assert fresh(r).pending_usd()==pytest.approx(b+d)


def test_external_settled_over_ceiling_still_blocks_even_with_proved_quarantine(tmp_path,monkeypatch):
 r,images,answer=setup(tmp_path);aid,path,bound=hold(r,images[0]);assert admit(r,images[0])is True
 writer=fresh(r);other=writer.reserve_attempt('overceiling',.001,vr.MODEL,vr.VERSION);writer.reconcile_attempt(other,.002)
 calls=no_dispatch(monkeypatch);result=r.read_unit(images[1],page=1)
 assert result['degraded_source']and calls==[];unchanged_hold(r,aid,bound);assert fresh(r).spent()==.002


def test_quarantine_does_not_remove_hold_from_new_primary_or_targeted_cap(tmp_path,monkeypatch):
 r,images,answer=setup(tmp_path);aid,path,bound=hold(r,images[0]);assert admit(r,images[0])is True
 r.ledger.cap_usd=bound+ceiling(r,images[1])*.5
 calls=no_dispatch(monkeypatch);assert r.reread_dispatch_allowed(images[0])is False
 result=r.read_unit(images[1],page=1)
 assert result['degraded_source']and calls==[];unchanged_hold(r,aid,bound);assert fresh(r).spent()==0


def test_two_readers_atomic_cap_includes_quarantine_and_executing_reservation(tmp_path,monkeypatch):
 r,images,answer=setup(tmp_path);aid,path,bound=hold(r,images[0]);assert admit(r,images[0])is True
 r.ledger.cap_usd=bound+ceiling(r,images[1])*1.5
 other=vr.Reader(r.key,r.cache,Ledger(r.ledger.path,r.ledger.cap_usd));release=threading.Event();calls,entered=local_transport(monkeypatch,r,answer,release=release)
 with ThreadPoolExecutor(max_workers=2)as pool:
  one=pool.submit(r.read_unit,images[1],page=1)
  try:
   assert entered.wait(2),'First other-source dispatch should be admitted under the complete held cap'
   two=pool.submit(other.read_unit,images[2],page=2);result=two.result(3)
   assert result['degraded_source']and len(calls)==1
   state=fresh(r);assert state.spent()+state.pending_usd()<=state.cap_usd+1e-9
  finally:release.set()
  assert one.result(3)['answer']==answer
 unchanged_hold(r,aid,bound);assert fresh(r).spent()==pytest.approx(.0001)
