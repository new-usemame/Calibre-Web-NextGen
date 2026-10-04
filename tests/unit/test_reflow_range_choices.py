"""NEW offline declared choices through request/compiler/review/native seams."""
import copy
import json
from dataclasses import replace
import pytest
import jsonschema
from cps.services.reflow import layout_ops as ops,layout_requests as req,layout_pipeline as lp,layout_wire as wire,native_codec
from cps.services.reflow.structural_ops import ContractError
from tests.unit.test_reflow_layout_ops import prepared,answer,boundary_source
from tests.unit.test_reflow_structural_ops import source
from tests.unit.test_reflow_layout_review import candidates
from tests.unit.test_reflow_layout_note_bindings import note_source
pytestmark=pytest.mark.unit
VERSION='layout-range-choices-1'


def range_fixture(prep, groups=None, *, incoming=None, joins=None, emphasis=None, note_bindings=None):
    """Declare new offline semantics; no old reply conversion or model adoption."""
    s=json.loads(prep.contract_json);ids=[a['id'] for a in s['atoms']]
    groups=groups if groups is not None else answer(prep)['groups']
    chosen=[dict(role=g['role'],ranges=[[ids.index(first),ids.index(last)] for first,last in g['ranges']]) for g in groups]
    raw=dict(contract=VERSION,snapshot=s['snapshot'],groups=chosen,joins=joins or [],incoming=incoming or {'continue':False,'previous':None,'current':None,'hyphen':None},emphasis=emphasis or [])
    if prep.model_view().get('note_candidates'):raw['note_bindings']=note_bindings or []
    return raw


def verdict(plan, previous=None):
    from cps.services.reflow import layout_ranges as x
    v=json.loads(req.review(plan.prepared,plan,previous)['messages'][-1]['content'])
    return dict(snapshot=v['snapshot'],accept=True,continuation_accept=False,problems=[],decisions='1'*x.decision_count(v['decisions']))


def test_new_request_raw_compile_review_wire_and_native_preserve_exact_provenance(source):
    book,doc,src,raw,p=prepared(source);chosen=range_fixture(p);original=copy.deepcopy(chosen)
    envelope=req.proposal(p)
    assert jsonschema.Draft202012Validator(envelope['response_schema']).is_valid(chosen)
    request=lp.LayoutClient('').stages['proposer'].prepare_request(envelope,p.raster)
    assert wire.source_view(request.payload['messages'][:-1])==json.loads(envelope['messages'][-1]['content'])
    plan=p.accept(book,doc,chosen,source_page=src,raw_page=raw,prototype=True)
    body=plan.compile(book,doc,source_page=src,raw_page=raw,prototype=True)
    selected=req.validate_review(p,plan,verdict(plan)).accepted_plan
    assert selected.compile(book,doc,source_page=src,raw_page=raw,prototype=True).body==body.body
    assert chosen==original and json.loads(selected.construction_json)==original
    assert native_codec.loads(native_codec.dumps(selected))==selected
    view=json.loads(req.proposal(p)['messages'][-1]['content'])
    assert view['domain']==VERSION and view['source_snapshot']==p.snapshot_id


