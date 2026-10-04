"""Opt-in route authority at the wire, quote, durable bill and source seams."""
import copy
import json
from dataclasses import replace

import pytest

from cps.services.reflow import (layout_model as lm, layout_pipeline as lp,
    layout_quote as quote, layout_requests as req, layout_wire, model, pipeline)
from cps.services.reflow.ledger import Ledger
from cps.services.reflow.structural_pipeline import EstimateStale
from tests.unit.test_reflow_layout_transport import envelope, jpeg, Session, source_atom_envelope
from tests.unit.test_reflow_structural_ops import source
from tests.unit.test_reflow_structural_pipeline import prepared_result
from tests.unit.test_reflow_layout_ops import prepared
from tests.unit.test_reflow_range_choices import range_fixture
from tests.unit.test_reflow_layout_review import candidates, boundary_source

pytestmark = pytest.mark.unit
SELECTION = {'proposer': 'mimo26-x1', 'reviewer': 'luna6-n1'}


def selected(stage='proposer', session=None):
    return lp.LayoutClient('offline-fixture', session=session, profile_selection=SELECTION).stages[stage]


class Alternative(Session):
    """Publicly shaped endpoint/control data and a bill-bearing offline response."""
    def __init__(self, stage='proposer'):
        super().__init__()
        self.stage = stage
        mimo = stage == 'proposer'
        self.model_id = 'xiaomi/mimo-v2.6-flash' if mimo else 'openai/gpt-6-luna'
        self.reasoning_options = ({'mandatory': False} if mimo else
            {'mandatory': False, 'supported_efforts': ['medium', 'none']})
        self.route.update(tag='xiaomi/fp8' if mimo else 'openai/flex',
            provider_name='Xiaomi' if mimo else 'OpenAI', model_id=self.model_id,
            context_length=1048576 if mimo else 1050000, max_prompt_tokens=None if mimo else 922000,
            max_completion_tokens=131072 if mimo else 128000,
            pricing={'prompt': .14e-6 if mimo else .05e-6,
                'completion': .28e-6 if mimo else .25e-6,
                'input_cache_read': .0028e-6 if mimo else .005e-6, 'discount': 0})
        self.data.update(id='offline-mimo-shaped-response', model=self.model_id,
            provider='Xiaomi' if mimo else 'OpenAI', service_tier='default' if mimo else 'flex')
        self.data['usage']['completion_tokens_details'] = {'reasoning_tokens': 10 if mimo else 0}
        self.data['usage']['cost'] = .00002
        self.gets = []

    def get(self, url, **kwargs):
        self.gets.append(url)
        if url.endswith('/models'):
            return self.response({'data': [{'id': self.model_id, 'reasoning': self.reasoning_options}]})
        return self.response({'data': {'id': self.model_id,
            'architecture': {'input_modalities': ['text', 'image']}, 'endpoints': [self.route]}})


def test_opt_in_is_exact_stage_scoped_and_preserves_default_wire_identity():
    """An opt-in must not replace defaults or admit arbitrary model/price objects."""
    defaults = lp.LayoutClient('').stages
    assert defaults['proposer'].profile == lm.PROFILES['proposer']
    assert defaults['reviewer'].profile.reasoning_effort == 'medium'
    assert defaults['reviewer'].profile.completion_cap == 4096
    direct = lm.LayoutStageClient('', 'proposer').prepare_request(envelope())
    assert defaults['proposer'].prepare_request(envelope()) == direct
    assert 'profile_id' not in json.loads(direct.payload['messages'][1]['content'])
    client = selected()
    wire = client.prepare_request(envelope(), jpeg())
    assert wire.payload['model'] == 'xiaomi/mimo-v2.6-flash'
    assert wire.payload['provider']['order'] == ['xiaomi/fp8']
    assert wire.payload['reasoning'] == {'enabled': True}
    assert 'service_tier' not in wire.payload
    assert wire.response_token_bound == 8192 + 16384
    audit = json.loads(wire.context_json)
    assert audit['profile_id'] == 'mimo26-x1' and audit['profile']['supporting_reasoning_tokens'] == 16384
    assert audit['reservation_rates'] == [.14, .28]
    client._check_request(wire)
    assert selected('reviewer').prepare_request(envelope()).payload['reasoning'] == {'effort': 'none'}
    for selection in [{'proposer': 'luna6-n1'}, {'reviewer': 'mimo26-x1'}, {'other': 'mimo26-x1'}, {'proposer': 'foreign'}, {'proposer': replace(client.profile, route='other')}, {'proposer': 'mimo26-x1'}, {'reviewer': 'luna6-n1'}]:
        with pytest.raises(ValueError):
            lp.LayoutClient('', profile_selection=selection)
    with pytest.raises(ValueError):
        lm.LayoutStageClient('', 'proposer', profile=replace(client.profile, spec=model.ModelSpec('foreign', .01, .01)))
    with pytest.raises(ValueError):
        lm.LayoutStageClient('', 'proposer', profile_id='mimo26-x1', model_id='foreign')
    with pytest.raises(ValueError):
        defaults['proposer']._check_request(wire)
    with pytest.raises(model.ModelError, match='aggregate billing'):
        lm.LayoutStageClient('fixture', 'reviewer', session=Session('reviewer')).preflight(100, 4096)
    # ModelSpec itself is mutable in the old transport. The named registry uses
    # immutable serialized records, and independently detects nested mutation.
    client.profile.spec.prompt_usd_per_mtok = .07
    with pytest.raises(ValueError): client.prepare_request(envelope())


