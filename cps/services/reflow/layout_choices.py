"""Future semantic choices; source facts are exact derivations, never paid repair."""
import copy
import json
from dataclasses import replace
from . import _layout_atoms as atoms, _layout_flow as flow, layout_construction as c
from . import layout_domain as legacy, layout_wire as wire
from .typed_model import _encoded

VERSION = 'layout-semantic-choices-1'
SOURCE = legacy.SOURCE.split('printed_lines ranges')[0] + '''printed_lines ranges supply ALL memberships for each current atom, in supplied line order. All memberships remain available; absent mapping is UNKNOWN. Endpoint IDs, ranges, positions and literal/resource neighbors derive exactly from complete ownership and declared order. No membership ordinal or endpoint count is a model choice. All other source fields, protected constraints, resources, geometry and previous context remain exact.'''
LAYOUT = legacy.LAYOUT
CHOICES = """Return ALL schema fields, contract and snapshot. ownership[i]=ONE scalar group index for EACH current atom, including empty protected glyphs; previous atoms are context only. Nonempty groups are in chosen final output order: {role,order,atoms}. order='source' explicitly chooses canonical order over assigned atoms, atoms=[]. order='explicit' requires the complete unique integer-ID permutation of exactly assigned atoms. Missing order, unused group, foreign/duplicate/missing permutation refuses; no defaults. joins={left,right,hyphen:keep|drop} for eligible adjacent current atoms. incoming={continue,previous,current,hyphen}. FALSE requires previous/current/hyphen=null; review derives actual current first-main/available previous candidate last-main context. TRUE chooses those scoped endpoints explicitly; hyphen=null means no lexical join, keep/drop an eligible boundary join. Both pages need admission. Never recopy memberships/counts. emphasis=[] or inclusive integer-ID pairs, independently judged; offered note_bindings use exact candidate IDs. """
REVIEW = legacy.REVIEW.replace('incoming=[0] selects previous/current evidence/proposed flag/boundary_join else []', 'associations interval selects EACH note binding; incoming=[0] ALWAYS selects the explicit incoming decision and derived actual candidate context').replace('Endpoints/roles/memberships derive exactly', 'Order mode, exact permutations, endpoints/roles/ALL memberships derive exactly')


def source_view(prepared, baseline=False):
    source=json.loads(prepared.contract_json)
    atoms.need(source['snapshot']==prepared.snapshot_id and atoms.digest({k:v for k,v in source.items() if k!='snapshot'})==source['snapshot'],
               'prepared semantic source snapshot differs')
    view = legacy.source_view(prepared, baseline)
    view.update(domain=VERSION, construction=dict(contract=VERSION, scope='current'))
    return view


def validate_response_fields(prepared, response):
    s = json.loads(prepared.contract_json)
    atoms.need(type(response) is dict and set(response) == set(schema(s, prepared.model_view())['required']),
               'complete semantic response fields required')


