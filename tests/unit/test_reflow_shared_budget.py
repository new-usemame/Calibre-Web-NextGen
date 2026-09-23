"""Cross-job money is reserved before transport, and survives every crash seam."""
import json
import multiprocessing
import pytest
from cps.services.reflow.ledger import Ledger
pytestmark=pytest.mark.unit

def store(root,cap=1,clock=lambda:100000):
    from cps.services.reflow.shared_budget import Store
    return Store(root,lambda:cap,clock=clock)

def ledger(root,name,cap=1,clock=lambda:100000):
    from cps.services.reflow.shared_budget import SharedLedger
    return SharedLedger(root/'jobs'/name/'job.jsonl',5,job_id=name,store=store(root,cap,clock),book_id=name,user_id=name)

def test_different_jobs_cannot_each_spend_the_same_shared_dollar(tmp_path):
    from cps.services.reflow.shared_budget import BudgetError
    a=ledger(tmp_path,'a');b=ledger(tmp_path,'b')
    first=a.reserve_attempt('0',.7)
    with pytest.raises(BudgetError):b.reserve_attempt('0',.7)
    a.reconcile_attempt(first,.2)
    second=b.reserve_attempt('0',.7)
    b.release_attempt(second,'pre_dispatch');b.release_attempt(second,'pre_dispatch')
    a.reconcile_attempt(first,.2)
    assert store(tmp_path).status()['remaining_usd']==pytest.approx(.8)

def _contend(root,ready,start,results,name):
    from pathlib import Path
    from cps.services.reflow.shared_budget import BudgetError
    led=ledger(Path(root),name);ready.put(True);start.wait()
    try:led.reserve_attempt('0',.6);results.put('dispatch')
    except BudgetError:results.put('refused')

def test_separate_processes_reserve_before_either_socket_can_open(tmp_path):
    context=multiprocessing.get_context('spawn');ready=context.Queue();results=context.Queue();start=context.Event()
    workers=[context.Process(target=_contend,args=(str(tmp_path),ready,start,results,str(i))) for i in range(2)]
    for worker in workers:worker.start()
    for _ in workers:assert ready.get(timeout=10)
    start.set();assert sorted(results.get(timeout=15) for _ in workers)==['dispatch','refused']
    for worker in workers:worker.join(timeout=10);assert worker.exitcode==0

def test_orphan_hold_survives_restart_and_window_expiry(tmp_path):
    from cps.services.reflow.shared_budget import BudgetError
    s=store(tmp_path);s.reserve('lost',.8,job='a',book='a',user='a',context={})
    with pytest.raises(BudgetError):store(tmp_path,clock=lambda:300000).reserve('next',.3,job='b',book='b',user='b',context={})
    assert store(tmp_path,clock=lambda:300000).status()['remaining_usd']==pytest.approx(.2)

def test_actual_settlement_survives_missing_job_mirror(tmp_path,monkeypatch):
    from cps.services.reflow.shared_budget import BudgetError
    led=ledger(tmp_path,'a');attempt=led.reserve_attempt('0',.9)
    def crash(*a,**k):raise OSError('disk fault')
    monkeypatch.setattr(led,'record',crash)
    with pytest.raises(OSError):led.reconcile_attempt(attempt,.3)
    assert store(tmp_path).status()['remaining_usd']==pytest.approx(.7)
    with pytest.raises(BudgetError):ledger(tmp_path,'b').reserve_attempt('0',.8)
    assert store(tmp_path,clock=lambda:200000).status()['remaining_usd']==pytest.approx(1)

def test_mirror_failure_before_dispatch_releases_only_new_reservation(tmp_path,monkeypatch):
    led=ledger(tmp_path,'a')
    monkeypatch.setattr(led,'record',lambda *a,**k:(_ for _ in ()).throw(OSError('disk fault')))
    with pytest.raises(OSError):led.reserve_attempt('0',.9)
    assert store(tmp_path).status()['remaining_usd']==1

@pytest.mark.parametrize('cap',[None,0,-1,float('nan'),float('inf'),'bad',True])
def test_invalid_admin_limit_disables_paid_without_touching_free_ledger(tmp_path,cap):
    from cps.services.reflow.shared_budget import BudgetError
    with pytest.raises(BudgetError):ledger(tmp_path,'a',cap).reserve_attempt('0',.1)
    free=Ledger(tmp_path/'jobs/free/job.jsonl',0);free.record({'kind':'job','event':'start'})
    assert free.spent()==0