def test_selected_quote_binds_actual_profiles_and_refuses_default_foreign_stale(source):
    """Only the explicitly selected quote may fund this exact transport request."""
    book, doc, src, _, value = prepared(source)
    q = quote.measure(doc, prepared_result=prepared_result(source), profile_selection=SELECTION)
    wire = selected().prepare_request(req.proposal(value), value.raster)
    assert q['profile_selection'] == SELECTION
    assert q['models']['proposer']['id'] == 'xiaomi/mimo-v2.6-flash'
    assert q['models']['reviewer']['reasoning_effort'] == 'none'
    assert q['models']['proposer']['reservation_input_usd_per_million'] == .14
    assert q['pages'][0]['proposer_request_sha256'] == wire.sha256
    assert quote.assert_request_bound(q, 'proposer', wire, 0, profile_selection=SELECTION)
    quote.consent_observer(q, doc, profile_selection=SELECTION)(book, value, src)
    default = quote.measure(doc, prepared_result=prepared_result(source))
    for candidate, selection in [(q, None), (default, SELECTION), (dict(q, version='stale'), SELECTION)]:
        with pytest.raises(EstimateStale):
            quote.assert_request_bound(candidate, 'proposer', wire, 0, profile_selection=selection)
    forged = copy.deepcopy(q); forged['profiles']['proposer']['route'] = 'other'
    forged['identity'] = quote._digest({k:v for k,v in forged.items() if k != 'identity'})
    with pytest.raises(EstimateStale):
        quote.assert_request_bound(forged, 'proposer', wire, 0, profile_selection=SELECTION)
    assert q['full_bound_usd'] == q['proposer_bound_usd']+q['verifier_bound_usd']+q['batch_bound_usd']


