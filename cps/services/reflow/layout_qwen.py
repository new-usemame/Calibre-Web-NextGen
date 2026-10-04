"""One closed Qwen disabled route. Public capability is not paid qualification.

The catalogue declares token-only pricing and no native tools. Original input
images use the existing pixel proxy, without a native image discount. Missing
optional surcharge keys are inactive declarations, never authority for a fee;
the complete reported bill must reconcile against the known token meters.
"""
from decimal import Decimal, InvalidOperation

PROFILE_ID = 'qwen38-d1'
MODEL = 'qwen/qwen3.8-flash'
CANONICAL = 'qwen/qwen3.8-flash-20260826'
ROUTE_VERSION = 'layout-structured-14-qwen-disabled'
QUOTE_VERSION = 'layout-quote-11-qwen-disabled'
POLICY_VERSION = 'qwen-disabled-meter-1'
INPUT_CEILING = .20  # Highest complete known input/cache-write liability per M.
PRICES = {'prompt': '0.00000015', 'completion': '0.00000047',
          'input_cache_write': '0.0000002', 'input_cache_read': '0.000000016'}
PRICE_ALIASES = {'input_cache_write_5m': 'input_cache_write',
                 'input_cache_creation_5m': 'input_cache_write',
                 'input_cache_read_5m': 'input_cache_read'}


def number(value):
    if type(value) not in (str, int, float):
        raise ValueError('invalid Qwen price or cost')
    try:
        n = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('invalid Qwen price or cost') from exc
    if not n.is_finite() or n < 0:
        raise ValueError('invalid Qwen price or cost')
    return n


def wire(payload):
    if (set(payload) != {'model', 'messages', 'max_tokens', 'usage',
                        'response_format', 'provider', 'reasoning'}
            or payload['model'] != MODEL
            or payload['reasoning'] != {'enabled': False}
            or type(payload['reasoning'].get('enabled')) is not bool
            or payload['usage'] != {'include': True} or payload['usage'].get('include') is not True
            or payload['response_format'].get('type') != 'json_schema'
            or payload['response_format']['json_schema'].get('strict') is not True
            or payload['provider'] != {'order': ['alibaba'], 'allow_fallbacks': False,
                'require_parameters': True, 'max_price': {'prompt': .15, 'completion': .47}}
            or type(payload['provider']['allow_fallbacks']) is not bool
            or type(payload['provider']['require_parameters']) is not bool):
        raise ValueError('Qwen closed disabled wire differs')


def endpoint(data, route):
    if (data.get('id') != MODEL or route.get('model_id') != MODEL
            or route.get('name') != 'Alibaba | '+CANONICAL
            or route.get('tag') != 'alibaba' or route.get('provider_name') != 'Alibaba'
            or route.get('quantization') != 'unknown' or route.get('native_tools') != {}
            or any(k in route for k in ('service_tier', 'service_tiers', 'tiers'))):
        raise ValueError('Qwen endpoint or native-tool fence differs')
    for key, expected in {'context_length': 1000000, 'max_prompt_tokens': 983616,
                          'max_completion_tokens': 131072, 'status': 0}.items():
        if type(route.get(key)) is not int or route[key] != expected:
            raise ValueError('Qwen endpoint capacity differs')
    pricing = route['pricing']
    optional = {'request', 'image', 'internal_reasoning', 'overrides'}
    if (not isinstance(pricing, dict) or not (set(PRICES)|{'discount'}) <= set(pricing)
            or set(pricing)-set(PRICES)-set(PRICE_ALIASES)-optional-{'discount'}):
        raise ValueError('unknown or missing Qwen price meter')
    for key, expected in PRICES.items():
        if number(pricing[key]) != Decimal(expected):
            raise ValueError('Qwen pricing differs')
    for alias, base in PRICE_ALIASES.items():
        if alias in pricing and number(pricing[alias]) != number(pricing[base]):
            raise ValueError('Qwen cache price aliases disagree')
    if number(pricing['discount']) != 0 or ('overrides' in pricing and pricing['overrides'] != []):
        raise ValueError('Qwen discount or price tiers unavailable')
    for key in optional-{'overrides'}:
        if key in pricing and number(pricing[key]) != 0:
            raise ValueError('Qwen inactive surcharge')


