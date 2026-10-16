"""Offline bill-bearing fixtures test transport, never model/source quality."""
import copy
import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from cps.services.reflow import layout_model as lm, layout_pipeline as lp, layout_quote as quote, model
from cps.services.reflow.ledger import Ledger
from cps.services.reflow.operation_cache import OperationCache
from cps.services.reflow.structural_pipeline import EstimateStale
from cps.services.reflow.typed_model import _encoded
from tests.unit.test_reflow_layout_transport import Session, envelope, jpeg
from tests.unit.test_reflow_layout_ops import prepared
from tests.unit.test_reflow_structural_ops import source
from tests.unit.test_reflow_structural_pipeline import prepared_result

pytestmark = pytest.mark.unit
FACTS = json.loads((Path(__file__).parents[1]/'fixtures/reflow-qwen-controls.json').read_text())
SELECTION = {'proposer': 'qwen38-d1', 'reviewer': 'luna6-n1'}


class Qwen(Session):
    def __init__(self):
        super().__init__()
        self.controls = copy.deepcopy(FACTS)
        self.route = self.controls['route']['data']['endpoints'][0]
        self.data.update(id='OFFLINE-synthetic-qwen', model='qwen/qwen3.8-flash', provider='Alibaba', service_tier='default')
        self.data['usage'] = dict(prompt_tokens=100, completion_tokens=30, total_tokens=130,
            cost=.0000291, is_byok=False,
            prompt_tokens_details=dict(cached_tokens=0, cache_write_tokens=0, audio_tokens=0, video_tokens=0),
            completion_tokens_details=dict(reasoning_tokens=0, image_tokens=0, audio_tokens=0))

    def get(self, url, **kwargs):
        return self.response(self.controls['models'] if url.endswith('/models') else self.controls['route'])


def client(session=None):
    return lp.LayoutClient('OFFLINE-fixture', profile_selection=SELECTION, session=session).stages['proposer']


def test_new_disabled_wire_reserves_all_cache_writes_and_preserves_old_authority(tmp_path):
    """Old profiles/wires stay exact; changed task bytes require new quote consent."""
    old = FACTS['old_authority']
    assert {k:asdict(v) for k,v in lm.PROFILES.items()} == old['defaults']
    for key, value in old['named'].items():
        assert lm.NAMED_PROFILES[key][1].decode() == value
    for row in old['rows']:
        c = lp.LayoutClient('', profile_selection=row['selection'])
        current = quote._versions(row['selection'], True)
        changed = {'version', 'proposer_prompt', 'range_required_task_sha256'}
        assert {k:v for k,v in current.items() if k not in changed} == {
            k:v for k,v in row['versions'].items() if k not in changed}
        assert current['version'] != row['versions']['version']
        assert current['proposer_prompt'] != row['versions']['proposer_prompt']
        assert {stage:asdict(c.prepare_request(old['envelope'])) for stage,c in c.stages.items()} == row['wires']
    c = client(); w = c.prepare_request(envelope(), jpeg())
    assert w.payload['reasoning'] == {'enabled': False}
    assert w.payload['provider']['order'] == ['alibaba']
    assert w.payload['provider']['require_parameters'] is True and w.payload['provider']['allow_fallbacks'] is False
    assert set(w.payload) == {'model','messages','max_tokens','usage','response_format','provider','reasoning'}
    assert w.payload['response_format']['json_schema']['strict'] is True
    assert w.bound_usd == (w.prompt_tokens_bound*.20+8192*.47)/1e6
    assert json.loads(w.context_json)['supporting_reasoning_tokens'] == 0
    assert c.profile.completion_cap == 128000 and w.response_token_bound == 8192
    c._check_request(w)
    cache = OperationCache(tmp_path/'cache')
    other = lp.LayoutClient('', profile_selection=lm.DISABLED_SELECTION).stages['proposer'].prepare_request(envelope(), jpeg())
    token, _ = cache.claim(other.sha256); cache.finish(other.sha256, token, response={'old': True})
    token, saved = cache.claim(w.sha256); assert token and saved is None
    cache.release_unsent(w.sha256, token)
    assert lp.LayoutClient('', profile_selection=SELECTION).stages['reviewer'].prepare_request(envelope()).payload['reasoning'] == {'effort':'none'}


