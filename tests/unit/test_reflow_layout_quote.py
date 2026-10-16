"""Quote future arbitrary layout decisions without predicting their wire content."""
import json
from dataclasses import replace

import pymupdf
import pytest

from cps.services.reflow import (layout_quote as quote, layout_ops as ops,
    layout_requests, layout_model, model, layout_pipeline)
from cps.services.reflow.structural_pipeline import EstimateStale
from tests.unit.test_reflow_structural_ops import source
from tests.unit.test_reflow_structural_pipeline import prepared_result
from tests.unit.test_reflow_layout_ops import prepared, answer, boundary_source

pytestmark = pytest.mark.unit


@pytest.mark.parametrize('selection', [None, layout_model.CHALLENGER_SELECTION, layout_model.DISABLED_SELECTION, layout_model.QWEN_SELECTION, layout_model.LUNA_STANDARD_SELECTION, layout_model.LUNA_REVIEW_SELECTION, layout_model.GLM_SELECTION, layout_model.LUNA_HIGH_SELECTION])
def test_quote_is_no_network_exact_proposal_and_explicit_full_batch_ceiling(source, monkeypatch, selection):
    def forbidden(*args, **kwargs):
        raise AssertionError('quote attempted provider access')
    monkeypatch.setattr(model.requests, 'get', forbidden)
    monkeypatch.setattr(model.requests, 'post', forbidden)
    book, doc, source_page, raw, value = prepared(source)
    measured = quote.measure(doc, prepared_result=prepared_result(source), profile_selection=selection)
    row = measured['pages'][0]
    wire = layout_pipeline.LayoutClient('', profile_selection=selection).stages['proposer'].prepare_request(layout_requests.proposal(value, profile_selection=selection), value.raster)
    assert row['proposer_request_sha256'] == wire.sha256
    assert row['proposer_bound_usd'] == wire.bound_usd
    assert measured['full_bound_usd'] == measured['proposer_bound_usd']+measured['verifier_bound_usd']+measured['batch_bound_usd']
    assert measured['batch_bound_usd'] == measured['ordering_bound_usd']+measured['lexical_bound_usd']
    assert measured['kind'] == 'reservation_ceiling_not_expected_bill'
    assert measured['confirmed_usd'] == measured['held_usd'] == 0
    assert measured['eligible_pages'] == measured['source_context_pages'] == 1
    assert row['verifier_request_sha256'] is None
    assert measured['models']['reviewer']['id'] == measured['models']['verifier']['id'] == 'openai/gpt-6-luna'
    quote.consent_observer(measured, doc, profile_selection=selection)(book, value, source_page)
    assert quote.assert_request_bound(measured, 'proposer', wire, 0, profile_selection=selection)


def test_current_candidate_structure_does_not_require_predicting_reviewer_hash(source):
    book, doc, source_page, raw, value = prepared(source)
    measured = quote.measure(doc, prepared_result=prepared_result(source))
    client = layout_pipeline.LayoutClient('').stages['reviewer']
    contract = json.loads(value.contract_json)
    # Maximum fragmentation plus all local structural wrappers that preserve source.
    for role in ('paragraph', 'heading1', 'heading2', 'heading3', 'quote', 'lineblock', 'note'):
        response = dict(snapshot=value.snapshot_id, joins=[], continuation=False, boundary_join=None,
            groups=[dict(role='source' if contract['blocks'][a['block']]['opaque'] else role, ranges=[[a['id'], a['id']]])
                    for a in contract['atoms']])
        plan = value.accept(book, doc, response, source_page=source_page, raw_page=raw, prototype=True)
        wire = client.prepare_request(layout_requests.review(value, plan), value.raster)
        assert quote.assert_request_bound(measured, 'reviewer', wire, 0)
        assert wire.bound_usd <= measured['pages'][0]['verifier_bound_usd']


def test_exact_transport_maximum_fits_review_and_batch_quote():
    clients = layout_pipeline.LayoutClient('').stages
    for stage in ('reviewer', 'ordering', 'lexical'):
        schema = dict(type='object', properties={}, required=[], additionalProperties=False)
        request = layout_requests.envelope(stage, 'arbitrary-source', [], '', schema)
        raster = None
        if stage == 'reviewer':
            with pymupdf.open() as doc:
                raster = doc.new_page(width=2000, height=2000).get_pixmap().tobytes('jpeg')
        wire = clients[stage].prepare_request(request, raster)
        payload = wire.payload
        text_messages = payload['messages'][:-1] if raster else payload['messages']
        used = len(layout_model._encoded(text_messages))+len(layout_model._encoded(schema))
        request['messages'][0]['content'] = 'x'*(layout_model.MAX_TEXT_BYTES-used)
        maximum = clients[stage].prepare_request(request, raster)
        limit = quote._maximum(stage, clients[stage])
        assert maximum.prompt_tokens_bound <= limit['prompt_tokens_bound']
        assert maximum.bound_usd <= limit['bound_usd']
        if stage != 'reviewer':
            assert maximum.response_token_bound <= limit['response_token_bound']
        request['messages'][0]['content'] += 'x'
        with pytest.raises(ValueError, match='48000'):
            clients[stage].prepare_request(request, raster)


