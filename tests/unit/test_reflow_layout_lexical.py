import json
from dataclasses import replace
import pytest
from cps.services.reflow import layout_ops as ops, layout_lexical as lexical
from tests.unit.test_reflow_layout_ops import boundary_source,answer
pytestmark=pytest.mark.unit


def plans(fixture):
    book,doc,sources,raws=fixture
    prepared=[ops.prepare(book,doc,p,sources[p],raws[p],
        **({'previous_source':sources[0],'previous_raw':raws[0]} if p else {})) for p in range(2)]
    answers=[answer(p) for p in prepared]
    answers[0]['groups'][:1]=[dict(role='paragraph',ranges=[['a0','a1']]),dict(role='paragraph',ranges=[['a2','a3']])]
    answers[1].update(continuation=True,boundary_join=None)
    return [p.accept(book,doc,a,source_page=sources[i],raw_page=raws[i],
        **({'previous_source':sources[0],'previous_raw':raws[0]} if i else {}))
        for i,(p,a) in enumerate(zip(prepared,answers))]


def test_source_bound_drop_joins_actual_body_endpoint_and_passes_gate(boundary_source):
    pp=plans(boundary_source);rows=lexical.candidates(pp)
    assert len(rows)==1 and rows[0]['left_text']=='co-'
    request=lexical.request(pp)
    updated,refused=lexical.apply(pp,{'decisions':[{'id':rows[0]['id'],'decision':'drop'}]},request['source_identity'])
    assert not refused
    book,doc,sources,raws=boundary_source
    result=ops.compile_boundary(*updated,book,doc,left_source=sources[0],right_source=sources[1],left_raw=raws[0],right_raw=raws[1])
    assert result['hyphen']=='drop'
    assert json.loads(updated[1].answer_json)['boundary_join']['left']==rows[0]['left']


def test_ambiguous_word_refuses_both_affected_pages(boundary_source):
    pp=plans(boundary_source);row=lexical.candidates(pp)[0];request=lexical.request(pp)
    updated,refused=lexical.apply(pp,{'decisions':[{'id':row['id'],'decision':'separate'}]},request['source_identity'])
    assert updated==[] and refused=={0,1}


def test_verdict_cannot_follow_mutated_context_or_missing_decision(boundary_source):
    pp=plans(boundary_source);request=lexical.request(pp)
    with pytest.raises(ops.ContractError,match='missing'):
        lexical.apply(pp,{'decisions':[]},request['source_identity'])
    source=json.loads(pp[0].prepared.contract_json);source['snapshot']='changed'
    changed=replace(pp[0],prepared=replace(pp[0].prepared,contract_json=json.dumps(source)))
    with pytest.raises(ops.ContractError,match='source bytes changed'):
        lexical.apply([changed,pp[1]],{'decisions':[]},request['source_identity'])


def test_note_hyphen_operations_also_require_lexical_approval(boundary_source):
    pp=plans(boundary_source)
    answer=json.loads(pp[0].answer_json)
    answer['groups'][:2]=[dict(role='note',ranges=[['a0','a3']])]
    answer['joins']=[dict(left='a1',right='a2',hyphen='drop')]
    note=replace(pp[0],answer_json=json.dumps(answer))
    rows=lexical.candidates([note])
    assert len(rows)==1 and rows[0]['left_text']=='inter-'
    accepted,refused=lexical.apply([note],{'decisions':[dict(id=rows[0]['id'],decision='separate')]},lexical.request([note])['source_identity'])
    assert accepted==[] and refused=={0}