def expand(source, response):
    """Complete declared expansion, separately from the unchanged original JSON."""
    fields = {'contract','snapshot','ownership','groups','joins','incoming'}
    atoms.need(type(response) is dict and fields <= set(response) <= fields|{'emphasis','note_bindings'}, 'semantic response fields')
    atoms.need(response['contract'] == VERSION and response['snapshot'] == source['snapshot'], 'stale semantic source or contract')
    ids = [a['id'] for a in source['atoms']]
    owners, choices = response['ownership'], response['groups']
    atoms.need(type(owners) is list and len(owners) == len(ids) and type(choices) is list and len(choices) <= len(ids), 'incomplete semantic ownership')
    allocated = [[] for _ in choices]
    for identifier, gi in zip(ids, owners):
        atoms.need(type(gi) is int and 0 <= gi < len(choices), 'foreign or untyped semantic owner')
        allocated[gi].append(identifier)
    memberships = c._memberships(source)
    witness = dict(contract=VERSION, snapshot=source['snapshot'], ownership=[None]*len(ids), groups=[])
    selector = lambda x: [x, memberships[x][0] if memberships[x] else None]
    for gi, (choice, assigned) in enumerate(zip(choices, allocated)):
        atoms.need(type(choice) is dict and set(choice) == {'role','order','atoms'}, 'named semantic group fields')
        atoms.need(assigned and type(choice['role']) is str and choice['role'] in atoms.ROLES and type(choice['atoms']) is list, 'unused group or invalid role')
        if choice['order'] == 'source':
            atoms.need(choice['atoms'] == [], 'source order cannot contain permutation')
            ordered = assigned
        else:
            atoms.need(choice['order'] == 'explicit', 'required semantic order choice')
            ordered = [legacy._id(source, x) for x in choice['atoms']]
            atoms.need(len(ordered) == len(assigned) and len(set(ordered)) == len(ordered) and set(ordered) == set(assigned), 'foreign duplicate or missing semantic permutation')
        for pi, identifier in enumerate(ordered): witness['ownership'][ids.index(identifier)] = [gi, pi]
        ranges = c._ranges(ids, ordered)
        endpoints = list(dict.fromkeys(x for pair in ranges for x in pair))
        witness['groups'].append(dict(role=choice['role'], evidence=[selector(x) for x in endpoints]))
    def join(j):
        atoms.need(type(j) is dict and set(j) == {'left','right','hyphen'} and j['hyphen'] in ('keep','drop'), 'named semantic join fields')
        return dict(left=legacy._id(source,j['left']),right=legacy._id(source,j['right']),hyphen=j['hyphen'])
    atoms.need(type(response['joins']) is list, 'semantic joins')
    witness['joins'] = [join(j) for j in response['joins']]
    witness['join_evidence'] = [[selector(j[k]) for k in ('left','right')] for j in witness['joins']]
    inc = response['incoming']
    atoms.need(type(inc) is dict and set(inc) == {'continue','previous','current','hyphen'} and type(inc['continue']) is bool, 'named explicit incoming fields')
    witness.update(continuation=inc['continue'],boundary_join=None,continuation_evidence=None)
    if inc['continue']:
        previous = legacy._id(source,inc['previous'],True)
        current = legacy._id(source,inc['current'])
        atoms.need(inc['hyphen'] in (None,'keep','drop'), 'explicit incoming hyphen choice')
        witness['continuation_evidence'] = dict(previous=[previous,None],current=selector(current))
        if inc['hyphen'] is not None: witness['boundary_join'] = dict(left=previous,right=current,hyphen=inc['hyphen'])
    else:
        atoms.need(all(inc[k] is None for k in ('previous','current','hyphen')), 'false incoming cannot choose endpoints')
    if 'emphasis' in response: witness['emphasis'] = legacy.expand_emphasis(source,response['emphasis'])
    if 'note_bindings' in response: witness['note_bindings'] = copy.deepcopy(response['note_bindings'])
    answer = {k:copy.deepcopy(witness[k]) for k in ('snapshot','joins','continuation','boundary_join')}
    answer['groups'] = [dict(role=g['role'],ranges=c._ranges(ids,[ids[i] for i in sorted(range(len(ids)),key=lambda i:witness['ownership'][i][1]) if witness['ownership'][i][0]==gi])) for gi,g in enumerate(witness['groups'])]
    for key in ('emphasis','note_bindings'):
        if key in witness: answer[key] = witness[key]
    checked = atoms.validate(source,answer)
    if inc['continue']:
        edge = flow.edge(checked,False)
        atoms.need(edge and current == edge[1][0]['id'], 'incoming current differs from actual main endpoint')
    return witness, answer


def compile(source, response):
    return expand(source,response)[1]


def decisions(source, answer, response, prior=None):
    witness, _ = expand(source,response)
    # The legacy enumerator is mechanical, not the old paid choice contract.
    # Suppress its incoming row: NEW false decisions always have their own row.
    witness['continuation_evidence'] = None
    rows = c.decisions(source,answer,witness)
    members = c._memberships(source)
    for row in rows:
        row['memberships'] = [dict(id=s[0],state='supplied' if members[s[0]] else 'unknown',lines=members[s[0]]) for s in row['selectors']]
    for binding in response.get('note_bindings',[]):
        rows.append(dict(id='d'+str(len(rows)),kind='association',binding=copy.deepcopy(binding)))
    edge = flow.edge(atoms.validate(source,answer),False)
    current = edge[1][0]['id'] if edge else None
    prior_atoms = (prior or {}).get('last_main_paragraph')
    previous = prior_atoms[-1] if prior_atoms else None
    rows.append(dict(id='d'+str(len(rows)),kind='continuation',scope='previous_to_current',
        proposed=response['incoming']['continue'],choice=copy.deepcopy(response['incoming']),
        current_selector=[current, members[current][0] if members[current] else None] if current else None,
        current_memberships=dict(state='supplied' if members[current] else 'unknown',lines=members[current]) if current else None,
        current_role=edge[0] if edge else None,previous=previous,previous_role=(prior or {}).get('last_main_role')))
    return rows


