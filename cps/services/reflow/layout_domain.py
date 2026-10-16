"""Exact scoped source/choice adapter; semantic decisions remain model-owned.

No source factory, generic wire codec, historical answer or approved plan changes.
Integer references and membership ordinals expand only through immutable source.
"""
import copy
import json
from . import _layout_atoms as atoms, layout_construction as construction, layout_wire as wire
from .typed_model import _encoded

VERSION = 'layout-domain-2'
ROLES = sorted(atoms.ROLES)
SOURCE='''Expand wire3 exactly. Within each bound current/previous scope integer i means canonical ID a+decimal(i), verified bijective. Previous IDs are context only; only supplied previous atoms eligible as selectors. Current adjacent rows give predecessor/successor and neighboring literals. printed_lines ranges define memberships in supplied line order: evidence integer k explicitly selects membership[k], null ONLY for UNKNOWN/absent membership. Opaque=protected_blocks; other protected=inline; otherwise text. wrap text_from_atom/bbox_from_line mean exact atom literal/line rectangle. All other fields/resources/digests/geometry/previous context remain literal. Provenance/eligibility never prove semantics.'''
LAYOUT='''Judge COMPLETE raster, literals, line geometry, indentation/columns/order. Storage blocks are not paragraphs. Indented first line after prose normally starts NEW paragraph without blank line: compare first word x to following full-width lines. Correct FIRST/LAST words required; interior-line split needs source judgment, never automatic acceptance/refusal. Every current atom once; protected EMPTY glyph/image/link slots stay original inline position in same natural paragraph. No words/HTML/resources/OCR/transcription/spelling/new crops. Inline protected indivisible; ordinary atoms may regroup/reorder. lineblock=verse/dedication/display, each range one printed line. furniture/source_furniture ONLY recurring head/footer/folio, never chapter/body heading/number, figure/table/caption/note/uncertain body glyph; preserve whole retained furniture. Opaque atoms stay single under allowed_roles/protected_constraints. Complete printed note label/text separate; source mention is not note. Eligible whole source-list may be note ONLY if entirely printed citations/footnotes, never body/mixed list. Existing resolved/uncertain associations unchanged; only note_candidates authorize new bindings. Numeric agreement is eligibility, never association. Preserve SUP/digits, supplied span offsets/surroundings; invent no numbers/offsets/links.
Raster crops indivisible, not OCR: paragraph/quote only if WHOLE crop belongs there; mixed/multiparagraph stays separate, no internal repair claim. furniture only pure recurring head/folio. Retain notices/asides outside completed prose; disjoint paragraph ranges allowed across separately retained blocks, not one paragraph per crop. Tables/figures/captions/whole-page fallback and flow_by_role barriers remain. raster_panels LEFT=previous RIGHT=current; panel=image pixels, crop=page coordinates. Empty text proves no continuity.
Local joins: consecutive nonprotected atoms in ONE group, trusted wrap_lefts/wrap_rights; never cross groups/protected atoms. keep compound hyphens, drop only soft wraps; separate lexical judgment. Incoming continuation: actual adjacent last/first MAIN paragraph/quote endpoints, SAME role; no extended quote scope. Previous never allocated. Headings/figures/tables barriers, notes/notices separate channels. boundary_join only previous.wrap_lefts/current.wrap_rights, not local/spread joins; explicit hyphen choice. Both pages/endpoints require final admission. previous_candidate provisional; null main endpoint barrier.
Floating illustration retains complete heading/caption/legend/internal order. If bottom artwork interrupts next-page prose continuation, place WHOLE unit at preceding paragraph boundary leaving unfinished prose last; no prose split/merge, crossing headings/note associations/source edits. Other order source-supported. Optional emphasis: supported eligible subranges inside ONE paragraph/quote/source order; no overlaps/nesting/existing markup/notes/reference carriers/protected/joined/wrap_lefts atoms/unsupported labels-as-headings. Style cannot justify/withdraw valid structure.'''
CHOICES='''Return snapshot=source_snapshot and contract=construction.contract, plus all schema fields. ownership[i]=[group,position] EACH current atom; nonempty groups final order, unique contiguous positions from zero. groups[j]=[roles index,evidence]: explicit membership selector for EACH distinct endpoint of EVERY maximal consecutive source range in chosen order, encounter order; endpoint IDs expand EXACTLY from ownership, no missing choices filled. joins=[currentLeft,currentRight,hyphen], join_evidence=[leftMembership,rightMembership] each; hyphen 0=keep,1=drop. boundary_join=[previousLeft,currentRight,hyphen] or null. continuation_evidence={previous:[previousID,null],current:[currentID,membership]} when both contexts have atoms else null; without join select actual main endpoints. emphasis=[firstID,lastID] or []; note_bindings use candidate IDs. Source snapshot/contract bind all choices.'''
REVIEW='''Independently judge EVERY decision against full same raster/source/candidate, not correct selectors/coverage/digests. reference_layout deterministic baseline, not truth: reject NEW unsupported splits/merges, FIRST/LAST-word boundaries/roles/heading/quote/furniture/order/relocation/note-caption associations, not unchanged baseline limits. Inspect entire source lines/indentation including canonical boundaries across retained OUTPUT notices. Return snapshot,accept,continuation_accept,problems,decisions; exact ordered string, explicit 0=reject/1=supported EACH, no defaults/replacements. accept iff problems empty; false local bit requires rejection/problem. Incoming approval independent, only proposed continuity/available SAME-role actual endpoints; decline preserves valid local structure.
Exact decision intervals: group_relocations[j]=r enumerates group j (role/ranges/endpoints) then EACH relocation k=0..r-1 between consecutive maximal ranges. output_boundaries=[lo,hi] enumerates EACH adjacent output pair i/i+1 in interval, empty=[]. canonical_boundaries lists EACH current i/i+1 ownership crossing not already an output pair, even across notices. joins interval selects EACH candidate join/evidence; incoming=[0] selects previous/current evidence/proposed flag/boundary_join else []. Concatenate that order. Endpoints/roles/memberships derive exactly from complete candidate; +/-1 literal neighbors from current atoms. Every enumerated position needs its own bit, never implicit approval. Both-page/ordering/lexical/optional gates remain.'''


