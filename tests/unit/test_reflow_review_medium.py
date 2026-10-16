"""Independent reviewer mechanics; synthetic meters confer no quality."""
import copy,json
from decimal import Decimal
import pytest
from cps.services.reflow import layout_model as lm,layout_pipeline as lp,layout_quote as quote,layout_luna_standard as meter,model
from cps.services.reflow import layout_eligibility as eligibility,_layout_atoms as atoms,layout_ranges
from cps.services.reflow.ledger import Ledger
from cps.services.reflow.operation_cache import OperationCache
from tests.unit.test_reflow_luna_standard import Luna
from tests.unit.test_reflow_layout_transport import envelope,jpeg
pytestmark=pytest.mark.unit

def reviewer(s=None):return lp.LayoutClient('OFFLINE-fixture',profile_selection=lm.LUNA_REVIEW_SELECTION,session=s).stages['reviewer']

def test_review_support_independent_immutable_cache_identity(tmp_path):
    c=reviewer();old=lp.LayoutClient('',profile_selection=lm.LUNA_STANDARD_SELECTION).stages['reviewer']
    w=c.prepare_request(envelope(),jpeg());ow=old.prepare_request(envelope(),jpeg());a=json.loads(w.context_json)
    assert w.response_token_bound==w.payload['max_tokens']==c.profile.completion_cap==20480
    assert a['visible_answer_tokens']==4096 and a['supporting_reasoning_tokens']==16384
    assert a['allocation_version']==meter.REVIEW_ALLOCATION_VERSION and not a['reasoning_budget_is_native_cap']
    assert w.payload['reasoning']=={'effort':'medium'} and 'service_tier' not in w.payload
    assert w.payload['provider']=={'order':['openai'],'allow_fallbacks':False,'require_parameters':True,'max_price':{'prompt':.20,'completion':.75}}
    assert c.input_reservation_rate==.25 and c.reservation_rates==(.20,.75) and c.max_retries==1
    c._check_request(w);cache=OperationCache(tmp_path/'cache');t,_=cache.claim(ow.sha256);cache.finish(ow.sha256,t,response={'negative':True})
    t,saved=cache.claim(w.sha256);assert t and saved is None;cache.release_unsent(w.sha256,t)
    assert quote._versions(lm.LUNA_REVIEW_SELECTION)['version']!=quote._versions(lm.LUNA_STANDARD_SELECTION)['version']
    for sel in ({'reviewer':meter.REVIEWER_ID},{'proposer':'qwen38-d1','reviewer':meter.REVIEWER_ID}):
        with pytest.raises(ValueError):lp.LayoutClient('',profile_selection=sel)
    with pytest.raises(ValueError):lm.LayoutStageClient('', 'reviewer',profile_id=meter.REVIEWER_ID,max_retries=2)
    c.max_retries=2
    with pytest.raises(ValueError):c.prepare_request(envelope())

def test_review_bill_first_reason_visible_hidden_route_finish(tmp_path):
    for fault in (None,'write','high_write','opaque','reason','visible','completion','negative','hidden','fee','tier','length','malformed','choices'):
        s=Luna();c=reviewer(s);w=c.prepare_request(envelope(),jpeg(648,972));u=s.data['usage'];m=s.data['choices'][0]['message']
        pt,ct,r=100,20480,16384
        if fault=='reason':ct,r=16485,16385
        if fault=='visible':ct,r=16385,12288
        if fault=='completion':ct=20481
        if fault=='high_write':pt=272000
        wr=pt if fault in ('write','high_write') else 0;rates=meter.HIGH_PRICES if pt>=272000 else meter.PRICES
        cost=Decimal(pt-wr)*Decimal(rates['prompt'])+Decimal(wr)*Decimal(rates['input_cache_write'])+Decimal(ct)*Decimal(rates['completion'])
        u.update(prompt_tokens=pt,completion_tokens=ct,total_tokens=pt+ct,cost=float(cost),prompt_tokens_details=dict(cached_tokens=0,cache_write_tokens=wr),completion_tokens_details=dict(reasoning_tokens=r))
        if fault=='opaque':m['reasoning_details']=[{'type':'reasoning.encrypted','data':'SYNTHETIC opaque','format':'openai-responses-v1'}]
        elif fault=='negative':u['prompt_tokens']=-1
        elif fault=='hidden':m['thinking']='SYNTHETIC unknown'
        elif fault=='fee':s.data['hidden_fee']=0
        elif fault=='tier':s.data['service_tier']='flex'
        elif fault=='length':s.data['choices'][0]['finish_reason']='length';m['content']=None
        elif fault=='malformed':m['content']='{'
        elif fault=='choices':s.data['choices'].append(copy.deepcopy(s.data['choices'][0]))
        l=Ledger(str(tmp_path/str(fault)),cap_usd=5)
        if fault in (None,'write','high_write','opaque'):assert c.call(w,ledger=l).reasoning_tokens==16384
        else:
            with pytest.raises(model.ModelError):c.call(w,ledger=l)
        assert l.spent()==float(cost) and l.pending_usd()==0 and len(s.posts)==1,fault
        a=l.entries('reasoning_control_audit');assert len(a)==1
        if a[0]['accepted']:assert not a[0]['full_internal_reasoning_observed']
        else:assert 'finding' in a[0]
    s=Luna();s.data['usage'].pop('cost');c=reviewer(s);w=c.prepare_request(envelope());l=Ledger(str(tmp_path/'unknown'),cap_usd=5)
    with pytest.raises(model.UncertainBilling):c.call(w,ledger=l)
    assert l.pending_usd()==w.bound_usd and not l.entries('reasoning_control_audit')

