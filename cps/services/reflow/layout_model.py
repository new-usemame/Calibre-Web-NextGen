"""Inactive layout JSON transport, using the shared durable billing boundary.

Standard routes reserve UTF-8 byte/pixel input admission and requested output,
including cache writes at 125% of prompt price. Optional full endpoint ceilings
are diagnostic only; reported billing is reconciled even when it violates bounds.
Pro aggregate billing is unsupported pending a demonstrated hard ceiling.
The byte hash is suitable for OperationCache claims. Callers own those claims and
semantic response validation; this module does not activate a pipeline or retry
an uncertain bill. Image reservations intentionally use one token per pixel.
"""
import base64
import copy
import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from decimal import Decimal, InvalidOperation
from types import MappingProxyType

from . import model, layout_qwen, layout_luna_standard, layout_glm, layout_luna_high
from .typed_model import TypedRequest, TypedAnswer, TypedStageRejected, _encoded

ROUTE_VERSION = 'layout-structured-10-ranges'
PROFILE_ROUTE_VERSION = 'layout-structured-13-ranges-profile'
MAX_TEXT_BYTES = 48000
MAX_IMAGE_PIXELS = 4000000
CACHE_WRITE_MULTIPLIER = 1.25


def _nonnegative_price(value):
    if type(value) not in (str, int, float):
        raise ValueError('invalid price value')
    try:
        rate = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('invalid price value') from exc
    if not rate.is_finite() or rate < 0:
        raise ValueError('invalid price value')
    return rate


