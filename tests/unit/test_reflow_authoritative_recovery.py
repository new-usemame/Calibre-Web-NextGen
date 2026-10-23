"""Synthetic financial state recovered across durable write boundaries."""
import json
from pathlib import Path
import pytest
from cps.services.reflow.ledger import Ledger
from cps.services.reflow.shared_budget import SharedLedger, Store, BudgetError

pytestmark = pytest.mark.unit


def setup(root):
    store = Store(root, lambda: 5, clock=lambda: 100000)
    ledger = SharedLedger(root/'jobs'/'5'/'job.jsonl', 1, job_id='job',
                          store=store, book_id=5, user_id=7)
    return store, ledger


@pytest.mark.parametrize('terminal,cost', [('settled', .3), ('released', None)])
def test_authoritative_terminal_repairs_stale_pending_mirror_once(tmp_path, terminal, cost):
    store, ledger = setup(tmp_path)
    attempt = ledger.reserve_attempt('page', .9)
    store.finish(attempt, cost)
    assert Ledger(ledger.path, 1).pending_usd() == .9
    ledger.recover_authoritative()
    first = Path(ledger.path).read_bytes()
    ledger.recover_authoritative()
    assert Path(ledger.path).read_bytes() == first
    summary = Ledger(ledger.path, 1).summary()
    assert summary['pending_usd'] == 0
    assert summary['spend_usd'] == (cost or 0)
    assert [e['event'] for e in ledger.entries('reservation')] == [
        'pending', 'reconciled' if terminal == 'settled' else 'released']


def test_worker_exit_releases_only_proven_unsent_vision_attempts(tmp_path):
    store, ledger = setup(tmp_path)
    unsent = ledger.reserve_attempt('page', .2, context={'route_version':'vision-parent-v1'})
    dispatched = ledger.reserve_attempt('page', .3, context={'route_version':'vision-parent-v1'})
    legacy = ledger.reserve_attempt('page', .1)
    ledger.record(dict(kind='vision_dispatch', attempt=dispatched), durable=True)
    ledger.recover_authoritative(worker_gone=False)
    assert ledger.pending_usd() == pytest.approx(.6)
    ledger.recover_authoritative(worker_gone=True)
    assert ledger._financial_state()[1] == {dispatched:.3, legacy:.1}
    assert store.status()['remaining_usd'] == pytest.approx(4.6)
    assert next(e for e in ledger.entries('reservation') if e.get('attempt') == unsent and e['event']=='released')['reason'] == 'worker_exited_before_dispatch'
    before = Path(ledger.path).read_bytes()
    ledger.recover_authoritative(worker_gone=True)
    assert Path(ledger.path).read_bytes() == before


def test_store_only_vision_attempt_is_discovered_and_released(tmp_path):
    store, ledger = setup(tmp_path)
    store.reserve('only-store', .9, job='job', book=5, user=7,
                  context={'route_version':'vision-parent-v1'}, ledger=ledger.path)
    assert not Path(ledger.path).exists()
    assert store.vision_job_ledgers() == [Path(ledger.path)]
    ledger.recover_authoritative(worker_gone=True)
    assert ledger.spent() == ledger.pending_usd() == 0
    assert store.status()['remaining_usd'] == 5
    assert [e['event'] for e in ledger.entries('reservation')] == ['pending', 'released']


def test_partial_dispatch_marker_is_not_proof_of_no_dispatch(tmp_path):
    store, ledger = setup(tmp_path)
    ledger.reserve_attempt('page', .9, context={'route_version':'vision-parent-v1'})
    with open(ledger.path, 'ab') as out:
        out.write(b'{"kind":"vision_dispatch",')
    before = Path(ledger.path).read_bytes()
    with pytest.raises(BudgetError):
        ledger.recover_authoritative(worker_gone=True)
    assert Path(ledger.path).read_bytes() == before


def test_missing_previously_imported_ledger_is_not_store_only(tmp_path):
    store, ledger = setup(tmp_path)
    ledger.reserve_attempt('page', .9, context={'route_version':'vision-parent-v1'})
    store.status()  # persist the file digest as part of shared admission
    Path(ledger.path).unlink()
    with pytest.raises(BudgetError):
        ledger.recover_authoritative(worker_gone=True)
    assert not Path(ledger.path).exists()


def test_valid_but_lost_dispatch_tail_keeps_shared_dispatch_hold(tmp_path):
    store, ledger = setup(tmp_path)
    attempt=ledger.reserve_attempt('page', .9, context={'route_version':'vision-parent-v1'})
    pending=Path(ledger.path).read_bytes()
    ledger.record(dict(kind='vision_dispatch', attempt=attempt),durable=True)
    Path(ledger.path).write_bytes(pending)
    ledger.recover_authoritative(worker_gone=True)
    assert ledger.pending_usd()==.9
    assert store.status()['remaining_usd']==pytest.approx(4.1)


def test_vision_job_route_does_not_prove_untagged_attempt_was_unsent(tmp_path):
    store, ledger = setup(tmp_path)
    ledger.record(dict(kind='job', event='start', route='vision-page-1', cap_usd=1))
    ambiguous = ledger.reserve_attempt('page', .2)
    unsent = ledger.reserve_attempt('page', .3, context={'route_version':'vision-parent-v1'})
    ledger.recover_authoritative(worker_gone=True)
    assert ledger._financial_state()[1] == {ambiguous: .2}
    assert store.status()['remaining_usd'] == pytest.approx(4.8)
    assert next(e for e in ledger.entries('reservation')
        if e.get('attempt') == unsent and e.get('event') == 'released')['reason'] == 'worker_exited_before_dispatch'
    assert not any(e.get('attempt') == ambiguous and e.get('event') == 'released'
                   for e in ledger.entries('reservation'))
