"""Source-operation transport. Both stages use the common durable HTTP boundary."""
import copy
import base64
import hashlib
import json
import math
from dataclasses import dataclass

from . import model

ROUTE_VERSION = 'source-operations-flex-1'
SOURCE_REVISION = 'source-operations-enriched-heading-evidence-4'
MAX_OUTPUT_TOKENS = 4096
# Not publicly activated until independent semantic and application gates pass.
QUALITY_RELEASED = False
STAGES = {
    'proposer': model.ModelSpec('openai/gpt-5.6-luna', .10, .60, label='Source proposal'),
    'verifier': model.ModelSpec('openai/gpt-5.6-terra', 1.0, 6.0, label='Source approval'),
}


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


@dataclass(frozen=True)
class TypedRequest:
    payload_json: str
    sha256: str
    bound_usd: float
    prompt_tokens_bound: int
    response_token_bound: int
    prompt_version: str
    context_json: str

    @property
    def payload(self):
        return json.loads(self.payload_json)


@dataclass
class TypedAnswer:
    response: dict
    attempt: str
    model: str
    provider: str
    service_tier: str
    cost_usd: float
    prompt_tokens: int
    completion_tokens: int
    request_sha256: str
    prompt_version: str
    reasoning_tokens: int = 0


class TypedStageRejected(model.UnusableAnswer):
    def __init__(self, message, reason_code='response_rejected', **kwargs):
        super().__init__(message, **kwargs)
        self.reason_code = reason_code
        self.stop_dispatch = reason_code in ('route_mismatch', 'billing_bound')