def test_legacy_jobs_import_charges_and_timeless_unknowns_once(tmp_path):
    p=tmp_path/'jobs/old/job.jsonl';p.parent.mkdir(parents=True)
    events=[dict(kind='reservation',event='pending',attempt='held',bound_usd=.3,ts=1),dict(kind='reservation',event='pending',attempt='done',bound_usd=.5,ts=99900),dict(kind='reservation',event='reconciled',attempt='done',cost_usd=.2,ts=99950),dict(kind='page',attempt='done',cost_usd=.2,ts=99950)]
    p.write_text(''.join(json.dumps(e)+'\n' for e in events))
    assert store(tmp_path).status()['remaining_usd']==pytest.approx(.5)
    assert store(tmp_path).status()['remaining_usd']==pytest.approx(.5)
    assert store(tmp_path,clock=lambda:200000).status()['remaining_usd']==pytest.approx(.7)

def test_corrupt_legacy_record_and_backward_clock_fail_closed(tmp_path):
    from cps.services.reflow.shared_budget import BudgetError
    s=store(tmp_path);s.status()
    with pytest.raises(BudgetError):store(tmp_path,clock=lambda:99999).reserve('new',.1,job='j',book='b',user='u',context={})
    p=tmp_path/'jobs/old/job.jsonl';p.parent.mkdir(parents=True);p.write_text('{broken')
    assert s.status()['status']=='unavailable'
    with pytest.raises(BudgetError):ledger(tmp_path,'a').reserve_attempt('0',.1)

def test_lowered_cap_and_conflicting_settlement_stop_further_spend(tmp_path):
    from cps.services.reflow.shared_budget import BudgetError
    led=ledger(tmp_path,'a');attempt=led.reserve_attempt('0',.6);led.reconcile_attempt(attempt,.4)
    with pytest.raises(BudgetError):ledger(tmp_path,'b',.3).reserve_attempt('0',.01)
    with pytest.raises(BudgetError):led.reconcile_attempt(attempt,.2)
    assert store(tmp_path).status()['status']=='unavailable'


def test_real_typed_transport_has_shared_and_job_hold_before_post(tmp_path):
    from cps.services.reflow.typed_model import TypedStageClient
    from cps.services.reflow.shared_budget import BudgetError
    from tests.unit.test_reflow_typed_transport import Session,reply,request
    req,raster=request();led=ledger(tmp_path,'a');second=ledger(tmp_path,'b')
    class Inspect(Session):
        def post(self,*args,**kwargs):
            assert led.pending_usd()>0
            assert store(tmp_path).status()['remaining_usd']<1
            with pytest.raises(BudgetError):second.reserve_attempt('0',1)
            return super().post(*args,**kwargs)
    session=Inspect(reply());client=TypedStageClient('inert','proposer',session=session,max_retries=1)
    client.call(client.prepare_request(req,raster),ledger=led)
    assert len(session.calls)==1 and led.pending_usd()==0
    assert store(tmp_path).status()['remaining_usd']==pytest.approx(1-led.spent())


def test_changed_legacy_history_and_retention_preserve_financial_evidence(tmp_path):
    import os
    from cps.services.reflow import retention
    led=ledger(tmp_path,'a');led.reserve_attempt('0',.4)
    path=tmp_path/'jobs/a/job.jsonl';original=path.read_text();path.write_text(original.replace('0.4','0.1'))
    assert store(tmp_path).status()['status']=='unavailable'
    broken=tmp_path/'jobs/garbled/old.jsonl';broken.parent.mkdir(parents=True)
    broken.write_text(json.dumps({'kind':'job','event':'finish','status':'done'})+'\n'+ '{partial')
    os.utime(broken,(1,1));retention.sweep(str(tmp_path),now=9999999999)
    assert broken.exists() and path.exists() and (tmp_path/'financial-admission.sqlite3').exists()


def test_exact_window_boundary_expires_charge_but_not_unknown(tmp_path):
    s=store(tmp_path);s.reserve('settled',.4,job='j',book='b',user='u',context={});s.finish('settled',.2)
    s.reserve('unknown',.3,job='j',book='b',user='u',context={})
    assert store(tmp_path,clock=lambda:186399.999).status()['remaining_usd']==pytest.approx(.5)
    assert store(tmp_path,clock=lambda:186400).status()['remaining_usd']==pytest.approx(.7)


def test_unproven_release_never_discards_a_possible_bill(tmp_path):
    from cps.services.reflow.shared_budget import BudgetError
    led=ledger(tmp_path,'a');attempt=led.reserve_attempt('0',.6)
    with pytest.raises(BudgetError):led.release_attempt(attempt,'timeout')
    assert store(tmp_path).status()['remaining_usd']==pytest.approx(.4)


def test_provider_overbound_charge_is_retained_and_prevents_more_dispatch(tmp_path):
    from cps.services.reflow.shared_budget import BudgetError
    led=ledger(tmp_path,'a',.5);attempt=led.reserve_attempt('0',.4);led.reconcile_attempt(attempt,.8)
    assert led.spent()==.8 and store(tmp_path,.5).status()['status']=='exhausted'
    with pytest.raises(BudgetError):ledger(tmp_path,'b',.5).reserve_attempt('0',.01)


