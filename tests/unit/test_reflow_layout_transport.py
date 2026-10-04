"""Layout wire contracts and billing failures through the actual common transport."""
import copy
import hashlib
import json
from dataclasses import replace

import pymupdf
import pytest
import requests

from cps.services.reflow import layout_model as layout, model
from cps.services.reflow.ledger import Ledger
from cps.services.reflow.operation_cache import OperationCache, PriorRequestPending

pytestmark = pytest.mark.unit


def envelope():
    return {'protocol': 'layout-test-v1', 'source_identity': {'pdf': 'a' * 64, 'page': 0},
            'prompt_version': 'prompt-v1', 'context': {'snapshot_id': 'b' * 64},
            'messages': [{'role': 'user', 'content': 'Classify source spans.'}],
            'response_schema': {'type': 'object', 'properties': {'items': {'type': 'array', 'items': {'type': 'string'}}},
                                'required': ['items'], 'additionalProperties': False}}


def jpeg(width=200, height=200):
    with pymupdf.open() as doc:
        return doc.new_page(width=width, height=height).get_pixmap().tobytes('jpeg')


class Session:
    def __init__(self, stage='proposer'):
        profile = layout.PROFILES[stage]
        self.posts = []
        self.error = None
        self.on_post = None
        self.route = {'tag': profile.route, 'status': 0, 'context_length': 200000,
                      'max_prompt_tokens': 200000, 'max_completion_tokens': 32768,
                      'supported_parameters': ['max_tokens', 'reasoning', 'response_format', 'structured_outputs'],
                      'pricing': {'prompt': profile.spec.prompt_usd_per_mtok / 1e6,
                                  'completion': profile.spec.completion_usd_per_mtok / 1e6,
                                  'input_cache_write': profile.spec.prompt_usd_per_mtok * 1.25 / 1e6}}
        self.data = {'model': profile.spec.model_id, 'provider': profile.provider,
                     'usage': {'prompt_tokens': 100, 'completion_tokens': 30, 'cost': .0001},
                     'choices': [{'finish_reason': 'stop', 'message': {'content': '{"items":[]}'}}]}
        if profile.service_tier:
            self.data['service_tier'] = profile.service_tier

    def get(self, *args, **kwargs):
        return self.response({'data': {'architecture': {'input_modalities': ['text', 'image']},
                                       'endpoints': [self.route]}})

    def post(self, *args, **kwargs):
        self.posts.append(kwargs)
        if self.on_post:
            self.on_post()
        if self.error:
            raise self.error
        return self.response(self.data)

    @staticmethod
    def response(data):
        class Response:
            status_code = 200
            def json(self):
                return data
        return Response()


def client(stage='proposer', session=None):
    profile = layout.PROFILES[stage]
    if stage == 'ordering':
        profile = replace(profile, spec=layout.PROFILES['proposer'].spec, reasoning_effort=layout.PROFILES['proposer'].reasoning_effort)
        if session:
            session.data['model'] = profile.spec.model_id
    return layout.LayoutStageClient('test', stage, session=session or Session(stage), profile=profile)


def test_durable_wire_schema_and_bounded_standard_cache_write_reservation(tmp_path):
    session = Session(); transport = client(session=session)
    request = transport.prepare_request(envelope(), jpeg())
    ledger = Ledger(str(tmp_path / 'ledger'), cap_usd=10)
    def assert_reserved():
        assert Ledger(ledger.path, cap_usd=10).pending_usd() == request.bound_usd
    session.on_post = assert_reserved
    answer = transport.call(request, ledger=ledger)
    wire = json.loads(session.posts[0]['data'])
    assert wire['response_format']['json_schema']['strict'] is True
    assert wire['response_format']['json_schema']['schema'] == envelope()['response_schema']
    assert wire['max_tokens'] == 8192+16384 and 'max_completion_tokens' not in wire
    assert wire['reasoning'] == {'effort': 'medium'}
    assert request.bound_usd == (request.prompt_tokens_bound * .10 * 1.25 + (8192+16384) * .375) / 1e6
    assert request.prompt_tokens_bound > 40000  # One token per image pixel, plus text.
    assert hashlib.sha256(session.posts[0]['data']).hexdigest() == request.sha256
    assert answer.response == {'items': []} and ledger.spent() == .0001 and ledger.pending_usd() == 0
    reservation = ledger.entries('reservation')[0]
    assert reservation['snapshot_id'] == 'b' * 64 and reservation['prompt_version'] == 'prompt-v1'


