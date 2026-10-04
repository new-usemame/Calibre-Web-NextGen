"""Complete constructive source choices and individual boundary review, without layout oracles."""
import copy
import json
from dataclasses import replace

import pytest
from cps.services.reflow import layout_ops as ops, layout_requests as req, _layout_atoms as atoms
from cps.services.reflow.structural_ops import ContractError
from tests.unit.test_reflow_layout_ops import prepared, boundary_source
from tests.unit.test_reflow_structural_ops import source

pytestmark = pytest.mark.unit


def fixture_answer(prep):
    from cps.services.reflow import layout_construction as construction
    s = json.loads(prep.contract_json)
    from tests.unit.test_reflow_layout_ops import answer
    groups = answer(prep)['groups']
    return construction_from_groups(s, groups)


def construction_from_groups(s, groups, joins=None):
    """Explicit offline choices only; never transforms/adopts a paid answer."""
    from cps.services.reflow import layout_construction as c
    ids = [a['id'] for a in s['atoms']]
    owners = [None] * len(ids)
    chosen = []
    for gi, g in enumerate(groups):
        own = [x for first,last in g['ranges'] for x in ids[ids.index(first):ids.index(last)+1]]
        for pi, identifier in enumerate(own):
            assert owners[ids.index(identifier)] is None
            owners[ids.index(identifier)] = [gi, pi]
        endpoints = list(dict.fromkeys(x for pair in g['ranges'] for x in pair))
        chosen.append(dict(role=g['role'], evidence=[selector(s, x) for x in endpoints]))
    joins = joins or []
    return dict(contract=c.VERSION, snapshot=s['snapshot'], ownership=owners, groups=chosen,
                joins=joins, join_evidence=[[selector(s,j['left']),selector(s,j['right'])] for j in joins],
                continuation=False, boundary_join=None, continuation_evidence=None, emphasis=[])


def selector(s, identifier):
    ids=[a['id'] for a in s['atoms']]
    lines=[l['source_line'] for l in s.get('printed_lines',[]) if any(identifier in ids[ids.index(a):ids.index(b)+1] for a,b in l['ranges'])]
    return [identifier, lines[0] if lines else None]


def verdict(plan, **changes):
    env=req.review(plan.prepared,plan)
    view=json.loads(env['messages'][-1]['content'])
    result=dict(snapshot=view['snapshot'],accept=True,continuation_accept=False,problems=[],
                decisions={d['id']: True for d in view['decisions']})
    result.update(changes)
    return result


def test_actual_request_construct_compile_and_review_preserve_source(source):
    book,doc,s,raw,prep=prepared(source)
    env=req.proposal(prep);view=json.loads(env['messages'][-1]['content'])
    assert view['construction']['contract']
    from cps.services.reflow import layout_domain
    assert [a['id'] for a in layout_domain.expand_source(view)['atoms']] == [a['id'] for a in json.loads(prep.contract_json)['atoms']]
    response=fixture_answer(prep)
    plan=prep.accept(book,doc,response,source_page=s,raw_page=raw,prototype=True)
    compiled=plan.compile(book,doc,source_page=s,raw_page=raw,prototype=True)
    assert json.loads(compiled.word_gates_json)['body']=='PASS'
    assert json.loads(plan.construction_json)==response
    selected=req.validate_review(prep,plan,verdict(plan)).accepted_plan
    assert selected.construction_json==plan.construction_json
    assert selected.compile(book,doc,source_page=s,raw_page=raw,prototype=True).body==compiled.body


@pytest.mark.parametrize('mutation',['missing','duplicate_position','bool','foreign_group','empty_group','stale','foreign_selector','wrong_line','missing_selector'])
def test_incomplete_or_forged_constructive_choice_refuses(source,mutation):
    book,doc,s,raw,prep=prepared(source);response=fixture_answer(prep)
    if mutation=='missing':response['ownership'].pop()
    elif mutation=='duplicate_position':response['ownership'][-1]=response['ownership'][0]
    elif mutation=='bool':response['ownership'][0][0]=False
    elif mutation=='foreign_group':response['ownership'][0][0]=99
    elif mutation=='empty_group':response['groups'].append(copy.deepcopy(response['groups'][0]))
    elif mutation=='stale':response['snapshot']='stale'
    elif mutation=='foreign_selector':response['groups'][0]['evidence'][0][0]='previous:a0'
    elif mutation=='wrong_line':response['groups'][0]['evidence'][0][1]=99999
    elif mutation=='missing_selector':response['groups'][0]['evidence'].pop()
    with pytest.raises(ContractError):prep.accept(book,doc,response,source_page=s,raw_page=raw,prototype=True)


