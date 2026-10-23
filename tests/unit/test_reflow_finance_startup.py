"""Synthetic startup records preserve actual dispatch uncertainty."""
from pathlib import Path
from types import SimpleNamespace
import pytest
from tests.unit.test_reflow_task import rig
from cps.services.reflow.ledger import Ledger
from cps.services.reflow.shared_budget import SharedLedger, Store
from cps.services.reflow.native_resources import Lease

pytestmark = pytest.mark.unit


@pytest.mark.parametrize('state', ['settled', 'released', 'unsent', 'dispatched', 'store_only'])
def test_startup_reconciles_terminal_and_store_only_attempts_idempotently(rig, monkeypatch, state):
    root=Path(rig.root)
    store=Store(root,lambda:5)
    monkeypatch.setattr(rig.mod,'instance_budget_store',lambda:store)
    path=root/'jobs'/'5'/'job.jsonl'
    led=SharedLedger(path,1,job_id='job',store=store,book_id=5,user_id=7)
    if state=='store_only':
        store.reserve('attempt',.9,job='job',book=5,user=7,
            context={'route_version':'vision-parent-v1'},ledger=path)
    else:
        led.record(dict(kind='job',event='start',route='vision-page-1',mode='full',cap_usd=1))
        attempt=led.reserve_attempt('page',.9,context={'route_version':'vision-parent-v1'})
        if state in ('settled','released'):
            store.finish(attempt,.3 if state=='settled' else None)
            # Already-terminal jobs still need their crash-stale finance mirror repaired.
            led.record(dict(kind='job',event='finish',status='failed'))
        if state=='dispatched':led.record(dict(kind='vision_dispatch',attempt=attempt),durable=True)
    rig.mod.recover_interrupted_jobs(worker=SimpleNamespace(tasks=[]))
    first=path.read_bytes()
    rig.mod.recover_interrupted_jobs(worker=SimpleNamespace(tasks=[]))
    assert path.read_bytes()==first
    summary=Ledger(path,1,job_id='job').summary()
    assert summary['pending_usd']==(.9 if state=='dispatched' else 0)
    assert summary['spend_usd']==(.3 if state=='settled' else 0)
    assert store.status()['remaining_usd']==pytest.approx(4.1 if state=='dispatched' else 4.7 if state=='settled' else 5)


def test_startup_cannot_release_an_unsent_attempt_while_native_lease_is_live(rig, monkeypatch):
    root=Path(rig.root);store=Store(root,lambda:5)
    monkeypatch.setattr(rig.mod,'instance_budget_store',lambda:store)
    led=SharedLedger(root/'jobs'/'5'/'job.jsonl',1,job_id='job',store=store,book_id=5,user_id=7)
    led.reserve_attempt('page',.9,context={'route_version':'vision-parent-v1'})
    with Lease(root):
        rig.mod.recover_interrupted_jobs(worker=SimpleNamespace(tasks=[]))
    assert Ledger(led.path,1).pending_usd()==.9
    rig.mod.recover_interrupted_jobs(worker=SimpleNamespace(tasks=[]))
    assert Ledger(led.path,1).pending_usd()==0


@pytest.mark.parametrize('receipt_kind', ['unsent', 'charged', 'wrong_bound', 'malformed', 'dispatched'])
def test_receipt_cleanup_removes_only_exact_released_unsent_request(tmp_path, receipt_kind):
    import json
    from cps.services.reflow.vision_bridge import recover_unsent_receipts
    root=tmp_path/'state';store=Store(root,lambda:5)
    ledger=SharedLedger(root/'jobs'/'5'/'job.jsonl',1,job_id='job',store=store,book_id=5,user_id=7)
    label='a'*64;cache=root/'cache'/'vision';cache.mkdir(parents=True)
    attempt=ledger.reserve_attempt(label,.2,context={'route_version':'vision-parent-v1'})
    path=cache/(label+'-'+'b'*64+'.pending')
    receipt=dict(reservation=attempt,bound_usd=.2)
    if receipt_kind=='charged':receipt.update(cost_usd=.1,response={'synthetic':True})
    if receipt_kind=='wrong_bound':receipt['bound_usd']=.3
    if receipt_kind=='dispatched':ledger.record(dict(kind='vision_dispatch',attempt=attempt),durable=True)
    path.write_text('{' if receipt_kind=='malformed' else json.dumps(receipt))
    before=path.read_bytes()
    ledger.recover_authoritative(worker_gone=True)
    recover_unsent_receipts(cache,ledger)
    recover_unsent_receipts(cache,ledger)
    if receipt_kind=='unsent':assert not path.exists()
    else:assert path.read_bytes()==before
    assert ledger.pending_usd()==(.2 if receipt_kind=='dispatched' else 0)
