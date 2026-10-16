"""Two admission channels over one exact proposal, with independent style review.

The structural compiler remains strict. Only the optional emphasis field is
isolated, never a role, range, binding or lexical decision. Its original decision
and any refusal survive in the qualification receipt.
"""
import json
from dataclasses import replace

from . import layout_ops as ops, layout_requests as requests

VERSION = 'layout-emphasis-admission-2-domain'
PROMPT = '''Review ONLY the supplied optional inline strong ranges against the original page image and exact source atoms. The supplied structure already passed separate structural, ordering and lexical review. Do not change or rejudge paragraph/quote roles, ranges, words, joins, notes or furniture. Accept styling only when every proposed range is supported by printed emphasis; otherwise reject all optional emphasis using unsupported_change. Existing source markup is retained independently. Return exactly snapshot, accept, problems. accept is true exactly when problems is empty. No source words, HTML, replacement ranges or structural decisions.'''


def split(prepared, response):
    """Validate complete structure first; retain a separate strict styling verdict."""
    ops._need(type(prepared) is ops.PreparedLayout and type(response) is dict,
              'prepared source and object proposal required')
    source = json.loads(prepared.contract_json)
    if response.get('contract') == 'layout-domain-2':
        from . import layout_domain
        layout_domain.validate_response_fields(prepared,response)
    if response.get('contract') == 'layout-semantic-choices-1':
        from . import layout_choices
        layout_choices.validate_response_fields(prepared,response)
    original = json.loads(ops._json(response))
    structural = dict(original)
    requested = structural.pop('emphasis', None)
    if 'contract' in structural:
        from . import layout_construction
        structural = layout_construction.compile(source, structural)
    structural = ops._answer(source, structural)  # Exact coverage and every other gate.
    record = dict(snapshot=prepared.snapshot_id,
                  proposal_sha256=ops.atoms.digest(original), requested=requested,
                  status='not_requested' if 'emphasis' not in original else 'no_choices')
    if 'emphasis' in original:
        try:
            if original.get('contract') == 'layout-domain-2':
                from . import layout_domain
                requested = layout_domain.expand_emphasis(source,requested)
                record['requested'] = requested
                record['original_requested'] = original['emphasis']
            if original.get('contract') in ('layout-semantic-choices-1','layout-range-choices-1'):
                from . import layout_domain
                requested = layout_domain.expand_emphasis(source,requested)
                record['requested'] = requested
                record['original_requested'] = original['emphasis']
            if 'contract' in original:
                from . import layout_construction
                layout_construction.compile(source, original)
            else:
                ops._answer(source, original)  # The existing strict emphasis validator.
            if requested:
                record['status'] = 'pending_review'
        except ops.ContractError as exc:
            record.update(status='rejected', reason=str(exc))
    return structural, record


def _material(plan, requested):
    source, answer, _ = requests._candidate(plan.prepared, plan)
    ops._need(not answer.get('emphasis'), 'structure already contains optional emphasis')
    candidate = ops._answer(source, dict(answer, emphasis=requested))
    ops._need(bool(candidate['emphasis']), 'empty emphasis review')
    groups = ops.atoms.validate(source, candidate)
    from . import _layout_emphasis
    ranges = _layout_emphasis.checked(source, candidate, groups)
    selected = {a for r in ranges for a in r['ids']}
    ids = [a['id'] for a in source['atoms']]
    binding = ops.atoms.digest(dict(version=VERSION, snapshot=plan.prepared.snapshot_id,
                                   structure=answer, emphasis=requested))
    view = dict(snapshot=binding, page=plan.prepared.page, emphasis=requested,
        source_atoms=[dict(id=a['id'], text=a['text']) for a in source['atoms'] if a['id'] in selected],
        prose_groups=[dict(role=role, ranges=answer['groups'][i]['ranges'],
                           text=' '.join(a['text'] for a in group))
                      for i, (role, group) in enumerate(groups) if any(a['id'] in selected for a in group)],
        printed_lines=[line for line in source.get('printed_lines', [])
                       if any(selected.intersection(ids[ids.index(first):ids.index(last)+1])
                              for first, last in line['ranges'])])
    if source['binding'].get('raster_panels'):
        view['raster_panels'] = source['binding']['raster_panels']
    return binding, view, candidate


def review(plan, requested):
    binding, view, _ = _material(plan, requested)
    properties = dict(snapshot=dict(type='string', enum=[binding]), accept=dict(type='boolean'),
        problems=dict(type='array', items=dict(type='string', enum=['unsupported_change']), maxItems=1))
    schema = dict(type='object', properties=properties, required=list(properties), additionalProperties=False)
    request = requests.envelope('emphasis-reviewer',
        dict(snapshot=plan.prepared.snapshot_id, emphasis_snapshot=binding), view, PROMPT, schema)
    request['context']['decision_channel'] = 'emphasis'
    return request


def accept(plan, requested, response):
    binding, _, candidate = _material(plan, requested)
    ops._need(type(response) is dict and set(response) == {'snapshot', 'accept', 'problems'},
              'missing or malformed emphasis review')
    ops._need(response['snapshot'] == binding, 'stale emphasis review')
    approved, problems = response['accept'], response['problems']
    ops._need(type(approved) is bool and type(problems) is list and
              problems in ([], ['unsupported_change']) and approved == (not problems),
              'invalid emphasis review verdict')
    return replace(plan, answer_json=ops._json(candidate)) if approved else None