def _index(identifier):
    atoms.need(type(identifier) is str and identifier.startswith('a')
               and identifier[1:].isdigit() and identifier == 'a'+str(int(identifier[1:])),
               'unsupported canonical domain ID')
    return int(identifier[1:])


def _id(source, index, previous=False):
    table = (source.get('previous') or {}).get('atoms', [])[-120:] if previous else source['atoms']
    atoms.need(type(index) is int and any(_index(a['id']) == index for a in table),
               'foreign scoped domain selector')
    return 'a'+str(index)


def _source_refs(view, reverse=False):
    """Only declared ID fields; never literals, attributes, hashes or note IDs."""
    result = copy.deepcopy(view)
    def ref(v):
        if not reverse: return _index(v)
        atoms.need(type(v) is int and v >= 0, 'invalid source domain reference')
        return 'a'+str(v)
    def section(s):
        for row in s.get('atoms', []): row['id'] = ref(row['id'])
        for key in ('wrap_lefts', 'wrap_rights'):
            if key in s: s[key] = [ref(v) for v in s[key]]
        for key in ('printed_lines', 'protected_blocks', 'reference_layout'):
            for row in s.get(key, []):
                if 'range' in row: row['range'] = [ref(v) for v in row['range']]
                if 'ranges' in row: row['ranges'] = [[ref(v) for v in pair] for pair in row['ranges']]
        for key in ('protected_constraints', 'wrap_evidence'):
            for row in s.get(key, []): row['atom'] = ref(row['atom'])
        if 'emphasis_ranges' in s: s['emphasis_ranges'] = [[ref(v) for v in pair] for pair in s['emphasis_ranges']]
        for row in s.get('raster_atoms', []): row['id'] = ref(row['id'])
    section(result)
    if result.get('previous'): section(result['previous'])
    return result


