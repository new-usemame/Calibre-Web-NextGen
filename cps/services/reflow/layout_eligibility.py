"""Opt-in role eligibility from the unchanged canonical compiler authority.

This removes only a globally impossible role. It supplies no ownership, ranges,
paragraphs or semantic classification. Current source must still be factory
reissued/compiled at dispatch and after every fully settled response.
"""
import copy
import json
from . import _layout_atoms as atoms, layout_domain, layout_requests

VERSION = 'source-role-eligibility-1'
PROMPT_VERSION = 'source-bound-layout-prompt-14-eligibility'
PROMPT = '\nThe supplied role enum excludes source_furniture only when no whole protected source block is mechanically eligible under the compiler. Choose every role and group from the remaining complete enum; eligibility is not source-quality approval.'


def universe(source):
    atoms.need(atoms.digest({k:v for k,v in source.items() if k != 'snapshot'}) == source['snapshot'],
               'eligibility source snapshot differs')
    # validate() requires a singleton whole opaque block, then tests precisely
    # this owning predicate. No ordinary atom or protected inline can qualify.
    # For unfamiliar/malformed shapes retain the role rather than infer absence.
    try:
        blocks = source['blocks']
        ids = {a['id'] for a in source['atoms']}
        complete = all(type(b['opaque']) is bool and type(b['kind']) is str
            and type(b.get('attributes', {})) is dict and len(b['range']) == 2
            and set(b['range']) <= ids for b in blocks)
        eligible = [b['range'][0] for b in blocks if atoms.can_be_source_furniture(b)]
        constraints = atoms.protected_constraints(source)
        supplied = [r['atom'] for r in constraints if 'source_furniture' in r['allowed_roles']]
        proven = complete and eligible == supplied
    except (KeyError, TypeError, AttributeError, IndexError):
        eligible, proven = [], False
    roles = list(layout_domain.ROLES)
    excluded = proven and not eligible
    if excluded: roles.remove('source_furniture')
    return dict(version=VERSION, roles=roles, source_furniture_excluded=excluded,
        absence_proven=excluded, eligible_protected_atoms=eligible if proven else None,
        source_snapshot=source['snapshot'], authority='canonical compiler can_be_source_furniture + protected_constraints')


def proposal(prepared, original):
    """Preserve every source/hint/choice except the proven-ineligible enum entry."""
    source = json.loads(prepared.contract_json)
    atoms.need(source['snapshot'] == prepared.snapshot_id, 'eligibility prepared snapshot differs')
    receipt = universe(source)
    request = copy.deepcopy(original)
    role = request['response_schema']['properties']['groups']['items']['properties']['role']
    atoms.need(role == dict(type='string', enum=list(layout_domain.ROLES)), 'eligibility requires unchanged range role enum')
    role['enum'] = receipt['roles']
    request['source_identity']['source_policy'] = VERSION
    request['prompt_version'] = PROMPT_VERSION + original['prompt_version'].split('-proposer',1)[-1] + '-proposer'
    prompt = request['messages'][0]['content']
    suffix = layout_requests.PREVIOUS_CANDIDATE_PROMPT
    if prompt.endswith(suffix): prompt = prompt[:-len(suffix)] + PROMPT + suffix
    else: prompt += PROMPT
    request['messages'][0]['content'] = prompt
    return request
