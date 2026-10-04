"""Supporting reasoning buys extra completion space, never a provider subcap."""
import json
from dataclasses import replace
import pytest
from cps.services.reflow import layout_model as lm, layout_allocation as allocation, layout_pipeline as lp, layout_quote as quote, model
from cps.services.reflow.ledger import Ledger
from cps.services.reflow.structural_pipeline import EstimateStale
from tests.unit.test_reflow_layout_transport import source_atom_envelope, envelope, Session
pytestmark=pytest.mark.unit


def test_complete_answer_gets_additional_support_and_distinct_identity():
    en,_=source_atom_envelope(700)
    medium=lp.LayoutClient('').stages['proposer'];wire=medium.prepare_request(en)
    assert wire.payload['reasoning']=={'effort':'medium'}
    none=lm.LayoutStageClient('', 'proposer', profile=replace(lm.PROFILES['proposer'],reasoning_effort='none',supporting_reasoning_tokens=0))
    old=none.prepare_request(en)
    assert wire.response_token_bound==old.response_token_bound+16384
    assert wire.sha256!=old.sha256
    audit=json.loads(wire.context_json)
    assert audit['visible_answer_tokens']==old.response_token_bound
    assert audit['supporting_reasoning_tokens']==16384
    assert wire.bound_usd-old.bound_usd==pytest.approx(16384*.375/1e6)
    medium._check_request(wire)
    with pytest.raises(ValueError):none._check_request(wire)


def test_full_allocation_refuses_over_ceiling_and_reviewer_keeps_4096():
    en,_=source_atom_envelope(700);view=json.loads(en['messages'][-1]['content'])
    profile=lm.PROFILES['proposer'];needed=allocation.completion_tokens('proposer',profile,view)
    with pytest.raises(ValueError,match='capacity'):
        allocation.completion_tokens('proposer',replace(profile,completion_cap=needed-1),view)
    assert profile.completion_cap==128000
    review=lp.LayoutClient('').stages['reviewer']
    assert review.profile.completion_cap==4096 and review.profile.supporting_reasoning_tokens==0
    assert review.profile.reasoning_effort=='medium'


def test_support_is_not_a_subcap_and_explicit_none_still_refuses(tmp_path):
    for effort,usage in [('medium',16385),('none',1)]:
        session=Session();session.route['max_completion_tokens']=128000
        session.data['usage']['completion_tokens_details']={'reasoning_tokens':usage}
        session.data['usage']['completion_tokens']=usage+30
        profile=lm.PROFILES['proposer'] if effort=='medium' else replace(lm.PROFILES['proposer'],reasoning_effort='none',supporting_reasoning_tokens=0)
        client=lm.LayoutStageClient('fixture','proposer',profile=profile,session=session)
        wire=client.prepare_request(envelope());ledger=Ledger(str(tmp_path/effort),cap_usd=1)
        session.on_post=lambda:None if ledger.pending_usd()==wire.bound_usd else pytest.fail('not reserved before POST')
        if effort=='medium':
            answer=client.call(wire,ledger=ledger);assert answer.reasoning_tokens==usage
        else:
            with pytest.raises(model.ModelError) as e:client.call(wire,ledger=ledger)
            assert e.value.reason_code=='allocation_mismatch' and e.value.stop_dispatch
        assert ledger.pending_usd()==0 and ledger.spent()==.0001 and len(session.posts)==1


def test_quote_and_cache_refuse_changed_support(monkeypatch):
    versions=quote._versions();en,_=source_atom_envelope(700);wire=lp.LayoutClient('').stages['proposer'].prepare_request(en)
    monkeypatch.setitem(lm.PROFILES,'proposer',replace(lm.PROFILES['proposer'],supporting_reasoning_tokens=8192))
    changed=lp.LayoutClient('').stages['proposer'].prepare_request(en)
    assert changed.sha256!=wire.sha256 and changed.response_token_bound==wire.response_token_bound-8192
    with pytest.raises(ValueError):lp.LayoutClient('').stages['proposer']._check_request(wire)
    with pytest.raises(EstimateStale):quote._current_quote(dict(versions,identity=quote._digest(versions)))
