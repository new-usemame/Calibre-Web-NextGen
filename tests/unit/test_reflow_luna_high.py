"""Offline HIGH/HIGH controls, billing and independent review; never quality."""
import copy
import json
from decimal import Decimal

import pytest
from jsonschema import validate, ValidationError
from cps.services.reflow import (layout_luna_high as high, layout_model as lm,
    layout_pipeline as lp, layout_requests as req, layout_ops as ops, layout_quote as quote, model)
from cps.services.reflow.ledger import Ledger
from cps.services.reflow.operation_cache import OperationCache
from tests.unit.test_reflow_luna_standard import Luna
from tests.unit.test_reflow_layout_transport import envelope, jpeg
from tests.unit.test_reflow_layout_ops import boundary_source
from tests.unit.test_reflow_range_choices import range_fixture, verdict

pytestmark = pytest.mark.unit


def test_high_pair_wire_controls_support_and_separate_cache(tmp_path, monkeypatch):
    new = lp.LayoutClient('OFFLINE', profile_selection=lm.LUNA_HIGH_SELECTION)
    old = lp.LayoutClient('', profile_selection=lm.LUNA_REVIEW_SELECTION)
    cache = OperationCache(tmp_path/'cache')
    for stage in ('proposer', 'reviewer'):
        c = new.stages[stage]; w = c.prepare_request(envelope(), jpeg())
        a = json.loads(w.context_json)
        assert w.payload['reasoning'] == {'effort': 'high'}
        assert w.payload['max_tokens'] == a['visible_answer_tokens'] + 65536
        assert c.profile.completion_cap == (128000 if stage == 'proposer' else 69632)
        assert c.profile.supporting_reasoning_tokens == 65536
        assert a['meter_policy'] == high.POLICY_VERSION and a['control_version'] == high.CONTROL_VERSION
        assert json.loads(w.payload['messages'][1]['content'])['c'] == high.CONTROL_VERSION
        assert not a['reasoning_budget_is_native_cap'] and a['requested_tier'] == 'default'
        assert c.max_retries == 1
        high.wire(w.payload); c._check_request(w)
        oldwire = old.stages[stage].prepare_request(envelope(), jpeg())
        token, _ = cache.claim(oldwire.sha256); cache.finish(oldwire.sha256, token, response={'negative': True})
        token, saved = cache.claim(w.sha256)
        assert token and saved is None; cache.release_unsent(w.sha256, token)
        control = high.CONTROL_VERSION
        monkeypatch.setattr(high, 'CONTROL_VERSION', control+'x')
        changed = c.prepare_request(envelope(), jpeg())
        assert changed.sha256 != w.sha256
        with pytest.raises(ValueError): c._check_request(w)
        monkeypatch.setattr(high, 'CONTROL_VERSION', control)
        session = Luna(); c._session = session
        c.preflight(w.prompt_tokens_bound, w.response_token_bound)
        session.controls['models']['data'][0]['reasoning']['supported_efforts'].remove('high')
        ledger = Ledger(str(tmp_path/stage), cap_usd=1)
        with pytest.raises(model.ModelError): c.call(w, ledger=ledger)
        assert not session.posts and not ledger.entries()
        for reason in ({'effort': 'medium'}, {'effort': 'high', 'max_tokens': 65536}, {'effort': 'high', 'exclude': True}):
            with pytest.raises(ValueError): high.wire(dict(w.payload, reasoning=reason))
        for key, value in (('tools', []), ('service_tier', 'priority'), ('model', high.MODEL+':floor'),
                ('provider', dict(w.payload['provider'], allow_fallbacks=True))):
            with pytest.raises(ValueError): high.wire(dict(w.payload, **{key:value}))
        with pytest.raises(ValueError): lm.LayoutStageClient('', stage, profile_id=c.profile_id, max_retries=2)
    with pytest.raises(ValueError): lp.LayoutClient('', profile_selection={'proposer': high.PROFILE_ID})