def source_view(prepared, baseline=False):
    source = json.loads(prepared.contract_json)
    view = prepared.model_view()
    ids = [a['id'] for a in source['atoms']]
    atoms.need([_index(x) for x in ids] == list(range(len(ids))), 'nonpositional current source IDs')
    view['atoms'] = [dict(a, **cue) for a, cue in zip(view['atoms'], construction.cues(source))]
    for a in view['atoms']:
        for key in ('predecessor', 'successor', 'membership', 'ownership'): a.pop(key)
    byid = {a['id']: a for a in view['atoms']}
    lines = {l['source_line']: l for l in view.get('printed_lines', [])}
    for row in view.get('wrap_evidence', []):
        if row.get('text') == byid[row['atom']]['text'] and 'text' in row:
            row.pop('text'); row['text_from_atom'] = True
        if row.get('bbox') == lines[row['source_line']]['bbox'] and 'bbox' in row:
            row.pop('bbox'); row['bbox_from_line'] = True
    if view.get('previous'):
        view['previous']['scope'] = 'previous_context_only'
        for a in view['previous']['atoms']: a['membership'] = dict(state='unknown', lines=[])
    if baseline:
        view['reference_layout'] = [dict(kind=b['kind'], range=b['range'], protected=b['opaque'],
                                         bbox=b.get('bbox')) for b in source['blocks']]
    view = _source_refs(view)
    view.update(domain=VERSION, roles=ROLES, canonical_ids=dict(prefix='a', format='decimal_no_padding',
        scopes=['current', 'previous']), construction=dict(contract=VERSION, scope='current'))
    view['source_snapshot'] = view.pop('snapshot')
    return view


def expand_source(view):
    """Recover full literal V11 cues for source-equivalence verification."""
    result = _source_refs(view, True)
    ids = [a['id'] for a in result['atoms']]
    members = construction._memberships(result)
    opaque = {b['range'][0] for b in result.get('protected_blocks', [])}
    for i, a in enumerate(result['atoms']):
        a.update(predecessor=ids[i-1] if i else None, successor=ids[i+1] if i+1<len(ids) else None,
                 membership=dict(state='supplied' if members[a['id']] else 'unknown', lines=members[a['id']]),
                 ownership='opaque' if a['id'] in opaque else 'inline_protected' if a['protected'] else 'text')
    byid = {a['id']: a for a in result['atoms']}
    lines = {l['source_line']: l for l in result.get('printed_lines', [])}
    for row in result.get('wrap_evidence', []):
        if 'text_from_atom' in row:
            atoms.need(row.pop('text_from_atom') is True, 'invalid domain literal reference')
            row['text'] = byid[row['atom']]['text']
        if 'bbox_from_line' in row:
            atoms.need(row.pop('bbox_from_line') is True, 'invalid domain geometry reference')
            row['bbox'] = lines[row['source_line']]['bbox']
    return result


def _selector(source, index, code, memberships=None):
    identifier = _id(source, index)
    choices = (memberships if memberships is not None else construction._memberships(source))[identifier]
    atoms.need((type(code) is int and 0 <= code < len(choices)) if choices else code is None,
               'false or unknown domain line membership')
    return [identifier, choices[code] if choices else None]


def expand_emphasis(source, chosen):
    atoms.need(type(chosen) is list, 'domain emphasis choices')
    result = []
    for pair in chosen:
        atoms.need(type(pair) is list and len(pair) == 2, 'domain emphasis pair')
        result.append([_id(source, x) for x in pair])
    return result


