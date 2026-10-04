"""Opt-in controls and billing/review boundaries, never source-quality claims."""
import copy
import json
from dataclasses import replace
import pytest
from jsonschema import validate, ValidationError
from cps.services.reflow import (layout_glm as glm, layout_model as lm,
    layout_pipeline as lp, layout_requests as req, layout_ops as ops, model)
from cps.services.reflow.ledger import Ledger
from tests.unit.test_reflow_layout_transport import envelope, Session
from tests.unit.test_reflow_layout_ops import boundary_source, answer
from tests.unit.test_reflow_structural_ops import source
from tests.unit.test_reflow_range_choices import range_fixture, verdict

pytestmark = pytest.mark.unit


def controls():
    pricing={k:str(v) for k,v in glm.CEILINGS.items()}
    route=dict(model_id=glm.MODEL,name='DeepInfra | '+glm.CANONICAL,
        provider_name='DeepInfra',tag=glm.ROUTE,quantization='fp4',native_tools={},
        supports_implicit_caching=False,context_length=1048576,max_prompt_tokens=None,
        max_completion_tokens=131072,status=0,
        supported_parameters=sorted(glm.PARAMETERS),
        pricing=dict(pricing,discount=.5))
    data=dict(id=glm.MODEL,created=1787752741,architecture=dict(input_modalities=['text','image']),endpoints=[route])
    record=dict(id=glm.MODEL,canonical_slug=glm.CANONICAL,created=1787752741,context_length=1048576,
        supported_parameters=route['supported_parameters'],reasoning=dict(mandatory=True,default_enabled=True,
            supported_efforts=['max','high','low'],default_effort='max'))
    return data,record


class FixtureSession(Session):
    def __init__(self):
        super().__init__();self.endpoint,self.record=controls()
        self.data=dict(model=glm.MODEL,provider='DeepInfra',service_tier='default',
            usage=dict(prompt_tokens=100,completion_tokens=30,total_tokens=130,cost=.00003,
                prompt_tokens_details=dict(cached_tokens=0,cache_write_tokens=100),
                completion_tokens_details=dict(reasoning_tokens=20)),
            choices=[dict(finish_reason='stop',message=dict(content='{"items":[]}',reasoning='accounted native text'))])
    def get(self,url,**kwargs):
        return self.response(dict(data=[self.record]) if url.endswith('/models') else dict(data=self.endpoint))


def test_closed_pair_wire_and_unknown_complete_control_never_dispatches(tmp_path):
    client=lp.LayoutClient('fixture',profile_selection=lm.GLM_SELECTION)
    proposer=client.stages['proposer'];session=FixtureSession();proposer._session=session
    wire=proposer.prepare_request(envelope())
    assert wire.payload['reasoning']=={'effort':'low'}
    assert wire.response_token_bound==8192+65536
    assert wire.payload['provider']['order']==['deepinfra/fp4']
    assert wire.payload['provider']['allow_fallbacks'] is False
    with pytest.raises(ValueError):lm.LayoutStageClient('fixture','proposer',profile_id=glm.PROFILE_ID,max_retries=2)
    with pytest.raises(ValueError):lp.LayoutClient('',profile_selection={'proposer':glm.PROFILE_ID})
    for key in ('input_cache_write','request','image','internal_reasoning'):
        endpoint,_=controls();del endpoint['endpoints'][0]['pricing'][key];session.endpoint=endpoint
        ledger=Ledger(str(tmp_path/key),cap_usd=1)
        with pytest.raises(model.ModelError):proposer.call(wire,ledger=ledger)
        assert session.posts==[] and ledger.pending_usd()==ledger.spent()==0
    for field,value in [('request','.01'),('input_cache_write','.0000002'),('overrides',[{'min_prompt_tokens':10,'prompt':'.0000003'}]),('discount',.75),('input_cache_creation_5m','.0000001')]:
        endpoint,_=controls();endpoint['endpoints'][0]['pricing'][field]=value;session.endpoint=endpoint
        with pytest.raises(model.ModelError):proposer.call(wire,ledger=Ledger(str(tmp_path/field),cap_usd=1))
        assert session.posts==[]
    changed=wire.payload;changed['reasoning']['max_tokens']=1
    with pytest.raises(ValueError):glm.wire(changed)