@pytest.mark.parametrize('stage', ['proposer', 'reviewer'])
def test_high_full_support_known_bill_precedes_refusal_unknown_holds(tmp_path, stage):
    for fault in ('valid', 'high_write', 'opaque', 'counts', 'support', 'visible', 'hidden', 'fee', 'alias', 'tier', 'length', 'schema', 'duplicate', 'unknown'):
        s = Luna(); c = lp.LayoutClient('OFFLINE', profile_selection=lm.LUNA_HIGH_SELECTION, session=s).stages[stage]
        w = c.prepare_request(envelope(), jpeg(648, 972)); u = s.data['usage']; message = s.data['choices'][0]['message']
        reason = 65536 + (fault == 'support'); visible = c.profile.output_tokens + (fault == 'visible')
        pt = 272000 if fault == 'high_write' else 100
        write = pt if fault == 'high_write' else 0; rates = high.standard.HIGH_PRICES if pt >= 272000 else high.standard.PRICES
        cost = Decimal(pt-write)*Decimal(rates['prompt']) + Decimal(write)*Decimal(rates['input_cache_write']) + Decimal(reason+visible)*Decimal(rates['completion'])
        u.update(prompt_tokens=pt, completion_tokens=reason+visible, total_tokens=pt+reason+visible, cost=float(cost),
            prompt_tokens_details=dict(cached_tokens=0, cache_write_tokens=write), completion_tokens_details=dict(reasoning_tokens=reason))
        if fault == 'opaque': message['reasoning_details'] = [{'type':'reasoning.encrypted','data':'OFFLINE','format':'openai-responses-v1'}]
        elif fault == 'counts': u['prompt_tokens'] = -1
        elif fault == 'hidden': message['thinking'] = 'OFFLINE'
        elif fault == 'fee': u['tool_fee'] = 0
        elif fault == 'alias': u['prompt_tokens_details']['cache_creation_tokens'] = 1
        elif fault == 'tier': s.data['service_tier'] = 'flex'
        elif fault == 'length': s.data['choices'][0]['finish_reason'] = 'length'
        elif fault == 'schema': message['content'] = '{"items":[],"unasked":true}'
        elif fault == 'duplicate': message['content'] = '{"items":[1],"items":[]}'
        elif fault == 'unknown': u.pop('cost')
        ledger = Ledger(str(tmp_path/(stage+'-'+fault)), cap_usd=1)
        if fault in ('valid', 'high_write', 'opaque'):
            assert c.call(w, ledger=ledger).reasoning_tokens == 65536
            audit = ledger.entries('reasoning_control_audit')[0]
            assert audit['meter_policy'] == high.POLICY_VERSION and not audit['full_internal_reasoning_observed']
            if fault == 'opaque': assert audit['native_content_visibility'] == 'opaque encrypted'
        else:
            with pytest.raises(model.ModelError): c.call(w, ledger=ledger)
        assert len(s.posts) == 1
        if fault == 'unknown': assert ledger.pending_usd() == w.bound_usd and ledger.spent() == 0
        else: assert ledger.pending_usd() == 0 and ledger.spent() == float(cost)


def test_high_eligible_continuation_keeps_all_bits_impossible_only_flag(boundary_source, monkeypatch):
    book, doc, sources, raws = boundary_source
    left = ops.prepare(book, doc, 0, sources[0], raws[0])
    right = ops.prepare(book, doc, 1, sources[1], raws[1], previous_source=sources[0], previous_raw=raws[0])
    previous = left.accept(book, doc, range_fixture(left), source_page=sources[0], raw_page=raws[0])
    candidate = right.accept(book, doc, range_fixture(right, incoming={'continue':True,'previous':3,'current':0,'hyphen':'keep'}),
        source_page=sources[1], raw_page=raws[1], previous_source=sources[0], previous_raw=raws[0])
    old = req.review(right, candidate, previous)
    requested = req.review(right, candidate, previous, profile_selection=lm.LUNA_HIGH_SELECTION)
    assert requested['messages'][-1]['content'] == old['messages'][-1]['content']
    assert requested['response_schema'] == old['response_schema']
    assert requested['messages'][0]['content'] == old['messages'][0]['content'] + high.JOIN
    response = verdict(candidate, previous); response['continuation_accept'] = True
    validate(response, requested['response_schema'])
    assert req.validate_review(right, candidate, response, previous).accepted_plan is not None
    for i in range(len(response['decisions'])):
        negative = dict(response, decisions=response['decisions'][:i]+'0'+response['decisions'][i+1:])
        with pytest.raises(ops.ContractError): req.validate_review(right, candidate, negative, previous)
    impossible = req.review(right, candidate, profile_selection=lm.LUNA_HIGH_SELECTION)
    baseline = req.review(right, candidate)
    expected = copy.deepcopy(baseline['response_schema']); expected['properties']['continuation_accept']['enum'] = [False]
    assert impossible['response_schema'] == expected
    assert impossible['messages'][-1]['content'] == baseline['messages'][-1]['content']
    response = verdict(candidate); response['continuation_accept'] = True
    with pytest.raises(ValidationError): validate(response, impossible['response_schema'])
    with pytest.raises(ops.ContractError): req.validate_review(right, candidate, response)
    original_material = req._review_material
    def unknown(*args):
        view, decisions, current, can_continue = original_material(*args)
        return view, decisions, current, None
    monkeypatch.setattr(req, '_review_material', unknown)
    unknown_request = high.review(right, candidate, previous, old)
    assert unknown_request['response_schema'] == old['response_schema']
    assert high.continuation_eligibility({}, None) is None


def test_high_control_prompt_policy_changes_invalidate_existing_quote(monkeypatch):
    prepared = quote._versions(lm.LUNA_HIGH_SELECTION, True)
    prepared['identity'] = quote._digest(prepared)
    quote._current_quote(prepared, lm.LUNA_HIGH_SELECTION)
    for name in ('CONTROL_VERSION', 'QUOTE_VERSION', 'POLICY_VERSION', 'PROPOSER_PROMPT', 'REVIEW_POLICY', 'JOIN'):
        original = getattr(high, name)
        monkeypatch.setattr(high, name, original+'-changed')
        with pytest.raises(quote.EstimateStale): quote._current_quote(prepared, lm.LUNA_HIGH_SELECTION)
        monkeypatch.setattr(high, name, original)