def test_exact_source_schema_prompt_raster_protocol_identity_separates_cache(tmp_path):
    transport = client(); original = envelope(); raster = jpeg()
    first = transport.prepare_request(original, raster)
    variants = []
    for key, value in [('source_identity', {'pdf': 'c' * 64}), ('protocol', 'v2'), ('prompt_version', 'v2')]:
        changed = copy.deepcopy(original); changed[key] = value
        variants.append(transport.prepare_request(changed, raster))
    changed = copy.deepcopy(original); changed['response_schema']['properties']['items']['maxItems'] = 2
    variants += [transport.prepare_request(changed, raster), transport.prepare_request(original, jpeg(201))]
    assert len({first.sha256, *(item.sha256 for item in variants)}) == 6
    cache = OperationCache(tmp_path)
    token, _ = cache.claim(first.sha256)
    with pytest.raises(PriorRequestPending):
        cache.claim(first.sha256)
    cache.finish(first.sha256, token, response={'items': []})
    assert cache.claim(first.sha256)[1]['response'] == {'items': []}
    assert cache.claim(variants[0].sha256)[0]


@pytest.mark.parametrize('failure', ['price', 'cache_write', 'context', 'endpoint_completion', 'surcharge', 'unsupported', 'reasoning', 'model_tamper', 'reservation_tamper', 'prompt_tamper'])
def test_invalid_route_or_request_never_reserves_or_dispatches(tmp_path, failure):
    session = Session(); transport = client(session=session)
    request = transport.prepare_request(envelope())
    if failure == 'price': session.route['pricing']['prompt'] *= 3
    elif failure == 'cache_write': session.route['pricing']['input_cache_write'] *= 3
    elif failure == 'context': session.route['context_length'] = 10
    elif failure == 'endpoint_completion': session.route['max_completion_tokens'] = 4096
    elif failure == 'surcharge': session.route['pricing']['request'] = .001
    elif failure == 'reasoning': session.route['supported_parameters'].remove('reasoning')
    elif failure == 'unsupported': session.route['supported_parameters'].remove('structured_outputs')
    elif failure == 'reservation_tamper': request = replace(request, bound_usd=0)
    elif failure == 'prompt_tamper': request = replace(request, prompt_version='other')
    elif failure == 'model_tamper':
        payload = request.payload; payload['model'] = 'other'
        request = replace(request, payload_json=json.dumps(payload))
    ledger = Ledger(str(tmp_path / 'ledger'), cap_usd=10)
    with pytest.raises((ValueError, model.ModelError)):
        transport.call(request, ledger=ledger)
    assert session.posts == [] and ledger.entries() == []


@pytest.mark.parametrize('failure', ['malformed', 'length', 'provider', 'tier', 'model', 'bill', 'timeout', 'missingbill'])
def test_paid_failures_settle_before_parse_and_unknown_billing_stays_held(tmp_path, failure):
    session = Session(); transport = client(session=session)
    request = transport.prepare_request(envelope())
    if failure == 'malformed': session.data['choices'][0]['message']['content'] = 'private malformed text'
    elif failure == 'length': session.data['choices'][0]['finish_reason'] = 'length'
    elif failure == 'provider': session.data['provider'] = 'Anthropic'
    elif failure == 'tier': session.data['service_tier'] = 'default'
    elif failure == 'model': session.data['model'] = 'other'
    elif failure == 'bill': session.data['usage']['cost'] = request.bound_usd + 1
    elif failure == 'timeout': session.error = requests.ReadTimeout('private timeout text')
    elif failure == 'missingbill': del session.data['usage']['cost']
    ledger = Ledger(str(tmp_path / 'ledger'), cap_usd=10)
    with pytest.raises(model.ModelError) as caught:
        transport.call(request, ledger=ledger)
    assert len(session.posts) == 1
    assert 'private' not in str(caught.value) and 'private' not in (tmp_path / 'ledger').read_text()
    if failure in ('timeout', 'missingbill'):
        assert ledger.pending_usd() == request.bound_usd and ledger.spent() == 0
    else:
        assert ledger.pending_usd() == 0 and ledger.spent() == session.data['usage']['cost']
    if failure in ('bill', 'model', 'provider', 'tier'):
        assert caught.value.stop_dispatch