def test_complete_range_and_singleton_equivalence_arbitrary_permutation_and_refusals(source):
    book,doc,src,raw,p=prepared(source);a=range_fixture(p)
    accept=lambda r:p.accept(book,doc,r,source_page=src,raw_page=raw,prototype=True)
    initial=accept(a);b=copy.deepcopy(a)
    for g in b['groups']:g['ranges']=[[i,i] for f,l in g['ranges'] for i in range(f,l+1)]
    assert accept(b).answer_json==initial.answer_json
    assert accept(b).construction_json!=initial.construction_json
    from cps.services.reflow import layout_ranges as x
    s=json.loads(p.contract_json)
    ordered=copy.deepcopy(b);ordered['groups'][0]['ranges'].reverse()
    derived=x.compile(s,ordered)
    assert derived['groups'][0]['ranges']==[['a'+str(i),'a'+str(i)] for i,_ in ordered['groups'][0]['ranges']]
    for damage in ['missing','gap','bool','foreign','previous','unused','duplicate','empty','reverse','untyped','role','oldorder','snapshot']:
        bad=copy.deepcopy(b)
        if damage=='missing':bad['groups'][0].pop('ranges')
        elif damage=='gap':bad['groups'][0]['ranges'].pop()
        elif damage=='bool':bad['groups'][0]['ranges'][0][0]=False
        elif damage=='foreign':bad['groups'][0]['ranges'][0]=[99999,99999]
        elif damage=='previous':bad['groups'][0]['ranges'][0]=[-1,-1]
        elif damage=='unused':bad['groups'].append(dict(role='paragraph',ranges=[]))
        elif damage=='duplicate':bad['groups'][0]['ranges'].append(bad['groups'][0]['ranges'][0])
        elif damage=='empty':bad['groups']=[]
        elif damage=='reverse':bad['groups'][0]['ranges'][0]=[1,0]
        elif damage=='untyped':bad['groups'][0]['ranges'][0][0]='0'
        elif damage=='role':bad['groups'][0]['role']='guessed'
        elif damage=='oldorder':bad['groups'][0]['order']='source'
        else:bad['snapshot']='f'*64
        with pytest.raises(ContractError):accept(bad)
    invalid=copy.deepcopy(a);invalid['groups'][0]['ranges'][0][0]=True
    assert not jsonschema.Draft202012Validator(req.proposal(p)['response_schema']).is_valid(invalid)
    # A previous-scope integer outside current sequence cannot acquire ownership.
    s['previous']={'atoms':[dict(id='a99999',text='context only')]}
    foreign=copy.deepcopy(a);foreign['groups'][0]['ranges'][0]=[99999,99999]
    with pytest.raises(ContractError):x.compile(s,foreign)


def test_every_local_and_incoming_verdict_is_bound_without_categorical_shortcut(source):
    book,doc,src,raw,p=prepared(source);a=range_fixture(p)
    plan=p.accept(book,doc,a,source_page=src,raw_page=raw,prototype=True);r=verdict(plan)
    for bits in [r['decisions'][:-1],r['decisions']+'1','x'*len(r['decisions']),'0'+r['decisions'][1:]]:
        with pytest.raises(ContractError):req.validate_review(p,plan,dict(r,decisions=bits))
    a['groups'][0]['role']='quote';other=p.accept(book,doc,a,source_page=src,raw_page=raw,prototype=True)
    with pytest.raises(ContractError):req.validate_review(p,other,r)
    assert req.validate_review(p,plan,dict(r,decisions=r['decisions'][:-1]+'0')).accepted_plan is not None
    assert req.validate_review(p,plan,dict(r,accept=False,problems=['paragraph_split'],decisions='0'*len(r['decisions']))).accepted_plan is None


def test_empty_glyph_all_memberships_unknown_and_chosen_permutation_are_exact():
    s=ops.atoms.prepare('<p>Even <a href="source.xhtml#glyph"><img src="glyph.png" /></a> more.</p>',{},0)
    s['printed_lines']=[dict(source_line=23,ranges=[['a0','a2']],bbox=[0,0,10,10]),dict(source_line=24,ranges=[['a0','a0']],bbox=[0,0,10,10])]
    s['snapshot']=ops.atoms.digest({k:v for k,v in s.items() if k!='snapshot'})
    p=ops.PreparedLayout(0,'',s['snapshot'],s['identity'],ops._json(s),'{}',b'')
    from cps.services.reflow import layout_ranges as x,layout_domain as d
    a=range_fixture(p,[dict(role='paragraph',ranges=[['a0','a2']])]);w,b=x.expand(s,a)
    expanded=d.expand_source(x.source_view(p));slot=expanded['atoms'][1]
    assert slot['text']=='' and slot['membership']['lines']==[23]
    assert {r['type'] for r in slot['resources']}=={'a','img'}
    rows=x.decisions(s,b,a)
    assert rows[0]['memberships'][0]['lines']==[23,24]
    a['groups'][0]['ranges']=[[2,2],[1,1],[0,0]];_,b=x.expand(s,a)
    assert b['groups'][0]['ranges']==[['a2','a2'],['a1','a1'],['a0','a0']]
    s['printed_lines']=[]
    s['snapshot']=ops.atoms.digest({k:v for k,v in s.items() if k!='snapshot'});a['snapshot']=s['snapshot'];b['snapshot']=s['snapshot']
    assert all(r['state']=='unknown' and r['lines']==[] for r in x.decisions(s,b,a)[0]['memberships'])


