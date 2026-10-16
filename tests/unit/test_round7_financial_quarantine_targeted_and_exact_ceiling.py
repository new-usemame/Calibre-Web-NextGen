"""Additive controls: targeted unknown liability + recomputed strict ceiling."""
import json
from pathlib import Path
import pytest
from tests.unit.test_round7_source_unit_financial_quarantine import setup,hold,admit,local_transport,no_dispatch,unchanged_hold,fresh

def test_targeted_transport_unknown_is_separately_quarantined_and_never_third_dispatched(tmp_path,monkeypatch):
 r,images,answer=setup(tmp_path);aid,path,bound=hold(r,images[0]);assert admit(r,images[0])is True
 calls,_=local_transport(monkeypatch,r,answer,failure='unknown');first=r.read_unit(images[0],page=0)
 assert first['degraded_source']and first['answer']is None and len(calls)==1 and calls[0]['targeted']
 state=fresh(r);assert len(state._financial_state()[1])==2;unchanged_hold(r,aid,bound)
 second=r.read_unit(images[0],page=0)
 assert second['degraded_source']and len(calls)==1,'Shared physical allowance cannot reset after unknown targeted billing'
 calls,_=local_transport(monkeypatch,r,answer);other=r.read_unit(images[1],page=1)
 assert other['answer']==answer and len(calls)==1 and not calls[0]['targeted']
 state=fresh(r);assert len(state._financial_state()[1])==2 and state.spent()==pytest.approx(.0001)
 assert not any(e.get('event')=='released'for e in state.entries('reservation'))

@pytest.mark.parametrize('bad_bound',[.0000001,1.0,float('nan')])
def test_matching_marker_and_journal_do_not_substitute_for_exact_recomputed_source_ceiling(tmp_path,monkeypatch,bad_bound):
 r,images,answer=setup(tmp_path);aid,path,bound=hold(r,images[0]);rows=fresh(r)._entries;rows[0]['bound_usd']=bad_bound
 Path(r.ledger.path).write_text(''.join(json.dumps(e)+'\n'for e in rows));path.write_text(json.dumps({'reservation':aid,'bound_usd':bad_bound}))
 original=path.read_bytes();assert admit(r,images[0])is False,'Matching corrupted amounts are not independently computed request proof'
 calls=no_dispatch(monkeypatch)
 assert r.financial_dispatch_allowed()is False and calls==[] and path.read_bytes()==original
 assert not any(e.get('attempt')==aid and e.get('event')in('reconciled','released')for e in fresh(r).entries('reservation'))
