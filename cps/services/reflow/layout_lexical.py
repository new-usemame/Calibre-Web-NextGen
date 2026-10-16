"""Lexical choices limited to actual adjacent immutable printed-wrap atoms."""
import json
from dataclasses import replace
from . import _layout_atoms as atoms
from .layout_ops import _need as need, ContractError
from .layout_requests import envelope
from ._layout_atoms import digest

PROMPT='''Decide whether a hyphen printed at a line/page break belongs to the word or merely splits a word for typesetting. You receive immutable left and right source fragments and their sentence context. Return one decision for every supplied ID: keep joins the fragments while retaining the printed hyphen; drop joins them while removing only that final hyphen; separate means these are not two parts of one word/compound or the evidence is insufficient. Preserve conventional spelling and meaningful compound hyphens, including productive compounds, rather than assuming every line-final hyphen is discretionary. Do not normalize spelling, case, accents or source punctuation. Output only the requested JSON decisions. You do not determine paragraph order or create words.'''

def schema(rows):
 return dict(type='object',properties={'decisions':dict(type='array',items=dict(type='object',properties={'id':dict(type='string',enum=[r['id'] for r in rows]),'decision':dict(type='string',enum=['keep','drop','separate'])},required=['id','decision'],additionalProperties=False))},required=['decisions'],additionalProperties=False)

def accept(rows,answer):
 need(isinstance(answer,dict) and set(answer)=={'decisions'},'decision fields')
 need(isinstance(answer['decisions'],list),'decision list')
 wanted={r['id'] for r in rows};seen=set()
 for value in answer['decisions']:
  need(isinstance(value,dict) and set(value)=={'id','decision'},'decision fields')
  need(value['id'] in wanted and value['id'] not in seen,'unknown or repeated decision')
  need(value['decision'] in ('keep','drop','separate'),'decision mode');seen.add(value['id'])
 need(seen==wanted,'missing lexical decision')
 return {v['id']:v['decision'] for v in answer['decisions']}

def edge(source, groups, first):
    from . import _layout_flow
    value = _layout_flow.edge(groups, not first)
    return value[1] if value else None


def candidates(plans):
    pages={p.prepared.page:(p,json.loads(p.prepared.contract_json),json.loads(p.answer_json)) for p in plans}
    need(len(pages)==len(plans),'duplicate lexical page')
    groups={p:atoms.validate(s,a) for p,(_,s,a) in pages.items()}
    rows=[]
    def add(left, la, right, ra, scope):
        l,r=la[-1],ra[0]
        if l['id'] not in left['wrap_lefts'] or r['id'] not in right['wrap_rights'] or l['protected'] or r['protected']:
            return
        row=dict(scope=scope,left_snapshot=left['snapshot'],right_snapshot=right['snapshot'],
            left_page=left['page'],right_page=right['page'],left=l['id'],right=r['id'],
            left_text=l['text'],right_text=r['text'],before=' '.join(a['text'] for a in la[-12:]),
            after=' '.join(a['text'] for a in ra[:12]))
        row['id']='h'+digest(row)[:20];rows.append(row)
    for page,(_,source,answer) in pages.items():
        for role,owned in groups[page]:
            if role in ('source','furniture','source_furniture'):
                continue
            for index in range(len(owned)-1):
                add(source,owned[:index+1],source,owned[index+1:],'within_page')
        if answer.get('continuation') and page-1 in pages:
            left=pages[page-1][1]
            prior=source.get('previous',{})
            binding={k:v for k,v in left['binding'].items() if k not in ('raster_sha256','raster_panels')}
            need(prior.get('identity')==left['identity'] and prior.get('binding')==binding,
                 'stale previous lexical source')
            before=edge(left,groups[page-1],False);after=edge(source,groups[page],True)
            from . import _layout_flow
            left_edge = _layout_flow.edge(groups[page-1], True)
            right_edge = _layout_flow.edge(groups[page], False)
            if before and after and left_edge[0] == right_edge[0]:
                add(left,before,source,after,'page_boundary')
    return rows


def request(plans):
    rows=candidates(plans)
    need(bool(rows),'empty lexical request')
    view=[{k:r[k] for k in ('id','left_text','right_text','before','after')} for r in rows]
    return envelope('lexical',digest(rows),view,PROMPT,schema(rows))


def apply(plans,response,source_binding):
    rows=candidates(plans)
    need(digest(rows)==source_binding,'stale lexical batch')
    decisions=accept(rows,response)
    answers={p.prepared.page:json.loads(p.answer_json) for p in plans}
    refused=set()
    for row in rows:
        mode=decisions[row['id']]
        if mode=='separate':
            refused.update((row['left_page'],row['right_page']))
            continue
        answer=answers[row['right_page']]
        join=dict(left=row['left'],right=row['right'],hyphen=mode)
        if row['scope']=='within_page':
            answer['joins']=[j for j in answer['joins'] if j['left']!=row['left']]+[join]
        else:
            answer['boundary_join']=join
    result=[]
    for plan in plans:
        page=plan.prepared.page
        if page in refused:
            continue
        answer=answers[page]
        if page-1 in refused:
            answer['continuation']=False;answer['boundary_join']=None
        try:
            atoms.validate(json.loads(plan.prepared.contract_json),answer)
        except ContractError:
            refused.add(page)
            continue
        result.append(replace(plan,answer_json=json.dumps(answer,sort_keys=True,separators=(',',':'))))
    final=[]
    for plan in result:
        answer=json.loads(plan.answer_json)
        if plan.prepared.page-1 in refused:
            answer.update(continuation=False,boundary_join=None)
            plan=replace(plan,answer_json=json.dumps(answer,sort_keys=True,separators=(',',':')))
        final.append(plan)
    return final,refused