def test_public_controls_pin_prices_tiers_and_full_capacity_refuse_before_reservation(tmp_path):
    """Independent controls must fail before a bill or cache adoption can occur."""
    for failure in ('pin', 'foreign_model', 'schema', 'reasoning', 'price', 'cheap_price',
                    'cache_read', 'cache_write', 'tier', 'surcharge', 'foreign_price',
                    'completion', 'context', 'none_unsupported', 'none_mandatory', 'none_shape', 'review_floor', 'stale_wire'):
        stage = 'reviewer' if failure.startswith(('none_', 'review_')) else 'proposer'
        session = Alternative(stage); client = selected(stage, session)
        en, _ = source_atom_envelope(700) if stage == 'proposer' else (envelope(), None)
        wire = client.prepare_request(en)
        if failure == 'pin': session.route['tag'] = 'deepinfra/fp8'
        elif failure == 'foreign_model': session.route['model_id'] = 'foreign'
        elif failure == 'schema': session.route['supported_parameters'].remove('structured_outputs')
        elif failure == 'reasoning': session.route['supported_parameters'].remove('reasoning')
        elif failure == 'price': session.route['pricing']['prompt'] *= 2
        elif failure == 'cheap_price': session.route['pricing']['prompt'] /= 2
        elif failure == 'cache_read': session.route['pricing']['input_cache_read'] *= 2
        elif failure == 'cache_write': session.route['pricing']['input_cache_write'] = 1e-6
        elif failure == 'tier': session.route['pricing']['overrides'] = [{'min_prompt_tokens': 0, 'prompt': .14e-6}]
        elif failure == 'surcharge': session.route['pricing']['image'] = .001
        elif failure == 'foreign_price': session.route['pricing']['audio'] = .001
        elif failure == 'completion': session.route['max_completion_tokens'] = wire.response_token_bound - 1
        elif failure == 'context': session.route['context_length'] = wire.prompt_tokens_bound + wire.response_token_bound - 1
        elif failure == 'none_unsupported': session.reasoning_options['supported_efforts'] = ['medium']
        elif failure == 'none_mandatory': session.reasoning_options['mandatory'] = True
        elif failure == 'none_shape': session.reasoning_options['supported_efforts'] = 'none'
        elif failure == 'review_floor': session.route['pricing']['overrides'] = [{'min_prompt_tokens': -1, 'prompt': .05e-6}]
        else:
            payload = wire.payload
            shell = json.loads(payload['messages'][1]['content']); shell['route_version'] = 'stale'
            payload['messages'][1]['content'] = json.dumps(shell)
            wire = replace(wire, payload_json=json.dumps(payload))
        ledger = Ledger(str(tmp_path/failure), cap_usd=5)
        with pytest.raises((ValueError, model.ModelError)):
            client.call(wire, ledger=ledger)
        assert session.posts == [] and ledger.entries() == [], failure
    session = Alternative(); client = selected(session=session)
    en, _ = source_atom_envelope(700); wire = client.prepare_request(en)
    client.preflight(wire.prompt_tokens_bound, wire.response_token_bound)
    assert wire.response_token_bound > 8192 + 16384
    assert session.route['max_completion_tokens'] == 131072 and client.profile.completion_cap == 128000


def test_image_schema_raw_controls_and_bill_settlement_roundtrip(tmp_path):
    """Full raw request, profile reservation and rejected bill remain exact."""
    for failure in (None, 'provider', 'tier', 'model', 'reasoning_none', 'hidden_reasoning',
                    'fractional_reasoning', 'missing_reasoning', 'usage', 'fractional_usage', 'rate_bill', 'length', 'changed_profile'):
        stage = 'reviewer' if failure in ('reasoning_none', 'hidden_reasoning', 'fractional_reasoning', 'missing_reasoning') else 'proposer'
        session = Alternative(stage); client = selected(stage, session)
        en = envelope(); raster = jpeg(); wire = client.prepare_request(en, raster)
        ledger = Ledger(str(tmp_path/str(failure)), cap_usd=5)
        def reserved():
            assert Ledger(ledger.path, cap_usd=5).pending_usd() == wire.bound_usd
            if failure == 'changed_profile': client.profile = replace(client.profile, route='foreign')
        session.on_post = reserved
        if failure == 'provider': session.data['provider'] = 'DeepInfra'
        elif failure == 'tier': session.data['service_tier'] = 'flex'
        elif failure == 'model': session.data['model'] = 'foreign'
        elif failure == 'reasoning_none': session.data['usage']['completion_tokens_details']['reasoning_tokens'] = 1
        elif failure == 'hidden_reasoning': session.data['choices'][0]['message']['reasoning_details'] = [{'text': 'thinking'}]
        elif failure == 'fractional_reasoning': session.data['usage']['completion_tokens_details']['reasoning_tokens'] = .5
        elif failure == 'missing_reasoning': session.data['usage'].pop('completion_tokens_details')
        elif failure == 'usage': session.data['usage']['completion_tokens'] = wire.response_token_bound + 1
        elif failure == 'fractional_usage': session.data['usage']['completion_tokens'] = 30.5
        elif failure == 'rate_bill': session.data['usage']['cost'] = .0001
        elif failure == 'length':
            session.data['choices'][0].update(finish_reason='length', message={'content': None})
        if failure is None:
            response = client.call(wire, ledger=ledger)
            assert response.response == {'items': []} and response.reasoning_tokens == 10
            assert response.request_sha256 == wire.sha256
        else:
            with pytest.raises(model.ModelError) as error: client.call(wire, ledger=ledger)
            assert error.value.reason_code in ('route_mismatch', 'allocation_mismatch', 'completion_limit', 'billing_bound'), failure
        assert ledger.spent() == session.data['usage']['cost'] and ledger.pending_usd() == 0, failure
        posted = session.posts[0]['data']
        assert posted == wire.payload_json.encode() and json.loads(posted) == wire.payload
        assert wire.payload['response_format']['json_schema']['strict']
        assert wire.payload['messages'][-1]['content'][0]['image_url']['detail'] == 'original'
        reservation = ledger.entries('reservation')[0]
        assert reservation['request_sha256'] == wire.sha256
        assert reservation['route_version'] == client.route_version
        assert reservation['model'] == wire.payload['model']
        assert json.loads(wire.context_json)['profile_id'] == SELECTION[stage]


