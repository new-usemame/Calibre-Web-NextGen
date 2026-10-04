"""A categorical review binds exact candidates, independently from page joins."""
import json
from dataclasses import replace

import pytest

from cps.services.reflow import layout_ops as ops, layout_requests as requests
from cps.services.reflow.structural_ops import ContractError
from tests.unit.test_reflow_layout_ops import boundary_source, answer

pytestmark = pytest.mark.unit


@pytest.fixture
def candidates(boundary_source):
    book, doc, sources, raws = boundary_source
    left = ops.prepare(book, doc, 0, sources[0], raws[0])
    right = ops.prepare(book, doc, 1, sources[1], raws[1], previous_source=sources[0], previous_raw=raws[0])
    left_plan = left.accept(book, doc, answer(left), source_page=sources[0], raw_page=raws[0], prototype=True)
    proposal = answer(right)
    source = json.loads(right.contract_json)
    proposal.update(continuation=True, boundary_join=dict(left=source['previous']['wrap_lefts'][-1],
        right=source['wrap_rights'][0], hyphen='drop'))
    right_plan = right.accept(book, doc, proposal, source_page=sources[1], raw_page=raws[1],
        previous_source=sources[0], previous_raw=raws[0], prototype=True)
    return boundary_source, left_plan, right_plan


def verdict(plan, previous=None, **changes):
    request = requests.review(plan.prepared, plan, previous)
    result = dict(snapshot=request['response_schema']['properties']['snapshot']['enum'][0],
                  accept=True, continuation_accept=False, problems=[])
    result.update(changes)
    return result


def test_local_approval_with_declined_boundary_compiles_same_source_without_join(candidates):
    source, left, right = candidates
    book, doc, sources, raws = source
    original = json.loads(right.answer_json)
    review = requests.validate_review(right.prepared, right, verdict(right, left), left)
    assert review.accept and not review.continuation_accept and review.problems == ()
    selected = json.loads(review.accepted_plan.answer_json)
    assert selected['groups'] == original['groups'] and selected['joins'] == original['joins']
    assert selected['continuation'] is False and selected['boundary_join'] is None
    assert json.loads(right.answer_json) == original, 'review must not mutate the candidate'
    compiled = review.accepted_plan.compile(book, doc, source_page=sources[1], raw_page=raws[1],
        previous_source=sources[0], previous_raw=raws[0], prototype=True)
    assert 'operation resumes with ordinary source words.' in compiled.body
    assert ops.compile_boundary(left, review.accepted_plan, book, doc, left_source=sources[0],
        right_source=sources[1], left_raw=raws[0], right_raw=raws[1], prototype=True) is None
    approved = requests.validate_review(right.prepared, right,
        verdict(right, left, continuation_accept=True), left)
    assert ops.compile_boundary(left, approved.accepted_plan, book, doc, left_source=sources[0],
        right_source=sources[1], left_raw=raws[0], right_raw=raws[1], prototype=True)['hyphen'] == 'drop'


def test_missing_neighbor_does_not_reject_local_layout_but_cannot_approve_boundary(candidates):
    _, _, right = candidates
    request = requests.review(right.prepared, right)
    view = json.loads(request['messages'][1]['content'])
    assert 'unavailable' in view['previous']['status']
    decision = requests.validate_review(right.prepared, right, verdict(right))
    assert decision.accepted_plan is not None
    assert json.loads(decision.accepted_plan.answer_json)['continuation'] is False
    with pytest.raises(ContractError, match='context'):
        requests.validate_review(right.prepared, right, verdict(right, continuation_accept=True))


def test_local_rejection_never_selects_plan_even_when_boundary_would_be_supported(candidates):
    _, left, right = candidates
    result = requests.validate_review(right.prepared, right,
        verdict(right, left, accept=False, continuation_accept=True, problems=['paragraph_merge']), left)
    assert result.accepted_plan is None and result.problems == ('paragraph_merge',)