@pytest.mark.parametrize('stage', ['ordering', 'lexical'])
def test_batch_stages_text_only_with_exact_route_and_cancellation(tmp_path, stage):
    session = Session(stage); transport = client(stage, session)
    with pytest.raises(ValueError, match='text only'):
        transport.prepare_request(envelope(), jpeg())
    request = transport.prepare_request(envelope())
    ledger = Ledger(str(tmp_path / 'ledger'), cap_usd=10)
    with pytest.raises(model.AttemptCancelled):
        transport.call(request, ledger=ledger, should_stop=lambda: True)
    assert session.posts == [] and ledger.entries('reservation') == []
    answer = transport.call(request, ledger=ledger)
    wire = request.payload
    assert wire['provider']['order'] == [layout.PROFILES[stage].route]
    assert all(isinstance(m['content'], str) for m in wire['messages'])
    assert answer.model == transport.spec.model_id
    if stage == 'lexical':
        assert 'service_tier' not in wire and wire['max_tokens'] == 2048


def test_text_and_image_bounds_refuse_before_dispatch():
    transport = client()
    request = envelope(); request['messages'][0]['content'] = 'x' * 48001
    with pytest.raises(ValueError, match='48000'):
        transport.prepare_request(request)
    with pytest.raises(ValueError, match='megapixels'):
        transport.prepare_request(envelope(), jpeg(2001, 2000))


def test_pro_aggregate_billing_profile_is_inactive_even_with_endpoint_ceiling(tmp_path):
    session = Session('reviewer')
    transport = layout.LayoutStageClient('test', 'reviewer', session=session,
        reservation_prompt_tokens=200000, reservation_completion_tokens=32768)
    ledger = Ledger(str(tmp_path / 'ledger'), cap_usd=10)
    with pytest.raises(model.ModelError, match='aggregate billing'):
        transport.call(transport.prepare_request(envelope()), ledger=ledger)
    assert session.posts == [] and ledger.entries() == []


def test_price_tier_is_checked_at_computed_prompt_bound_before_paid_dispatch(tmp_path):
    session = Session(); transport = client(session=session)
    request = transport.prepare_request(envelope())
    session.route['pricing']['overrides'] = [{'min_prompt_tokens': request.prompt_tokens_bound + 1,
        'prompt': 1e-6, 'completion': 1e-6}]
    transport.preflight(request.prompt_tokens_bound)
    session.route['pricing']['overrides'][0]['min_prompt_tokens'] = request.prompt_tokens_bound
    ledger = Ledger(str(tmp_path / 'ledger'), cap_usd=.5)
    with pytest.raises(model.ModelError):
        transport.call(request, ledger=ledger)
    assert session.posts == [] and ledger.entries() == []


def test_full_endpoint_reservation_is_explicit_diagnostic_option(tmp_path):
    session = Session()
    transport = layout.LayoutStageClient('test', 'proposer', session=session,
        reservation_prompt_tokens=200000, reservation_completion_tokens=32768)
    request = transport.prepare_request(envelope())
    assert request.bound_usd == (200000 * .10 * 1.25 + 32768 * .375) / 1e6
    session.route['max_completion_tokens'] = 65536
    with pytest.raises(model.ModelError):
        transport.call(request, ledger=Ledger(str(tmp_path / 'ledger'), cap_usd=10))
    assert not session.posts


def test_lexical_default_bound_fits_small_authorized_cap(tmp_path):
    session = Session('lexical'); transport = client('lexical', session)
    request = transport.prepare_request(envelope())
    assert request.bound_usd < .05
    ledger = Ledger(str(tmp_path / 'ledger'), cap_usd=.5)
    answer = transport.call(request, ledger=ledger)
    assert answer.response == {'items': []} and ledger.pending_usd() == 0


def test_live_tier_shape_optional_search_price_and_large_image_are_admitted():
    session=Session()
    session.route.update(context_length=1050000,max_prompt_tokens=922000,max_completion_tokens=128000)
    session.route['pricing'].update(web_search=.01,overrides=[dict(min_prompt_tokens=272000,
        prompt=.10/1e6,completion=.375/1e6,input_cache_write=.125/1e6,input_cache_read=.01/1e6)])
    transport=client(session=session)
    wire=transport.prepare_request(envelope(),jpeg(1000,1000))
    assert wire.prompt_tokens_bound==922000
    transport.preflight(wire.prompt_tokens_bound)
    assert not session.posts
    session.route['pricing']['overrides'][0]['completion']=.5/1e6
    with pytest.raises(model.ModelError):transport.preflight(wire.prompt_tokens_bound)


def test_anthropic_default_tier_is_the_admitted_non_flex_route(tmp_path):
    session=Session('lexical');session.data['service_tier']='default'
    transport=client('lexical',session)
    wire=transport.prepare_request(envelope())
    ledger=Ledger(str(tmp_path/'ledger'),cap_usd=1)
    assert transport.call(wire,ledger=ledger).response=={'items':[]}
    assert ledger.spent()==.0001
    session.data['service_tier']='priority'
    with pytest.raises(layout.TypedStageRejected,match='route'):
        transport.call(wire,ledger=ledger)