def test_false_incoming_uses_actual_candidate_and_true_requires_exact_scoped_endpoints(candidates):
    book,left,right=candidates
    from cps.services.reflow import layout_ranges as x
    s=json.loads(right.prepared.contract_json);a=range_fixture(right.prepared,json.loads(right.answer_json)['groups'])
    plan=replace(right,answer_json=ops._json(x.compile(s,a)),construction_json=ops._json(a))
    v=verdict(plan,left);assert req.validate_review(plan.prepared,plan,v,left).accepted_plan
    env=req.review(plan.prepared,plan,left);view=json.loads(env['messages'][-1]['content'])
    assert view['decisions']['incoming']==[0]
    _,prior,_=req._previous_material(plan.prepared,s,left)
    rows=x.decisions(s,json.loads(plan.answer_json),a,prior)
    assert rows[-1]['previous_context_state']=='supplied' and rows[-1]['previous']['id']=='a3'
    assert x.decisions(s,json.loads(plan.answer_json),a)[-1]['previous_context_state']=='unknown'
    with pytest.raises(ContractError):req.validate_review(plan.prepared,plan,dict(v,continuation_accept=True),left)
    a['incoming']={'continue':True,'previous':3,'current':0,'hyphen':'keep'}
    true=replace(plan,answer_json=ops._json(x.compile(s,a)),construction_json=ops._json(a))
    vr=verdict(true,left);vr['continuation_accept']=True
    assert req.validate_review(true.prepared,true,vr,left).continuation_accept
    with pytest.raises(ContractError):req.validate_review(true.prepared,true,dict(vr,decisions=vr['decisions'][:-1]+'0'),left)
    with pytest.raises(ContractError):req.validate_review(true.prepared,true,dict(verdict(true),continuation_accept=True))
    a['incoming']['current']=1
    with pytest.raises(ContractError):x.compile(s,a)
    a['incoming']['current']=0;a['incoming']['previous']=0;a['incoming']['hyphen']=None
    bad=replace(plan,answer_json=ops._json(x.compile(s,a)),construction_json=ops._json(a))
    with pytest.raises(ContractError):req.review(bad.prepared,bad,left)


def test_optional_refusal_retains_new_raw_and_legacy_strict_provenance(source):
    book,doc,src,raw,p=prepared(source)
    from cps.services.reflow import layout_emphasis as style,layout_domain as d
    from tests.unit.test_reflow_layout_domain import domain_fixture
    old=domain_fixture(p);oldplan=p.accept(book,doc,old,source_page=src,raw_page=raw,prototype=True)
    a=range_fixture(p);a['emphasis']=[[99999,99999]]
    structural,record=style.split(p,a);assert record['status']=='rejected'
    plan=p.accept(book,doc,structural,source_page=src,raw_page=raw,prototype=True)
    plan=replace(plan,construction_json=ops._json(a));assert req.validate_review(p,plan,verdict(plan)).accepted_plan
    assert json.loads(plan.construction_json)==a and json.loads(oldplan.construction_json)==old
    broken=copy.deepcopy(old);broken['groups'][0][1]=[]
    with pytest.raises(ContractError):d.expand(json.loads(p.contract_json),broken)


def test_new_local_join_needs_same_group_and_independent_lexical_verdict(boundary_source):
    from cps.services.reflow import layout_lexical as lexical,layout_ranges as x
    book,doc,sources,raws=boundary_source
    p=ops.prepare(book,doc,0,sources[0],raws[0]);a=range_fixture(p,joins=[dict(left=1,right=2,hyphen='keep')])
    plan=p.accept(book,doc,a,source_page=sources[0],raw_page=raws[0])
    selected=req.validate_review(p,plan,verdict(plan)).accepted_plan
    rows=lexical.candidates([selected]);assert len(rows)==1 and rows[0]['left_text']=='inter-'
    changed,refused=lexical.apply([selected],dict(decisions=[dict(id=rows[0]['id'],decision='drop')]),lexical.request([selected])['source_identity'])
    assert not refused and json.loads(changed[0].construction_json)==a
    assert json.loads(changed[0].answer_json)['joins'][0]['hyphen']=='drop'
    changed[0].compile(book,doc,source_page=sources[0],raw_page=raws[0])
    separate,refused=lexical.apply([selected],dict(decisions=[dict(id=rows[0]['id'],decision='separate')]),lexical.request([selected])['source_identity'])
    assert not separate and refused=={0}
    bad=range_fixture(p,[dict(role='paragraph',ranges=[['a0','a1']]),dict(role='paragraph',ranges=[['a2','a3']])]+answer(p)['groups'][1:],joins=a['joins'])
    with pytest.raises(ContractError,match='adjacent'):x.compile(json.loads(p.contract_json),bad)


