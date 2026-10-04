"""Offline Standard/Medium transport receipts, never source-quality evidence."""
import copy
import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from cps.services.reflow import layout_model as lm, layout_pipeline as lp, layout_quote as quote, model
from cps.services.reflow.ledger import Ledger
from cps.services.reflow.operation_cache import OperationCache
from tests.unit.test_reflow_layout_transport import Session, envelope, jpeg

pytestmark = pytest.mark.unit
FACTS = json.loads((Path(__file__).parents[1]/'fixtures/reflow-luna-standard-controls.json').read_text())
SELECTION = {'proposer': 'luna6-m1-standard', 'reviewer': 'luna6-n1'}


class Luna(Session):
    def __init__(self):
        super().__init__()
        self.controls = copy.deepcopy(FACTS)
        self.route = next(r for r in self.controls['route']['data']['endpoints'] if r['tag'] == 'openai')
        self.data.update(model='openai/gpt-6-luna', provider='OpenAI', service_tier='default')
        self.data['choices'][0]['message']['reasoning'] = 'OFFLINE synthetic native reasoning'
        self.data['usage'] = dict(prompt_tokens=100, completion_tokens=30, total_tokens=130,
            cost=.000025, is_byok=False,
            prompt_tokens_details=dict(cached_tokens=0, cache_write_tokens=0),
            completion_tokens_details=dict(reasoning_tokens=12))

    def get(self, url, **kwargs):
        return self.response(self.controls['models'] if url.endswith('/models') else self.controls['route'])


def client(session=None):
    return lp.LayoutClient('OFFLINE-fixture', profile_selection=SELECTION, session=session).stages['proposer']


def test_standard_medium_identity_preserves_all_old_bytes_and_cache(tmp_path):
    """Add one arm; no default/MiMo/Qwen/reviewer bytes or arithmetic change."""
    old = FACTS['old_authority']
    assert {k:asdict(v) for k,v in lm.PROFILES.items()} == old['defaults']
    for k,v in old['named'].items():
        assert lm.NAMED_PROFILES[k][1].decode() == v
    for row in old['rows']:
        cs = lp.LayoutClient('', profile_selection=row['selection']).stages
        assert quote._versions(row['selection'], True) == row['versions']
        assert {k:asdict(c.prepare_request(old['envelope'])) for k,c in cs.items()} == row['wires']
    c=client(); w=c.prepare_request(envelope(),jpeg())
    assert w.payload['reasoning'] == {'effort':'medium'}
    assert w.payload['provider'] == {'order':['openai'],'allow_fallbacks':False,'require_parameters':True,
        'max_price':{'prompt':.20,'completion':.75}}
    assert set(w.payload)=={'model','messages','max_tokens','usage','response_format','provider','reasoning'}
    assert w.response_token_bound == 8192+16384 and c.profile.service_tier == ''
    assert c.reservation_rates == (.20,.75) and c.input_reservation_rate == .25
    assert w.bound_usd==(w.prompt_tokens_bound*.25+w.response_token_bound*.75)/1e6
    audit=json.loads(w.context_json)
    assert audit['supporting_reasoning_tokens']==16384 and audit['reasoning_budget_is_native_cap'] is False
    assert quote._versions(SELECTION)['version'] != quote._versions(lm.QWEN_SELECTION)['version']
    c._check_request(w)
    cache=OperationCache(tmp_path/'cache')
    oldwire=lp.LayoutClient('').stages['proposer'].prepare_request(envelope(),jpeg())
    token,_=cache.claim(oldwire.sha256);cache.finish(oldwire.sha256,token,response={'old':True})
    token,saved=cache.claim(w.sha256);assert token and saved is None;cache.release_unsent(w.sha256,token)
    for selection in ({'proposer':SELECTION['proposer']},{'proposer':SELECTION['proposer'],'reviewer':'mimo26-d1'}):
        with pytest.raises(ValueError):lp.LayoutClient('',profile_selection=selection)


