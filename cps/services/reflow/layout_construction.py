"""Explicit model-owned allocation and source-bound decision witnesses.

Canonical identities and source factories are untouched. This translates complete
choices, never fills holes or judges layout from geometry. Evidence is eligibility;
independent judgment, lexical and full-component gates remain necessary.
"""
import json
import hashlib
from xml.etree import ElementTree as ET

from . import _layout_atoms as atoms

VERSION = 'layout-construction-1'
PROMPT = '''Return the supplied constructive contract, snapshot, ownership, groups, joins, join_evidence, continuation, boundary_join, continuation_evidence and emphasis (plus note_bindings when offered). ownership has EXACTLY one [groupIndex,positionWithinGroup] pair for each CURRENT atom, in the supplied atoms order. This positional vector is not an ID alias. Every position in every nonempty group must be unique and contiguous from zero. groups are in final reading order and contain role and evidence, not ranges. You own roles, ordering, paragraphs and relocation; no missing choice will be filled. Previous atoms are context only and cannot be allocated.
Each group's evidence is the distinct first/last canonical IDs of every maximal consecutive source range in its chosen order, in encounter order. Each selector is [canonicalID,source_line] using an actual supplied membership, or [canonicalID,null] ONLY when membership is unknown. Supply join_evidence with exactly [leftSelector,rightSelector] for each local join. continuation_evidence is null without supplied previous atoms; otherwise {previous:[previousID,null],current:currentSelector}, or null only for a page with no atoms. If boundary_join is chosen these must be its endpoints; otherwise select the relevant previous/current reading-flow endpoints. Previous line membership is unknown. Source/HTML/resource digests and retained-furniture origin are provenance and confer no semantic role. Inspect the complete raster and all literal neighbors for each chosen boundary, particularly first/last-word ownership. A split inside one supplied printed line requires explicit source judgment; it is neither automatically permitted nor forbidden. Never generate words, IDs, HTML or resources.'''
REVIEW_PROMPT = '''Review EVERY supplied decision against the complete source image, scoped canonical atoms, selected printed-line evidence and neighboring literals. Return exactly snapshot, accept, continuation_accept, problems and decisions. Return decisions as the exact supplied decision-ID object with a boolean for each: true only if that individual role/boundary/relocation/join/continuation choice has source support. Group endpoints and boundaries must own the correct FIRST and LAST words. An interior-line split is a review question, not a deterministic error; inspect its entire source line and paragraph indentation. Provenance, complete coverage, correct selector spelling and preserved words do not prove semantics. A false LOCAL decision requires accept=false and at least one categorical problem. A false incoming continuation decision requires continuation_accept=false but need not reject valid local structure. Existing local/categorical and independent continuation, lexical, ordering and optional style gates still apply. Never supply replacement choices or source prose.'''


def _memberships(source):
    ids = [a['id'] for a in source['atoms']]
    result = {identifier: [] for identifier in ids}
    for line in source.get('printed_lines', []):
        for first, last in line['ranges']:
            atoms.need(first in result and last in result, 'foreign printed-line range')
            lo, hi = ids.index(first), ids.index(last)
            atoms.need(lo <= hi, 'reversed printed-line range')
            for identifier in ids[lo:hi+1]:
                if line['source_line'] not in result[identifier]:
                    result[identifier].append(line['source_line'])
    return result