def test_new_association_has_its_own_bound_verdict_and_original_resource_truth(note_source):
    from cps.services.reflow import layout_ranges as x
    book,doc,_,src,raw,p,old=note_source
    a=range_fixture(p,old['groups'],note_bindings=old['note_bindings'])
    plan=p.accept(book,doc,a,source_page=src,raw_page=raw);r=verdict(plan)
    s=json.loads(p.contract_json);rows=x.decisions(s,json.loads(plan.answer_json),a)
    index=next(i for i,row in enumerate(rows) if row['kind']=='association')
    assert rows[-1]['kind']=='continuation' and rows[index]['binding']==a['note_bindings'][0]
    v=json.loads(req.review(p,plan)['messages'][-1]['content']);assert v['decisions']['associations']==[0,0]
    bits=r['decisions'][:index]+'0'+r['decisions'][index+1:]
    with pytest.raises(ContractError,match='local'):req.validate_review(p,plan,dict(r,decisions=bits))
    selected=req.validate_review(p,plan,r).accepted_plan
    compiled=selected.compile(book,doc,source_page=src,raw_page=raw)
    assert json.loads(compiled.word_gates_json)['body']=='PASS' and 'noteref' in compiled.body
    assert json.loads(selected.construction_json)==a


def test_new_quote_refuses_legacy_proposer_and_changed_control_identity(source):
    from cps.services.reflow import layout_quote as quote,layout_domain as d,layout_ranges as x
    from cps.services.reflow.structural_pipeline import EstimateStale
    from tests.unit.test_reflow_structural_pipeline import prepared_result
    book,doc,src,raw,p=prepared(source)
    measured=quote.measure(doc,prepared_result=prepared_result(source))
    client=lp.LayoutClient('').stages['proposer']
    current=client.prepare_request(req.proposal(p),p.raster)
    legacy=client.prepare_request(d.proposal(p),p.raster)
    from cps.services.reflow import layout_choices as old_semantic
    prior_semantic=client.prepare_request(old_semantic.proposal(p),p.raster)
    with pytest.raises(EstimateStale):quote.assert_request_bound(measured,'proposer',prior_semantic,0)
    assert current.sha256!=prior_semantic.sha256
    assert current.sha256!=legacy.sha256
    assert quote.assert_request_bound(measured,'proposer',current,0)
    with pytest.raises(EstimateStale):quote.assert_request_bound(measured,'proposer',legacy,0)
    stale=copy.deepcopy(measured);stale['construction_version']=d.VERSION
    stale.pop('identity');stale['identity']=quote._digest(stale)
    with pytest.raises(EstimateStale):quote.assert_request_bound(stale,'proposer',current,0)
    damaged=json.loads(current.context_json);damaged['route_version']='layout-structured-8-reasoning'
    with pytest.raises(ValueError):client._check_request(replace(current,context_json=ops._json(damaged)))


def test_new_disabled_raw_transport_cache_identity_and_old_contracts_are_strict(tmp_path,source):
    """Exact offline wire and unmodified original choices survive transport and provenance."""
    from cps.services.reflow import layout_ranges as x,layout_choices as old
    from cps.services.reflow.ledger import Ledger
    from cps.services.reflow.operation_cache import OperationCache
    from tests.unit.test_reflow_mimo_disabled import Saved,DISABLED
    book,doc,src,raw,p=prepared(source);chosen=range_fixture(p)
    session=Saved();literal=' \n'+json.dumps(chosen,ensure_ascii=False,indent=2)+'\n '
    session.data['choices'][0]['message']['content']=literal
    c=lp.LayoutClient('offline',profile_selection=DISABLED,session=session).stages['proposer']
    new=c.prepare_request(req.proposal(p),p.raster);oldwire=c.prepare_request(old.proposal(p),p.raster)
    cache=OperationCache(tmp_path/'cache');token,_=cache.claim(oldwire.sha256)
    cache.finish(oldwire.sha256,token,response={'historical':True})
    token,saved=cache.claim(new.sha256);assert token and saved is None
    cache.release_unsent(new.sha256,token)
    response=c.call(new,ledger=Ledger(str(tmp_path/'ledger'),cap_usd=5))
    assert response.response==chosen and session.data['choices'][0]['message']['content']==literal
    assert session.posts[0]['data']==new.payload_json.encode()
    plan=p.accept(book,doc,response.response,source_page=src,raw_page=raw,prototype=True)
    assert json.loads(plan.construction_json)==chosen
    assert native_codec.loads(native_codec.dumps(plan)).construction_json==plan.construction_json
    rebadged=copy.deepcopy(chosen);rebadged['contract']=old.VERSION
    with pytest.raises(ContractError):p.accept(book,doc,rebadged,source_page=src,raw_page=raw,prototype=True)
    altered=copy.deepcopy(chosen);altered['groups'][0]['ranges'].reverse()
    if altered==chosen:altered['groups'][0]['role']='quote'
    with pytest.raises(ContractError):
        req.review(p,replace(plan,construction_json=ops._json(altered)))
    del chosen['incoming']
    with pytest.raises(ContractError):p.accept(book,doc,chosen,source_page=src,raw_page=raw,prototype=True)


