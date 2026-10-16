"""Opt-in Standard HIGH/HIGH pair; support is accounted, never a native cap.

Reuse the established exact OpenAI controls and complete known-bill meter.
The short identities keep full-source reviewer universes within the unchanged
transport limit. Existing profiles and their cached requests remain untouched.
"""
from . import layout_luna_standard as standard, layout_glm

PROFILE_ID = 'l6h-p'
REVIEWER_ID = 'l6h-r'
PROFILE_IDS = frozenset({PROFILE_ID, REVIEWER_ID})
MODEL = standard.MODEL
CANONICAL = standard.CANONICAL
ROUTE_VERSION = 'h40'
ALLOCATION_VERSION = 'a40'
QUOTE_VERSION = 'q40'
CONTROL_VERSION = 'c40'
POLICY_VERSION = 'lh40'
PROPOSER_PROMPT = 'p40'
REVIEW_POLICY = 'r4'
SUPPORT = 65536
INPUT_CEILING = standard.INPUT_CEILING
JOIN = layout_glm.JOIN


def selected(selection):
    from . import layout_model
    return dict(selection or {}) == dict(layout_model.LUNA_HIGH_SELECTION)


def wire(payload):
    # The shared closed wire checks every field and forbids native budgets,
    # tools, Fast, fallback and excluded reasoning. Only effort differs.
    if payload.get('reasoning') != {'effort': 'high'}:
        raise ValueError('Luna Standard closed HIGH wire differs')
    standard.wire(dict(payload, reasoning={'effort': 'medium'}))


endpoint = standard.endpoint
billing_record = standard.billing_record


def reasoning(options, record):
    standard.reasoning(options, record)
    if 'high' not in options['supported_efforts']:
        raise ValueError('Luna Standard HIGH control unavailable')


def usage(data, pt, ct, visible_bound, completion_bound):
    return standard.usage(data, pt, ct, visible_bound, completion_bound,
                          support=SUPPORT, policy=POLICY_VERSION)


def proposal(prepared, original):
    request = layout_glm.proposal(prepared, original)
    request['prompt_version'] = PROPOSER_PROMPT
    request['source_identity']['join_policy'] = REVIEW_POLICY
    return request


def review(prepared, plan, previous, original):
    request = layout_glm.review(prepared, plan, previous, original)
    request['prompt_version'] = REVIEW_POLICY
    request['source_identity']['review_policy'] = REVIEW_POLICY
    return request


continuation_eligibility = layout_glm.continuation_eligibility
decode = layout_glm.decode
