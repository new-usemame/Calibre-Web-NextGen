"""Domain choices drive real compile/review/provenance, never prompt/source pins."""
import copy
import json
from dataclasses import replace
import pytest
import jsonschema
from cps.services.reflow import layout_domain as d,layout_construction as c,layout_requests as req,layout_ops as ops,layout_emphasis as style,layout_pipeline as lp,layout_wire as wire,native_codec
from cps.services.reflow.structural_ops import ContractError
from tests.unit.test_reflow_layout_ops import prepared
from tests.unit.test_reflow_boundary_construction import fixture_answer
from tests.unit.test_reflow_structural_ops import source
pytestmark=pytest.mark.unit


def domain_fixture(prep):
    return d.choice_fixture(json.loads(prep.contract_json),fixture_answer(prep))


def test_explicit_choices_reach_compile_review_and_native_without_rewriting_raw(source):
    book,doc,src,raw,prep=prepared(source);answer=domain_fixture(prep);original=copy.deepcopy(answer)
    envelope=d.proposal(prep);assert jsonschema.Draft202012Validator(envelope['response_schema']).is_valid(answer)
    request=lp.LayoutClient('').stages['proposer'].prepare_request(envelope,prep.raster)
    assert wire.source_view(request.payload['messages'][:-1])==json.loads(envelope['messages'][-1]['content'])
    lp.LayoutClient('').stages['proposer']._check_request(request)
    plan=prep.accept(book,doc,answer,source_page=src,raw_page=raw,prototype=True)
    body=plan.compile(book,doc,source_page=src,raw_page=raw,prototype=True)
    review=req.review(prep,plan);view=json.loads(review['messages'][-1]['content']);reply=dict(snapshot=view['snapshot'],accept=True,continuation_accept=False,problems=[],decisions='1'*d.decision_count(view['decisions']))
    selected=req.validate_review(prep,plan,reply).accepted_plan
    assert answer==original and json.loads(selected.construction_json)==original
    assert selected.compile(book,doc,source_page=src,raw_page=raw,prototype=True).body==body.body
    assert native_codec.loads(native_codec.dumps(selected))==selected


@pytest.mark.parametrize('bad',['missing_owner','bool_owner','foreign_role','wrong_member','missing_evidence','foreign_id','stale'])
def test_schema_and_authoritative_source_gate_refuse_distinct_invalid_choices(source,bad):
    book,doc,src,raw,prep=prepared(source);s=json.loads(prep.contract_json);answer=domain_fixture(prep)
    if bad=='missing_owner':answer['ownership'].pop()
    elif bad=='bool_owner':answer['ownership'][0][0]=True
    elif bad=='foreign_role':answer['groups'][0][0]=100
    elif bad=='wrong_member':answer['groups'][0][1][0]=9999
    elif bad=='missing_evidence':answer['groups'][0][1].pop()
    elif bad=='foreign_id':answer['emphasis']=[[9999,9999]]
    elif bad=='stale':answer['snapshot']='s'*64
    with pytest.raises(ContractError):prep.accept(book,doc,answer,source_page=src,raw_page=raw,prototype=True)


def test_each_ordered_bit_is_required_bound_and_false_local_cannot_approve(source):
    book,doc,src,raw,prep=prepared(source);answer=domain_fixture(prep);plan=prep.accept(book,doc,answer,source_page=src,raw_page=raw,prototype=True)
    v=json.loads(req.review(prep,plan)['messages'][-1]['content']);K=d.decision_count(v['decisions']);r=dict(snapshot=v['snapshot'],accept=True,continuation_accept=False,problems=[],decisions='1'*K)
    for bits in [r['decisions'][:-1],r['decisions']+'1',False,'x'*K,'0'*K]:
        with pytest.raises(ContractError):req.validate_review(prep,plan,dict(r,decisions=bits))
    changed=copy.deepcopy(answer);changed['groups'][0][0]=d.ROLES.index('quote')
    other=prep.accept(book,doc,changed,source_page=src,raw_page=raw,prototype=True)
    with pytest.raises(ContractError):req.validate_review(prep,other,r)
    assert req.validate_review(prep,plan,dict(r,accept=False,problems=['paragraph_split'],decisions='0'*K)).accepted_plan is None


def test_invalid_optional_aliases_cannot_withdraw_valid_structure_or_change_raw(source):
    book,doc,src,raw,prep=prepared(source);response=domain_fixture(prep);response['emphasis']=[[9999,9999]]
    structural,record=style.split(prep,response)
    assert record['status']=='rejected'
    plan=prep.accept(book,doc,structural,source_page=src,raw_page=raw,prototype=True)
    plan=replace(plan,construction_json=ops._json(response))
    assert plan.compile(book,doc,source_page=src,raw_page=raw,prototype=True).body
    v=json.loads(req.review(prep,plan)['messages'][-1]['content'])
    r=dict(snapshot=v['snapshot'],accept=True,continuation_accept=False,problems=[],decisions='1'*d.decision_count(v['decisions']))
    selected=req.validate_review(prep,plan,r).accepted_plan
    assert json.loads(selected.construction_json)==response


def test_empty_unknown_resources_remain_explicit_and_no_membership_is_invented():
    s=ops.atoms.prepare('<p>Even <a href="source.xhtml#glyph"><img src="glyph.png" /></a> more.</p>',{},0)
    p=ops.PreparedLayout(0,'',s['snapshot'],s['identity'],ops._json(s),'{}',b'')
    v=d.expand_source(d.source_view(p));slot=next(a for a in v['atoms'] if a['text']=='')
    assert slot['membership']==dict(state='unknown',lines=[]) and {r['type'] for r in slot['resources']}=={'a','img'}
    from tests.unit.test_reflow_boundary_construction import construction_from_groups
    raw=construction_from_groups(s,[dict(role='paragraph',ranges=[['a0','a2']])]);r=d.choice_fixture(s,raw)
    assert d.expand(s,r)==raw
    r['groups'][0][1][0]=0
    with pytest.raises(ContractError):d.expand(s,r)


def test_request_schema_union_cannot_authorize_wrong_positional_role(source):
    book,doc,src,raw,prep=prepared(source);response=domain_fixture(prep);response['groups'][0][0]=[]
    assert jsonschema.Draft202012Validator(d.proposal(prep)['response_schema']).is_valid(response)
    with pytest.raises(ContractError,match='domain group'):
        prep.accept(book,doc,response,source_page=src,raw_page=raw,prototype=True)


def test_current_paid_contract_requires_optional_choice_field_even_when_empty(source):
    book,doc,src,raw,prep=prepared(source);response=domain_fixture(prep);response.pop('emphasis')
    with pytest.raises(ContractError,match='complete domain'):
        prep.accept(book,doc,response,source_page=src,raw_page=raw,prototype=True)
    with pytest.raises(ContractError,match='complete domain'):
        style.split(prep,response)
    valid=domain_fixture(prep)
    plan=prep.accept(book,doc,valid,source_page=src,raw_page=raw,prototype=True)
    forged=replace(plan,construction_json=ops._json(response))
    with pytest.raises(ContractError,match='complete domain'):
        forged.compile(book,doc,source_page=src,raw_page=raw,prototype=True)