def test_stable_instructions_precede_page_identity_without_sharing_paid_response(tmp_path):
    """Different pages may reuse a prompt prefix, never a paid answer or source binding."""
    transport = client()
    first = envelope()
    first['messages'] = [
        {'role': 'system', 'content': 'Preserve every source atom. ' * 150},
        {'role': 'user', 'content': '{"page": 0, "atoms": ["a0"]}'},
    ]
    second = copy.deepcopy(first)
    second['source_identity']['page'] = 1
    second['context']['snapshot_id'] = 'c' * 64
    second['messages'][1]['content'] = '{"page": 1, "atoms": ["a0"]}'
    a = transport.prepare_request(first, jpeg())
    b = transport.prepare_request(second, jpeg())
    assert a.payload['messages'][0] == b.payload['messages'][0] == first['messages'][0]
    assert a.sha256 != b.sha256
    transport._check_request(a)
    transport._check_request(b)
    cache = OperationCache(tmp_path/'prefix-cache')
    owner, _ = cache.claim(a.sha256)
    cache.finish(a.sha256, owner, response={'items': []})
    assert cache.claim(a.sha256)[1]['response'] == {'items': []}
    assert cache.claim(b.sha256)[0]


def test_ordering_finishes_above_old_reasoning_ceiling_with_reserved_bound(tmp_path):
    session = Session('ordering')
    session.data['usage'].update(completion_tokens=3072, cost=.0009)
    transport = client('ordering', session=session)
    request = transport.prepare_request(envelope())
    ledger = Ledger(str(tmp_path / 'ordering-ledger'), cap_usd=1)
    def verify_before_send():
        assert Ledger(ledger.path, cap_usd=1).pending_usd() == request.bound_usd
    session.on_post = verify_before_send
    answer = transport.call(request, ledger=ledger)
    assert answer.response == {'items': []}
    assert json.loads(session.posts[0]['data'])['max_tokens'] >= 3072
    assert ledger.pending_usd() == 0 and ledger.spent() == .0009


def source_atom_envelope(count):
    request = envelope()
    request['protocol'] = 'source-bound-layout-1'
    view = {'atoms': [{'id': f'a{i}', 'text': 'literal é word', 'protected': i % 7 == 0}
                      for i in range(count)],
            'previous': {'atoms': [{'id': 'p0', 'text': 'prior', 'protected': True}]},
            'printed_lines': [{'ranges': [['a0', 'a3']], 'bbox': [1, 2, 3, 4]}],
            'wrap_evidence': {'a3': {'line': 7, 'literal': 'word-'}},
            'wrap_lefts': ['a3'], 'wrap_rights': ['a4']}
    request['messages'] = [{'role': 'system', 'content': 'Stable common instructions'},
                           {'role': 'user', 'content': json.dumps(view, ensure_ascii=False)}]
    return request, view


def test_oversized_source_atoms_compact_losslessly_and_roundtrip():
    request, source = source_atom_envelope(700)
    source['atoms'][3]['origin'] = {'block': 2, 'range': [4, 9]}
    request['messages'][-1]['content'] = json.dumps(source, ensure_ascii=False)
    original = copy.deepcopy(request)
    transport = client()
    result = transport.prepare_request(request)
    packed = json.loads(result.payload['messages'][-1]['content'])
    from cps.services.reflow.layout_wire import unpack
    packed = unpack(packed)
    assert packed == source  # Includes every geometry and wrapping proof field.
    assert request == original
    assert result.payload['messages'][0] == original['messages'][0]
    assert result.payload['response_format']['json_schema']['schema'] == request['response_schema']
    transport._check_request(result)


def test_admitted_source_wire_keeps_original_message_bytes():
    request, _ = source_atom_envelope(2)
    result = client().prepare_request(request)
    assert result.payload['messages'][0] == request['messages'][0]
    assert result.payload['messages'][-1] == request['messages'][-1]


def test_compaction_does_not_relax_ceiling_or_other_protocols():
    request, _ = source_atom_envelope(4000)
    with pytest.raises(ValueError, match='48000'):
        client().prepare_request(request)
    request, _ = source_atom_envelope(700)
    request['protocol'] = 'unrelated'
    with pytest.raises(ValueError, match='48000'):
        client().prepare_request(request)
