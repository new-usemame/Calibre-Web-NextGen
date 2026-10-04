"""A paid order verdict cannot approve a different protected-image relocation."""
import json
from dataclasses import replace
import pytest
from cps.services.reflow import layout_ordering as ordering, layout_ops
from tests.unit.test_reflow_layout_ops import source, prepared, answer

pytestmark=pytest.mark.unit


def moved_plan(source):
    book,doc,page,raw,value=prepared(source)
    response=answer(value)
    contract=json.loads(value.contract_json)
    image=next(b['range'] for b in contract['blocks'] if b['kind']=='figure')
    index=next(i for i,g in enumerate(response['groups']) if image in g['ranges'])
    response['groups'].insert(0,response['groups'].pop(index))
    return value.accept(book,doc,response,source_page=page,raw_page=raw)


def test_rejected_image_relocation_falls_back_and_never_counts_as_approval(source):
    plan=moved_plan(source)
    rows=ordering.candidates([plan])
    assert len(rows)==1
    assert any(g['protected'] and g['source_rectangles'] for g in rows[0]['groups'])
    request=ordering.request([plan])
    response={'decisions':[{'id':rows[0]['id'],'accept':False,'problems':['association']}]}
    accepted,rejected=ordering.approved([plan],response,request['source_identity'])
    assert accepted==[] and rejected=={0}


def test_positive_order_review_is_bound_to_complete_candidate(source):
    plan=moved_plan(source)
    request=ordering.request([plan]);row=ordering.candidates([plan])[0]
    response={'decisions':[{'id':row['id'],'accept':True,'problems':[]}]}
    assert ordering.approved([plan],response,request['source_identity'])==([plan],set())
    changed=json.loads(plan.answer_json)
    changed['groups'][-2:]=reversed(changed['groups'][-2:])
    stale=replace(plan,answer_json=json.dumps(changed))
    with pytest.raises(layout_ops.ContractError,match='stale'):
        ordering.approved([stale],response,request['source_identity'])
    response['decisions'].append(dict(response['decisions'][0]))
    with pytest.raises(layout_ops.ContractError,match='repeated'):
        ordering.approved([plan],response,request['source_identity'])


def test_original_order_needs_no_extra_review(source):
    book,doc,page,raw,value=prepared(source)
    plan=value.accept(book,doc,answer(value),source_page=page,raw_page=raw)
    assert ordering.candidates([plan])==[]