def expand(source, response):
    """Exact declared expansion; original response remains untouched provenance."""
    fields = {'contract','snapshot','ownership','groups','joins','join_evidence',
              'continuation','boundary_join','continuation_evidence'}
    atoms.need(type(response) is dict and fields <= set(response) <= fields|{'emphasis','note_bindings'},
               'domain response fields')
    atoms.need(response['contract'] == VERSION and response['snapshot'] == source['snapshot'],
               'stale domain source or contract')
    owners, groups = response['ownership'], response['groups']
    ids = [a['id'] for a in source['atoms']]
    atoms.need(type(groups) is list and len(groups) <= len(ids) and type(owners) is list
               and len(owners) == len(ids), 'incomplete domain ownership')
    owned = [{} for _ in groups]
    for identifier, pair in zip(ids, owners):
        atoms.need(type(pair) is list and len(pair) == 2 and all(type(x) is int for x in pair),
                   'typed domain ownership')
        g, p = pair
        atoms.need(0 <= g < len(groups) and 0 <= p < len(ids) and p not in owned[g], 'foreign or duplicate domain position')
        owned[g][p] = identifier
    memberships = construction._memberships(source)
    result = copy.deepcopy(response)
    result['contract'] = construction.VERSION
    result['groups'] = []
    for choice, seq in zip(groups, owned):
        atoms.need(type(choice) is list and len(choice) == 2 and type(choice[0]) is int
                   and 0 <= choice[0] < len(ROLES) and type(choice[1]) is list, 'domain group choices')
        atoms.need(seq and set(seq) == set(range(len(seq))), 'empty or missing domain position')
        ranges = construction._ranges(ids, [seq[i] for i in range(len(seq))])
        endpoints = list(dict.fromkeys(x for pair in ranges for x in pair))
        atoms.need(len(choice[1]) == len(endpoints), 'missing domain endpoint evidence')
        result['groups'].append(dict(role=ROLES[choice[0]], evidence=[_selector(source, _index(x), code, memberships)
                                 for x, code in zip(endpoints, choice[1])]))
    def join(j, previous=False):
        atoms.need(type(j) is list and len(j) == 3 and type(j[2]) is int and j[2] in (0,1), 'domain join choice')
        return dict(left=_id(source,j[0],previous), right=_id(source,j[1]), hyphen=('keep','drop')[j[2]])
    atoms.need(type(response['joins']) is list and type(response['join_evidence']) is list, 'domain join fields')
    result['joins'] = [join(j) for j in response['joins']]
    atoms.need(len(response['join_evidence']) == len(result['joins']), 'missing domain join evidence')
    result['join_evidence'] = []
    for j, ev in zip(response['joins'], response['join_evidence']):
        atoms.need(type(ev) is list and len(ev) == 2, 'domain join evidence pair')
        result['join_evidence'].append([_selector(source, j[k], ev[k], memberships) for k in (0,1)])
    result['boundary_join'] = join(response['boundary_join'],True) if response['boundary_join'] is not None else None
    ce = response['continuation_evidence']
    if ce is not None:
        atoms.need(type(ce) is dict and set(ce)=={'previous','current'} and all(type(v) is list and len(v)==2 for v in ce.values())
                   and ce['previous'][1] is None, 'domain incoming evidence')
        result['continuation_evidence'] = dict(previous=[_id(source,ce['previous'][0],True),None],
                current=_selector(source,*ce['current'], memberships))
    if 'emphasis' in response: result['emphasis'] = expand_emphasis(source,response['emphasis'])
    # Independent authoritative compiler checks source eligibility, exact coverage,
    # all roles/resources/protected atoms, joins, notes, continuation and style.
    construction.compile(source,result)
    return result