def cues(source):
    """Literal DOM cues; unknown mappings stay unknown, no new geometry."""
    memberships = _memberships(source)
    opaque = {b['range'][0] for b in source['blocks'] if b['opaque']}
    result = []
    for i, atom in enumerate(source['atoms']):
        row = dict(id=atom['id'], text=atom['text'],
                   protected=atom['protected'], ownership='opaque' if atom['id'] in opaque else 'inline_protected' if atom['protected'] else 'text',
                   predecessor=source['atoms'][i-1]['id'] if i else None,
                   successor=source['atoms'][i+1]['id'] if i+1 < len(source['atoms']) else None,
                   membership=dict(state='supplied' if memberships[atom['id']] else 'unknown', lines=memberships[atom['id']]))
        if atom['protected']:
            row['html_sha256'] = hashlib.sha256(atom['html'].encode()).hexdigest()
            root = ET.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">'+atom['html']+'</root>')
            row['resources'] = [dict(type=atoms.tag(n), attributes=dict(n.attrib),
                                     dom_sha256=hashlib.sha256(atoms.xml(n).encode()).hexdigest())
                                for n in root.iter() if atoms.tag(n) in ('a', 'img', 'sup')]
        if atom.get('origin'): row['origin'] = atom['origin']
        if atom.get('raster_region'): row['raster_region'] = atom['raster_region']
        result.append(row)
    return result


def schema(source, legacy):
    """Bounded typed choices; cross-field ownership/provenance still checked."""
    n = len(source['atoms'])
    props = dict(legacy['properties'])
    obj = lambda p: dict(type='object', properties=p, required=list(p), additionalProperties=False)
    current_ids = [a['id'] for a in source['atoms']]
    line_ids = [l['source_line'] for l in source.get('printed_lines', [])]
    selector = dict(type='array', minItems=2, maxItems=2,
        items=dict(anyOf=[dict(type='string', enum=current_ids or ['NO_VALID_ID']),
                          dict(type='integer', enum=line_ids or [-1]), dict(type='null')]))
    # JSON schema permits unions in array items; strict positional/type/provenance
    # checks happen before compilation. No sentinel can become source evidence.
    integer = dict(type='integer', minimum=0, maximum=max(0,n-1))
    props.update(contract=dict(type='string', enum=[VERSION]),
        ownership=dict(type='array', minItems=n, maxItems=n, items=dict(type='array', minItems=2, maxItems=2, items=integer)),
        groups=dict(type='array', maxItems=n, items=obj(dict(
            role=dict(type='string', enum=sorted(atoms.ROLES)),
            evidence=dict(type='array', maxItems=n, items={'$ref':'#/$defs/source_selector'})))),
        join_evidence=dict(type='array', maxItems=n, items=dict(type='array',minItems=2,maxItems=2,items={'$ref':'#/$defs/source_selector'})))
    previous = (source.get('previous') or {}).get('atoms', [])
    prior_selector = dict(type='array', minItems=2, maxItems=2,
        items=dict(anyOf=[dict(type='string',enum=[a['id'] for a in previous] or ['NO_VALID_ID']),dict(type='null')]))
    props['continuation_evidence'] = dict(anyOf=[dict(type='null'), obj(dict(previous=prior_selector, current={'$ref':'#/$defs/source_selector'}))]) if previous and n else dict(type='null')
    result = obj(props)
    result['$defs'] = dict(legacy.get('$defs', {}), source_selector=selector)
    return result


def _selector(source, selector, expected, memberships, previous=False):
    atoms.need(type(selector) is list and len(selector)==2, 'malformed source selector')
    identifier, line = selector
    atoms.need(type(identifier) is str and identifier==expected and identifier in memberships, 'foreign or misplaced source selector')
    lines = memberships[identifier]
    atoms.need((type(line) is int and line in lines) if lines else line is None,
               'false or unknown printed-line evidence')


def _ranges(ids, owned):
    result = []
    for identifier in owned:
        if result and ids.index(identifier)==ids.index(result[-1][1])+1:
            result[-1][1]=identifier
        else: result.append([identifier,identifier])
    return result


