"""Closed Standard/Medium Luna arm; accounting eligibility is not a native cap.

Public controls support the requested effort and ordinary default tier. Actual
OpenRouter translation is unobserved. Reasoning shares the whole output limit;
excess supporting usage is refused only after the complete durable bill.
"""
import hashlib
from decimal import Decimal

from .layout_qwen import number, _count, _aliases
from .typed_model import _encoded

PROFILE_ID = 'luna6-m1-standard'
REVIEWER_ID = 'luna6-r1-standard'
PROFILE_IDS = frozenset({PROFILE_ID, REVIEWER_ID})
REVIEW_ROUTE_VERSION = 'layout-structured-16-review-medium'
REVIEW_ALLOCATION_VERSION = 'layout-answer-allocation-7-review'
PAIR_QUOTE_VERSION = 'layout-quote-14-review-eligibility'
MODEL = 'openai/gpt-6-luna'
CANONICAL = 'openai/gpt-6-luna-20260922'
ROUTE_VERSION = 'layout-structured-15-luna-standard-medium'
QUOTE_VERSION = 'layout-quote-13-luna-standard-medium'
POLICY_VERSION = 'luna-standard-medium-meter-1'
INPUT_CEILING = .25
SUPPORT = 16384
PRICES = {'prompt':'0.0000001', 'completion':'0.0000005',
          'input_cache_write':'0.000000125', 'input_cache_read':'0.00000001'}
HIGH_PRICES = {'prompt':'0.0000002', 'completion':'0.00000075',
               'input_cache_write':'0.00000025', 'input_cache_read':'0.00000002'}
PRICE_ALIASES = {'input_cache_write_5m':'input_cache_write',
                 'input_cache_creation_5m':'input_cache_write',
                 'input_cache_read_5m':'input_cache_read'}
NATIVE_TOOLS = {'openrouter:web_search':{'type':'web_search'},
                'openrouter:apply_patch':{'type':'apply_patch'}}
PARAMETERS = {'reasoning','include_reasoning','seed','max_tokens','response_format',
              'structured_outputs','tools','tool_choice','verbosity','reasoning_effort'}


def wire(payload):
    if (set(payload) != {'model','messages','max_tokens','usage','response_format','provider','reasoning'}
            or payload['model'] != MODEL or payload['reasoning'] != {'effort':'medium'}
            or payload['usage'] != {'include':True} or payload['usage']['include'] is not True
            or payload['response_format'].get('type') != 'json_schema'
            or payload['response_format']['json_schema'].get('strict') is not True
            or payload['provider'] != {'order':['openai'],'allow_fallbacks':False,
                'require_parameters':True,'max_price':{'prompt':.20,'completion':.75}}
            or payload['provider']['allow_fallbacks'] is not False
            or payload['provider']['require_parameters'] is not True):
        raise ValueError('Luna Standard closed Medium wire differs')


def _prices(row, expected, extra):
    if not isinstance(row,dict) or not set(PRICES)<=set(row) or set(row)-set(PRICES)-set(PRICE_ALIASES)-extra:
        raise ValueError('unknown or missing Luna Standard price meter')
    for k,v in expected.items():
        if number(row[k]) != Decimal(v):
            raise ValueError('Luna Standard known pricing differs')
    for alias,base in PRICE_ALIASES.items():
        if alias in row and number(row[alias]) != number(row[base]):
            raise ValueError('Luna Standard cache price aliases disagree')
    for key in ('request','image','internal_reasoning'):
        if key in row and number(row[key]) != 0:
            raise ValueError('Luna Standard unreserved surcharge')


def endpoint(data, route):
    if (data.get('id') != MODEL or type(data.get('created')) is not int or data['created'] != 1790100786
            or route.get('model_id') != MODEL or route.get('name') != 'OpenAI | '+CANONICAL
            or route.get('provider_name') != 'OpenAI' or route.get('tag') != 'openai'
            or route.get('quantization') != 'unknown' or route.get('native_tools') != NATIVE_TOOLS
            or route.get('supports_implicit_caching') is not False
            or any(k in route for k in ('service_tier','service_tiers','tiers'))
            or set(route.get('supported_parameters',[])) != PARAMETERS):
        raise ValueError('Luna Standard endpoint controls or native-tool fence differ')
    for k,v in {'context_length':1050000,'max_prompt_tokens':922000,'max_completion_tokens':128000,'status':0}.items():
        if type(route.get(k)) is not int or route[k] != v:
            raise ValueError('Luna Standard endpoint capacity differs')
    p=route['pricing'];fees={'request','image','internal_reasoning'}
    _prices(p,PRICES,fees|{'discount','web_search','overrides'})
    if number(p['discount']) != 0 or number(p['web_search']) != Decimal('.01'):
        raise ValueError('Luna Standard discount or native fee differs')
    rows=p['overrides']
    if (not isinstance(rows,list) or len(rows)!=1 or not isinstance(rows[0],dict)
            or type(rows[0].get('min_prompt_tokens')) is not int or rows[0]['min_prompt_tokens']!=272000):
        raise ValueError('Luna Standard whole prompt-tier policy differs')
    _prices(rows[0],HIGH_PRICES,fees|{'min_prompt_tokens'})