def test_opt_in_pipeline_real_guards_cache_and_missing_every_bit(source):
    """An all-true fixture is only mechanical; missing even one bit rejects it."""
    book, doc, tmp = source; value = prepared(source)[-1]
    class Workflow(Alternative):
        missing = False
        def get(self, url, **kwargs):
            if '/openai/' in url:
                self.stage = 'reviewer'
            elif '/xiaomi/' in url:
                self.stage = 'proposer'
            other = Alternative(self.stage)
            return other.get(url, **kwargs)
        def post(self, *args, **kwargs):
            wire = json.loads(kwargs['data']); shell = json.loads(wire['messages'][1]['content'])
            stage = shell['stage']; view = layout_wire.source_view(wire['messages'][:-1])
            if stage == 'proposer': response = range_fixture(value)
            else:
                from cps.services.reflow import layout_ranges
                bits = '1'*layout_ranges.decision_count(view['decisions'])
                response = dict(snapshot=view['snapshot'], accept=True, continuation_accept=False,
                    problems=[], decisions=bits[:-1] if self.missing else bits)
            self.data = Alternative(stage).data
            self.data['choices'][0]['message']['content'] = json.dumps(response)
            return super().post(*args, **kwargs)
    session = Workflow(); client = lp.LayoutClient('fixture', session=session, profile_selection=SELECTION)
    cache = pipeline.PageCache(tmp/'selected-cache'); ledger = Ledger(str(tmp/'selected-ledger'), cap_usd=5)
    q = quote.measure(doc, prepared_result=prepared_result(source), profile_selection=SELECTION)
    def guard(stage, wire, page):
        assert quote.assert_request_bound(q, stage, wire, page, profile_selection=SELECTION)
    result = lp.run_layout(doc, client=client, ledger=ledger, cache=cache,
        prepared_result=prepared_result(source), request_observer=guard)
    assert len(result.layout_plans) == 1 and len(session.posts) == 2
    assert json.loads(result.layout_plans[0].construction_json) == range_fixture(value)
    resumed = lp.run_layout(doc, client=client, ledger=ledger, cache=cache,
        prepared_result=prepared_result(source), request_observer=guard)
    assert len(session.posts) == 2 and resumed.structural['cached_stages'] == 2
    assert ledger.spent() == pytest.approx(.00004)
    session.missing = True
    refused = lp.run_layout(doc, client=client, ledger=ledger, cache=pipeline.PageCache(tmp/'missing-cache'),
        prepared_result=prepared_result(source), request_observer=guard)
    assert not refused.layout_plans and len(session.posts) == 4
    assert refused.page_html[0] == refused.source_pages[0].html
    assert ledger.spent() == pytest.approx(.00008)


def test_selected_native_predecessor_and_review_keep_full_source_binding(candidates):
    """The new route cannot make a foreign or incomplete predecessor authoritative."""
    from cps.services.reflow.structural_ops import ContractError
    _, left, right = candidates
    client = selected(); env = req.proposal(right.prepared, left)
    wire = client.prepare_request(env, right.prepared.raster)
    assert layout_wire.source_view(wire.payload['messages'][:-1]) == json.loads(env['messages'][-1]['content'])
    client._check_request(wire)
    assert wire.sha256 != client.prepare_request(req.proposal(right.prepared), right.prepared.raster).sha256
    view = layout_wire.source_view(wire.payload['messages'][:-1])
    assert len(view['previous_candidate']['last_main_paragraph']) <= 120
    assert view['previous']['scope'] == 'previous_context_only'
    for foreign in (replace(left, prepared=replace(left.prepared, pdf_digest='f'*64)), right):
        with pytest.raises(ContractError): req.proposal(right.prepared, foreign)
    review = selected('reviewer').prepare_request(req.review(right.prepared, right, left), right.prepared.raster)
    selected('reviewer')._check_request(review)
    assert review.response_token_bound == 4096


