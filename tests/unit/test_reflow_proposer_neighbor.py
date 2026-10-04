"""Proposer context follows the preceding checked candidate, without admitting it."""
import copy
import json
from dataclasses import replace

import pytest
from cps.services.reflow import layout_ops as ops, layout_requests as requests
from cps.services.reflow import layout_pipeline, pipeline, extract,layout_domain,layout_ranges,layout_wire
from cps.services.reflow.structural_ops import ContractError
from cps.services.reflow.ledger import Ledger
from tests.unit.test_reflow_layout_review import candidates, boundary_source
from tests.unit.test_reflow_layout_ops import answer
from tests.unit.test_reflow_layout_transport import Session

pytestmark = pytest.mark.unit


def test_previous_candidate_matches_independent_review_context_and_binds_full_answer(candidates):
    _, left, right = candidates
    old = requests.proposal(right.prepared)
    old_view = json.loads(old['messages'][1]['content'])
    base=right.prepared.model_view()
    expanded=layout_domain.expand_source(old_view)
    assert [dict((k,a[k]) for k in b) for a,b in zip(expanded['atoms'],base['atoms'])]==base['atoms']
    assert old_view['previous']['scope']=='previous_context_only'
    assert set(old['source_identity']) == {'snapshot','construction_version'}
    req = requests.proposal(right.prepared, left)
    view = json.loads(req['messages'][1]['content'])
    reviewed = json.loads(requests.review(right.prepared, right, left)['messages'][1]['content'])
    native=view.pop('previous_candidate')
    normalized=copy.deepcopy(native)
    for a in normalized.get('last_main_paragraph') or []:a['id']='a'+str(a['id'])
    assert normalized == reviewed['previous']
    assert view == old_view
    assert req['source_identity']['previous_candidate'] == reviewed['previous']['snapshot']
    assert req['response_schema'] == old['response_schema']
    assert 'final' in reviewed['previous']['status']
    changed = json.loads(left.answer_json)
    changed['groups'][0]['role'] = 'quote'
    changed_req = requests.proposal(right.prepared, replace(left, answer_json=json.dumps(changed)))
    assert changed_req['source_identity'] != req['source_identity']
    quote_view = json.loads(changed_req['messages'][1]['content'])['previous_candidate']
    assert quote_view['last_main_role'] == 'quote'
    assert quote_view['last_main_paragraph'] == native['last_main_paragraph']
    changed['groups'][0]['role'] = 'heading2'
    barrier = requests.proposal(right.prepared, replace(left, answer_json=json.dumps(changed)))
    assert json.loads(barrier['messages'][1]['content'])['previous_candidate']['last_main_paragraph'] is None
    assert requests.proposal(right.prepared, None) == old

    split = json.loads(left.answer_json)
    split['groups'][:1] = [dict(role='paragraph', ranges=[['a0', 'a1']]),
                           dict(role='paragraph', ranges=[['a2', 'a3']])]
    first = requests.proposal(right.prepared, replace(left, answer_json=json.dumps(split)))
    split['groups'][0]['role'] = 'quote'
    second = requests.proposal(right.prepared, replace(left, answer_json=json.dumps(split)))
    edge = lambda request: json.loads(request['messages'][1]['content'])['previous_candidate']['last_main_paragraph']
    assert edge(first) == edge(second)
    assert first['source_identity'] != second['source_identity'], 'bind changes outside displayed endpoint'
    with pytest.raises(ContractError, match='snapshot'):
        requests.proposal(replace(right.prepared, snapshot_id='forged'), left)


@pytest.mark.parametrize('damage', ['nonadjacent', 'foreign_pdf', 'identity', 'atoms', 'malformed_answer', 'not_a_plan'])
def test_unbound_or_invalid_previous_candidates_fail_before_transport(candidates, damage):
    _, left, right = candidates
    if damage == 'nonadjacent': left = right
    elif damage == 'foreign_pdf': left = replace(left, prepared=replace(left.prepared, pdf_digest='f'*64))
    elif damage == 'identity': left = replace(left, prepared=replace(left.prepared, source_identity='foreign'))
    elif damage == 'atoms':
        source = json.loads(left.prepared.contract_json)
        source['atoms'][0]['text'] = 'forged context'
        left = replace(left, prepared=replace(left.prepared, contract_json=json.dumps(source)))
    elif damage == 'malformed_answer':
        response = json.loads(left.answer_json); response['groups'] = []
        left = replace(left, answer_json=json.dumps(response))
    else: left = json.loads(left.answer_json)
    with pytest.raises(ContractError): requests.proposal(right.prepared, left)


def test_real_pipeline_supplies_previous_candidate_and_reuses_the_bound_wire(boundary_source, tmp_path):
    book, doc, sources, raws = boundary_source
    prepared = [ops.prepare(book, doc, p, sources[p], raws[p],
        **(dict(previous_source=sources[0], previous_raw=raws[0]) if p else {})) for p in range(2)]
    responses = {v.snapshot_id: answer(v) for v in prepared}
    responses[prepared[0].snapshot_id]['groups'][:1] = [
        dict(role='paragraph', ranges=[['a0', 'a1']]),
        dict(role='paragraph', ranges=[['a2', 'a3']])]
    for response in responses.values(): response.update(continuation=False, boundary_join=None)
    seen = []
    class PairSession(Session):
        def __init__(self):
            super().__init__(); self.route.update(context_length=1050000, max_prompt_tokens=922000)
        def post(self, *args, **kwargs):
            wire = json.loads(kwargs['data']); identity = json.loads(wire['messages'][1]['content'])
            view = layout_wire.source_view(wire['messages'][:-1])
            if identity['stage'] == 'proposer':
                if view['source_snapshot'] == prepared[1].snapshot_id:
                    prior = view.get('previous_candidate')
                    assert prior is not None, 'next-page proposer lost the preceding candidate'
                    assert [a['text'] for a in prior['last_main_paragraph']] == ['Another', 'co-']
                    assert identity['source_identity']['previous_candidate'] == prior['snapshot']
                    seen.append(prior['snapshot'])
                else: assert 'previous_candidate' not in view
                from tests.unit.test_reflow_range_choices import range_fixture
                pp=next(v for v in prepared if v.snapshot_id==view['source_snapshot'])
                prev=(json.loads(prepared[0].contract_json),responses[prepared[0].snapshot_id]) if pp.page else None
                response = range_fixture(pp,responses[view['source_snapshot']]['groups'])
            else:
                assert identity['stage'] == 'reviewer'
                response = dict(snapshot=view['snapshot'], accept=True, continuation_accept=False, problems=[])
            if 'decisions' in view:response['decisions']='1'*layout_ranges.decision_count(view['decisions'])
            self.data['choices'][0]['message']['content'] = json.dumps(response)
            return super().post(*args, **kwargs)
    session = PairSession(); client = layout_pipeline.LayoutClient('test', enabled=True, session=session)
    def run(name):
        result = pipeline.ReflowResult(book=book, raw_pages=raws,
            page_html={p:sources[p].html for p in range(2)}, fingerprint=extract.document_fingerprint(doc))
        ledger = Ledger(str(tmp_path/name), cap_usd=1)
        return layout_pipeline.run_layout(doc, client=client, ledger=ledger,
            cache=pipeline.PageCache(tmp_path/'cache'), prepared_result=result), ledger
    first, _ = run('first')
    assert [p.prepared.page for p in first.layout_plans] == [0,1]
    assert len(seen) == 1 and len(session.posts) == 4
    second, ledger = run('second')
    assert second.structural['cached_stages'] == 4 and ledger.spent() == 0
    assert len(session.posts) == 4