def test_missing_changed_endpoint_control_or_price_refuses_before_any_bill(tmp_path):
    """The complete known catalogue, toggle, cache fees and pin are pre-hold requirements."""
    for fault in (None, 'mandatory', 'missing_toggle', 'invalid_toggle', 'canonical', 'missing_reasoning', 'unsupported',
                  'price', 'write', 'missing_write', 'read', 'alias', 'unknown_meter', 'tier', 'discount',
                  'endpoint', 'native_tools', 'capacity', 'surcharge'):
        s = Qwen(); c = client(s); w = c.prepare_request(envelope())
        m = s.controls['models']['data'][0]; p = s.route['pricing']
        if fault == 'mandatory': m['reasoning']['mandatory'] = True
        elif fault == 'missing_toggle': m['reasoning'].pop('default_enabled')
        elif fault == 'invalid_toggle': m['reasoning']['default_enabled'] = 'true'
        elif fault == 'canonical': m['canonical_slug'] = 'qwen/qwen3.8-flash-next'
        elif fault == 'missing_reasoning': m.pop('reasoning')
        elif fault == 'unsupported': s.route['supported_parameters'].remove('reasoning')
        elif fault == 'price': p['prompt'] = '0.000000149'
        elif fault == 'write': p['input_cache_write'] = '0.000000201'
        elif fault == 'missing_write': p.pop('input_cache_write')
        elif fault == 'read': p['input_cache_read'] = None
        elif fault == 'alias': p['input_cache_creation_5m'] = '0.0000003'
        elif fault == 'unknown_meter': p['web_search'] = 0
        elif fault == 'tier': p['overrides'] = [{'min_prompt_tokens':999999,'prompt':'0.00000015'}]
        elif fault == 'discount': p['discount'] = None
        elif fault == 'endpoint': s.route['tag'] = 'alibaba/fast'
        elif fault == 'native_tools': s.route['native_tools'] = {'search':{}}
        elif fault == 'capacity': s.route['max_prompt_tokens'] = 983617
        elif fault == 'surcharge': p['image'] = .01
        ledger = Ledger(str(tmp_path/str(fault)), cap_usd=5)
        if fault is None:
            p.update(input_cache_creation_5m='0.0000002', input_cache_read_5m='0.000000016')
            c.preflight(w.prompt_tokens_bound,w.response_token_bound)
        else:
            with pytest.raises(model.ModelError): c.call(w,ledger=ledger)
        assert not s.posts and not ledger.entries(), fault


def test_whole_response_audits_refuse_after_durable_billing(tmp_path):
    """A small reported bill cannot hide new meters, cache ambiguity or reasoning."""
    for fault in (None,'full_write','cache_mix','reason_missing','reason_bool','reason_nonzero','reason_other',
                  'hidden_top','encrypted','meter','missing_cache','cache_bool','cache_alias','cache_over',
                  'audio','cost_meter','price_bill','provider','tier','tier_bool','model','total','length'):
        s = Qwen(); c = client(s); w = c.prepare_request(envelope(),jpeg())
        u=s.data['usage']; pd=u['prompt_tokens_details']; cd=u['completion_tokens_details']
        if fault == 'full_write': pd['cache_write_tokens']=100; u['cost']=.0000341
        elif fault == 'cache_mix': pd.update(cached_tokens=50,cache_write_tokens=30,cache_read_input_tokens=50); u['cost']=.0000239
        elif fault == 'reason_missing': cd.pop('reasoning_tokens')
        elif fault == 'reason_bool': cd['reasoning_tokens']=False
        elif fault == 'reason_nonzero': cd['reasoning_tokens']=1
        elif fault == 'reason_other': s.data['choices'].append({'message':{'reasoning_content':'hidden'}})
        elif fault == 'hidden_top': s.data['reasoning_details']=[{'text':'hidden'}]
        elif fault == 'encrypted': s.data['choices'][0]['message']['encrypted_content']='hidden'
        elif fault == 'meter': u['search_cost']=0
        elif fault == 'missing_cache': pd.pop('cache_write_tokens')
        elif fault == 'cache_bool': pd['cached_tokens']=False
        elif fault == 'cache_alias': pd['cache_creation_input_tokens']=1
        elif fault == 'cache_over': pd['cached_tokens']=101
        elif fault == 'audio': cd['audio_tokens']=1
        elif fault == 'cost_meter': u['cost_details']={'new_fee':0}
        elif fault == 'price_bill': u['cost']=.0000301
        elif fault == 'provider': s.data['provider']='other'
        elif fault == 'tier': s.data['service_tier']='prime'
        elif fault == 'tier_bool': s.data['service_tier']=False
        elif fault == 'model': s.data['model']='qwen/qwen3.8-flash:online'
        elif fault == 'total': u['total_tokens']=131
        elif fault == 'length': s.data['choices'][0].update(finish_reason='length',message={'content':None})
        ledger=Ledger(str(tmp_path/str(fault)),cap_usd=5)
        if fault in (None,'full_write','cache_mix'):
            assert c.call(w,ledger=ledger).reasoning_tokens==0
        else:
            with pytest.raises(model.ModelError): c.call(w,ledger=ledger)
        assert ledger.spent()==u['cost'] and ledger.pending_usd()==0,fault
        assert s.posts[0]['data']==w.payload_json.encode()