def decision_table(source, answer, response):
    witness,_ = expand(source,response)
    table = legacy.decision_table(source,answer,witness)
    table.update(incoming=[0],associations=[0,len(response['note_bindings'])-1] if response.get('note_bindings') else [])
    return table


def decision_count(table):
    v=table.get('associations',[])
    return legacy.decision_count(table)+(v[1]-v[0]+1 if v else 0)


def proposal(prepared, previous=None):
    from . import layout_requests as req
    view = source_view(prepared); source=json.loads(prepared.contract_json)
    identity=dict(snapshot=prepared.snapshot_id,construction_version=VERSION)
    if previous is not None:
        _, prior, _ = req._previous_material(prepared,source,previous)
        view['previous_candidate']=legacy._native_candidate(prior)
        identity['previous_candidate']=prior['snapshot']
    return req.envelope('proposer',identity,view,SOURCE+'\n'+LAYOUT+'\n'+CHOICES+(req.PREVIOUS_CANDIDATE_PROMPT if previous is not None else ''),schema(source,prepared.model_view()))


def review(prepared, plan, previous=None):
    from . import layout_requests as req
    source,answer,_=req._candidate(prepared,plan)
    raw=json.loads(plan.construction_json); raw.pop('emphasis',None)
    witness,_=expand(source,raw)
    prior_identity,prior,prior_edge=req._previous_material(prepared,source,previous)
    if raw['incoming']['continue'] and prior_edge:
        atoms.need(witness['continuation_evidence']['previous'][0]==prior_edge[-1]['id'],'incoming evidence differs from previous endpoint')
    atoms.need([(j['left'],j['right']) for j in answer['joins']]==[(j['left'],j['right']) for j in witness['joins']], 'structural join choices differ')
    binding=atoms.digest(dict(version=VERSION,source=prepared.snapshot_id,candidate=answer,original_response=json.loads(plan.construction_json),previous=prior_identity))
    chosen={k:v for k,v in raw.items() if k not in ('contract','snapshot')}
    table=decision_table(source,answer,raw)
    atoms.need(decision_count(table)==len(decisions(source,answer,raw,prior)), 'semantic decision coverage differs')
    view=source_view(prepared,True); view.update(snapshot=binding,candidate_choices=chosen,decisions=table)
    if prior is not None: view['previous_candidate']=legacy._native_candidate(prior)
    return req.envelope('reviewer',dict(snapshot=prepared.snapshot_id,review_snapshot=binding,construction_version=VERSION),view,SOURCE+'\n'+LAYOUT+'\n'+CHOICES+'\n'+REVIEW,review_schema(binding,decision_count(table)))


def review_schema(binding,count):
    from . import layout_requests as req
    p=dict(snapshot=dict(type='string',enum=[binding]),accept=dict(type='boolean'),continuation_accept=dict(type='boolean'),problems=dict(type='array',items=dict(type='string',enum=list(req.REVIEW_CODES)),maxItems=len(req.REVIEW_CODES)),decisions=dict(type='string',minLength=count,maxLength=count,pattern='^[01]*$'))
    return dict(type='object',properties=p,required=list(p),additionalProperties=False)


def validate_review(prepared,plan,response,previous=None):
    from . import layout_requests as req
    view=json.loads(review(prepared,plan,previous)['messages'][-1]['content'])
    atoms.need(type(response) is dict and response.get('snapshot')==view['snapshot'],'stale semantic review')
    bits=response.get('decisions'); count=decision_count(view['decisions'])
    atoms.need(type(bits) is str and len(bits)==count and all(v in '01' for v in bits),'missing foreign or untyped semantic verdict bits')
    binding,original,_,_=req._review_material(prepared,plan,previous)
    expanded=dict(response,snapshot=binding,decisions={d['id']:bit=='1' for d,bit in zip(original['decisions'],bits)})
    return replace(req._legacy_validate_review(prepared,plan,expanded,previous),snapshot=view['snapshot'])


