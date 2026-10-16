"""Separate actual Controller predicate; admitted-source precondition is fixture-only.

Local transport and Reader._read reserve/reconcile are real. No converter/provider.
This adapter isolates the Controller's independent pending veto; it does not award
Reader quarantine proof validation, which has its own unchanged 24 controls.
"""
import copy,hashlib,types
import pytest
from cps.services.reflow import vision_reading as vr
from cps.services.reflow.unit_recovery import Controller,UnitFailure
from tests.unit.test_round7_source_unit_financial_quarantine import setup,hold,fresh,ceiling,local_transport,unchanged_hold

class AdmittedSourceRoute(vr.Reader):
 def financial_dispatch_allowed(self):
  from cps.services.reflow.ledger import _read
  with self._budget_lock:
   self.ledger._entries=_read(self.ledger.path);pending=self.ledger._financial_state()[1]
   return all(self.proved_holds.get(aid)==bound for aid,bound in pending.items())
 def cached_reread(self,image,source_repair=None):return None
 def reread_dispatch_allowed(self,image):
  retry=self._retry_reader();return self.financial_dispatch_allowed()and not fresh(self).would_exceed(ceiling(retry,image))
 def reread(self,image):
  self.targeted_calls+=1;retry=self._retry_reader();retry.financial_dispatch_allowed=self.financial_dispatch_allowed
  try:return retry.read(image)
  except Exception:
   # Only this fixture's known new targeted request becomes explicitly admitted.
   # Original Reader proof and durable validation are tested in separate module.
   for aid,bound in fresh(self)._financial_state()[1].items():
    if aid not in self.proved_holds:self.proved_holds[aid]=bound
   raise

def fixture(tmp_path):
 base,images,answer=setup(tmp_path);r=AdmittedSourceRoute(base.key,base.cache,base.ledger);r.proved_holds={};r.targeted_calls=0
 aid,path,bound=hold(r,images[0]);r.proved_holds[aid]=bound
 c=Controller(r,[0,1,2],tmp_path/'controller');original={}
 for n,image in enumerate(images[:3]):
  c.register_image(n,image);c.readings[n]={'answer':copy.deepcopy(answer),'config_sha256':r._configuration()};c.answers[n]=copy.deepcopy(answer);original[n]=copy.deepcopy(c.readings[n])
 return r,images,answer,c,aid,bound,original

def failure():return UnitFailure([{'page':1,'block':'b1'}],'page_compilation',ValueError('Synthetic exact owned middle-unit failure'))
def isolated(c,original):
 assert c.readings[0]==original[0]and c.readings[2]==original[2]
 assert not any(page!=1 for page,bid in c.degraded)

def test_assembly_unit_can_use_one_real_bounded_reread_with_other_source_quarantined(tmp_path,monkeypatch):
 r,images,answer,c,aid,bound,original=fixture(tmp_path);calls,_=local_transport(monkeypatch,r,answer)
 assert r.financial_dispatch_allowed()is True and r.ledger.pending_usd()==bound
 c.recover(failure())
 assert r.targeted_calls==1 and len(calls)==1 and calls[0]['targeted'],'Controller must use admitted financial route instead of a second unconditional pending veto'
 assert c.events[-1]['transition']=='TARGETED_REREAD_REASSEMBLE'and c.retried=={1}and c.degraded=={}
 assert c.answers[1]==answer;unchanged_hold(r,aid,bound);isolated(c,original)

@pytest.mark.parametrize('mode',['known_rejected','unknown'])
def test_bad_targeted_read_only_degrades_exact_owned_unit_and_retains_all_liabilities(tmp_path,monkeypatch,mode):
 r,images,answer,c,aid,bound,original=fixture(tmp_path);calls,_=local_transport(monkeypatch,r,answer,failure=mode)
 c.recover(failure())
 assert r.targeted_calls==1 and len(calls)==1 and calls[0]['targeted']
 assert set(c.degraded)=={(1,'b1')}and c.degraded[1,'b1']['stage']=='page_compilation'
 assert c.degraded[1,'b1']['reason']=='Synthetic exact owned middle-unit failure'
 assert c.events[-1]['transition']=='VISIBLE_SOURCE_DEGRADATION'and c.retried=={1}
 unchanged_hold(r,aid,bound);isolated(c,original)
 report={'degraded_units':[{'page':1,'block':'b1','source_image':'images/synthetic-source.jpg','source_reference':'synthetic-source-target'}]};c.decorate_report(report)
 assert report['degraded_unit_count']==1 and report['degraded_units'][0]['page']==1 and report['degraded_units'][0]['block']=='b1'
 assert report['recovery_financial_state']['held_usd']==fresh(r).pending_usd()>0
 if mode=='known_rejected':assert fresh(r).spent()==pytest.approx(.0001)and len(fresh(r)._financial_state()[1])==1
 else:assert fresh(r).spent()==0 and len(fresh(r)._financial_state()[1])==2
 assert c.claim_reread(1,c.images[1]['sha256'])is False,'Shared physical allowance remains consumed'


def test_unproved_other_hold_keeps_actual_controller_paid_route_blocked(tmp_path,monkeypatch):
 r,images,answer,c,aid,bound,original=fixture(tmp_path);r.proved_holds={};calls,_=local_transport(monkeypatch,r,answer)
 c.recover(failure());assert r.targeted_calls==0 and calls==[]and set(c.degraded)=={(1,'b1')}and c.retried==set()
 unchanged_hold(r,aid,bound);isolated(c,original)


def test_all_holds_still_count_before_controller_claims_paid_allowance(tmp_path,monkeypatch):
 r,images,answer,c,aid,bound,original=fixture(tmp_path);r.ledger.cap_usd=bound+ceiling(r._retry_reader(),images[1])*.5;calls,_=local_transport(monkeypatch,r,answer)
 c.recover(failure());assert r.targeted_calls==0 and calls==[]and set(c.degraded)=={(1,'b1')}and c.retried==set()
 unchanged_hold(r,aid,bound);isolated(c,original)


def test_legacy_reader_without_financial_predicate_keeps_original_pending_block(tmp_path,monkeypatch):
 r,images,answer,c,aid,bound,original=fixture(tmp_path)
 c.reader=types.SimpleNamespace(key=r.key,cache=r.cache,ledger=r.ledger,reread=r.reread,reread_dispatch_allowed=r.reread_dispatch_allowed,cached_reread=lambda *a:None)
 calls,_=local_transport(monkeypatch,r,answer);c.recover(failure())
 assert r.targeted_calls==0 and calls==[]and set(c.degraded)=={(1,'b1')};unchanged_hold(r,aid,bound);isolated(c,original)