def reasoning(options, record):
    if (record.get('id') != MODEL or record.get('canonical_slug') != CANONICAL
            or type(record.get('created')) is not int or record['created'] != 1790100786
            or not isinstance(options,dict)
            or set(options) != {'mandatory','default_enabled','default_effort','supported_efforts'}
            or options['mandatory'] is not False or options['default_enabled'] is not True
            or options['default_effort'] != 'medium'
            or options['supported_efforts'] != ['max','xhigh','high','medium','low','none']
            or not {'reasoning','reasoning_effort'}.issubset(record.get('supported_parameters',[]))):
        raise ValueError('Luna Standard explicit Medium controls differ')
    if type(record.get('context_length')) is not int or record['context_length']!=1050000:
        raise ValueError('Luna Standard model catalogue context differs')
    p=record['pricing']
    _prices(p,PRICES,{'overrides','web_search'})
    if number(p['web_search'])!=Decimal('.01'):
        raise ValueError('Luna model catalogue native fee differs')
    tiers=p['overrides']
    if (not isinstance(tiers,list) or len(tiers)!=1 or not isinstance(tiers[0],dict)
            or type(tiers[0].get('min_prompt_tokens')) is not int or tiers[0]['min_prompt_tokens']!=272000):
        raise ValueError('Luna model catalogue price tiers differ')
    _prices(tiers[0],HIGH_PRICES,{'min_prompt_tokens'})


def _native(message, reason):
    """Known native paths are metered, including opaque/encrypted content."""
    fields=[];opaque=False
    for key in ('reasoning','reasoning_content'):
        if key in message:
            value=message[key]
            if value is not None and type(value) is not str:
                raise ValueError('Luna native reasoning text type differs')
            if value:fields.append(key)
    if 'reasoning_details' in message and message['reasoning_details'] is not None:
        details=message['reasoning_details']
        if not isinstance(details,list):raise ValueError('Luna native reasoning details type differs')
        for detail in details:
            if not isinstance(detail,dict):raise ValueError('Luna reasoning detail is not an object')
            kind=detail.get('type');text={'reasoning.text':'text','reasoning.summary':'summary','reasoning.encrypted':'data'}.get(kind)
            allowed={'type','id','format','index',text} | ({'signature'} if kind=='reasoning.text' else set())
            if (text is None or set(detail)-allowed or type(detail.get(text)) is not str
                    or ('format' in detail and detail['format'] not in ('unknown','openai-responses-v1'))
                    or ('id' in detail and detail['id'] is not None and type(detail['id']) is not str)
                    or ('index' in detail and (type(detail['index']) is not int or detail['index']<0))
                    or ('signature' in detail and detail['signature'] is not None and type(detail['signature']) is not str)):
                raise ValueError('Luna native reasoning detail controls differ')
            if detail[text]:fields.append(kind)
            opaque |= kind=='reasoning.encrypted'
    if fields and reason==0:raise ValueError('Luna native content lacks accounted reasoning tokens')
    return fields,opaque


def billing_record(data):
    """Let the shared hook settle a known cost before refusing invalid counts.

    Only settlement metadata gets neutral placeholders for unusable counts.
    The complete ORIGINAL reply is audited immediately afterward and cannot be
    accepted with those counts. Missing/invalid reported cost stays unresolved
    under the shared hook. No bill, raw response, or legacy path is rewritten.
    """
    if not isinstance(data,dict) or not isinstance(data.get('usage'),dict):
        return data
    result=dict(data);usage=dict(data['usage']);result['usage']=usage
    for key in ('prompt_tokens','completion_tokens'):
        if type(usage.get(key)) is not int or usage[key]<0:
            usage[key]=0
    return result