@pytest.mark.parametrize('change', ['missing', 'missing_boundary', 'extra', 'integer_bool', 'unknown_code', 'inconsistent', 'duplicates', 'wrong_binding'])
def test_forged_or_incomplete_review_cannot_select_candidate(candidates, change):
    _, left, right = candidates
    response = verdict(right, left)
    if change == 'missing': response = None
    elif change == 'missing_boundary': del response['continuation_accept']
    elif change == 'extra': response['html'] = '<p>forged</p>'
    elif change == 'integer_bool': response['accept'] = 1
    elif change == 'unknown_code': response.update(accept=False, problems=['invented'])
    elif change == 'inconsistent': response['accept'] = False
    elif change == 'duplicates': response.update(accept=False, problems=['furniture', 'furniture'])
    elif change == 'wrong_binding': response['snapshot'] = right.prepared.snapshot_id
    with pytest.raises(ContractError):
        requests.validate_review(right.prepared, right, response, left)


def test_prior_or_current_candidate_change_invalidates_old_review(candidates):
    _, left, right = candidates
    response = verdict(right, left)
    previous = json.loads(left.answer_json)
    previous['groups'][0]['role'] = 'quote'
    changed_left = replace(left, answer_json=json.dumps(previous))
    current = json.loads(right.answer_json)
    current['boundary_join']['hyphen'] = 'keep'
    changed_right = replace(right, answer_json=json.dumps(current))
    for current_plan, previous_plan in [(right, changed_left), (changed_right, left), (right, None)]:
        with pytest.raises(ContractError, match='stale'):
            requests.validate_review(current_plan.prepared, current_plan, response, previous_plan)


def test_stale_prepared_source_and_foreign_previous_candidate_are_refused(candidates):
    _, left, right = candidates
    with pytest.raises(ContractError, match='different prepared'):
        requests.review(replace(right.prepared, snapshot_id='forged'), right, left)
    forged = replace(left, prepared=replace(left.prepared, source_identity='foreign'))
    with pytest.raises(ContractError, match='identity'):
        requests.review(right.prepared, right, forged)
    with pytest.raises(ContractError, match='adjacent'):
        requests.review(right.prepared, right, right)


def test_review_schema_and_view_carry_exact_candidate_evidence_and_proposer_still_builds(candidates):
    _, left, right = candidates
    request = requests.review(right.prepared, right, left)
    view = json.loads(request['messages'][1]['content'])
    assert request['source_identity']['review_snapshot'] == view['snapshot']
    assert set(request['response_schema']['required']) == {'snapshot', 'accept', 'continuation_accept', 'problems'}
    assert request['response_schema']['additionalProperties'] is False
    assert view['proposed_layout'][0]['ranges'] == json.loads(right.answer_json)['groups'][0]['ranges']
    assert view['previous']['last_main_paragraph'][-1]['text'] == 'co-'
    assert view['reference_layout'] and view['printed_lines']
    source_blocks = json.loads(right.prepared.contract_json)['blocks']
    assert any(block.get('bbox') for block in source_blocks)
    assert [block['bbox'] for block in view['reference_layout']] == [block.get('bbox') for block in source_blocks]
    from cps.services.reflow import layout_domain
    proposal = layout_domain.proposal(right.prepared)
    from jsonschema import Draft202012Validator
    group_schema = proposal['response_schema']['properties']['groups']['items']
    from tests.unit.test_reflow_boundary_construction import explicit_fixture
    constructive=explicit_fixture(right.prepared,json.loads(right.answer_json))
    from cps.services.reflow import layout_domain
    compact=layout_domain.choice_fixture(json.loads(right.prepared.contract_json),constructive)
    paragraphs = [g for g in compact['groups'] if layout_domain.ROLES[g[0]] == 'paragraph']
    # Keep the root schema's $defs when validating a group subschema.
    validator = Draft202012Validator(proposal['response_schema']).evolve(schema=group_schema)
    assert paragraphs and all(validator.is_valid(g) for g in paragraphs)