def test_control_price_tier_native_fee_changes_refuse_before_hold(tmp_path):
    """Current exact catalogue/endpoint policy must pass before any reservation."""
    for fault in (None,'effort','default','mandatory','native_cap','canonical','created','parameters',
            'write','alias','unknown_fee','web_fee','discount','tier','bad_tier','high_price','catalog_price','native','implicit','provider','capacity'):
        s=Luna(); c=client(s); w=c.prepare_request(envelope())
        m=s.controls['models']['data'][0];r=s.route;p=r['pricing']
        if fault=='effort':m['reasoning']['supported_efforts'].remove('medium')
        elif fault=='default':m['reasoning']['default_enabled']='true'
        elif fault=='mandatory':m['reasoning']['mandatory']=True
        elif fault=='native_cap':m['reasoning']['supports_max_tokens']=True
        elif fault=='canonical':m['canonical_slug']='openai/gpt-6-luna-next'
        elif fault=='created':m['created']+=1
        elif fault=='parameters':r['supported_parameters'].remove('reasoning')
        elif fault=='write':p['input_cache_write']='0.000000126'
        elif fault=='alias':p['input_cache_creation_5m']='0.00000025'
        elif fault=='unknown_fee':p['new_fee']=0
        elif fault=='web_fee':p['web_search']='0.02'
        elif fault=='discount':p['discount']=.01
        elif fault=='tier':p['overrides'][0]['min_prompt_tokens']+=1
        elif fault=='bad_tier':p['overrides']=[True]
        elif fault=='high_price':p['overrides'][0]['completion']='0.000000751'
        elif fault=='catalog_price':m['pricing']['overrides'][0]['input_cache_read']='0.000000021'
        elif fault=='native':r['native_tools']['new_tool']={}
        elif fault=='implicit':r['supports_implicit_caching']=True
        elif fault=='provider':r['provider_name']='Azure'
        elif fault=='capacity':r['max_prompt_tokens']+=1
        ledger=Ledger(str(tmp_path/str(fault)),cap_usd=5)
        if fault is None:c.preflight(w.prompt_tokens_bound,w.response_token_bound)
        else:
            with pytest.raises(model.ModelError):c.call(w,ledger=ledger)
        assert not ledger.entries() and not s.posts,fault


def test_enabled_reasoning_full_bill_and_raw_audit_precede_every_refusal(tmp_path):
    """Metered native content is valid; malformed/hidden/extra meters settle then stop."""
    for fault in (None,'no_native','encrypted','full_write','cache_mix','high_tier','high_write',
            'missing_reason','bool_reason','reason_gt_completion','reason_gt_support','unknown_reason',
            'unmetered_native','top_hidden','extra_choice','missing_cache','alias','negative','typed_prompt','typed_completion','total',
            'unknown_meter','new_fee','cost','cost_detail','visible','tool','tier','missing_tier','length','malformed'):
        s=Luna();c=client(s);w=c.prepare_request(envelope(),jpeg(648,972))
        u=s.data['usage'];pd=u['prompt_tokens_details'];cd=u['completion_tokens_details'];msg=s.data['choices'][0]['message']
        if fault=='no_native':msg.pop('reasoning')
        elif fault=='encrypted':msg.pop('reasoning');msg['reasoning_details']=[{'type':'reasoning.encrypted','data':'OFFLINE','format':'openai-responses-v1'}]
        elif fault=='full_write':pd['cache_write_tokens']=100;u['cost']=.0000275
        elif fault=='cache_mix':pd.update(cached_tokens=50,cache_write_tokens=30);u['cost']=.00002125
        elif fault in ('high_tier','high_write'):
            u.update(prompt_tokens=272000,total_tokens=272030,cost=.0544225 if fault=='high_tier' else .0680225)
            if fault=='high_write':pd['cache_write_tokens']=272000
        elif fault=='missing_reason':cd.pop('reasoning_tokens')
        elif fault=='bool_reason':cd['reasoning_tokens']=True
        elif fault=='reason_gt_completion':cd['reasoning_tokens']=31
        elif fault=='reason_gt_support':cd['reasoning_tokens']=16385;u.update(completion_tokens=16400,total_tokens=16500,cost=.00821)
        elif fault=='unknown_reason':msg['thinking']='unmetered field'
        elif fault=='unmetered_native':cd['reasoning_tokens']=0
        elif fault=='top_hidden':s.data['reasoning_details']=[{'text':'outside native path'}]
        elif fault=='extra_choice':s.data['choices'].append({'message':{'reasoning':'other'}})
        elif fault=='missing_cache':pd.pop('cache_write_tokens')
        elif fault=='alias':pd['cache_read_input_tokens']=1
        elif fault=='negative':u['prompt_tokens']=-1
        elif fault=='typed_prompt':u['prompt_tokens']='100'
        elif fault=='typed_completion':u['completion_tokens']=True
        elif fault=='total':u['total_tokens']=129
        elif fault=='unknown_meter':cd['accepted_prediction_tokens']=0
        elif fault=='new_fee':s.data['search_cost']=0
        elif fault=='cost':u['cost']=.000024
        elif fault=='cost_detail':u['cost_details']={'new_fee':0}
        elif fault=='visible':cd['reasoning_tokens']=500;u.update(completion_tokens=9000,total_tokens=9100,cost=.00451)
        elif fault=='tool':msg['tool_calls']=[{'type':'function'}]
        elif fault=='tier':s.data['service_tier']='flex'
        elif fault=='missing_tier':s.data.pop('service_tier')
        elif fault=='length':s.data['choices'][0]['finish_reason']='length';msg['content']=None
        elif fault=='malformed':msg['content']='{'
        ledger=Ledger(str(tmp_path/str(fault)),cap_usd=5)
        if fault in (None,'no_native','encrypted','full_write','cache_mix','high_tier','high_write'):
            assert c.call(w,ledger=ledger).reasoning_tokens==12
        else:
            with pytest.raises(model.ModelError):c.call(w,ledger=ledger)
        assert ledger.spent()==u['cost'] and ledger.pending_usd()==0,fault
        assert ledger.entries('reasoning_control_audit'),fault
        assert s.posts[0]['data']==w.payload_json.encode()