def reasoning(options, record):
    # An absent effort list is not NONE support. A positive generic reasoning
    # capability and nonmandatory typed toggle are the requested disabled mode.
    if (record.get('canonical_slug') != CANONICAL
            or not isinstance(options, dict)
            or type(options.get('default_enabled')) is not bool
            or type(options.get('mandatory')) is not bool or options['mandatory']
            or 'reasoning' not in record.get('supported_parameters', [])):
        raise ValueError('Qwen disabled reasoning controls unavailable')


def _count(value):
    if type(value) is not int or value < 0:
        raise ValueError('Qwen token meter must be a nonnegative integer')
    return value


def _aliases(details, names):
    supplied = [_count(details[k]) for k in names if k in details]
    if not supplied or len(set(supplied)) != 1:
        raise ValueError('Qwen cache usage missing or aliases disagree')
    return supplied[0]


def usage(data, pt, ct):
    """After durable settlement, audit every meter and the whole raw reply."""
    tier = data.get('service_tier')
    if tier is not None and (type(tier) is not str or tier not in ('', 'default')):
        raise ValueError('Qwen service tier differs')
    u = data['usage']
    if set(u)-{'prompt_tokens', 'completion_tokens', 'total_tokens', 'cost',
               'is_byok', 'prompt_tokens_details', 'completion_tokens_details', 'cost_details'}:
        raise ValueError('unknown Qwen usage meter')
    if 'total_tokens' in u and _count(u['total_tokens']) != pt+ct:
        raise ValueError('Qwen total usage differs')
    if 'is_byok' in u and u['is_byok'] is not False:
        raise ValueError('Qwen billing route differs')
    pd, cd = u['prompt_tokens_details'], u['completion_tokens_details']
    read = ('cached_tokens', 'cache_read_tokens', 'cache_read_input_tokens')
    write = ('cache_write_tokens', 'cache_creation_tokens', 'cache_creation_input_tokens')
    if (not isinstance(pd, dict) or set(pd)-set(read)-set(write)-{'image_tokens', 'audio_tokens', 'video_tokens'}
            or not isinstance(cd, dict) or set(cd)-{'reasoning_tokens', 'image_tokens', 'audio_tokens', 'video_tokens'}):
        raise ValueError('unknown Qwen detailed token meter')
    reads, writes = _aliases(pd, read), _aliases(pd, write)
    if reads+writes > pt or _count(cd['reasoning_tokens']) != 0:
        raise ValueError('Qwen cache or reasoning usage differs')
    for detail in (pd, cd):
        for key in ('audio_tokens', 'video_tokens'):
            if key in detail and _count(detail[key]) != 0:
                raise ValueError('Qwen unrequested nontext meter')
    if 'image_tokens' in pd and _count(pd['image_tokens']) > pt:
        raise ValueError('Qwen input image usage differs')
    if 'image_tokens' in cd and _count(cd['image_tokens']) != 0:
        raise ValueError('Qwen unrequested image output')
    prompt_cost = ((pt-reads-writes)*Decimal(PRICES['prompt'])
        + reads*Decimal(PRICES['input_cache_read']) + writes*Decimal(PRICES['input_cache_write']))
    completion_cost = ct*Decimal(PRICES['completion'])
    tolerance = Decimal('0.000000000001')
    if abs(number(u['cost'])-prompt_cost-completion_cost) > tolerance:
        raise ValueError('Qwen reported bill differs from complete known meters')
    if 'cost_details' in u:
        costs = u['cost_details']
        expected = {'upstream_inference_cost': prompt_cost+completion_cost,
                    'upstream_inference_prompt_cost': prompt_cost,
                    'upstream_inference_completions_cost': completion_cost}
        if not isinstance(costs, dict) or set(costs)-set(expected):
            raise ValueError('unknown Qwen cost meter')
        for key, value in costs.items():
            if abs(number(value)-expected[key]) > tolerance:
                raise ValueError('Qwen detailed bill differs')
    def inspect(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if (any(word in key.lower() for word in ('reasoning', 'thinking', 'encrypted'))
                        or key in ('tool_calls', 'function_call', 'annotations')) and child:
                    raise ValueError('Qwen hidden reasoning or tool content')
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)
    inspect(data)
