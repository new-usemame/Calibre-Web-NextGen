"""Answer-space policy. Byte ceilings buy compact complete decisions, not quality.

Luna supports effort=none; OpenAI effort levels are not numeric reasoning caps.
A requested none with reported reasoning is a contract violation after settlement.
No guarantee is made about a provider's willingness or correctness of its answer.
"""
from .typed_model import _encoded
VERSION='layout-answer-allocation-6-ranges'


def version(profile_id=None, review_policy=None):
    from . import layout_luna_standard as standard, layout_glm, layout_luna_high
    if profile_id in layout_luna_high.PROFILE_IDS: return layout_luna_high.ALLOCATION_VERSION
    if review_policy or profile_id == layout_glm.PROFILE_ID: return layout_glm.ALLOCATION_VERSION
    return standard.REVIEW_ALLOCATION_VERSION if profile_id == standard.REVIEWER_ID else VERSION


def completion_tokens(stage,profile,view):
    visible = visible_completion_tokens(stage, profile, view)
    support = profile.supporting_reasoning_tokens
    if type(support) is not int or support < 0 or (support and
            (stage not in ('proposer', 'reviewer') or profile.reasoning_effort in ('none', 'disabled')
             or (stage == 'reviewer' and not _reasoning_review(profile)))):
        raise ValueError('invalid supporting reasoning allocation')
    # Added space shares the provider's total completion budget. It is neither
    # a numeric reasoning subcap nor a guarantee that the provider will use it.
    needed = visible + support
    if needed > profile.completion_cap:
        raise ValueError('complete layout answer exceeds configured completion capacity')
    return needed


def _reasoning_review(profile):
    from dataclasses import asdict
    from . import layout_model, layout_luna_standard, layout_luna_high
    return any(_encoded(asdict(profile)) == layout_model.NAMED_PROFILES[name][1]
               for name in (layout_luna_standard.REVIEWER_ID, layout_luna_high.REVIEWER_ID))


def visible_completion_tokens(stage,profile,view):
    needed=profile.output_tokens
    if isinstance(view,dict) and view.get('domain') == 'layout-range-choices-1':
        from . import layout_ranges,layout_requests
        needed=max(needed,layout_ranges.completion_bound(view,stage=='reviewer',layout_requests.REVIEW_CODES))
        if needed>profile.completion_cap:
            raise ValueError('complete layout answer exceeds configured completion capacity')
        return needed
    if isinstance(view,dict) and view.get('domain') == 'layout-semantic-choices-1':
        from . import layout_choices,layout_requests
        needed=max(needed,layout_choices.completion_bound(view,stage=='reviewer',layout_requests.REVIEW_CODES))
        if needed>profile.completion_cap:
            raise ValueError('complete layout answer exceeds configured completion capacity')
        return needed
    if isinstance(view,dict) and view.get('domain') == 'layout-domain-2':
        from . import layout_domain,layout_requests
        needed=max(needed,layout_domain.completion_bound(view,stage=='reviewer',layout_requests.REVIEW_CODES))
        if needed>profile.completion_cap:
            raise ValueError('complete layout answer exceeds configured completion capacity')
        return needed
    if stage=='proposer' and isinstance(view,dict) and isinstance(view.get('atoms'),list) and all(isinstance(a,dict) and isinstance(a.get('id'),str) for a in view['atoms']):
        ids=[a['id'] for a in view['atoms']]
        longest=max(ids,key=lambda s:len(_encoded(s)),default='NO_VALID_ID')
        # Coverage bounds groups/ranges/emphasis by atom count. Joins cannot reuse
        # an endpoint. Use the longest ID on both sides, including prior context.
        previous=[a['id'] for a in (view.get('previous') or {}).get('atoms',[])]
        endpoint=max(ids+previous,key=lambda s:len(_encoded(s)),default=longest)
        shape=dict(snapshot=view.get('snapshot','s'*64),
            groups=[dict(role='source_furniture',ranges=[[longest,longest]])]*len(ids),
            joins=[dict(left=endpoint,right=endpoint,hyphen='keep')]*len(ids),
            emphasis=[[longest,longest]]*len(ids),continuation=False,
            boundary_join=dict(left=endpoint,right=endpoint,hyphen='keep'))
        candidates=view.get('note_candidates') or {}
        if candidates:
            references=candidates.get('references',[]);labels=candidates.get('labels',[])
            selector=max([r['id'] for r in references+labels],key=lambda s:len(_encoded(s)),default=longest)
            shape['note_bindings']=[dict(reference=selector,note=selector)]*min(len(references),len(labels))
        needed=max(needed,len(_encoded(shape))+1024)
    if stage=='proposer' and isinstance(view,dict) and view.get('construction'):
        from . import layout_construction
        ids=[a['id'] for a in view['atoms']];n=len(ids)
        longest=max(ids,key=lambda s:len(_encoded(s)),default='NO_VALID_ID')
        lines=[l['source_line'] for l in view.get('printed_lines',[])]
        line=max([None]+lines,key=lambda v:len(_encoded(v)))
        # Across all groups there are at most N maximal source ranges and 2N
        # endpoint selectors. Each local join uses two more selectors; every
        # endpoint/owner index is bounded by N. No tokenizer estimate is used.
        previous=[a['id'] for a in (view.get('previous') or {}).get('atoms',[])]
        endpoint=max(ids+previous,key=lambda s:len(_encoded(s)),default=longest)
        shape=dict(contract=layout_construction.VERSION,snapshot=view['snapshot'],
            ownership=[[max(0,n-1),max(0,n-1)]]*n,
            groups=[dict(role='source_furniture',evidence=[[longest,line],[longest,line]])]*n,
            joins=[dict(left=longest,right=longest,hyphen='keep')]*n,
            join_evidence=[[[longest,line],[longest,line]]]*n,
            continuation=False,boundary_join=dict(left=endpoint,right=longest,hyphen='keep'),
            continuation_evidence=dict(previous=[endpoint,None],current=[longest,line]),
            emphasis=[[longest,longest]]*n)
        candidates=view.get('note_candidates') or {}
        if candidates:
            refs=candidates.get('references',[]);labels=candidates.get('labels',[])
            selector=max([r['id'] for r in refs+labels],key=lambda s:len(_encoded(s)),default=longest)
            shape['note_bindings']=[dict(reference=selector,note=selector)]*min(len(refs),len(labels))
        needed=max(needed,len(_encoded(shape))+1024)
    elif stage=='reviewer' and isinstance(view,dict) and isinstance(view.get('decisions'),list):
        from . import layout_requests
        shape=dict(snapshot=view['snapshot'],accept=False,continuation_accept=False,
            problems=list(layout_requests.REVIEW_CODES),decisions={d['id']:False for d in view['decisions']})
        needed=max(needed,len(_encoded(shape))+1024)
    elif stage in ('ordering','lexical') and isinstance(view,list):
        rows=[dict(id=r['id'],accept=False,problems=['reading_order','association','insufficient_geometry'])
              if stage=='ordering' else dict(id=r['id'],decision='separate') for r in view]
        needed=max(needed,len(_encoded(dict(decisions=rows)))+1024)
    if needed>profile.completion_cap:
        raise ValueError('complete layout answer exceeds configured completion capacity')
    return needed