@pytest.mark.parametrize('mutation',['categorical_only','missing','foreign','false','stale_construction'])
def test_every_individual_review_is_required_and_bound(source,mutation):
    book,doc,s,raw,prep=prepared(source);response=fixture_answer(prep)
    plan=prep.accept(book,doc,response,source_page=s,raw_page=raw,prototype=True);v=verdict(plan)
    if mutation=='categorical_only':del v['decisions']
    elif mutation=='missing':v['decisions'].pop(next(iter(v['decisions'])))
    elif mutation=='foreign':v['decisions']['foreign']=True
    elif mutation=='false':v['decisions'][next(iter(v['decisions']))]=False
    elif mutation=='stale_construction':
        changed=copy.deepcopy(response);changed['groups'][0]['role']='quote'
        plan=prep.accept(book,doc,changed,source_page=s,raw_page=raw,prototype=True)
    with pytest.raises(ContractError):req.validate_review(prep,plan,v)


def test_empty_inline_resources_and_unknown_lines_keep_exact_ownership():
    from cps.services.reflow import layout_construction as c
    s=atoms.prepare('<p>Even <a href="original.xhtml#glyph"><img src="glyph.png" /></a> more.</p>',{},0)
    view=c.cues(s)
    slot=next(a for a in view if a['text']=='')
    assert slot['ownership']=='inline_protected'
    assert slot['membership']==dict(state='unknown',lines=[])
    assert {r['type'] for r in slot['resources']}=={'a','img'}
    assert slot['html_sha256']
    result=c.compile(s,construction_from_groups(s,[dict(role='paragraph',ranges=[['a0','a2']])]))
    assert ops._protected(atoms.render(s,result)['body'])==ops._protected('<p>Even <a href="original.xhtml#glyph"><img src="glyph.png" /></a> more.</p>')


def test_interior_line_boundary_is_a_review_witness_not_a_layout_rule():
    from cps.services.reflow import layout_construction as c
    s=atoms.prepare('<p>Prior words. Even more significant text.</p>',{},0)
    s.pop('snapshot');s['printed_lines']=[dict(source_line=23,bbox=[1,2,3,4],ranges=[['a2','a5']])];s['snapshot']=atoms.digest(s)
    response=construction_from_groups(s,[dict(role='paragraph',ranges=[['a0','a2']]),dict(role='paragraph',ranges=[['a3','a5']])])
    canonical=c.compile(s,response)
    decisions=c.decisions(s,canonical,response)
    boundary=next(d for d in decisions if d['kind']=='boundary')
    assert boundary['interior_line'] is True
    assert [v['text'] for v in boundary['neighbors']]==['words.','Even','more','significant']
    assert [x[0] for x in boundary['selectors']]==['a2','a3']
    # Mechanical admission retains the model's choice. Only independent judgment rejects it.
    assert canonical['groups'][1]['ranges'][0][0]=='a3'


def explicit_fixture(prep, canonical, previous=None):
    """Fixture adapter for existing offline fake providers, never paid responses."""
    from cps.services.reflow import _layout_flow
    s=json.loads(prep.contract_json)
    result=construction_from_groups(s,canonical['groups'],canonical.get('joins',[]))
    for key in ('continuation','boundary_join','emphasis','note_bindings'):
        if key in canonical:result[key]=copy.deepcopy(canonical[key])
    if s.get('previous') and s['atoms']:
        groups=atoms.validate(s,canonical)
        edge=_layout_flow.edge(groups,False)
        current=edge[1][0]['id'] if edge else s['atoms'][0]['id']
        if previous:
            ps,pa=previous
            pe=_layout_flow.edge(atoms.validate(ps,pa),True)
            prior=pe[1][-1]['id'] if pe else s['previous']['atoms'][-1]['id']
        else:prior=s['previous']['atoms'][-1]['id']
        if canonical.get('boundary_join'):
            prior=canonical['boundary_join']['left'];current=canonical['boundary_join']['right']
        result['continuation_evidence']=dict(previous=[prior,None],current=selector(s,current))
    return result


