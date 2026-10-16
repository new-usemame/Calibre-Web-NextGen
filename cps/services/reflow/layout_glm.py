"""Opt-in GLM LOW arm. Unknown price/counter/control is never dispatch authority."""
import copy
import json
from . import layout_luna_standard as standard
from .layout_qwen import number
from .typed_model import _encoded

PROFILE_ID = 'g53-l1'
MODEL = 'z-ai/glm-5.3-flash'
CANONICAL = 'z-ai/glm-5.3-flash-20260826'
ROUTE = 'deepinfra/fp4'
ROUTE_VERSION = 'g38'
ALLOCATION_VERSION = 'a8'
QUOTE_VERSION = 'q38'
POLICY_VERSION = 'gm38'
REVIEW_POLICY = 'r8'
SUPPORT = 65536
INPUT_CEILING = .15
OUTPUT_CEILING = .5
# These are ceilings, not a claim that an omitted cache-write SKU is free.
CEILINGS = {'prompt': '.00000015', 'completion': '.0000005',
            'input_cache_read': '.00000003', 'input_cache_write': '.00000015',
            'request': '0', 'image': '0', 'internal_reasoning': '0'}
PARAMETERS = frozenset({'reasoning','include_reasoning','max_tokens','temperature','top_p','stop','frequency_penalty','presence_penalty','repetition_penalty','top_k','seed','min_p','logit_bias','structured_outputs','tools','tool_choice','response_format','reasoning_effort'})
JOIN = '\nKEEP joins fragments retaining the final printed hyphen; DROP removes only that hyphen. Preserve meaningful compounds and source-supported soft wraps.'


def selected(selection):
    from . import layout_model
    return dict(selection or {}) == dict(layout_model.GLM_SELECTION)


def wire(payload):
    if (set(payload) != {'model','messages','max_tokens','usage','response_format','provider','reasoning'}
            or payload['model'] != MODEL or payload['reasoning'] != {'effort':'low'}
            or payload['usage'] != {'include': True} or payload['usage']['include'] is not True
            or payload['response_format'].get('type') != 'json_schema'
            or payload['response_format']['json_schema'].get('strict') is not True
            or payload['provider'] != {'order':[ROUTE], 'allow_fallbacks':False,
                'require_parameters':True, 'max_price':{'prompt':INPUT_CEILING,'completion':OUTPUT_CEILING}}
            or payload['provider']['allow_fallbacks'] is not False
            or payload['provider']['require_parameters'] is not True):
        raise ValueError('GLM closed LOW wire differs')


def prices(row):
    # An explicit known write/fee schedule is needed. Public flat listings that
    # omit these fields do not establish it, including supports_implicit_caching.
    if not isinstance(row, dict) or not set(CEILINGS) <= set(row) or set(row)-set(CEILINGS)-set(standard.PRICE_ALIASES)-{'discount','overrides'}:
        raise ValueError('GLM complete cache-write/fee schedule unavailable')
    for key, cap in CEILINGS.items():
        if number(row[key]) > number(cap): raise ValueError('GLM price exceeds ceiling')
    for alias, base in standard.PRICE_ALIASES.items():
        if alias in row and number(row[alias]) != number(row[base]):
            raise ValueError('GLM cache price aliases disagree')
    # Listed rates already include this metadata. Never multiply the bill by it.
    if number(row.get('discount', 0)) not in (0, number('.5')) or row.get('overrides', []) != []:
        raise ValueError('GLM unknown discount or high-tier schedule')


def endpoint(data, route):
    if (data.get('id') != MODEL or data.get('created') != 1787752741
            or route.get('model_id') != MODEL or route.get('name') != 'DeepInfra | '+CANONICAL
            or route.get('provider_name') != 'DeepInfra' or route.get('tag') != ROUTE
            or route.get('quantization') != 'fp4' or route.get('native_tools') != {}
            or route.get('supports_implicit_caching') is not False
            or any(k in route for k in ('service_tier','service_tiers','tiers'))):
        raise ValueError('GLM exact native endpoint controls differ')
    for key, value in {'context_length':1048576,'max_completion_tokens':131072,'status':0}.items():
        if type(route.get(key)) is not int or route[key] != value:
            raise ValueError('GLM native capacity differs')
    if route.get('max_prompt_tokens') not in (None, 1048576):
        raise ValueError('GLM native input capacity differs')
    if set(route.get('supported_parameters', [])) != PARAMETERS:
        raise ValueError('GLM LOW/strict schema unavailable')
    prices(route.get('pricing'))


def reasoning(options, record):
    if (record.get('id') != MODEL or record.get('canonical_slug') != CANONICAL
            or record.get('created') != 1787752741 or record.get('context_length') != 1048576
            or options != {'mandatory':True,'default_enabled':True,'supported_efforts':['max','high','low'],'default_effort':'max'}
            or options.get('mandatory') is not True or options.get('default_enabled') is not True
            or not {'reasoning','reasoning_effort'} <= set(record.get('supported_parameters', []))):
        raise ValueError('GLM explicit mandatory LOW controls differ')


billing_record = standard.billing_record


def usage(data, pt, ct, visible_bound, completion_bound, actual_prices=None):
    # Reuse the full known-meter/native-content audit. Its defaults still produce
    # the historical Luna receipts; GLM gets a separate immutable policy.
    rates = {k: CEILINGS[k] for k in ('prompt','completion','input_cache_read','input_cache_write')}
    return standard.usage(data, pt, ct, visible_bound, completion_bound,
        model_id=MODEL, provider='DeepInfra', support=SUPPORT, policy=POLICY_VERSION,
        base_prices=rates, high_prices=rates, tier_floor=None, ceiling_bill=True, actual_prices=actual_prices)


def proposal(prepared, original):
    from . import layout_eligibility
    request = layout_eligibility.proposal(prepared, original)
    request['prompt_version'] = 'p38'
    request['source_identity']['join_policy'] = REVIEW_POLICY
    from . import layout_requests
    prompt = request['messages'][0]['content']
    suffix = layout_requests.PREVIOUS_CANDIDATE_PROMPT
    request['messages'][0]['content'] = prompt[:-len(suffix)] + JOIN + suffix if prompt.endswith(suffix) else prompt + JOIN
    return request


def continuation_eligibility(candidate, can_continue):
    """Unknown is not absence. Callers pass owning _review_material results."""
    if not isinstance(candidate, dict) or type(candidate.get('continuation')) is not bool or type(can_continue) is not bool:
        return None
    return candidate['continuation'] and can_continue


def review(prepared, plan, previous, original):
    from . import layout_requests
    _, _, candidate, can_continue = layout_requests._review_material(prepared, plan, previous)
    request = copy.deepcopy(original)
    if continuation_eligibility(candidate, can_continue) is False:
        request['response_schema']['properties']['continuation_accept']['enum'] = [False]
    request['source_identity']['review_policy'] = REVIEW_POLICY
    request['prompt_version'] = REVIEW_POLICY
    request['messages'][0]['content'] += JOIN
    return request


def decode(content):
    """Strict local decoding is an audit, never proof of provider enforcement."""
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('nonfinite JSON constant')
    return json.loads(content, object_pairs_hook=object_pairs, parse_constant=constant)