def _has_reasoning_content(value):
    """Inspect the whole raw reply, including unexpected additional choices."""
    if isinstance(value, dict):
        return any((k in ('reasoning', 'reasoning_content', 'reasoning_details') and bool(v))
                   or _has_reasoning_content(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_reasoning_content(v) for v in value)
    return False


@dataclass(frozen=True)
class LayoutProfile:
    spec: model.ModelSpec
    output_tokens: int
    route: str
    provider: str
    service_tier: str
    images: bool
    reasoning_effort: str = ''
    completion_cap: int = 128000
    supporting_reasoning_tokens: int = 0


PROFILES = {
    'proposer': LayoutProfile(model.ModelSpec('openai/gpt-6-luna', .05, .25), 8192,
                              'openai/flex', 'OpenAI', 'flex', True, 'medium',
                              supporting_reasoning_tokens=16384),
    'reviewer': LayoutProfile(model.ModelSpec('openai/gpt-6-luna-pro', .05, .25), 4096,
                              'openai/flex', 'OpenAI', 'flex', True, 'medium', 4096),
    'ordering': LayoutProfile(model.ModelSpec('openai/gpt-6-luna-pro', .05, .25), 4096,
                              'openai/flex', 'OpenAI', 'flex', False, 'medium'),
    'lexical': LayoutProfile(model.ModelSpec('anthropic/claude-opus-5.5', 4, 20), 2048,
                             'anthropic', 'Anthropic', '', False, '', 2048),
}


# Explicit future challengers only. Defaults and their old byte/cache identities
# stay in PROFILES. MiMo advertises a reasoning toggle, not an effort selector;
# "enabled" is an honest control identity, never a provider reasoning subcap.
NAMED_PROFILES = MappingProxyType({name: (stage, _encoded(asdict(profile)))
    for name, (stage, profile) in {
    'mimo26-x1': ('proposer', LayoutProfile(
        model.ModelSpec('xiaomi/mimo-v2.6-flash', .14, .28), 8192,
        'xiaomi/fp8', 'Xiaomi', '', True, 'enabled', 128000, 16384)),
    'mimo26-d1': ('proposer', LayoutProfile(
        model.ModelSpec('xiaomi/mimo-v2.6-flash', .14, .28), 8192,
        'xiaomi/fp8', 'Xiaomi', '', True, 'disabled', 128000, 0)),
    'luna6-n1': ('reviewer', replace(PROFILES['reviewer'],
        spec=PROFILES['proposer'].spec, reasoning_effort='none')),
    'qwen38-d1': ('proposer', LayoutProfile(
        model.ModelSpec('qwen/qwen3.8-flash', .15, .47), 8192,
        'alibaba', 'Alibaba', '', True, 'disabled', 128000, 0)),
    'luna6-r1-standard': ('reviewer', LayoutProfile(
        model.ModelSpec('openai/gpt-6-luna', .10, .50), 4096,
        'openai', 'OpenAI', '', True, 'medium', 20480, 16384)),
    layout_glm.PROFILE_ID: ('proposer', LayoutProfile(
        model.ModelSpec(layout_glm.MODEL, .15, .5), 8192,
        layout_glm.ROUTE, 'DeepInfra', '', True, 'low', 128000, layout_glm.SUPPORT)),
    layout_luna_high.PROFILE_ID: ('proposer', LayoutProfile(
        model.ModelSpec(layout_luna_high.MODEL, .10, .50), 8192,
        'openai', 'OpenAI', '', True, 'high', 128000, layout_luna_high.SUPPORT)),
    layout_luna_high.REVIEWER_ID: ('reviewer', LayoutProfile(
        model.ModelSpec(layout_luna_high.MODEL, .10, .50), 4096,
        'openai', 'OpenAI', '', True, 'high', 69632, layout_luna_high.SUPPORT)),
    'luna6-m1-standard': ('proposer', LayoutProfile(
        model.ModelSpec('openai/gpt-6-luna', .10, .50), 8192,
        'openai', 'OpenAI', '', True, 'medium', 128000, 16384)),
}.items()})
CHALLENGER_SELECTION = MappingProxyType({'proposer': 'mimo26-x1', 'reviewer': 'luna6-n1'})
DISABLED_SELECTION = MappingProxyType({'proposer': 'mimo26-d1', 'reviewer': 'luna6-n1'})
QWEN_SELECTION = MappingProxyType({'proposer': 'qwen38-d1', 'reviewer': 'luna6-n1'})
LUNA_REVIEW_SELECTION = MappingProxyType({'proposer': 'luna6-m1-standard', 'reviewer': 'luna6-r1-standard'})
LUNA_STANDARD_SELECTION = MappingProxyType({'proposer': 'luna6-m1-standard', 'reviewer': 'luna6-n1'})

LUNA_HIGH_SELECTION = MappingProxyType({'proposer': layout_luna_high.PROFILE_ID, 'reviewer': layout_luna_high.REVIEWER_ID})

GLM_SELECTION = MappingProxyType({'proposer': layout_glm.PROFILE_ID, 'reviewer': 'luna6-r1-standard'})

def profile_selection(selection=None):
    """Only immutable named profiles for their declared stage may be selected."""
    if selection is None:
        return {}
    if not isinstance(selection, (dict, MappingProxyType)):
        raise ValueError('unsupported layout profile selection')
    result = dict(selection)
    for stage, name in result.items():
        if not isinstance(name, str) or name not in NAMED_PROFILES or NAMED_PROFILES[name][0] != stage:
            raise ValueError('unsupported layout profile selection')
    return result


class LayoutStageClient(model.OpenRouterClient):
    def __init__(self, api_key, stage, *, reservation_prompt_tokens=None,
                 reservation_completion_tokens=None, profile=None, profile_id=None, review_policy=None, **kwargs):
        if review_policy is not None and (stage != 'reviewer' or (profile_id, review_policy) not in ((layout_luna_standard.REVIEWER_ID, layout_glm.REVIEW_POLICY), (layout_luna_high.REVIEWER_ID, layout_luna_high.REVIEW_POLICY))):
            raise ValueError('unsupported review policy')
        self.review_policy = self._admitted_review_policy = review_policy
        self._glm_prices = None
        if stage not in PROFILES:
            raise ValueError('unknown layout stage')
        if profile_id is not None and (set(kwargs).intersection({'model_id', 'tier'})
                or reservation_prompt_tokens is not None or reservation_completion_tokens is not None):
            raise ValueError('named profile controls and computed reservations cannot be overridden')
        if profile_id in (*layout_luna_standard.PROFILE_IDS, *layout_luna_high.PROFILE_IDS, layout_glm.PROFILE_ID):
            if type(kwargs.get('max_retries', 1)) is not int or kwargs.get('max_retries', 1) != 1:
                raise ValueError('Luna Standard arm permits one attempt only')
            kwargs['max_retries'] = 1
        super().__init__(api_key, **kwargs)
        if (reservation_prompt_tokens is None) != (reservation_completion_tokens is None):
            raise ValueError('diagnostic endpoint ceilings must be supplied together')
        if reservation_prompt_tokens is not None:
            for ceiling in (reservation_prompt_tokens, reservation_completion_tokens):
                if type(ceiling) is not int or ceiling <= 0:
                    raise ValueError('positive diagnostic endpoint ceilings required')
        self.reservation_prompt_tokens = reservation_prompt_tokens
        self.reservation_completion_tokens = reservation_completion_tokens
        self.stage = stage
        self.profile_id = profile_id
        self._admitted_profile_id = profile_id
        if profile_id is not None:
            profile_selection({stage: profile_id})
            if profile is not None:
                raise ValueError('named profile cannot be overridden')
            record = json.loads(NAMED_PROFILES[profile_id][1])
            self.profile = LayoutProfile(spec=model.ModelSpec(**record.pop('spec')), **record)
        else:
            self.profile = profile or PROFILES[stage]
        allowed = [PROFILES[stage]]
        if stage == 'proposer':
            allowed.append(replace(PROFILES[stage], reasoning_effort='none',
                                   supporting_reasoning_tokens=0))
        if stage in ('reviewer', 'ordering'):
            allowed.append(replace(PROFILES[stage], spec=PROFILES['proposer'].spec, reasoning_effort=PROFILES['proposer'].reasoning_effort))
        if profile_id is None and self.profile not in allowed:
            raise ValueError('unsupported layout profile override')
        self.spec = self.profile.spec
        self.model_id = self.spec.model_id

    @property
    def route_version(self):
        if self.profile_id in layout_luna_high.PROFILE_IDS:
            return self.review_policy or layout_luna_high.ROUTE_VERSION
        if self.review_policy: return layout_glm.REVIEW_POLICY
        if self.profile_id == layout_glm.PROFILE_ID: return layout_glm.ROUTE_VERSION
        if self.profile_id in layout_luna_standard.PROFILE_IDS:
            return (layout_luna_standard.REVIEW_ROUTE_VERSION if self.profile_id == layout_luna_standard.REVIEWER_ID
                    else layout_luna_standard.ROUTE_VERSION)
        if self.profile_id == layout_qwen.PROFILE_ID:
            return layout_qwen.ROUTE_VERSION
        return PROFILE_ROUTE_VERSION if self.profile_id else ROUTE_VERSION

    def _check_profile(self):
        if self.review_policy != self._admitted_review_policy:
            raise ValueError('review policy changed')
        if self.profile_id != self._admitted_profile_id:
            raise ValueError('named layout profile changed')
        if self.profile_id in (*layout_luna_standard.PROFILE_IDS, *layout_luna_high.PROFILE_IDS, layout_glm.PROFILE_ID) and (type(self.max_retries) is not int or self.max_retries != 1):
            raise ValueError('Luna Standard retry policy changed')
        if self.profile_id is not None:
            profile_selection({self.stage: self.profile_id})
            if (self.reservation_prompt_tokens is not None or self.reservation_completion_tokens is not None
                    or _encoded(asdict(self.profile)) != NAMED_PROFILES[self.profile_id][1]
                    or self.spec != self.profile.spec or self.model_id != self.spec.model_id):
                raise ValueError('named layout profile changed')

    @property
    def reservation_rates(self):
        if self.profile_id == layout_glm.PROFILE_ID: return (.15, .5)
        if self.profile_id in (*layout_luna_standard.PROFILE_IDS, *layout_luna_high.PROFILE_IDS):
            return (.20, .75)
        # Historical flex liability: preserve previous profiles and arithmetic.
        return (.10, .375) if self.model_id == 'openai/gpt-6-luna' else (self.spec.prompt_usd_per_mtok, self.spec.completion_usd_per_mtok)

    @property
    def input_reservation_rate(self):
        if self.profile_id == layout_glm.PROFILE_ID: return layout_glm.INPUT_CEILING
        if self.profile_id in (*layout_luna_standard.PROFILE_IDS, *layout_luna_high.PROFILE_IDS):
            return layout_luna_standard.INPUT_CEILING
        if self.profile_id == layout_qwen.PROFILE_ID:
            return layout_qwen.INPUT_CEILING
        return self.reservation_rates[0] * CACHE_WRITE_MULTIPLIER

    def reservation_bound(self, prompt_tokens, completion_tokens):
        if self.profile_id in (layout_glm.PROFILE_ID, layout_qwen.PROFILE_ID, *layout_luna_standard.PROFILE_IDS, *layout_luna_high.PROFILE_IDS):
            return (prompt_tokens * self.input_reservation_rate
                    + completion_tokens * self.reservation_rates[1]) / 1e6
        # Preserve predecessor floating arithmetic and exact TypedRequest bytes.
        return (prompt_tokens * self.reservation_rates[0] * CACHE_WRITE_MULTIPLIER
                + completion_tokens * self.reservation_rates[1]) / 1e6

    def _check_search_disabled(self, payload):
        if self.profile_id == layout_glm.PROFILE_ID: layout_glm.wire(payload)
        if self.profile_id in (*layout_luna_standard.PROFILE_IDS, *layout_luna_high.PROFILE_IDS):
            (layout_luna_high if self.profile_id in layout_luna_high.PROFILE_IDS else layout_luna_standard).wire(payload)
        if self.profile_id == layout_qwen.PROFILE_ID:
            layout_qwen.wire(payload)
        # Only this pinned NONE profile admits an advertised optional search fee.
        # A closed wire forbids plugins, tools, search options, model variants and
        # any future activation field. Check independently of reconstruction so
        # extending the builder cannot silently acquire paid-search authority.
        if self.profile_id == 'luna6-n1' and (
                set(payload) != {'model', 'messages', 'max_tokens', 'usage',
                    'response_format', 'provider', 'service_tier', 'reasoning'}
                or payload['model'] != 'openai/gpt-6-luna'
                or payload['reasoning'] != {'effort': 'none'}):
            raise ValueError('selected layout request cannot enable search')

    def prepare_request(self, request, raster=None):
        """Build from text messages, exact source identity and a strict object schema.

        Required envelope keys: protocol, source_identity, prompt_version,
        messages, response_schema. Optional context is retained in reservations.
        A JPEG may accompany proposer/reviewer only.
        """
        self._check_profile()
        envelope = copy.deepcopy(request)
        if set(envelope) - {'protocol', 'source_identity', 'prompt_version', 'messages',
                            'response_schema', 'context'}:
            raise ValueError('unknown layout envelope fields')
        for key in ('protocol', 'prompt_version'):
            if not isinstance(envelope.get(key), str) or not envelope[key]:
                raise ValueError('layout protocol and prompt version required')
        if not envelope.get('source_identity'):
            raise ValueError('exact source identity required')
        messages = envelope.pop('messages')
        if not isinstance(messages, list) or not messages:
            raise ValueError('text messages required')
        for message in messages:
            if set(message) != {'role', 'content'} or message['role'] not in ('system', 'user', 'assistant') or not isinstance(message['content'], str):
                raise ValueError('layout messages must contain bounded text only')
        schema = envelope.pop('response_schema')
        if not isinstance(schema, dict) or schema.get('type') != 'object' or schema.get('additionalProperties') is not False or not isinstance(schema.get('properties'), dict) or set(schema.get('required', [])) != set(schema['properties']):
            raise ValueError('strict object response schema required')
        context = envelope.get('context', {})
        if not isinstance(context, dict):
            raise ValueError('layout context must be an object')
        from . import layout_allocation, layout_wire
        envelope.update(stage=self.stage, route_version=self.route_version,
                        allocation_version=layout_allocation.version(self.profile_id, self.review_policy), wire_version=layout_wire.VERSION,
                        raster_sha256=hashlib.sha256(raster).hexdigest() if raster is not None else None)
        if self.review_policy: envelope['review_policy'] = self.review_policy
        if self.profile_id:
            envelope['profile_id'] = self.profile_id
        if self.profile_id in layout_luna_high.PROFILE_IDS:
            envelope['c'] = layout_luna_high.CONTROL_VERSION
        # Identity is in transmitted bytes, so schema/prompt/source/protocol changes
        # cannot reuse another request's paid result even when prose is identical.
        # Keep the shared instructions before per-page identity so providers can
        # reuse their prompt prefix. Exact response-cache identity still hashes
        # the complete wire, including every source and raster binding.
        messages.insert(1, {'role': 'system', 'content': _encoded(envelope).decode()})
        pixels = 0
        if raster is not None:
            if not self.profile.images:
                raise ValueError('batch layout stages are text only')
            dimensions = model._jpeg_dimensions(raster)
            if dimensions is None:
                raise ValueError('layout raster must be a JPEG')
            pixels = dimensions[0] * dimensions[1]
            if not 0 < pixels <= MAX_IMAGE_PIXELS:
                raise ValueError('layout raster exceeds four megapixels')
        # Every source-bound stage uses the same exact reversible evidence
        # representation. Review/order/style/lexical context is never trimmed.
        # Schema and identity also consume prompt context, not just user prose.
        logical = layout_wire.source_view(messages)
        if isinstance(logical,dict) and logical.get('domain') == 'layout-domain-2' and 'wire_format' not in json.loads(messages[-1]['content']):
            from . import layout_domain
            messages[-1]['content'] = _encoded(layout_domain.pack_view(logical)).decode()
        if isinstance(logical,dict) and logical.get('domain') == 'layout-range-choices-1' and 'wire_format' not in json.loads(messages[-1]['content']):
            from . import layout_ranges
            messages[-1]['content'] = _encoded(layout_ranges.pack_view(logical)).decode()
        if isinstance(logical,dict) and logical.get('domain') == 'layout-semantic-choices-1' and 'wire_format' not in json.loads(messages[-1]['content']):
            from . import layout_choices
            messages[-1]['content'] = _encoded(layout_choices.pack_view(logical)).decode()
        text_bytes = len(_encoded(messages)) + len(_encoded(schema))
        if (text_bytes > MAX_TEXT_BYTES
                and envelope['protocol'] == 'source-bound-layout-1'):
            layout_wire.compact_message(messages[-1])
            text_bytes = len(_encoded(messages)) + len(_encoded(schema))
        if text_bytes > MAX_TEXT_BYTES:
            raise ValueError('layout text exceeds 48000 bytes')
        output_tokens = layout_allocation.completion_tokens(self.stage, self.profile, layout_wire.source_view(messages))
        prompt_bound = text_bytes + 1024 + pixels
        if self.model_id == 'openai/gpt-6-luna':
            # A standard single-context request cannot consume more input than
            # its admitted endpoint maximum, checked again before dispatch.
            prompt_bound = min(prompt_bound, 922000)
        if raster is not None:
            messages.append({'role': 'user', 'content': [{'type': 'image_url', 'image_url': {
                'url': 'data:image/jpeg;base64,' + base64.b64encode(raster).decode(), 'detail': 'original'}}]})
        if self.reservation_prompt_tokens is not None and (prompt_bound > self.reservation_prompt_tokens or output_tokens > self.reservation_completion_tokens):
            raise ValueError('request exceeds configured diagnostic ceilings')
        payload = {'model': self.model_id, 'messages': messages,
                   'max_tokens': output_tokens, 'usage': {'include': True},
                   'response_format': {'type': 'json_schema', 'json_schema': {
                       'name': 'layout_response', 'strict': True, 'schema': schema}},
                   'provider': {'order': [self.profile.route], 'allow_fallbacks': False,
                                'require_parameters': True, 'max_price': dict(prompt=self.reservation_rates[0], completion=self.reservation_rates[1])}}
        if self.profile.service_tier:
            payload['service_tier'] = self.profile.service_tier
        if self.profile.reasoning_effort:
            if self.profile.reasoning_effort in ('enabled', 'disabled'):
                payload['reasoning'] = {'enabled': self.profile.reasoning_effort == 'enabled'}
            else:
                payload['reasoning'] = {'effort': self.profile.reasoning_effort}
        self._check_search_disabled(payload)
        raw = _encoded(payload)
        digest = hashlib.sha256(raw).hexdigest()
        reserved_prompt = self.reservation_prompt_tokens or prompt_bound
        reserved_completion = self.reservation_completion_tokens or output_tokens
        bound = self.reservation_bound(reserved_prompt, reserved_completion)
        audit = dict(context, stage=self.stage, protocol=envelope['protocol'],
                     route_version=self.route_version, source_identity=envelope['source_identity'],
                     allocation_version=layout_allocation.version(self.profile_id, self.review_policy), reasoning_effort=self.profile.reasoning_effort,
                     completion_tokens=output_tokens, wire_version=layout_wire.VERSION,
                     visible_answer_tokens=layout_allocation.visible_completion_tokens(self.stage, self.profile, logical),
                     supporting_reasoning_tokens=self.profile.supporting_reasoning_tokens,
                     raster_sha256=envelope['raster_sha256'], request_sha256=digest)
        if self.profile_id:
            audit.update(profile_id=self.profile_id, profile=asdict(self.profile),
                         reservation_rates=list(self.reservation_rates))
        if self.profile_id == layout_qwen.PROFILE_ID:
            audit.update(meter_policy=layout_qwen.POLICY_VERSION,
                         input_liability_usd_per_million=self.input_reservation_rate,
                         canonical_model=layout_qwen.CANONICAL)
        if self.profile_id in (*layout_luna_standard.PROFILE_IDS, *layout_luna_high.PROFILE_IDS):
            audit.update(meter_policy=layout_luna_standard.POLICY_VERSION,
                         input_liability_usd_per_million=self.input_reservation_rate,
                         canonical_model=layout_luna_standard.CANONICAL,
                         reasoning_budget_is_native_cap=False,
                         requested_tier='default', max_attempts=1, actual_translation_observed=False)
        if self.profile_id in layout_luna_high.PROFILE_IDS:
            audit.update(meter_policy=layout_luna_high.POLICY_VERSION, control_version=layout_luna_high.CONTROL_VERSION)
        if self.profile_id == layout_glm.PROFILE_ID:
            audit.update(meter_policy=layout_glm.POLICY_VERSION, canonical_model=layout_glm.CANONICAL,
                input_liability_usd_per_million=self.input_reservation_rate, reasoning_budget_is_native_cap=False,
                requested_tier='default', max_attempts=1, actual_translation_observed=False)
        if self.review_policy: audit['review_policy'] = self.review_policy
        return TypedRequest(raw.decode(), digest, bound, prompt_bound,
                            output_tokens, envelope['prompt_version'], _encoded(audit).decode())

    def _check_request(self, request):
        try:
            payload = request.payload
            self._check_search_disabled(payload)
            messages = copy.deepcopy(payload['messages'])
            envelope = json.loads(messages.pop(1)['content'])
            raster = None
            if envelope['raster_sha256'] is not None:
                image = messages.pop()['content'][0]['image_url']
                raster = base64.b64decode(image['url'].removeprefix('data:image/jpeg;base64,'), validate=True)
                if hashlib.sha256(raster).hexdigest() != envelope['raster_sha256']:
                    raise ValueError('raster identity changed')
            for key in ('stage', 'route_version', 'raster_sha256', 'allocation_version', 'wire_version'):
                envelope.pop(key)
            if envelope.pop('review_policy', None) != self.review_policy:
                raise ValueError('foreign review policy')
            if self.profile_id in layout_luna_high.PROFILE_IDS:
                if envelope.pop('c') != layout_luna_high.CONTROL_VERSION:
                    raise ValueError('foreign High control version')
            if 'profile_id' in envelope:
                if envelope.pop('profile_id') != self.profile_id:
                    raise ValueError('foreign layout profile')
            envelope.update(messages=messages, response_schema=payload['response_format']['json_schema']['schema'])
            rebuilt = self.prepare_request(envelope, raster)
            if rebuilt != request:
                raise ValueError('layout request identity or reservation changed')
        except (KeyError, TypeError, IndexError, AttributeError, ValueError) as exc:
            raise ValueError('layout request identity or reservation changed') from exc

    def preflight(self, prompt_bound, completion_bound=None):
        """Read current route controls/prices before each dispatch; fail closed."""
        self._check_profile()
        if completion_bound is None:
            completion_bound = self.profile.output_tokens
        if type(completion_bound) is not int or not 0 < completion_bound <= self.profile.completion_cap:
            raise model.ModelError('invalid layout completion allocation; no paid request sent')
        if self.model_id.endswith('-pro'):
            raise model.ModelError('Pro aggregate billing has no established reservation ceiling; no paid request sent')
        try:
            response = (self._session or model.requests).get(
                'https://openrouter.ai/api/v1/models/' + self.model_id + '/endpoints',
                headers={'Authorization': 'Bearer ' + self._api_key}, timeout=self.timeout)
            data = response.json()['data']
            routes = [row for row in data['endpoints'] if row.get('tag') == self.profile.route]
            if response.status_code != 200 or len(routes) != 1:
                raise ValueError('route unavailable')
            route = routes[0]
            if self.profile_id == layout_glm.PROFILE_ID:
                layout_glm.endpoint(data, route)
                self._glm_prices = copy.deepcopy(route['pricing'])
            if self.profile_id == layout_qwen.PROFILE_ID:
                layout_qwen.endpoint(data, route)
            if self.profile_id in (*layout_luna_standard.PROFILE_IDS, *layout_luna_high.PROFILE_IDS):
                layout_luna_standard.endpoint(data, route)
            if self.profile_id and (data.get('id') != self.model_id or route.get('model_id') != self.model_id
                    or route.get('provider_name') != self.profile.provider):
                raise ValueError('foreign endpoint identity')
            modalities = {'text', 'image'} if self.profile.images else {'text'}
            if route.get('status') != 0 or not modalities.issubset(data['architecture']['input_modalities']):
                raise ValueError('route modality unavailable')
            controls = {'max_tokens', 'response_format', 'structured_outputs'}
            if self.profile.reasoning_effort:
                controls.add('reasoning')
            if not controls.issubset(route['supported_parameters']):
                raise ValueError('strict output controls unavailable')
            if int(route.get('max_completion_tokens') or 0) < completion_bound:
                raise ValueError('output capacity unavailable')
            context = int(route.get('context_length') or 0)
            prompt_limit = int(route.get('max_prompt_tokens') or context)
            if context <= 0 or prompt_limit <= 0 or prompt_bound > prompt_limit or prompt_bound + completion_bound > context:
                raise ValueError('context capacity unavailable')
            if self.reservation_prompt_tokens is not None and (prompt_limit > self.reservation_prompt_tokens or int(route['max_completion_tokens']) > self.reservation_completion_tokens):
                raise ValueError('endpoint capacity exceeds reserved ceiling')
            if self.model_id == 'openai/gpt-6-luna' and prompt_limit > 922000:
                raise ValueError('standard input capacity exceeds admitted ceiling')
            if self._admitted_profile_id:
                # Exact current model controls are mandatory on these NEW identities.
                # No account/key refresh: this is the same dispatch credential.
                options_response = (self._session or model.requests).get(
                    'https://openrouter.ai/api/v1/models',
                    headers={'Authorization': 'Bearer ' + self._api_key}, timeout=self.timeout)
                models = [r for r in options_response.json()['data'] if r.get('id') == self.model_id]
                if options_response.status_code != 200 or len(models) != 1:
                    raise ValueError('reasoning controls unavailable')
                options = models[0]['reasoning']
                if self.profile_id == layout_glm.PROFILE_ID:
                    layout_glm.reasoning(options, models[0])
                if self.profile_id == layout_qwen.PROFILE_ID:
                    layout_qwen.reasoning(options, models[0])
                if self.profile_id in (*layout_luna_standard.PROFILE_IDS, *layout_luna_high.PROFILE_IDS):
                    (layout_luna_high if self.profile_id in layout_luna_high.PROFILE_IDS else layout_luna_standard).reasoning(options, models[0])
                if not isinstance(options, dict) or type(options.get('mandatory')) is not bool:
                    raise ValueError('reasoning controls unavailable')
                if self.profile.reasoning_effort == 'none':
                    efforts = options.get('supported_efforts')
                    if options['mandatory'] or not isinstance(efforts, list) or not all(isinstance(e, str) for e in efforts) or 'none' not in efforts:
                        raise ValueError('answer-only control unavailable')
                if self.profile.reasoning_effort == 'disabled' and options['mandatory']:
                    raise ValueError('thinking-disabled control unavailable')
                if self.profile_id in ('mimo26-x1', 'mimo26-d1'):
                    if context > 1048576 or prompt_limit > 1048576 or int(route['max_completion_tokens']) > 131072:
                        raise ValueError('MiMo capacity outside admitted endpoint')
            pricing = route['pricing']
            if self.profile_id:
                if not isinstance(pricing, dict):
                    raise ValueError('invalid pricing controls')
                allowed_prices = {'prompt', 'completion', 'input_cache_write', 'input_cache_read',
                        'request', 'image', 'internal_reasoning', 'discount', 'overrides'}
                if self.profile_id == layout_qwen.PROFILE_ID:
                    allowed_prices.update(layout_qwen.PRICE_ALIASES)
                if self.profile_id in (*layout_luna_standard.PROFILE_IDS, *layout_luna_high.PROFILE_IDS):
                    allowed_prices.update(layout_luna_standard.PRICE_ALIASES)
                    allowed_prices.add('web_search')
                if self.profile_id == 'luna6-n1':
                    # preflight only admits the fixed builder policy. call checks
                    # its actual closed wire before preflight and any reservation.
                    allowed_prices.add('web_search')
                    if 'web_search' in pricing:
                        _nonnegative_price(pricing['web_search'])
                if self.profile_id == layout_glm.PROFILE_ID:
                    allowed_prices.update(layout_luna_standard.PRICE_ALIASES)
                if set(pricing) - allowed_prices:
                    raise ValueError('unsupported pricing control')
                if self.profile_id != layout_glm.PROFILE_ID and _nonnegative_price(pricing.get('discount', 0)) != 0:
                    raise ValueError('unsupported pricing discount')
                overrides = pricing.get('overrides', [])
                if not isinstance(overrides, list):
                    raise ValueError('unsupported price tiers')
                for row in overrides:
                    if (not isinstance(row, dict) or type(row.get('min_prompt_tokens')) is not int
                            or row['min_prompt_tokens'] < 0
                            or set(row) - {'min_prompt_tokens', 'prompt', 'completion', 'input_cache_write',
                                'input_cache_read', 'request', 'image', 'internal_reasoning'}
                                - (set(layout_luna_standard.PRICE_ALIASES) if self.profile_id in (*layout_luna_standard.PROFILE_IDS, *layout_luna_high.PROFILE_IDS) else set())):
                        raise ValueError('unsupported price tier floor or controls')
                # Validate supplied values even in tiers not used by this request;
                # a malformed advertised cost is never interpreted as free.
                for row in [pricing] + overrides:
                    for name in ('prompt', 'completion', 'input_cache_write', 'input_cache_read',
                                 'request', 'image', 'internal_reasoning'):
                        if name in row:
                            _nonnegative_price(row[name])
                if self.profile_id in ('mimo26-x1', 'mimo26-d1'):
                    if (float(pricing['prompt']) != .14e-6 or float(pricing['completion']) != .28e-6
                            or float(pricing['input_cache_read']) != .0028e-6 or pricing.get('overrides')):
                        raise ValueError('MiMo pricing outside admitted profile')
            rows = [pricing] + list(pricing.get('overrides') or [])
            for row in rows:
                if row is not pricing and (self.reservation_prompt_tokens or prompt_bound) < int(row.get('min_prompt_tokens') or 0):
                    continue
                for name, cap in [('prompt', self.reservation_rates[0]),
                                  ('completion', self.reservation_rates[1]),
                                  ('input_cache_write', self.input_reservation_rate),
                                  ('input_cache_read', self.input_reservation_rate)]:
                    value = row.get(name, pricing.get(name))
                    if value is None and name.startswith('input_cache_'):
                        continue
                    rate = float(value)
                    if not math.isfinite(rate) or rate < 0 or rate > cap / 1e6 + 1e-18:
                        raise ValueError('route price exceeds admission')
                # Non-token charges have no reservation authority in this route.
                for name in ('request', 'image', 'internal_reasoning'):
                    if float(row.get(name, pricing.get(name, 0)) or 0) != 0:
                        raise ValueError('unsupported route surcharge')
        except (model.requests.RequestException, ValueError, KeyError, TypeError, OverflowError):
            raise model.ModelError('layout route price, controls or capacity unavailable; no paid request sent')

    def call(self, request, ledger=None, page_label=None, should_stop=None):
        if not self.configured or self.dry_run:
            raise model.ModelError('layout model is not configured for dispatch')
        self._check_request(request)
        self.preflight(request.prompt_tokens_bound, request.response_token_bound)
        attempt_id = None
        try:
            data, _, attempt_id = self._post(request.payload, ledger=ledger,
                bound=request.bound_usd, page_label=page_label, should_stop=should_stop,
                prompt_version=request.prompt_version, attempt_context=json.loads(request.context_json),
                safe_errors=True, payload_bytes=request.payload_json.encode('utf-8'))
            billing_data = (layout_luna_standard.billing_record(data)
                if self._admitted_profile_id in (*layout_luna_standard.PROFILE_IDS, *layout_luna_high.PROFILE_IDS, layout_glm.PROFILE_ID) else data)
            pt, ct, cost, cost_source = self._settle(billing_data, ledger=ledger, attempt_id=attempt_id,
                                                   bound=request.bound_usd, require_reported=True)
            def refuse(message, code='response_rejected'):
                rejection = TypedStageRejected(message, reason_code=code, cost_usd=cost,
                    cost_source=cost_source, prompt_tokens=pt, completion_tokens=ct, attempt=attempt_id)
                if code == 'allocation_mismatch':
                    rejection.stop_dispatch = True
                return rejection
            if self._admitted_profile_id in (*layout_luna_standard.PROFILE_IDS, *layout_luna_high.PROFILE_IDS, layout_glm.PROFILE_ID):
                meter = (layout_glm if self._admitted_profile_id == layout_glm.PROFILE_ID else
                         layout_luna_high if self._admitted_profile_id in layout_luna_high.PROFILE_IDS else layout_luna_standard)
                # Every durably settled response is audited before route, finish,
                # JSON or source refusal. Preserve these findings for the owner.
                audit = dict(meter_policy=meter.POLICY_VERSION,
                             raw_response_sha256=hashlib.sha256(_encoded(data)).hexdigest(),
                             reported_usage=data.get('usage'),
                             reported_tier=data.get('service_tier'))
                try:
                    self._check_profile()
                    audit = meter.usage(data, pt, ct,
                        json.loads(request.context_json)['visible_answer_tokens'], request.response_token_bound,
                        **({'actual_prices':self._glm_prices} if meter is layout_glm else {}))
                except (KeyError, TypeError, ValueError) as exc:
                    if ledger is not None:
                        ledger.record(dict(kind='reasoning_control_audit', attempt=attempt_id,
                            stage=self.stage, accepted=False, finding=str(exc), **audit))
                    raise refuse(('GLM' if meter is layout_glm else 'Luna Standard')+' response meter/control audit differs: '+str(exc), 'allocation_mismatch')
                if ledger is not None:
                    ledger.record(dict(kind='reasoning_control_audit', attempt=attempt_id,
                        stage=self.stage, accepted=True, **audit))
            if cost > request.bound_usd + 1e-12:
                raise refuse('provider bill exceeded the reserved request bound', 'billing_bound')
            tier=data.get('service_tier') or ''
            allowed_tiers={self.profile.service_tier} if self.profile.service_tier else {'', 'default'}
            if data.get('model') != self.model_id or data.get('provider') != self.profile.provider or tier not in allowed_tiers:
                raise refuse('layout response route or service tier differs', 'route_mismatch')
            if self._admitted_profile_id:
                try:
                    self._check_profile()
                except ValueError:
                    raise refuse('layout response profile changed', 'route_mismatch')
                usage = data['usage']
                if any(type(usage.get(k)) is not int for k in ('prompt_tokens', 'completion_tokens')) or pt > request.prompt_tokens_bound or ct > request.response_token_bound:
                    raise refuse('provider usage exceeds the requested allocation', 'allocation_mismatch')
                metered_ceiling = self.reservation_bound(pt, ct)
                if cost > metered_ceiling + 1e-12:
                    raise refuse('provider bill exceeds admitted rates at reported usage', 'billing_bound')
                if self.profile_id == layout_qwen.PROFILE_ID:
                    try:
                        layout_qwen.usage(data, pt, ct)
                    except (KeyError, TypeError, ValueError):
                        raise refuse('Qwen response meter or hidden-field audit differs', 'allocation_mismatch')
            try:
                choice = data['choices'][0]
                if choice.get('finish_reason') == 'length':
                    raise refuse('layout completion limit reached before a complete JSON answer', 'completion_limit')
                if choice.get('finish_reason') != 'stop':
                    raise refuse('layout response did not finish a complete JSON answer', 'incomplete_response')
                answer = (layout_glm.decode(choice['message']['content'])
                    if self.profile_id == layout_glm.PROFILE_ID or self.profile_id in layout_luna_high.PROFILE_IDS or self.review_policy
                    else json.loads(choice['message']['content']))
                if not isinstance(answer, dict):
                    raise ValueError('not an object')
                if self.profile_id == layout_glm.PROFILE_ID or self.profile_id in layout_luna_high.PROFILE_IDS or self.review_policy:
                    from jsonschema import Draft202012Validator
                    schema=request.payload['response_format']['json_schema']['schema']
                    if not Draft202012Validator(schema).is_valid(answer):
                        raise refuse('GLM pair reply violates the exact requested schema', 'schema_mismatch')
                details = data['usage'].get('completion_tokens_details') or {}
                if self.profile_id and not isinstance(details, dict):
                    raise refuse('provider reasoning accounting is invalid', 'allocation_mismatch')
                raw_reasoning = details.get('reasoning_tokens', 0)
                if self.profile_id and self.profile.reasoning_effort in ('none', 'disabled') and 'reasoning_tokens' not in details:
                    raise refuse('provider omitted answer-only reasoning accounting', 'allocation_mismatch')
                if self.profile_id and (type(raw_reasoning) is not int or not 0 <= raw_reasoning <= ct):
                    raise refuse('provider reasoning usage is invalid', 'allocation_mismatch')
                reasoning = int(raw_reasoning or 0)
                if reasoning < 0:
                    raise ValueError('invalid reasoning usage')
                reported_reasoning = any(choice['message'].get(k) for k in ('reasoning', 'reasoning_content', 'reasoning_details'))
                if self.profile.reasoning_effort == 'disabled':
                    reported_reasoning = _has_reasoning_content(data)
                if self.profile.reasoning_effort in ('none', 'disabled') and (reasoning or (self.profile_id and reported_reasoning)):
                    raise refuse('provider used reasoning against the answer-only allocation', 'allocation_mismatch')
            except (KeyError, IndexError, TypeError, ValueError, OverflowError):
                raise refuse('layout response is incomplete or malformed JSON', 'malformed_json')
            return TypedAnswer(answer, attempt_id or '', self.model_id, self.profile.provider,
                self.profile.service_tier, cost, pt, ct, request.sha256, request.prompt_version, reasoning)
        except model.ModelError as exc:
            if ledger is not None:
                ledger.record({'kind': 'attempt_diagnostic', 'attempt': getattr(exc, 'attempt', None) or attempt_id,
                               'stage': self.stage, 'request_sha256': request.sha256, 'failure_type': type(exc).__name__,
                               'reason_code': getattr(exc, 'reason_code', None)})
            raise