def test_new_incoming_both_page_admission_and_protected_illegal_order(boundary_source):
    """TRUE selects actual main endpoints; actual source compilation remains required on both pages."""
    book,doc,sources,raws=boundary_source
    left=ops.prepare(book,doc,0,sources[0],raws[0])
    right=ops.prepare(book,doc,1,sources[1],raws[1],previous_source=sources[0],previous_raw=raws[0])
    la=range_fixture(left);ra=range_fixture(right,incoming={'continue':True,'previous':3,'current':0,'hyphen':'keep'})
    lp0=left.accept(book,doc,la,source_page=sources[0],raw_page=raws[0])
    rp=right.accept(book,doc,ra,source_page=sources[1],raw_page=raws[1],previous_source=sources[0],previous_raw=raws[0])
    rv=verdict(rp,lp0);rv['continuation_accept']=True
    admitted=req.validate_review(right,rp,rv,lp0).accepted_plan
    edge=ops.compile_boundary(lp0,admitted,book,doc,left_source=sources[0],right_source=sources[1],left_raw=raws[0],right_raw=raws[1])
    assert (edge['left'],edge['right'],edge['hyphen'])==('a3','a0','keep') and edge['production_ready']
    with pytest.raises(ContractError):ops.compile_boundary(None,admitted,book,doc,left_source=sources[0],right_source=sources[1],left_raw=raws[0],right_raw=raws[1])
    # A heading at the output edge is a main-flow barrier, never an inferred continuation.
    bad=copy.deepcopy(ra);bad['groups'][0]['role']='heading1'
    with pytest.raises(ContractError):right.accept(book,doc,bad,source_page=sources[1],raw_page=raws[1],previous_source=sources[0],previous_raw=raws[0])
    # A range cannot swallow an opaque note block under an ordinary paragraph role.
    s=json.loads(left.contract_json);foreign=range_fixture(left,[dict(role='paragraph',ranges=[[s['atoms'][0]['id'],s['atoms'][-1]['id']]])])
    from cps.services.reflow import layout_ranges as x
    with pytest.raises(ContractError):x.compile(s,foreign)


def test_all_small_permutations_match_explicit_legacy_semantics_without_migrating_replies():
    """All 24 chosen orders are expressible; equivalent offline declarations have identical compiler output."""
    import itertools
    from cps.services.reflow import layout_ranges as x,layout_choices as old
    s=ops.atoms.prepare('<p>A B C D.</p>',{},0)
    incoming={'continue':False,'previous':None,'current':None,'hyphen':None}
    assert len(s['atoms'])==4
    for permutation in itertools.permutations(range(4)):
        chosen=dict(contract=x.VERSION,snapshot=s['snapshot'],groups=[dict(role='paragraph',ranges=[[i,i] for i in permutation])],joins=[],incoming=incoming,emphasis=[])
        # This is a separately declared synthetic old-contract fixture, never a paid reply.
        prior=dict(contract=old.VERSION,snapshot=s['snapshot'],ownership=[0]*4,groups=[dict(role='paragraph',order='explicit',atoms=list(permutation))],joins=[],incoming=incoming,emphasis=[])
        assert x.compile(s,chosen)==old.compile(s,prior),permutation