def pack_view(view):
    fixed=copy.deepcopy(view)
    dynamic={k:fixed.pop(k) for k in ('snapshot','candidate_choices','decisions','previous_candidate') if k in fixed}
    packed=wire._pack(fixed,wire.VERSION)
    chosen=dynamic.get('candidate_choices')
    if chosen and chosen['groups']:
        # Exact existing wire3 typed dictionaries; readable named logical fields.
        chosen['groups']={wire.KEY:dict(fields=['role','order','atoms'],column_values=dict(role=legacy.ROLES,order=['source','explicit']),rows=[[legacy.ROLES.index(g['role']),['source','explicit'].index(g['order']),g['atoms']] for g in chosen['groups']])}
    prior=dynamic.get('previous_candidate') or {}
    if prior.get('last_main_paragraph') is not None:
        prior['last_main_paragraph']={wire.KEY:dict(fields=['id','text'],rows=[[a['id'],a['text']]+([{k:v for k,v in a.items() if k not in ('id','text')}] if set(a)-{'id','text'} else []) for a in prior['last_main_paragraph']])}
    packed['view'].update(dynamic)
    atoms.need(wire.unpack(packed)==view,'semantic wire expansion changed full evidence')
    return packed


def schema(source, model_view=None):
    view=model_view or atoms.model_view(source); n=len(source['atoms'])
    def obj(p):return dict(type='object',properties=p,required=list(p),additionalProperties=False)
    def arr(item,lo=0,hi=None):return dict(type='array',items=item,minItems=lo,**({'maxItems':hi} if hi is not None else {}))
    def enum(v):return dict(type='integer',enum=[legacy._index(x) for x in v] or [-1])
    idx=dict(type='integer',minimum=0,maximum=max(0,n-1))
    prev=(source.get('previous') or {}).get('atoms',[])[-120:]
    W=min(len(source.get('wrap_lefts',[])),len(source.get('wrap_rights',[])),n//2)
    nullable=lambda t:dict(anyOf=[t,dict(type='null')])
    p=dict(contract=dict(type='string',enum=[VERSION]),snapshot=dict(type='string',enum=[source['snapshot']]),ownership=arr(idx,n,n),groups=arr(obj(dict(role=dict(type='string',enum=legacy.ROLES),order=dict(type='string',enum=['source','explicit']),atoms=arr(idx,0,n))),0,n),joins=arr(obj(dict(left=enum(source.get('wrap_lefts',[])),right=enum(source.get('wrap_rights',[])),hyphen=dict(type='string',enum=['keep','drop']))),0,W),incoming=obj({'continue':dict(type='boolean',**({} if prev and n else {'enum':[False]})), 'previous':nullable(enum([a['id'] for a in prev])) if prev else dict(type='null'), 'current':nullable(idx) if n else dict(type='null'), 'hyphen':dict(type=['string','null'],enum=['keep','drop',None])}))
    eligible=[];ids=[a['id'] for a in source['atoms']]
    for first,last in view.get('emphasis_ranges',[]):eligible.extend(ids[ids.index(first):ids.index(last)+1])
    p['emphasis']=arr(arr(enum(eligible),2,2),0,n if eligible else 0)
    if view.get('note_candidates'):
        nc=view['note_candidates'];p['note_bindings']=arr(obj(dict(reference=dict(type='string',enum=[r['id'] for r in nc['references']] or ['NO_VALID_ID']),note=dict(type='string',enum=[r['id'] for r in nc['labels']] or ['NO_VALID_ID']))),0,min(len(nc['references']),len(nc['labels'])))
    return obj(p)


def completion_bound(view,reviewer=False,problem_codes=()):
    if reviewer:
        shape=dict(snapshot=view['snapshot'],accept=False,continuation_accept=False,problems=list(problem_codes),decisions='0'*decision_count(view['decisions']))
    else:
        n=len(view['atoms']);last=max([0]+[a['id'] for a in view['atoms']]);prior=max([0]+[a['id'] for a in view.get('previous',{}).get('atoms',[])])
        # Deliberately overcount permutations: N group envelopes AND N total IDs.
        shape=dict(contract=VERSION,snapshot=view['source_snapshot'],ownership=[last]*n,groups=[dict(role=max(legacy.ROLES,key=len),order='explicit',atoms=[])]*n,joins=[dict(left=last,right=last,hyphen='keep')]*n,incoming={'continue':False,'previous':prior,'current':last,'hyphen':'keep'},emphasis=[[last,last]]*n)
        if view.get('note_candidates'):
            nc=view['note_candidates'];longest=max([x['id'] for x in nc['references']+nc['labels']],key=lambda x:len(_encoded(x)),default='NO_VALID_ID');shape['note_bindings']=[dict(reference=longest,note=longest)]*min(len(nc['references']),len(nc['labels']))
        return len(_encoded(shape))+n*(len(_encoded(last))+1)+1024
    return len(_encoded(shape))+1024