def choice_fixture(source, raw):
    """Offline exact inverse for tests/sizing; never used on paid answers."""
    construction.compile(source,raw)
    result = copy.deepcopy(raw); result['contract'] = VERSION
    members = construction._memberships(source)
    code = lambda s: members[s[0]].index(s[1]) if members[s[0]] else None
    result['groups'] = [[ROLES.index(g['role']), [code(s) for s in g['evidence']]] for g in raw['groups']]
    result['joins'] = [[_index(j['left']),_index(j['right']),('keep','drop').index(j['hyphen'])] for j in raw['joins']]
    result['join_evidence'] = [[code(s) for s in pair] for pair in raw['join_evidence']]
    if raw['boundary_join']:
        j=raw['boundary_join'];result['boundary_join']=[_index(j['left']),_index(j['right']),('keep','drop').index(j['hyphen'])]
    if raw['continuation_evidence']:
        ce=raw['continuation_evidence'];result['continuation_evidence']=dict(previous=[_index(ce['previous'][0]),None],current=[_index(ce['current'][0]),code(ce['current'])])
    if 'emphasis' in raw: result['emphasis']=[[_index(x) for x in pair] for pair in raw['emphasis']]
    return result


def decision_table(source, answer, raw):
    rows = construction.decisions(source,answer,raw)
    interval = lambda n: [0,n-1] if n else []
    return dict(group_relocations=[len(g['ranges'])-1 for g in answer['groups']],
                output_boundaries=interval(max(0,len(answer['groups'])-1)),
                canonical_boundaries=[_index(d['selectors'][0][0]) for d in rows
                                      if d['kind']=='boundary' and d['origin']=='canonical_neighbors'],
                joins=interval(len(answer['joins'])), incoming=[0] if raw['continuation_evidence'] else [])


def decision_count(table):
    interval_count = lambda v: v[1]-v[0]+1 if v else 0
    return sum(1+r for r in table['group_relocations']) + interval_count(table['output_boundaries']) + len(table['canonical_boundaries']) + interval_count(table['joins']) + len(table['incoming'])


def expand_decisions(source, answer, raw, table):
    expected = decision_table(source,answer,raw)
    atoms.need(_encoded(table) == _encoded(expected), 'missing foreign or stale domain decision interval')
    rows = construction.decisions(source,answer,raw)
    atoms.need(decision_count(table)==len(rows), 'domain decision coverage differs')
    return rows


def _native_candidate(candidate):
    result = copy.deepcopy(candidate)
    if result.get('last_main_paragraph'):
        for a in result['last_main_paragraph']: a['id'] = _index(a['id'])
    return result


def proposal(prepared, previous=None):
    from . import layout_requests as requests
    view = source_view(prepared)
    source = json.loads(prepared.contract_json)
    identity = dict(snapshot=prepared.snapshot_id,construction_version=VERSION)
    if previous is not None:
        _, prior, _ = requests._previous_material(prepared,source,previous)
        view['previous_candidate'] = _native_candidate(prior)
        identity['previous_candidate'] = prior['snapshot']
    # The same complete instructions cover every source class; the suffix is a
    # quote/neighbor binding, never a source omission or provider cache promise.
    prompt = SOURCE+'\n'+LAYOUT+'\n'+CHOICES
    if previous is not None: prompt += requests.PREVIOUS_CANDIDATE_PROMPT
    return requests.envelope('proposer',identity,view,prompt,schema(source,prepared.model_view()))