@pytest.mark.parametrize('fault',['valid','counts','native','reason','fee','cost','length','json','route','cache','alias','choices','schema','duplicate','unknown_bill'])
def test_full_known_bill_settles_before_every_refusal_unknown_stays_held(tmp_path,fault):
    session=FixtureSession();client=lm.LayoutStageClient('fixture','proposer',profile_id=glm.PROFILE_ID,session=session)
    wire=client.prepare_request(envelope());ledger=Ledger(str(tmp_path/'ledger'),cap_usd=1)
    if fault=='counts':session.data['usage']['prompt_tokens']=-100
    if fault=='native':session.data['choices'][0]['message']['encrypted_other']='hidden'
    if fault=='reason':del session.data['usage']['completion_tokens_details']['reasoning_tokens']
    if fault=='fee':session.data['usage']['tool_fee']=.01
    if fault=='cost':session.data['usage']['cost']=.00004
    if fault=='length':session.data['choices'][0]['finish_reason']='length'
    if fault=='json':session.data['choices'][0]['message']['content']='incomplete'
    if fault=='duplicate':session.data['choices'][0]['message']['content']='{"items":["lost"],"items":[]}'
    if fault=='schema':session.data['choices'][0]['message']['content']='{"items":[],"foreign":true}'
    if fault=='route':session.data['provider']='Other'
    if fault=='cache':session.data['usage']['prompt_tokens_details']['cached_tokens']=1
    if fault=='alias':session.data['usage']['prompt_tokens_details']['cache_creation_tokens']=90
    if fault=='choices':session.data['choices'].append(copy.deepcopy(session.data['choices'][0]))
    if fault=='unknown_bill':del session.data['usage']['cost']
    if fault=='valid':
        got=client.call(wire,ledger=ledger);assert got.reasoning_tokens==20
    else:
        with pytest.raises(model.ModelError):client.call(wire,ledger=ledger)
    assert len(session.posts)==1
    if fault=='unknown_bill':assert ledger.pending_usd()==wire.bound_usd and ledger.spent()==0
    else:assert ledger.pending_usd()==0 and ledger.spent()==session.data['usage']['cost']
    if fault in ('counts','native','reason','fee','cost','route','cache','alias','choices'):
        audits=ledger.entries('reasoning_control_audit');assert len(audits)==1 and audits[0]['accepted'] is False


def test_owning_review_eligibility_preserves_eligible_true_and_all_source_bits(boundary_source):
    book,doc,sources,raws=boundary_source;plans=[]
    for n in range(2):
        kw=dict(source_page=sources[n],raw_page=raws[n],previous_source=sources[n-1] if n else None,previous_raw=raws[n-1] if n else None)
        p=ops.prepare(book,doc,n,sources[n],raws[n],previous_source=kw['previous_source'],previous_raw=kw['previous_raw'])
        a=answer(p);a['continuation']=bool(n);plans.append(p.accept(book,doc,a,prototype=True,**kw))
    p=plans[1].prepared
    old=req.review(p,plans[1],plans[0]);new=req.review(p,plans[1],plans[0],profile_selection=lm.GLM_SELECTION)
    assert new['messages'][-1]['content']==old['messages'][-1]['content']
    assert new['response_schema']==old['response_schema']
    assert new['response_schema']['properties']['continuation_accept']=={'type':'boolean'}
    # Missing candidate context makes true impossible according to the unchanged validator.
    impossible=req.review(p,plans[1],profile_selection=lm.GLM_SELECTION)
    assert impossible['response_schema']['properties']['continuation_accept']=={'type':'boolean','enum':[False]}
    response=dict(snapshot=json.loads(impossible['messages'][-1]['content'])['snapshot'],accept=True,continuation_accept=True,problems=[])
    with pytest.raises(ValidationError):validate(response,impossible['response_schema'])
    with pytest.raises(ops.ContractError):req.validate_review(p,plans[1],response)
    assert glm.continuation_eligibility({},None) is None
    assert glm.continuation_eligibility({'continuation':True},None) is None
    assert req.review(p,plans[1],plans[0])==old


def test_eligible_range_incoming_keeps_every_independent_decision(boundary_source):
    book,doc,sources,raws=boundary_source
    left=ops.prepare(book,doc,0,sources[0],raws[0])
    right=ops.prepare(book,doc,1,sources[1],raws[1],previous_source=sources[0],previous_raw=raws[0])
    previous=left.accept(book,doc,range_fixture(left),source_page=sources[0],raw_page=raws[0])
    incoming={'continue':True,'previous':3,'current':0,'hyphen':'keep'}
    candidate=right.accept(book,doc,range_fixture(right,incoming=incoming),source_page=sources[1],raw_page=raws[1],previous_source=sources[0],previous_raw=raws[0])
    old=req.review(right,candidate,previous)
    requested=req.review(right,candidate,previous,profile_selection=lm.GLM_SELECTION)
    assert requested['response_schema']==old['response_schema']
    assert requested['messages'][-1]['content']==old['messages'][-1]['content']
    response=verdict(candidate,previous);response['continuation_accept']=True
    validate(response,requested['response_schema'])
    accepted=req.validate_review(right,candidate,response,previous).accepted_plan
    assert accepted is not None
    for i in range(len(response['decisions'])):
        refused=dict(response,decisions=response['decisions'][:i]+'0'+response['decisions'][i+1:])
        with pytest.raises(ops.ContractError):req.validate_review(right,candidate,refused,previous)