def test_only_impossible_source_furniture_removed_true_protected_retained():
    from jsonschema import validate,ValidationError
    s=atoms.prepare('<p>Body words</p><p>156</p>','offline',0);schema=layout_ranges.schema(s)['properties']['groups']['items'];ordinary={'role':'source_furniture','ranges':[[2,2]]}
    validate(ordinary,schema);r=eligibility.universe(s);assert r['absence_proven']
    assert r['roles']==[x for x in schema['properties']['role']['enum'] if x!='source_furniture']
    narrow=copy.deepcopy(schema);narrow['properties']['role']['enum']=r['roles']
    with pytest.raises(ValidationError):validate(ordinary,narrow)
    a=dict(snapshot=s['snapshot'],groups=[dict(role='paragraph',ranges=[['a0','a1']]),dict(role='source_furniture',ranges=[['a2','a2']])],joins=[])
    with pytest.raises(atoms.Invalid):atoms.validate(s,a)
    protected=atoms.prepare('<p>Body</p><figure><img src="images/folio.jpg" /></figure>','offline',0)
    assert not eligibility.universe(protected)['source_furniture_excluded']
    a=dict(snapshot=protected['snapshot'],groups=[dict(role='paragraph',ranges=[['a0','a0']]),dict(role='source_furniture',ranges=[['a1','a1']])],joins=[])
    assert atoms.validate(protected,a)[1][0]=='source_furniture'
    notice=atoms.prepare('<p>Body <span class="source-glyph">?</span></p><p class="source-evidence-notice"><img src="x" /></p>','offline',0)
    assert eligibility.universe(notice)['absence_proven'] and atoms.protected_constraints(notice)[-1]['allowed_roles']==['source']

def test_review_current_controls_closed_wire_no_retry(tmp_path):
    """Wrong current controls or native activation refuse before paid reservation."""
    for fault in ('effort','fees','tier','tools','fallback','exclude','numeric_cap'):
        s=Luna();c=reviewer(s);w=c.prepare_request(envelope())
        if fault=='effort':s.controls['models']['data'][0]['reasoning']['supported_efforts'].remove('medium')
        elif fault=='fees':s.route['pricing']['new_fee']=0
        elif fault=='tier':s.route['pricing']['overrides'][0]['completion']='0.000000751'
        else:
            payload=w.payload
            if fault=='tools':payload['tools']=[]
            elif fault=='fallback':payload['provider']['allow_fallbacks']=True
            elif fault=='exclude':payload['reasoning']['exclude']=True
            else:payload['reasoning']['max_tokens']=16384
            from dataclasses import replace
            import hashlib
            raw=lm._encoded(payload).decode();w=replace(w,payload_json=raw,sha256=hashlib.sha256(raw.encode()).hexdigest())
        l=Ledger(str(tmp_path/fault),cap_usd=5)
        with pytest.raises((model.ModelError,ValueError)):c.call(w,ledger=l)
        assert not l.entries() and not s.posts
    s=Luna();s.error=model.requests.exceptions.ConnectTimeout('OFFLINE unsent');c=reviewer(s);w=c.prepare_request(envelope());l=Ledger(str(tmp_path/'retry'),cap_usd=5)
    with pytest.raises(model.ModelError):c.call(w,ledger=l)
    assert len(s.posts)==1 and l.spent()==l.pending_usd()==0