def test_constructive_plan_keeps_raw_choices_through_optional_channel_and_native_wire(source):
    from cps.services.reflow import native_codec,layout_emphasis as style
    book,doc,s,raw,prep=prepared(source)
    response=fixture_answer(prep)
    # This optional range includes protected/nonprose atoms and must refuse
    # independently. The raw response must remain available unchanged.
    response['emphasis']=[['a0',json.loads(prep.contract_json)['atoms'][-1]['id']]]
    structural,record=style.split(prep,response)
    assert record['status']=='rejected'
    plan=prep.accept(book,doc,structural,source_page=s,raw_page=raw,prototype=True)
    plan=replace(plan,construction_json=ops._json(response))
    decoded=native_codec.loads(native_codec.dumps(plan))
    assert decoded==plan and json.loads(decoded.construction_json)==response
    assert decoded.compile(book,doc,source_page=s,raw_page=raw,prototype=True).body
    canonical=json.loads(plan.answer_json);canonical['groups'][0]['role']='quote'
    with pytest.raises(ContractError,match='provenance'):
        replace(plan,answer_json=ops._json(canonical)).compile(book,doc,source_page=s,raw_page=raw,prototype=True)


def test_current_and_previous_literal_ids_remain_separate_under_scoped_aliases(boundary_source):
    from cps.services.reflow import layout_construction as c
    book,doc,sources,raws=boundary_source
    left=ops.prepare(book,doc,0,sources[0],raws[0])
    right=ops.prepare(book,doc,1,sources[1],raws[1],previous_source=sources[0],previous_raw=raws[0])
    from tests.unit.test_reflow_layout_ops import answer
    lp=left.accept(book,doc,answer(left),source_page=sources[0],raw_page=raws[0],prototype=True)
    env=req.proposal(right,lp);view=json.loads(env['messages'][-1]['content'])
    from cps.services.reflow import layout_domain
    expanded=layout_domain.expand_source(view)
    assert expanded['atoms'][0]['id']==expanded['previous']['atoms'][0]['id']=='a0'
    assert view['atoms'][0]['text']!=view['previous']['atoms'][0]['text']
    old=json.loads(right.contract_json)
    assert len(view['atoms'])==len(old['atoms'])
    response=explicit_fixture(right,answer(right),(json.loads(left.contract_json),answer(left)))
    plan=right.accept(book,doc,response,source_page=sources[1],raw_page=raws[1],previous_source=sources[0],previous_raw=raws[0],prototype=True)
    review=req.review(right,plan,lp);v=json.loads(review['messages'][-1]['content'])
    good=dict(snapshot=v['snapshot'],accept=True,continuation_accept=False,problems=[],decisions={d['id']:True for d in v['decisions']})
    assert req.validate_review(right,plan,good,lp).accepted_plan
    changed=answer(left);changed['groups'][0]['role']='quote'
    changed_left=left.accept(book,doc,changed,source_page=sources[0],raw_page=raws[0],prototype=True)
    with pytest.raises(ContractError,match='stale'):req.validate_review(right,plan,good,changed_left)
    foreign=copy.deepcopy(response);foreign['continuation_evidence']['previous'][0]='foreign'
    with pytest.raises(ContractError):c.compile(old,foreign)


def test_constructive_join_evidence_never_bypasses_group_adjacency():
    from cps.services.reflow import layout_construction as c
    s=atoms.prepare('<p>Christian- Islamic source.</p>',{},0,wrap_lefts=['a0'],wrap_rights=['a1'])
    good=construction_from_groups(s,[dict(role='paragraph',ranges=[['a0','a2']])],[dict(left='a0',right='a1',hyphen='keep')])
    assert c.compile(s,good)['joins']==good['joins']
    bad=construction_from_groups(s,[dict(role='paragraph',ranges=[['a0','a0']]),dict(role='paragraph',ranges=[['a1','a2']])],good['joins'])
    with pytest.raises(ContractError,match='not adjacent'):c.compile(s,bad)
    wrong=copy.deepcopy(good);wrong['join_evidence'][0][1][0]='a2'
    with pytest.raises(ContractError,match='selector'):c.compile(s,wrong)


def test_empty_previous_source_has_no_invented_evidence_selector():
    from cps.services.reflow import layout_construction as c
    s=atoms.prepare('<p>Current words.</p>',{},1)
    s.pop('snapshot');s['previous']=dict(atoms=[],wrap_lefts=[],page=0,identity='empty_previous');s['snapshot']=atoms.digest(s)
    response=construction_from_groups(s,[dict(role='paragraph',ranges=[['a0','a1']])])
    assert response['continuation_evidence'] is None
    assert c.compile(s,response)['groups']==[dict(role='paragraph',ranges=[['a0','a1']])]
