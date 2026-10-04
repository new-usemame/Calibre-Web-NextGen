"""Thinking toggle authority using the saved authenticated endpoint shapes."""
import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from cps.services.reflow import layout_model as lm, layout_pipeline as lp, layout_quote as quote, model
from cps.services.reflow.ledger import Ledger
from cps.services.reflow.operation_cache import OperationCache
from cps.services.reflow.structural_pipeline import EstimateStale
from tests.unit.test_reflow_alternative_route import Alternative, SELECTION
from tests.unit.test_reflow_layout_transport import envelope, jpeg
from tests.unit.test_reflow_structural_ops import source
from tests.unit.test_reflow_structural_pipeline import prepared_result

pytestmark = pytest.mark.unit
DISABLED = {'proposer': 'mimo26-d1', 'reviewer': 'luna6-n1'}


class Saved(Alternative):
    def __init__(self):
        super().__init__()
        self.controls = json.loads((Path(__file__).parents[1]/'fixtures/reflow-mimo-controls.json').read_text())
        self.data['usage']['completion_tokens_details'] = {'reasoning_tokens': 0}

    def get(self, url, **kwargs):
        if url.endswith('/models'):
            return self.response(self.controls['models'])
        mid = url.split('/models/')[1].removesuffix('/endpoints')
        return self.response(self.controls['routes'][mid])


def client(session=None):
    return lp.LayoutClient('offline', profile_selection=DISABLED, session=session).stages['proposer']


def test_disabled_wire_pair_and_allocation_cannot_reuse_enabled_authority(tmp_path, source):
    c = client(); w = c.prepare_request(envelope(), jpeg())
    enabled = lp.LayoutClient('', profile_selection=SELECTION).stages['proposer'].prepare_request(envelope(), jpeg())
    assert w.payload['reasoning'] == {'enabled': False}
    assert w.response_token_bound == 8192 and c.profile.completion_cap == 128000
    assert json.loads(w.context_json)['supporting_reasoning_tokens'] == 0
    assert w.sha256 != enabled.sha256 and w.bound_usd < enabled.bound_usd
    with pytest.raises(ValueError): c._check_request(enabled)
    with pytest.raises(ValueError): lp.LayoutClient('', profile_selection={'proposer': 'mimo26-d1'})
    assert lp.LayoutClient('', profile_selection=DISABLED).stages['reviewer'].prepare_request(envelope()).payload['reasoning'] == {'effort': 'none'}
    cache = OperationCache(tmp_path/'cache')
    token, _ = cache.claim(enabled.sha256)
    cache.finish(enabled.sha256, token, response={'old': True})
    token, saved = cache.claim(w.sha256)
    assert token and saved is None
    cache.release_unsent(w.sha256, token)
    q = quote.measure(source[1], prepared_result=prepared_result(source), profile_selection=SELECTION)
    with pytest.raises(EstimateStale): quote._current_quote(q, DISABLED)
    q = quote.measure(source[1], prepared_result=prepared_result(source), profile_selection=DISABLED)
    assert q['profile_selection'] == DISABLED and q['profiles']['proposer']['reasoning_effort'] == 'disabled'
    c.profile = replace(c.profile, supporting_reasoning_tokens=1)
    with pytest.raises(ValueError): c.prepare_request(envelope())


def test_saved_toggle_controls_refuse_mandatory_missing_and_foreign_before_hold(tmp_path):
    for fault in (None, 'mandatory', 'missing', 'pin', 'parameter', 'price'):
        s = Saved(); c = client(s); w = c.prepare_request(envelope())
        m = next(m for m in s.controls['models']['data'] if m['id'] == c.model_id)
        route = next(r for r in s.controls['routes'][c.model_id]['data']['endpoints'] if r['tag'] == c.profile.route)
        if fault == 'mandatory': m['reasoning']['mandatory'] = True
        elif fault == 'missing': m.pop('reasoning')
        elif fault == 'pin': route['provider_name'] = 'foreign'
        elif fault == 'parameter': route['supported_parameters'].remove('reasoning')
        elif fault == 'price': route['pricing']['completion'] = '0.00000029'
        ledger = Ledger(str(tmp_path/str(fault)), cap_usd=5)
        if fault is None: c.preflight(w.prompt_tokens_bound, w.response_token_bound)
        else:
            with pytest.raises(model.ModelError): c.call(w, ledger=ledger)
        assert not s.posts and not ledger.entries()


def test_disabled_meter_requires_explicit_zero_and_settles_every_refusal(tmp_path):
    for fault in (None, 'missing', 'null', 'bool', 'fractional', 'negative', 'nonzero', 'hidden', 'details', 'hidden_other', 'hidden_top', 'model', 'provider', 'tier', 'rate', 'length', 'malformed'):
        s = Saved(); c = client(s); w = c.prepare_request(envelope(), jpeg())
        detail = s.data['usage']['completion_tokens_details']
        if fault == 'missing': detail.pop('reasoning_tokens')
        elif fault in ('null', 'bool', 'fractional', 'negative', 'nonzero'): detail['reasoning_tokens'] = {'null': None, 'bool': False, 'fractional': .5, 'negative': -1, 'nonzero': 1}[fault]
        elif fault == 'hidden': s.data['choices'][0]['message']['reasoning'] = 'hidden'
        elif fault == 'details': s.data['choices'][0]['message']['reasoning_details'] = [{'data': 'hidden'}]
        elif fault == 'hidden_other': s.data['choices'].append({'message': {'reasoning_content': 'hidden'}})
        elif fault == 'hidden_top': s.data['reasoning_details'] = [{'data': 'hidden'}]
        elif fault == 'model': s.data['model'] = 'foreign'
        elif fault == 'provider': s.data['provider'] = 'foreign'
        elif fault == 'tier': s.data['service_tier'] = 'flex'
        elif fault == 'rate': s.data['usage']['cost'] = .001
        elif fault == 'length': s.data['choices'][0].update(finish_reason='length', message={'content': None})
        elif fault == 'malformed': s.data['choices'][0]['message']['content'] = '{'
        ledger = Ledger(str(tmp_path/str(fault)), cap_usd=5)
        if fault is None: assert c.call(w, ledger=ledger).reasoning_tokens == 0
        else:
            with pytest.raises(model.ModelError): c.call(w, ledger=ledger)
        assert ledger.spent() == s.data['usage']['cost'] and ledger.pending_usd() == 0
        assert s.posts[0]['data'] == w.payload_json.encode()