def test_inactive_luna_search_fee_is_validated_and_other_costs_refuse_before_hold(tmp_path):
    """An advertised optional fee is not a surcharge or permission to accept malformed costs."""
    for fee in ('0.01', '0', 0, .02):
        session = Alternative('reviewer'); session.route['pricing']['web_search'] = fee
        client = selected('reviewer', session)
        wire = client.prepare_request(envelope())
        client.preflight(wire.prompt_tokens_bound, wire.response_token_bound)
        assert session.posts == []
    changes = [('web_search', v) for v in ('-1e-999', 'NaN', 'Infinity', '-0.01', '', None, False, [], {})]
    changes += [(k, v) for k in ('request', 'image', 'internal_reasoning') for v in (.01, None, False, '')]
    changes += [('audio', 0), ('discount', None), ('discount', False), ('overrides', {}),
                ('overrides', False), ('overrides', [{'min_prompt_tokens': 999999, 'prompt': 'NaN'}]),
                ('overrides', [{'min_prompt_tokens': 0, 'web_search': '.01'}])]
    for n, (key, value) in enumerate(changes):
        session = Alternative('reviewer'); session.route['pricing']['web_search'] = '.01'
        session.route['pricing'][key] = value
        client = selected('reviewer', session); wire = client.prepare_request(envelope())
        ledger = Ledger(str(tmp_path/f'bad-{n}'), cap_usd=5)
        with pytest.raises(model.ModelError): client.call(wire, ledger=ledger)
        assert session.posts == [] and ledger.entries() == [], (key, value)
    session = Alternative(); session.route['pricing']['web_search'] = 0
    with pytest.raises(model.ModelError): selected(session=session).preflight(100, 24576)


def test_selected_request_cannot_activate_search_even_if_builder_changes(monkeypatch, tmp_path):
    """A coherently re-signed future builder adding search must fail before reservation."""
    from cps.services.reflow.typed_model import _encoded
    import hashlib
    client = selected('reviewer', Alternative('reviewer'))
    client._session.route['pricing']['web_search'] = '.01'
    original = lm.LayoutStageClient.prepare_request
    activations = [('plugins', [{'id': 'web'}]), ('tools', [{'type': 'openrouter:web_search'}]),
                   ('web_search_options', {}), ('tool_choice', 'auto'),
                   ('model', 'openai/gpt-6-luna:online'), ('models', ['openai/gpt-6-luna:online'])]
    for n, (key, value) in enumerate(activations):
        en = envelope(); en[key] = value
        with pytest.raises(ValueError): original(client, en)
        def search_builder(self, request, raster=None):
            wire = original(self, request, raster); payload = wire.payload; payload[key] = value
            raw = _encoded(payload).decode(); digest = hashlib.sha256(raw.encode()).hexdigest()
            audit = json.loads(wire.context_json); audit['request_sha256'] = digest
            return replace(wire, payload_json=raw, sha256=digest, context_json=_encoded(audit).decode())
        # Both the supplied wire and reconstruction use this changed builder.
        # Reconstruction alone would accept it; the independent search gate must reject it.
        monkeypatch.setattr(lm.LayoutStageClient, 'prepare_request', search_builder)
        wire = client.prepare_request(envelope())
        ledger = Ledger(str(tmp_path/f'activation-{n}'), cap_usd=5)
        with pytest.raises(ValueError): client.call(wire, ledger=ledger)
        assert client._session.posts == [] and ledger.entries() == []
        monkeypatch.setattr(lm.LayoutStageClient, 'prepare_request', original)


def test_unrequested_search_bill_is_settled_then_refused_at_metered_rates(tmp_path):
    """A fee below the image reservation still exceeds token liability and is never waived."""
    for cost in (.01, .05):
        session = Alternative('reviewer'); session.route['pricing']['web_search'] = '.01'
        session.data['usage']['cost'] = cost
        client = selected('reviewer', session); wire = client.prepare_request(envelope(), jpeg(400, 400))
        if cost == .01: assert cost < wire.bound_usd
        else: assert cost > wire.bound_usd
        ledger = Ledger(str(tmp_path/str(cost)), cap_usd=5)
        with pytest.raises(model.ModelError) as error: client.call(wire, ledger=ledger)
        assert error.value.reason_code == 'billing_bound'
        assert error.value.cost_usd == cost and error.value.stop_dispatch
        assert ledger.spent() == cost and ledger.pending_usd() == 0
        assert len(session.posts) == 1 and session.posts[0]['data'] == wire.payload_json.encode()