def compile(source, response):
    """Translate only complete explicit choices, then run the original gates."""
    if type(response) is dict and response.get('contract') == 'layout-domain-2':
        from . import layout_domain
        return compile(source, layout_domain.expand(source,response))
    if type(response) is dict and response.get('contract') == 'layout-range-choices-1':
        from . import layout_ranges
        return layout_ranges.compile(source,response)
    if type(response) is dict and response.get('contract') == 'layout-semantic-choices-1':
        from . import layout_choices
        return layout_choices.compile(source,response)
    allowed = {'contract','snapshot','ownership','groups','joins','join_evidence','continuation','boundary_join','continuation_evidence'}
    atoms.need(type(response) is dict and allowed <= set(response) <= allowed|{'emphasis','note_bindings'}, 'constructive response fields')
    atoms.need(response['contract']==VERSION and response['snapshot']==source['snapshot'], 'stale constructive contract or source')
    ids = [a['id'] for a in source['atoms']]
    owners, choices = response['ownership'], response['groups']
    atoms.need(type(owners) is list and len(owners)==len(ids) and type(choices) is list and len(choices)<=len(ids), 'incomplete current atom ownership')
    grouped = [dict() for _ in choices]
    for identifier, owner in zip(ids, owners):
        atoms.need(type(owner) is list and len(owner)==2 and all(type(x) is int for x in owner), 'typed ownership required')
        group, position = owner
        atoms.need(0<=group<len(choices) and 0<=position<len(ids), 'foreign ownership position')
        atoms.need(position not in grouped[group], 'duplicate ownership position')
        grouped[group][position]=identifier
    members = _memberships(source)
    canonical=[]
    for choice, owned in zip(choices, grouped):
        atoms.need(type(choice) is dict and set(choice)=={'role','evidence'}, 'constructive group fields')
        atoms.need(owned and set(owned)==set(range(len(owned))), 'empty or missing group position')
        ranges = _ranges(ids, [owned[i] for i in range(len(owned))])
        endpoints = list(dict.fromkeys(x for pair in ranges for x in pair))
        evidence = choice['evidence']
        atoms.need(type(evidence) is list and len(evidence)==len(endpoints), 'missing or duplicate group evidence')
        for identifier, selector in zip(endpoints,evidence): _selector(source,selector,identifier,members)
        canonical.append(dict(role=choice['role'],ranges=ranges))
    result={k:response[k] for k in ('snapshot','joins','continuation','boundary_join')}
    result['groups']=canonical
    for key in ('emphasis','note_bindings'):
        if key in response: result[key]=response[key]
    # Includes source-snapshot integrity, exact coverage, protected roles,
    # trusted wrap endpoints, same-group adjacency, notes and optional style.
    atoms.validate(source,result)
    evidence=response['join_evidence']
    atoms.need(type(evidence) is list and len(evidence)==len(result['joins']), 'missing or duplicate join evidence')
    for join, pair in zip(result['joins'],evidence):
        atoms.need(type(pair) is list and len(pair)==2, 'join evidence fields')
        for side,selector in zip(('left','right'),pair):_selector(source,selector,join[side],members)
    continuation_evidence=response['continuation_evidence']
    previous = source.get('previous')
    if previous and previous.get('atoms') and ids:
        atoms.need(type(continuation_evidence) is dict and set(continuation_evidence)=={'previous','current'}, 'missing scoped continuation evidence')
        # Only the supplied model context tail is eligible, even if the full
        # canonical previous contract has more atoms. IDs are never namespaced.
        prior={a['id']:[] for a in previous['atoms'][-120:]}
        ps=continuation_evidence['previous'];cs=continuation_evidence['current']
        atoms.need(type(ps) is list and len(ps)==2 and type(cs) is list and len(cs)==2,'continuation selector fields')
        boundary=result['boundary_join']
        _selector(source,ps,boundary['left'] if boundary else ps[0],prior,True)
        from . import _layout_flow
        edge = _layout_flow.edge(atoms.validate(source,result),False)
        current_endpoint = edge[1][0]['id'] if edge else ids[0]
        _selector(source,cs,boundary['right'] if boundary else current_endpoint,members)
    else: atoms.need(continuation_evidence is None,'foreign continuation evidence')
    return result