def test_legacy_settlement_before_reservation_timestamp_is_a_conflict(tmp_path):
    p=tmp_path/'jobs/old/job.jsonl';p.parent.mkdir(parents=True)
    p.write_text(json.dumps(dict(kind='reservation',event='pending',attempt='x',bound_usd=.4,ts=100))+'\n'+json.dumps(dict(kind='reservation',event='reconciled',attempt='x',cost_usd=.1,ts=99))+'\n')
    assert store(tmp_path).status()['status']=='unavailable'


def test_upgrade_waits_for_uninstrumented_paid_worker_to_finish(tmp_path):
    p=tmp_path/'jobs/old/job.jsonl';p.parent.mkdir(parents=True)
    p.write_text(json.dumps(dict(kind='job',event='start',cap_usd=1,ts=99900))+'\n')
    assert store(tmp_path).status()['status']=='unavailable'
    with p.open('a') as f:f.write(json.dumps(dict(kind='job',event='finish',status='interrupted',ts=99950))+'\n')
    assert store(tmp_path).status()['status']=='available'

@pytest.mark.parametrize('mirror_start',[False,True])
def test_missing_shared_database_cannot_forget_pre_mirror_paid_liability(tmp_path,mirror_start):
    from cps.services.reflow.shared_budget import BudgetError
    led=ledger(tmp_path,'a')
    if mirror_start:led.record(dict(kind='job',event='start',cap_usd=1),durable=True)
    led.store.reserve('before-mirror',.8,job='a',book='a',user='a',context={},ledger=led.path)
    led.store.path.replace(tmp_path/'preserved.sqlite3')
    with pytest.raises(BudgetError):store(tmp_path).reserve('new',.5,job='b',book='b',user='b',context={})
    assert store(tmp_path).status()['status']=='unavailable'
    assert not led.store.path.exists()  # never silently create empty replacement


def test_older_valid_same_identity_database_cannot_rollback_pending_liability(tmp_path):
    import shutil
    from cps.services.reflow.shared_budget import BudgetError
    s=store(tmp_path);s.reserve('settled',.2,job='a',book='a',user='a',context={});s.finish('settled',.2)
    shutil.copyfile(s.path,tmp_path/'older.sqlite3')
    s.reserve('pre-mirror',.7,job='a',book='a',user='a',context={})
    (tmp_path/'older.sqlite3').replace(s.path)
    with pytest.raises(BudgetError):store(tmp_path).reserve('new',.7,job='b',book='b',user='b',context={})
    assert store(tmp_path).status()['status']=='unavailable'


def _crash_at_witness_boundary(root,phase):
    import os
    from pathlib import Path
    s=store(Path(root));original=s._write_witness
    def write(witness):
        if phase=='genesis_before_ready' and witness['state']=='ready':os._exit(73)
        original(witness)
        if phase=='initial_witness' and witness['state']=='initializing':os._exit(73)
        if phase=='intent_before_commit' and witness['generation']==1:os._exit(73)
    s._write_witness=write
    checkpoint=s._checkpoint
    def committed(db,witness):
        checkpoint(db,witness)
        if phase=='committed_before_mirror':os._exit(73)
    s._checkpoint=committed
    s.reserve('crashed',.8,job='a',book='a',user='a',context={})
    os._exit(99)


@pytest.mark.parametrize('phase',['initial_witness','genesis_before_ready','intent_before_commit','committed_before_mirror'])
def test_actual_process_death_at_witness_boundaries_never_restores_empty_allowance(tmp_path,phase):
    from cps.services.reflow.shared_budget import BudgetError
    worker=multiprocessing.get_context('spawn').Process(target=_crash_at_witness_boundary,args=(str(tmp_path),phase))
    worker.start();worker.join(timeout=15);assert worker.exitcode==73
    s=store(tmp_path)
    with pytest.raises(BudgetError):s.reserve('next',.5,job='b',book='b',user='b',context={})
    status=s.status()
    if phase=='committed_before_mirror':assert status['remaining_usd']==pytest.approx(.2)
    else:assert status['status']=='unavailable'


def test_missing_witness_or_valid_foreign_store_is_not_automatically_adopted(tmp_path):
    import shutil
    root=tmp_path/'own';s=store(root);s.reserve('held',.8,job='a',book='a',user='a',context={})
    witness=s.witness.read_bytes();s.witness.unlink()
    assert s.status()['status']=='unavailable'
    s.witness.write_bytes(witness)
    other=store(tmp_path/'other');assert other.status()['status']=='available'
    shutil.copyfile(other.path,s.path)
    assert s.status()['status']=='unavailable'