def test_reconstructed_wire_cannot_gain_tools_tiers_fallback_or_reasoning(monkeypatch,tmp_path):
    """Even a coherently re-signed changed builder cannot widen the closed route."""
    original=lm.LayoutStageClient.prepare_request
    for index,(key,value) in enumerate([('reasoning',{}),('reasoning',{'enabled':True}),('reasoning',{'effort':'none'}),
            ('reasoning',{'enabled':False,'exclude':True}),('reasoning',{'enabled':0}),
            ('tools',[]),('plugins',[]),('usage',{'include':False}),('service_tier','fast'),('model','qwen/qwen3.8-flash:online'),
            ('provider',{'order':['alibaba'],'allow_fallbacks':True,'require_parameters':True})]):
        c=client(Qwen())
        def changed(self,en,raster=None):
            w=original(self,en,raster); payload=w.payload;payload[key]=value;raw=_encoded(payload).decode()
            digest=hashlib.sha256(raw.encode()).hexdigest();audit=json.loads(w.context_json);audit['request_sha256']=digest
            return replace(w,payload_json=raw,sha256=digest,context_json=_encoded(audit).decode())
        monkeypatch.setattr(lm.LayoutStageClient,'prepare_request',changed)
        w=c.prepare_request(envelope());ledger=Ledger(str(tmp_path/str(index)),cap_usd=5)
        with pytest.raises(ValueError):c.call(w,ledger=ledger)
        assert not c._session.posts and not ledger.entries()
        monkeypatch.setattr(lm.LayoutStageClient,'prepare_request',original)
    c=client();c.profile=replace(c.profile,route='alibaba/prime')
    with pytest.raises(ValueError):c.prepare_request(envelope())
    for selection in ({'proposer':'qwen38-d1'},{'proposer':'qwen38-d1','reviewer':'mimo26-d1'}):
        with pytest.raises(ValueError):lp.LayoutClient('',profile_selection=selection)


def test_quote_funds_only_same_source_profile_candidate_and_complete_liability(source):
    """A Qwen quote must cover its own write liability and reject old-profile consent."""
    book,doc,src,_,p=prepared(source)
    q=quote.measure(doc,prepared_result=prepared_result(source),profile_selection=SELECTION)
    from cps.services.reflow import layout_requests as req
    w=client().prepare_request(req.proposal(p),p.raster)
    assert q['profile_selection']==SELECTION and q['version']==quote.QWEN_GROUPING_VERSION
    assert q['full_bound_usd']==q['proposer_bound_usd']+q['verifier_bound_usd']+q['batch_bound_usd']
    assert quote.assert_request_bound(q,'proposer',w,0,profile_selection=SELECTION)
    quote.consent_observer(q,doc,profile_selection=SELECTION)(book,p,src)
    with pytest.raises(EstimateStale):quote._current_quote(q,lm.DISABLED_SELECTION)
    forged=copy.deepcopy(q);forged['input_liability_usd_per_million']['proposer']=.1875
    forged['identity']=quote._digest({k:v for k,v in forged.items() if k!='identity'})
    with pytest.raises(EstimateStale):quote._current_quote(forged,SELECTION)