class TypedStageClient(model.OpenRouterClient):
    def __init__(self, api_key, stage, **kwargs):
        if stage not in STAGES:
            raise ValueError('unknown structural stage')
        super().__init__(api_key, **kwargs)
        self.stage = stage
        self.spec = STAGES[stage]
        self.model_id = self.spec.model_id
        self.route = None

    def prepare_request(self, request, raster):
        envelope = copy.deepcopy(request)
        identity = envelope.pop('request_sha256', None)
        if identity != hashlib.sha256(_encoded(envelope)).hexdigest():
            raise ValueError('shared request envelope changed')
        if not 0 < request['response_token_bound'] <= MAX_OUTPUT_TOKENS:
            raise ValueError('response serialization does not fit output allowance')
        dimensions = model._jpeg_dimensions(raster)
        if dimensions is None:
            raise ValueError('source JPEG dimensions required')
        width, height = dimensions
        patches = math.ceil(width/32) * math.ceil(height/32)
        if max(width,height)>65535 or patches>30000:
            raise ValueError('source image exceeds documented patch bounds')
        image_tokens = math.ceil(patches * 1.2) + 1
        messages = copy.deepcopy(request['messages'])
        images = [p for m in messages if isinstance(m.get('content'), list)
                  for p in m['content'] if p.get('type') == 'image_url']
        if len(images) != 1 or images[0]['image_url']['url'] != 'data:image/jpeg;base64,' + model._b64(raster):
            raise ValueError('request image identity differs from bounded raster')
        images[0]['image_url']['detail'] = 'original'
        text_bytes = sum(model._utf8_bytes(m['content']) if isinstance(m.get('content'), str)
            else sum(model._utf8_bytes(p.get('text')) for p in m['content'] if p.get('type') == 'text')
            for m in messages)
        prompt_bound = text_bytes + 128 + image_tokens
        payload = {'model': self.model_id, 'messages': messages, 'temperature':0, 'max_tokens': MAX_OUTPUT_TOKENS,
                   'reasoning': {'effort': 'medium'}, 'service_tier': 'flex', 'usage': {'include': True},
                   'provider': {'order': ['openai/flex'], 'allow_fallbacks': False,
                                'max_price': self.spec.max_price}}
        schema = request['response_schema']['properties']
        context = {'stage': self.stage, 'route_version': ROUTE_VERSION,
                   'snapshot_id': schema['snapshot_id']['const']}
        if 'proposal_id' in schema: context['proposal_id'] = schema['proposal_id']['const']
        raw = _encoded(payload);digest = hashlib.sha256(raw).hexdigest()
        context['request_sha256'] = digest
        bound = (prompt_bound*self.spec.prompt_usd_per_mtok + MAX_OUTPUT_TOKENS*self.spec.completion_usd_per_mtok)/1e6
        return TypedRequest(raw.decode(), digest, bound, prompt_bound, request['response_token_bound'],
                            request['prompt_version'], json.dumps(context))

    def preflight(self, prompt_bound):
        if self.route is None:
            try:
                response = (self._session or model.requests).get(
                    'https://openrouter.ai/api/v1/models/' + self.model_id + '/endpoints',
                    headers={'Authorization':'Bearer '+self._api_key}, timeout=self.timeout)
                data = response.json()['data']
                routes = [r for r in data['endpoints'] if r.get('tag')=='openai/flex']
                if response.status_code!=200 or len(routes)!=1:
                    raise ValueError('route unavailable')
                route = routes[0]
                if route.get('status')!=0 or not {'image','text'}.issubset(data['architecture']['input_modalities']):
                    raise ValueError('route unavailable')
                if not {'reasoning','max_tokens'}.issubset(route['supported_parameters']):
                    raise ValueError('route controls unavailable')
                if int(route.get('max_completion_tokens') or 0)<MAX_OUTPUT_TOKENS:
                    raise ValueError('route output capacity')
                self.route=copy.deepcopy(route)
            except (model.requests.RequestException,ValueError,TypeError,KeyError,OverflowError):
                raise model.ModelError('structural route preflight unavailable; no paid request sent')
        try:
            route=self.route;pricing=route['pricing']
            limit=int(route.get('max_prompt_tokens') or route.get('context_length') or 0)
            if limit and prompt_bound+MAX_OUTPUT_TOKENS>limit:raise ValueError('context capacity')
            rates=[(float(pricing['prompt']),float(pricing['completion']))]
            for row in pricing.get('overrides') or []:
                if prompt_bound>=int(row.get('min_prompt_tokens') or 0):
                    rates.append((float(row['prompt']),float(row['completion'])))
            if any(not math.isfinite(p) or not math.isfinite(c) or p<0 or c<0 or
                   p>self.spec.prompt_usd_per_mtok/1e6 or c>self.spec.completion_usd_per_mtok/1e6
                   for p,c in rates):raise ValueError('route rate exceeds cap')
        except (ValueError,TypeError,KeyError,OverflowError):
            raise model.ModelError('structural route price or capacity exceeds admission; no paid request sent')

    def _check_request(self, request):
        payload=request.payload
        if request.payload_json.encode('utf-8') != _encoded(payload) or hashlib.sha256(_encoded(payload)).hexdigest()!=request.sha256 or payload.get('model')!=self.model_id:
            raise ValueError('typed request identity or model changed')
        if payload.get('provider')!={'order':['openai/flex'],'allow_fallbacks':False,'max_price':self.spec.max_price}:
            raise ValueError('typed request route changed')
        if payload.get('max_tokens')!=MAX_OUTPUT_TOKENS or payload.get('service_tier')!='flex' or payload.get('reasoning')!={'effort':'medium'}:
            raise ValueError('typed request controls changed')
        messages=payload['messages'];image=messages[1]['content'][0]['image_url']
        if image.get('detail')!='original':raise ValueError('typed image detail changed')
        raster=base64.b64decode(image['url'].split(',',1)[1],validate=True)
        dimensions=model._jpeg_dimensions(raster)
        if dimensions is None:raise ValueError('typed image invalid')
        w,h=dimensions;patches=math.ceil(w/32)*math.ceil(h/32)
        if max(w,h)>65535 or patches>30000:raise ValueError('typed image exceeds bounds')
        text=sum(model._utf8_bytes(m['content']) if isinstance(m['content'],str) else
                 sum(model._utf8_bytes(p.get('text')) for p in m['content'] if p.get('type')=='text') for m in messages)
        tokens=text+128+math.ceil(patches*1.2)+1
        bound=(tokens*self.spec.prompt_usd_per_mtok+MAX_OUTPUT_TOKENS*self.spec.completion_usd_per_mtok)/1e6
        if tokens!=request.prompt_tokens_bound or bound!=request.bound_usd:
            raise ValueError('typed request reservation changed')

    def call(self, request, ledger=None, page_label=None, should_stop=None):
        if not self.configured or self.dry_run:
            raise model.ModelError('structural model is not configured for dispatch')
        self._check_request(request)
        self.preflight(request.prompt_tokens_bound)
        attempt_id = None
        try:
            data, attempts, attempt_id = self._post(request.payload, ledger=ledger,
                bound=request.bound_usd, page_label=page_label, should_stop=should_stop,
                prompt_version=request.prompt_version, attempt_context=json.loads(request.context_json),
                safe_errors=True, payload_bytes=request.payload_json.encode("utf-8"))
            pt, ct, cost, cost_source = self._settle(data, ledger=ledger, attempt_id=attempt_id,
                                                    bound=request.bound_usd, require_reported=True)
            def refuse(reason, code="response_rejected"):
                return TypedStageRejected(reason, reason_code=code, cost_usd=cost, cost_source=cost_source,
                    prompt_tokens=pt, completion_tokens=ct, attempt=attempt_id)
            if cost > request.bound_usd + 1e-12:
                raise refuse('provider bill exceeded the reserved request bound', 'billing_bound')
            if data.get('model') != self.model_id or str(data.get('provider','')).lower() != 'openai' \
                    or data.get('service_tier') != 'flex':
                raise refuse('structural response route or service tier differs', 'route_mismatch')
            try:
                choice = data['choices'][0]
                if choice.get('finish_reason') != 'stop': raise ValueError('incomplete answer')
                content = choice['message']['content']
                if not isinstance(content, str): raise ValueError('missing answer')
                answer = json.loads(content)
                if not isinstance(answer, dict): raise ValueError('not an object')
            except (KeyError, IndexError, TypeError, ValueError):
                raise refuse('structural response is incomplete or malformed JSON')
            reasoning = (data['usage'].get('completion_tokens_details') or {}).get('reasoning_tokens', 0)
            return TypedAnswer(answer, attempt_id or '', self.model_id, str(data['provider']), 'flex',
                               cost, pt, ct, request.sha256, request.prompt_version, int(reasoning or 0))
        except model.ModelError as exc:
            if ledger is not None:
                ledger.record({'kind':'attempt_diagnostic', 'attempt':getattr(exc,'attempt',None) or attempt_id,
                    'stage':self.stage, 'request_sha256':request.sha256,
                    'failure_type':type(exc).__name__})
            raise