def review(prepared, plan, previous=None):
    from . import layout_requests as requests
    source, answer, _ = requests._candidate(prepared,plan)
    structural_raw = json.loads(plan.construction_json)
    structural_raw.pop('emphasis',None)
    raw = expand(source,structural_raw)
    prior_identity, prior, prior_edge = requests._previous_material(prepared,source,previous)
    if raw['continuation_evidence'] and prior_edge:
        atoms.need(raw['continuation_evidence']['previous'][0] == prior_edge[-1]['id'], 'incoming evidence differs from previous endpoint')
    atoms.need([(j['left'],j['right']) for j in answer['joins']] == [(j['left'],j['right']) for j in raw['joins']], 'structural join choices differ')
    binding = atoms.digest(dict(domain=VERSION,source=prepared.snapshot_id,candidate=answer,
                                original_response=json.loads(plan.construction_json),previous=prior_identity))
    chosen = copy.deepcopy(json.loads(plan.construction_json))
    # Optional emphasis has its own complete independent request and verdict;
    # its original raw choices still bind this candidate's identity/provenance.
    for key in ('contract','snapshot','emphasis'): chosen.pop(key,None)
    table = decision_table(source,answer,raw)
    view = source_view(prepared,True)
    view.update(snapshot=binding,candidate_choices=chosen,decisions=table)
    if prior is not None: view['previous_candidate'] = _native_candidate(prior)
    count = decision_count(table)
    properties = dict(snapshot=dict(type='string',enum=[binding]),accept=dict(type='boolean'),continuation_accept=dict(type='boolean'),
        problems=dict(type='array',items=dict(type='string',enum=list(requests.REVIEW_CODES))),
        decisions=dict(type='string',minLength=count,maxLength=count,pattern='^[01]*$'))
    response_schema = dict(type='object',properties=properties,required=list(properties),additionalProperties=False)
    return requests.envelope('reviewer',dict(snapshot=prepared.snapshot_id,review_snapshot=binding,construction_version=VERSION),
                            view,SOURCE+'\n'+LAYOUT+'\n'+CHOICES+'\n'+REVIEW,response_schema)


def validate_review(prepared,plan,response,previous=None):
    from . import layout_requests as requests
    envelope=review(prepared,plan,previous);view=json.loads(envelope['messages'][-1]['content'])
    atoms.need(type(response) is dict and response.get('snapshot')==view['snapshot'], 'stale domain structural review')
    count=decision_count(view['decisions']);bits=response.get('decisions')
    atoms.need(type(bits) is str and len(bits)==count and all(v in '01' for v in bits), 'missing foreign or untyped domain verdict bits')
    # Exact expansion feeds the original independent categorical/local/incoming
    # validation. The model response itself is never edited or saved as another.
    binding,original,_,_=requests._review_material(prepared,plan,previous)
    expanded=dict(response,snapshot=binding,decisions={d['id']:bit=='1' for d,bit in zip(original['decisions'],bits)})
    from dataclasses import replace
    return replace(requests._legacy_validate_review(prepared,plan,expanded,previous),snapshot=view['snapshot'])


def pack_view(view):
    fixed=copy.deepcopy(view)
    dynamic={k:fixed.pop(k) for k in ('snapshot','candidate_choices','decisions','previous_candidate') if k in fixed}
    packed=wire.pack(fixed)
    # Known native domain fields use the existing exact wire3 table syntax.
    # No new codec, pooling, lossy summary or hidden external pointer is added.
    prior=dynamic.get('previous_candidate') or {}
    if prior.get('last_main_paragraph') is not None:
        prior['last_main_paragraph']={wire.KEY:dict(fields=['id','text'],rows=[[a['id'],a['text']]+([{k:v for k,v in a.items() if k not in ('id','text')}] if set(a)-{'id','text'} else []) for a in prior['last_main_paragraph']])}
    packed['view'].update(dynamic)
    atoms.need(wire.unpack(packed)==view, 'domain wire expansion changed complete source')
    return packed