def usage(data, pt, ct, visible_bound, completion_bound, *, model_id=MODEL,
          provider='OpenAI', support=SUPPORT, policy=POLICY_VERSION,
          base_prices=PRICES, high_prices=HIGH_PRICES, tier_floor=272000, ceiling_bill=False, actual_prices=None):
    """Audit the complete raw reply after settlement, even length/malformed replies."""
    if (data.get('model') != model_id or data.get('provider') != provider
            or type(data.get('service_tier')) is not str or data['service_tier']!='default'):
        raise ValueError('Luna Standard actual route or reported tier differs')
    u=data['usage'];allowed={'prompt_tokens','completion_tokens','total_tokens','cost','is_byok',
                            'prompt_tokens_details','completion_tokens_details','cost_details'}
    if not isinstance(u,dict) or set(u)-allowed or _count(u['prompt_tokens'])!=pt or _count(u['completion_tokens'])!=ct:
        raise ValueError('Luna Standard unknown or invalid usage meter')
    if _count(u['total_tokens'])!=pt+ct or ('is_byok' in u and u['is_byok'] is not False):
        raise ValueError('Luna Standard total tokens or billing route differs')
    pd,cd=u['prompt_tokens_details'],u['completion_tokens_details']
    read=('cached_tokens','cache_read_tokens','cache_read_input_tokens');write=('cache_write_tokens','cache_creation_tokens','cache_creation_input_tokens')
    if (not isinstance(pd,dict) or set(pd)-set(read)-set(write)-{'image_tokens','audio_tokens','video_tokens'}
            or not isinstance(cd,dict) or set(cd)-{'reasoning_tokens','image_tokens','audio_tokens','video_tokens'}):
        raise ValueError('Luna Standard unknown detailed meter')
    reads,writes=_aliases(pd,read),_aliases(pd,write);reason=_count(cd['reasoning_tokens'])
    if reads+writes>pt or reason>ct or reason>support or ct>completion_bound or ct-reason>visible_bound:
        raise ValueError('Luna Standard cache/completion/reasoning eligibility differs')
    for detail in (pd,cd):
        for key in ('audio_tokens','video_tokens'):
            if key in detail and _count(detail[key])!=0:raise ValueError('Luna unrequested nontext meter')
    if ('image_tokens' in pd and _count(pd['image_tokens'])>pt) or ('image_tokens' in cd and _count(cd['image_tokens'])!=0):
        raise ValueError('Luna image token relationship differs')
    high = tier_floor is not None and pt>=tier_floor
    rates=high_prices if high else base_prices
    prompt=(pt-reads-writes)*Decimal(rates['prompt'])+reads*Decimal(rates['input_cache_read'])+writes*Decimal(rates['input_cache_write'])
    output=ct*Decimal(rates['completion']);tolerance=Decimal('0.000000000001')
    if (number(u['cost']) > prompt+output+tolerance if ceiling_bill else abs(number(u['cost'])-prompt-output)>tolerance):raise ValueError('Luna complete known-meter bill differs')
    if ceiling_bill:
        # A ceiling alone cannot identify an actual bill. Complete rates must be
        # attached from the exact after-bill controls and reconcile below.
        actual = actual_prices
        from . import layout_glm
        layout_glm.prices(actual)
        prompt=(pt-reads-writes)*number(actual['prompt'])+reads*number(actual['input_cache_read'])+writes*number(actual['input_cache_write'])
        output=ct*number(actual['completion'])
        if abs(number(u['cost'])-prompt-output)>tolerance:raise ValueError('GLM complete known-meter bill differs')
    if 'cost_details' in u:
        costs=u['cost_details'];expected={'upstream_inference_cost':prompt+output,'upstream_inference_prompt_cost':prompt,'upstream_inference_completions_cost':output}
        if not isinstance(costs,dict) or set(costs)-set(expected):raise ValueError('Luna unknown cost meter')
        for k,v in costs.items():
            if abs(number(v)-expected[k])>tolerance:raise ValueError('Luna detailed bill differs')
    choices=data['choices']
    if not isinstance(choices,list) or len(choices)!=1 or not isinstance(choices[0],dict):raise ValueError('Luna other choices are unadmitted')
    message=choices[0]['message']
    if not isinstance(message,dict):raise ValueError('Luna response message type differs')
    fields,opaque=_native(message,reason)
    def inspect(value,path=()):
        if isinstance(value,dict):
            for key,child in value.items():
                p=path+(key,)
                if path==('usage',) and key in allowed:continue
                if path==('choices',0,'message') and key in ('reasoning','reasoning_content','reasoning_details'):continue
                if p==('service_tier',):continue
                if any(word in key.lower() for word in ('reasoning','thinking','encrypted','search','cost','fee','plugin','activation','service_tier')):
                    raise ValueError('Luna unknown/unmetered hidden or control field')
                if key in ('tool_calls','function_call','annotations','tools','native_tools') and child not in (None,[]):
                    raise ValueError('Luna unrequested native tool content')
                inspect(child,p)
        elif isinstance(value,list):
            for i,child in enumerate(value):inspect(child,path+(i,))
    inspect(data)
    return dict(meter_policy=policy,actual_service_tier='default',reasoning_tokens=reason,visible_tokens=ct-reason,
        cache_read_tokens=reads,cache_write_tokens=writes,price_tier='high' if high else 'base',
        prompt_cost_usd=str(prompt),completion_cost_usd=str(output),known_native_fields=fields,
        native_content_visibility='opaque encrypted' if opaque else ('returned native text/summary' if fields else 'not returned'),
        full_internal_reasoning_observed=False,reasoning_budget_is_native_cap=False,
        raw_response_sha256=hashlib.sha256(_encoded(data)).hexdigest(),actual_translation_observed=False)