def validate_plan(source, answer, raw):
    """Retain exact construction while downstream gates decline/change spelling.

    Review can clear continuation; lexical review owns keep/drop and may add a
    trusted join. Optional style is independently admitted. Group/order/role/note
    choices cannot be rebound to the original construction.
    """
    response=json.loads(raw)
    if response.get('contract') == 'layout-domain-2':
        from . import layout_domain
        atoms.need(set(response)==set(layout_domain.schema(source)['required']),
                   'complete domain provenance fields required')
    if response.get('contract') == 'layout-range-choices-1':
        from . import layout_ranges
        atoms.need(set(response)==set(layout_ranges.schema(source)['required']),
                   'complete range provenance fields required')
    if response.get('contract') == 'layout-semantic-choices-1':
        from . import layout_choices
        atoms.need(set(response)==set(layout_choices.schema(source)['required']),
                   'complete semantic provenance fields required')
    response.pop('emphasis', None)  # Its exact choices remain in raw and the optional channel.
    original=compile(source,response)
    for key in ('snapshot','groups','note_bindings'):
        atoms.need(answer.get(key)==original.get(key),'construction provenance differs')
    atoms.need(not answer.get('continuation') or original.get('continuation'), 'construction continuation was not proposed')
    return original


def decisions(source, answer, response):
    """All role/endpoints, boundaries, relocations, joins and incoming choices."""
    members=_memberships(source)
    cues_by_id={a['id']:a for a in cues(source)}
    ids=list(cues_by_id)
    evidence={s[0]:s for g in response['groups'] for s in g['evidence']}
    rows=[]
    def neighbors(identifiers):
        indices=sorted(set(i for identifier in identifiers for i in (ids.index(identifier)-1,ids.index(identifier),ids.index(identifier)+1) if 0<=i<len(ids)))
        return [dict(id=ids[i],text=cues_by_id[ids[i]]['text']) for i in indices]
    def add(kind, selectors, **fields):
        selected=[s[0] for s in selectors]
        row=dict(id='d'+str(len(rows)),kind=kind,selectors=selectors,neighbors=neighbors(selected),**fields)
        rows.append(row)
    for i,g in enumerate(answer['groups']):
        add('group',response['groups'][i]['evidence'],group=i,role=g['role'],ranges=g['ranges'])
        for left,right in zip(g['ranges'],g['ranges'][1:]):
            add('relocation',[evidence[left[1]],evidence[right[0]]],group=i)
    boundary_pairs=set()
    def boundary(a,b,left_role,right_role,origin):
        if (a,b) in boundary_pairs:return
        boundary_pairs.add((a,b))
        add('boundary',[evidence[a],evidence[b]],left_role=left_role,right_role=right_role,
            origin=origin,interior_line=bool(set(members[a]) & set(members[b])))
    for left,right in zip(answer['groups'],answer['groups'][1:]):
        boundary(left['ranges'][-1][1],right['ranges'][0][0],left['role'],right['role'],'output_neighbors')
    # Retained notices/furniture may lie between final groups. Every canonical
    # neighbor pair assigned to different groups is still a boundary choice.
    # This is ownership evidence, never a paragraph classifier.
    owners=response['ownership']
    for i,(left,right) in enumerate(zip(owners,owners[1:])):
        if left[0]!=right[0]:
            boundary(ids[i],ids[i+1],answer['groups'][left[0]]['role'],
                     answer['groups'][right[0]]['role'],'canonical_neighbors')
    for j,pair in zip(answer['joins'],response['join_evidence']): add('join',pair,hyphen=j['hyphen'])
    ce=response['continuation_evidence']
    if ce:
        prior={a['id']:a for a in source['previous']['atoms'][-120:]}
        add('continuation',[ce['current']],scope='previous_to_current',previous_selector=ce['previous'],
            previous_literal=prior[ce['previous'][0]]['text'],proposed=answer['continuation'],boundary_join=answer['boundary_join'])
    return rows