@pytest.mark.parametrize('change', ['source', 'raster', 'prompt', 'profile_version', 'integrity'])
def test_consent_remeasures_source_and_versions_before_allowing_work(source, monkeypatch, change):
    book, doc, source_page, raw, value = prepared(source)
    measured = quote.measure(doc, prepared_result=prepared_result(source))
    observer = quote.consent_observer(measured, doc)
    if change == 'source':
        raw.blocks[0].lines[0].spans[0].text += ' changed'
        value = ops.prepare(book, doc, 0, source_page, raw)
    elif change == 'raster':
        with pymupdf.open() as other:
            raster = other.new_page(width=100, height=100).get_pixmap().tobytes('jpeg')
        value = replace(value, raster=raster)
    elif change == 'prompt': monkeypatch.setattr(layout_requests, 'PROMPT_VERSION', 'changed')
    elif change == 'profile_version': monkeypatch.setattr(layout_model, 'ROUTE_VERSION', 'changed')
    elif change == 'integrity': measured['full_bound_usd'] = 0
    with pytest.raises(EstimateStale):
        observer(book, value, source_page)


def test_wrong_source_or_inflated_request_cannot_pass_quote_check(source):
    _, doc, _, _, value = prepared(source)
    measured = quote.measure(doc, prepared_result=prepared_result(source))
    client = layout_pipeline.LayoutClient('').stages['proposer']
    envelope = layout_requests.proposal(value)
    wire = client.prepare_request(envelope, value.raster)
    with pytest.raises(EstimateStale):
        quote.assert_request_bound(measured, 'proposer', replace(wire, bound_usd=wire.bound_usd+1), 0)
    envelope['source_identity']['snapshot'] = 'different'
    with pytest.raises(EstimateStale):
        quote.assert_request_bound(measured, 'proposer', client.prepare_request(envelope, value.raster), 0)


def test_lexical_quote_bounds_singleton_splits_and_real_page_boundary(boundary_source):
    from cps.services.reflow import layout_lexical
    book, doc, sources, raws = boundary_source
    plans = []
    rows = []
    for p in range(2):
        value = ops.prepare(book, doc, p, sources[p], raws[p],
            previous_source=sources[p-1] if p else None, previous_raw=raws[p-1] if p else None)
        rows.append(quote.measure_page(book, doc, value, sources[p]))
        response = answer(value)
        if p:
            contract = json.loads(value.contract_json)
            response.update(continuation=True, boundary_join=dict(left=contract['previous']['wrap_lefts'][-1],
                right=contract['wrap_rights'][0], hyphen='drop'))
        plans.append(value.accept(book, doc, response, source_page=sources[p], raw_page=raws[p],
            previous_source=sources[p-1] if p else None, previous_raw=raws[p-1] if p else None, prototype=True))
    candidates = layout_lexical.candidates(plans)
    assert any(row['scope']=='page_boundary' for row in candidates)
    assert len(candidates) <= sum(row['lexical_max_requests'] for row in rows)
    # The two current wrap-lefts and one incoming boundary get individual ceilings,
    # even when a real combined batch would be much cheaper.
    assert sum(row['lexical_max_requests'] for row in rows) == 3
    assert sum(row['lexical_bound_usd'] for row in rows) == 3*quote._maximum('lexical')['bound_usd']


@pytest.mark.parametrize('selection', [None, layout_model.CHALLENGER_SELECTION, layout_model.DISABLED_SELECTION, layout_model.QWEN_SELECTION,
    layout_model.LUNA_STANDARD_SELECTION, layout_model.LUNA_REVIEW_SELECTION, layout_model.GLM_SELECTION, layout_model.LUNA_HIGH_SELECTION])
def test_unchanged_inadmissible_page_quotes_zero_and_never_gets_request_authority(source, monkeypatch, selection):
    book, doc, source_page, _, value = prepared(source)
    original = layout_requests.proposal
    def too_large(prepared, previous_plan=None, *, hints=None, profile_selection=None):
        result = original(prepared, previous_plan, hints=hints, profile_selection=profile_selection)
        result['messages'][0]['content'] += 'x'*layout_model.MAX_TEXT_BYTES
        return result
    monkeypatch.setattr(layout_requests, 'proposal', too_large)
    measured = quote.measure(doc, prepared_result=prepared_result(source), profile_selection=selection)
    assert measured['limited_pages'] == 1 and measured['eligible_pages'] == 0
    assert measured['full_bound_usd'] == 0 and measured['pages'] == []
    quote.consent_observer(measured, doc, profile_selection=selection)(book, value, source_page)
    client = layout_pipeline.LayoutClient('', profile_selection=selection).stages['proposer']
    wire = client.prepare_request(original(value, profile_selection=selection), value.raster)
    with pytest.raises(EstimateStale):
        quote.assert_request_bound(measured, 'proposer', wire, 0, profile_selection=selection)