def test_closed_wire_refuses_coherent_builder_tool_or_tier_activation(monkeypatch,tmp_path):
    """Re-signing a widened builder cannot buy native tools, a fallback, or hidden thinking."""
    original=lm.LayoutStageClient.prepare_request
    for i,(key,value) in enumerate([('tools',[]),('plugins',[]),('native_tools',{}),('service_tier','default'),
            ('reasoning',{'effort':'medium','exclude':True}),('reasoning',{'effort':'medium','max_tokens':16384}),
            ('reasoning',{'effort':'high'}),('model','openai/gpt-6-luna:floor'),
            ('provider',{'order':['openai'],'allow_fallbacks':True,'require_parameters':True})]):
        c=client(Luna())
        def changed(self,en,raster=None):
            w=original(self,en,raster);v=w.payload;v[key]=value;raw=lm._encoded(v).decode();h=hashlib.sha256(raw.encode()).hexdigest()
            ctx=json.loads(w.context_json);ctx['request_sha256']=h
            return replace(w,payload_json=raw,sha256=h,context_json=lm._encoded(ctx).decode())
        monkeypatch.setattr(lm.LayoutStageClient,'prepare_request',changed)
        w=c.prepare_request(envelope());l=Ledger(str(tmp_path/str(i)),cap_usd=5)
        with pytest.raises(ValueError):c.call(w,ledger=l)
        assert not l.entries() and not c._session.posts
        monkeypatch.setattr(lm.LayoutStageClient,'prepare_request',original)


def test_unknown_reported_bill_retains_original_reservation(tmp_path):
    """An unreconcilable cost keeps its original hold; no synthetic retry/cap reset."""
    for fault in ('missing','boolean','nonfinite'):
        s=Luna();c=client(s);w=c.prepare_request(envelope());ledger=Ledger(str(tmp_path/fault),cap_usd=5)
        if fault=='missing':s.data['usage'].pop('cost')
        elif fault=='boolean':s.data['usage']['cost']=False
        else:s.data['usage']['cost']=float('nan')
        with pytest.raises(model.UncertainBilling):c.call(w,ledger=ledger)
        assert ledger.spent()==0 and ledger.pending_usd()==w.bound_usd
        assert len(s.posts)==1 and not ledger.entries('reasoning_control_audit')


def test_standard_arm_cannot_retry_even_known_unsent_transport_failure(tmp_path):
    """This arm gets one attempt; no inherited three-attempt network retry policy."""
    s=Luna();c=client(s);w=c.prepare_request(envelope())
    assert c.max_retries==1
    s.error=model.requests.exceptions.ConnectTimeout('OFFLINE pre-dispatch failure')
    ledger=Ledger(str(tmp_path/'retry'),cap_usd=5)
    with pytest.raises(model.ModelError):c.call(w,ledger=ledger)
    assert len(s.posts)==1 and ledger.spent()==0 and ledger.pending_usd()==0
    with pytest.raises(ValueError):
        lm.LayoutStageClient('', 'proposer',profile_id=SELECTION['proposer'],max_retries=2)
    c.max_retries=2
    with pytest.raises(ValueError):c.prepare_request(envelope())