def schema(source, model_view=None):
    """Small request schema plus strict source/position/compiler gates."""
    view=model_view or atoms.model_view(source)
    n=len(source['atoms']);ids=[a['id'] for a in source['atoms']]
    previous=(source.get('previous') or {}).get('atoms',[])[-120:]
    pri=[a['id'] for a in previous]
    idx=dict(type='integer',minimum=0,maximum=max(0,n-1))
    max_members=max([0]+[len(m)-1 for m in construction._memberships(source).values()])
    line=dict(anyOf=[dict(type='integer',minimum=0,maximum=max_members),dict(type='null')])
    def obj(p):return dict(type='object',properties=p,required=list(p),additionalProperties=False)
    def arr(item,lo=0,hi=None):return dict(type='array',items=item,minItems=lo,**({'maxItems':hi} if hi is not None else {}))
    def pair(item):return arr(item,2,2)
    def tuple_(choices,length):return arr(dict(anyOf=choices),length,length)
    def enum(values):return dict(type='integer',enum=[_index(x) for x in values] or [-1])
    hy=dict(type='integer',enum=[0,1]);wl=source.get('wrap_lefts',[]);wr=source.get('wrap_rights',[])
    W=min(len(wl),len(wr),n//2)
    props=dict(contract=dict(type='string',enum=[VERSION]),snapshot=dict(type='string',enum=[source['snapshot']]),
        ownership=arr(pair(idx),n,n),groups=arr(tuple_([dict(type='integer',minimum=0,maximum=len(ROLES)-1),arr(line,0,n)],2),0,n),
        joins=arr(tuple_([enum(wl),enum(wr),hy],3),0,W),join_evidence=arr(pair(line),0,W),
        continuation=dict(type='boolean',**({} if source.get('previous') else {'enum':[False]})),
        boundary_join=dict(anyOf=[tuple_([enum([x for x in source.get('previous',{}).get('wrap_lefts',[]) if x in pri]),enum(wr),hy],3),dict(type='null')]) if pri and source.get('previous',{}).get('wrap_lefts') and wr else dict(type='null'),
        continuation_evidence=dict(anyOf=[obj(dict(previous=tuple_([enum(pri),dict(type='null')],2),current=tuple_([idx,line],2))),dict(type='null')]) if pri and n else dict(type='null'))
    eligible=[]
    for first,last in view.get('emphasis_ranges',[]):eligible.extend(ids[ids.index(first):ids.index(last)+1])
    props['emphasis']=arr(pair(enum(eligible)),0,n if eligible else 0)
    if view.get('note_candidates'):
        nc=view['note_candidates']
        props['note_bindings']=arr(obj(dict(reference=dict(type='string',enum=[r['id'] for r in nc['references']] or ['NO_VALID_ID']),
            note=dict(type='string',enum=[r['id'] for r in nc['labels']] or ['NO_VALID_ID']))),0,min(len(nc['references']),len(nc['labels'])))
    return obj(props)


def completion_bound(view, reviewer=False, problem_codes=()):
    if reviewer:
        count=decision_count(view['decisions'])
        shape=dict(snapshot=view['snapshot'],accept=False,continuation_accept=False,problems=list(problem_codes),decisions='0'*count)
    else:
        n=len(view['atoms']);last=max([0]+[a['id'] for a in view['atoms']]);prior=max([0]+[a['id'] for a in view.get('previous',{}).get('atoms',[])])
        memberships=construction._memberships(_source_refs(view,True))
        code=max([0]+[len(x)-1 for x in memberships.values()]);evidence=max([None,code],key=lambda x:len(_encoded(x)))
        shape=dict(contract=VERSION,snapshot=view['source_snapshot'],ownership=[[last,last]]*n,
            groups=[[len(ROLES)-1,[evidence,evidence]]]*n,
            joins=[[last,last,1]]*n,join_evidence=[[evidence,evidence]]*n,
            continuation=False,boundary_join=[prior,last,1],continuation_evidence=dict(previous=[prior,None],current=[last,evidence]),emphasis=[[last,last]]*n)
        if view.get('note_candidates'):
            nc=view['note_candidates'];refs=nc['references'];labels=nc['labels'];longest=max([x['id'] for x in refs+labels],key=lambda x:len(_encoded(x)),default='NO_VALID_ID')
            shape['note_bindings']=[dict(reference=longest,note=longest)]*min(len(refs),len(labels))
    return len(_encoded(shape))+1024


def validate_response_fields(prepared,response):
    source=json.loads(prepared.contract_json)
    atoms.need(type(response) is dict and set(response)==set(schema(source,prepared.model_view())['required']),
               'complete domain response fields required')
